"""Telegram bot: listen for /generate and trigger the pipeline."""

from __future__ import annotations

import threading
import sys
from pathlib import Path

import structlog
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import configure_logging, get_settings
from .run import main as run_pipeline

log = structlog.get_logger(__name__)


def _run_pipeline_sync(chat_id: int, bot_app: Application) -> None:
    """Run the pipeline in a background thread and report back."""
    try:
        import asyncio
        log.info("bot.pipeline_starting")
        exit_code = run_pipeline(dry_run=False)
        if exit_code == 0:
            msg = "✅ Podcast generated and delivered!"
        else:
            msg = f"❌ Pipeline failed (exit {exit_code})"
    except Exception as exc:
        log.error("bot.pipeline_error", error=str(exc))
        msg = f"❌ Pipeline error: {exc}"
    asyncio.run(bot_app.bot.send_message(chat_id=chat_id, text=msg))


async def _generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    await context.bot.send_message(chat_id=chat_id, text="⏳ Generating podcast...")
    threading.Thread(
        target=_run_pipeline_sync,
        args=(chat_id, context.application),
        daemon=True,
    ).start()


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("generate", "Generate and deliver today's podcast"),
        BotCommand("status", "Show last run status"),
    ])


async def _status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    out_dir = Path("/app/out")
    if not out_dir.exists():
        out_dir = Path("/opt/_podcaster-ai/out")

    episodes = sorted(out_dir.glob("*_episode.mp3")) if out_dir.exists() else []
    if not episodes:
        await context.bot.send_message(chat_id=chat_id, text="No episodes found yet.")
        return

    latest = episodes[-1]
    date_str = latest.stem.split("_")[0]
    size_mb = latest.stat().st_size / (1024 * 1024)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📡 Last episode: {date_str}\n📁 {size_mb:.1f} MB\n⏰ Next scheduled: 09:30 IST",
    )


def _check_authorization(update: Update) -> bool:
    settings = get_settings()
    allowed = settings.telegram_chat_id
    if not allowed:
        return False
    uid = str(update.effective_user.id) if update.effective_user else ""
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return uid == allowed or chat_id == allowed


async def _authorized_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_authorization(update):
        await update.message.reply_text("⛔ Unauthorized") if update.message else None
        return
    await _generate(update, context)


async def _status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_authorization(update):
        await update.message.reply_text("⛔ Unauthorized") if update.message else None
        return
    await _status(update, context)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("bot.update_error", error=str(context.error))


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    token = settings.telegram_bot_token
    if not token:
        log.error("bot.no_token", detail="Set TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("generate", _authorized_handler))
    app.add_handler(CommandHandler("status", _status_handler))
    app.add_error_handler(_error_handler)

    log.info("bot.starting", provider=settings.llm_provider, model=settings.llm_model)
    app.run_polling()


if __name__ == "__main__":
    main()
