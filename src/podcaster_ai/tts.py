"""Text-to-speech layer: pluggable backends (edge-tts free, ElevenLabs paid)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

import structlog
from pydub import AudioSegment, effects

from .config import get_settings

log = structlog.get_logger(__name__)

# Target inter-turn silence: 80-120ms. We'll use 100ms.
INTER_TURN_SILENCE_MS: Final[int] = 100
# Linear crossfade: 30-50ms. We'll use 40ms.
CROSS_FADE_MS: Final[int] = 40
# Target loudness normalization (dBFS)
TARGET_DBFS: Final[float] = -20.0
OUTPUT_BITRATE: Final[str] = "128k"


class TTSError(RuntimeError):
    """Raised when TTS generation fails."""


async def _edge_tts_render(speaker: str, voice: str, text: str, rate: str = "+0%") -> bytes:
    """Render text to speech using edge-tts (free, no key required)."""
    try:
        import edge_tts  # type: ignore
    except ImportError:
        raise TTSError("edge-tts not installed. Install via: pip install edge-tts")

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, volume="+0%")
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    if not chunks:
        raise TTSError(f"edge-tts returned no audio for speaker={speaker}, voice={voice}")
    return b"".join(chunks)


async def _elevenlabs_render(
    speaker: str, voice_id: str, text: str, api_key: str, model: str
) -> bytes:
    """Render text to speech using ElevenLabs API."""
    try:
        from elevenlabs import generate  # type: ignore
    except ImportError:
        raise TTSError("elevenlabs not installed. Install via: pip install elevenlabs")

    try:
        audio = generate(text=text, voice=voice_id, api_key=api_key, model=model)
        if isinstance(audio, bytes):
            return audio
        if hasattr(audio, "read"):
            return audio.read()
        return bytes(audio)
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"ElevenLabs render failed: {exc}") from exc


async def render_speaker_turns(
    turns: list[tuple[str, str]], output_path: Path
) -> None:
    """Render all speaker turns, normalize, and concatenate with crossfades.

    Args:
        turns: list of (speaker_name, text) tuples. speaker_name must be
               "MAYA" or "ARJUN".
        output_path: where to write the final MP3.

    Raises:
        TTSError: if any render fails.
    """
    settings = get_settings()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    segments: list[AudioSegment] = []

    for i, (speaker, text) in enumerate(turns):
        if not text.strip():
            continue

        speaker_upper = speaker.upper()
        if speaker_upper not in ("MAYA", "ARJUN"):
            raise TTSError(f"Unknown speaker: {speaker}")

        try:
            if settings.tts_provider == "edge":
                edge_voice = (
                    settings.maya_voice if speaker_upper == "MAYA"
                    else settings.arjun_voice
                )
                audio_bytes = await _edge_tts_render(speaker_upper, edge_voice, text, rate=settings.tts_rate)
            elif settings.tts_provider == "elevenlabs":
                if speaker_upper == "MAYA":
                    voice_id = settings.elevenlabs_maya_voice_id
                else:
                    voice_id = settings.elevenlabs_arjun_voice_id
                if not voice_id:
                    raise TTSError(
                        f"ElevenLabs voice ID not set for {speaker_upper}. "
                        "Set ELEVENLABS_MAYA_VOICE_ID or ELEVENLABS_ARJUN_VOICE_ID."
                    )
                audio_bytes = await _elevenlabs_render(
                    speaker_upper,
                    voice_id,
                    text,
                    settings.elevenlabs_api_key or "",
                    settings.elevenlabs_model,
                )
            else:
                raise TTSError(f"Unknown TTS provider: {settings.tts_provider}")

            # Load into pydub
            seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
            
            # Normalize loudness
            seg = effects.normalize(seg)
            # Apply target dBFS for consistency across turns
            gain = TARGET_DBFS - seg.dBFS
            seg = seg.apply_gain(gain)
            
            segments.append(seg)
            log.debug("tts.rendered_turn", speaker=speaker_upper, turn=i)
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Failed to render turn {i} ({speaker_upper}): {exc}") from exc

    if not segments:
        raise TTSError("No speaker turns to render.")

    # Concatenate with crossfades and inter-turn silence
    combined = segments[0]
    silence = AudioSegment.silent(duration=INTER_TURN_SILENCE_MS)
    
    for i in range(1, len(segments)):
        # Append silence and then the next turn with crossfade
        combined = combined.append(silence, crossfade=0)
        combined = combined.append(segments[i], crossfade=CROSS_FADE_MS)

    # Export to final MP3
    try:
        combined.export(
            output_path,
            format="mp3",
            bitrate=OUTPUT_BITRATE,
            parameters=["-ac", "1"]
        )
    except Exception as exc:
        raise TTSError(f"Failed to export final audio: {exc}") from exc

    log.info("tts.rendered", output=output_path, turns=len(segments))


import io

def render_async(turns: list[tuple[str, str]], output_path: Path) -> None:
    """Sync wrapper around async render_speaker_turns."""
    try:
        asyncio.run(render_speaker_turns(turns, output_path))
    except Exception as exc:
        raise TTSError(f"TTS render failed: {exc}") from exc
