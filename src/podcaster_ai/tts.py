"""Text-to-speech layer: pluggable backends (edge-tts free, ElevenLabs paid)."""

from __future__ import annotations

import asyncio
import io
import subprocess
from pathlib import Path
from typing import Final, Optional

import structlog

from .config import get_settings

log = structlog.get_logger(__name__)

PAUSE_SAME_SPEAKER_MS: Final[int] = 350
PAUSE_SEGMENT_MS: Final[int] = 550
OUTPUT_BITRATE: Final[str] = "128k"


class TTSError(RuntimeError):
    """Raised when TTS generation fails."""


async def _edge_tts_render(speaker: str, voice: str, text: str) -> bytes:
    """Render text to speech using edge-tts (free, no key required)."""
    try:
        import edge_tts  # type: ignore
    except ImportError:
        raise TTSError("edge-tts not installed. Install via: pip install edge-tts")

    communicate = edge_tts.Communicate(text, voice=voice, rate="+0%", volume="+0%")
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
    """Render all speaker turns to individual WAV files, then concat with ffmpeg.

    Args:
        turns: list of (speaker_name, text) tuples. speaker_name must be
               "MAYA" or "ARJUN".
        output_path: where to write the final MP3 (128 kbps, mono).

    Raises:
        TTSError: if any render fails.
    """
    settings = get_settings()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render each turn to a temporary WAV.
    temp_dir = output_path.parent / ".tts_temp"
    temp_dir.mkdir(exist_ok=True)
    wav_files: list[tuple[str, Path]] = []

    for i, (speaker, text) in enumerate(turns):
        if not text.strip():
            continue

        speaker_upper = speaker.upper()
        if speaker_upper == "MAYA":
            voice = settings.maya_voice
        elif speaker_upper == "ARJUN":
            voice = settings.arjun_voice
        else:
            raise TTSError(f"Unknown speaker: {speaker}")

        wav_path = temp_dir / f"{i:04d}_{speaker_upper}.wav"
        try:
            if settings.tts_provider == "edge":
                audio_bytes = await _edge_tts_render(speaker_upper, voice, text)
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

            wav_path.write_bytes(audio_bytes)
            wav_files.append((speaker_upper, wav_path))
            log.debug("tts.rendered_turn", speaker=speaker_upper, turn=i)
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Failed to render turn {i} ({speaker_upper}): {exc}") from exc

    if not wav_files:
        raise TTSError("No speaker turns to render.")

    # Build ffmpeg concat filter with pauses.
    # Strategy: interleave audio + silence segments.
    silence_same_speaker = _make_silence(PAUSE_SAME_SPEAKER_MS)
    silence_segment = _make_silence(PAUSE_SEGMENT_MS)
    silence_same_path = temp_dir / "silence_same.wav"
    silence_segment_path = temp_dir / "silence_segment.wav"
    silence_same_path.write_bytes(silence_same_speaker)
    silence_segment_path.write_bytes(silence_segment)

    # Determine pause between consecutive turns.
    concat_parts: list[Path] = []
    for idx, (speaker, wav) in enumerate(wav_files):
        concat_parts.append(wav)
        if idx < len(wav_files) - 1:
            next_speaker = wav_files[idx + 1][0]
            # Use segment pause if speaker changes, same-speaker pause otherwise.
            pause_path = silence_segment_path if speaker != next_speaker else silence_same_path
            concat_parts.append(pause_path)

    # Concat all WAVs + silences via ffmpeg.
    concat_file = temp_dir / "concat.txt"
    with concat_file.open("w") as f:
        for p in concat_parts:
            f.write(f"file '{p.absolute()}'\n")

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "pcm_s16le",
                "-y",
                str(temp_dir / "merged.wav"),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"ffmpeg concat failed: {exc.stderr.decode()}") from exc
    except FileNotFoundError:
        raise TTSError("ffmpeg not found. Install via: apt-get install ffmpeg")

    # Convert merged WAV to MP3 (128 kbps, mono).
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(temp_dir / "merged.wav"),
                "-b:a",
                OUTPUT_BITRATE,
                "-ac",
                "1",
                "-y",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"ffmpeg encode to MP3 failed: {exc.stderr.decode()}") from exc

    # Clean up temp files.
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)
    log.info("tts.rendered", output=output_path, turns=len(wav_files))


def _make_silence(duration_ms: int) -> bytes:
    """Generate a silent WAV segment (PCM s16le, 24 kHz, mono)."""
    sample_rate = 24000
    channels = 1
    sample_width = 2
    num_samples = (duration_ms * sample_rate) // 1000
    silence = b"\x00" * (num_samples * channels * sample_width)
    # Minimal WAV header for PCM s16le, 24kHz, mono.
    fmt_chunk = (
        b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # audio format (PCM)
        + channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + (sample_rate * channels * sample_width).to_bytes(4, "little")
        + (channels * sample_width).to_bytes(2, "little")
        + (16).to_bytes(2, "little")  # bits per sample
    )
    data_chunk = b"data" + len(silence).to_bytes(4, "little") + silence
    wav_header = (
        b"RIFF"
        + (36 + len(data_chunk)).to_bytes(4, "little")
        + b"WAVE"
        + fmt_chunk
        + data_chunk
    )
    return wav_header


def render_async(turns: list[tuple[str, str]], output_path: Path) -> None:
    """Sync wrapper around async render_speaker_turns."""
    try:
        asyncio.run(render_speaker_turns(turns, output_path))
    except Exception as exc:
        raise TTSError(f"TTS render failed: {exc}") from exc
