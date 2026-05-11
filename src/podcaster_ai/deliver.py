"""Delivery stage: send podcast + show notes to Telegram."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog
from telegram import Bot
from telegram.error import TelegramError

from .config import get_settings

log = structlog.get_logger(__name__)


class DeliveryError(RuntimeError):
    """Raised when delivery fails."""


async def send_to_telegram(
    mp3_path: Path,
    shownotes_text: str,
    episode_title: str,
    dry_run: bool = False,
) -> None:
    """Send the podcast MP3 and show notes to Telegram.

    Args:
        mp3_path: path to the MP3 file.
        shownotes_text: markdown show notes.
        episode_title: episode title for the caption.
        dry_run: if True, log the action but don't actually send.

    Raises:
        DeliveryError: if sending fails (unless dry_run).
    """
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        raise DeliveryError(
            "Telegram delivery not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )

    if dry_run:
        log.info(
            "deliver.dry_run",
            mp3_path=str(mp3_path),
            chat_id=chat_id,
            title=episode_title,
        )
        return

    if not mp3_path.exists():
        raise DeliveryError(f"MP3 file not found: {mp3_path}")

    bot = Bot(token=token)

    try:
        # Send the MP3 as a voice/audio message.
        with mp3_path.open("rb") as f:
            await bot.send_audio(
                chat_id=chat_id,
                audio=f,
                title=episode_title,
                caption=f"🎙️ {episode_title}",
                parse_mode="HTML",
            )
        log.info("deliver.audio_sent", chat_id=chat_id, title=episode_title)
    except TelegramError as exc:
        raise DeliveryError(f"Failed to send audio to Telegram: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise DeliveryError(f"Telegram audio send error: {exc}") from exc

    try:
        # Send the show notes as a document (or as a message if it's short enough).
        if len(shownotes_text) <= 4096:
            await bot.send_message(
                chat_id=chat_id,
                text=shownotes_text,
                parse_mode="Markdown",
            )
            log.info("deliver.shownotes_sent_as_message", chat_id=chat_id)
        else:
            # Write to a temp file and send as a document.
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as f:
                f.write(shownotes_text)
                temp_path = f.name

            try:
                with open(temp_path, "rb") as f:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=f"shownotes_{episode_title.replace(' ', '_')}.md",
                        caption="📋 Show notes",
                        parse_mode="Markdown",
                    )
                log.info("deliver.shownotes_sent_as_document", chat_id=chat_id)
            finally:
                Path(temp_path).unlink(missing_ok=True)
    except TelegramError as exc:
        raise DeliveryError(f"Failed to send show notes to Telegram: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise DeliveryError(f"Telegram show notes send error: {exc}") from exc

    log.info("deliver.complete", chat_id=chat_id, title=episode_title)


def send_sync(
    mp3_path: Path,
    shownotes_text: str,
    episode_title: str,
    dry_run: bool = False,
) -> None:
    """Sync wrapper around async send_to_telegram."""
    import asyncio

    try:
        asyncio.run(send_to_telegram(mp3_path, shownotes_text, episode_title, dry_run))
    except DeliveryError:
        raise
    except Exception as exc:
        raise DeliveryError(f"Delivery failed: {exc}") from exc
