"""Main entry point: orchestrate the full pipeline end-to-end."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .config import configure_logging, get_settings
from .deliver import DeliveryError, send_sync
from .llm import LLMCallError, LLMConfigError
from .research import build_research_brief
from .script import ScriptResult, write_script
from .shownotes import generate_shownotes
from .tts import TTSError, render_async

log = structlog.get_logger(__name__)


def main(dry_run: bool = False) -> int:
    """Run the full podcast generation pipeline.

    Args:
        dry_run: if True, skip Telegram delivery and save artifacts to ./out/

    Returns:
        0 on success, 1 on failure.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    episode_date = datetime.now(timezone.utc)
    date_str = episode_date.strftime("%Y%m%d")

    log.info("pipeline.start", dry_run=dry_run, date=date_str)

    # Stage 1: Research — gather, dedupe, rank, build brief.
    try:
        brief = build_research_brief()
    except Exception as exc:  # noqa: BLE001
        log.error("pipeline.research_failed", error=str(exc))
        return 1

    brief_path = output_dir / f"{date_str}_brief.json"
    import json

    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2))
    log.info("pipeline.brief_saved", path=str(brief_path))

    # Stage 2: Script — turn brief into two-host dialogue.
    try:
        script = write_script(brief)
    except Exception as exc:  # noqa: BLE001
        log.error("pipeline.script_failed", error=str(exc))
        return 1

    script_path = output_dir / f"{date_str}_script.txt"
    script_path.write_text(script.dialogue)
    log.info("pipeline.script_saved", path=str(script_path))

    # Stage 3: TTS — render dialogue to MP3.
    mp3_path = output_dir / f"{date_str}_episode.mp3"
    try:
        render_async(script.turns, mp3_path)
    except TTSError as exc:
        log.error("pipeline.tts_failed", error=str(exc))
        return 1

    log.info("pipeline.audio_rendered", path=str(mp3_path), size_bytes=mp3_path.stat().st_size)

    # Stage 4: Show notes — generate markdown.
    try:
        shownotes = generate_shownotes(script, brief, episode_date)
    except Exception as exc:  # noqa: BLE001
        log.error("pipeline.shownotes_failed", error=str(exc))
        return 1

    shownotes_path = output_dir / f"{date_str}_shownotes.md"
    shownotes_path.write_text(shownotes, encoding="utf-8")
    log.info("pipeline.shownotes_saved", path=str(shownotes_path))

    # Stage 5: Deliver — send to Telegram (or dry-run).
    try:
        send_sync(mp3_path, shownotes, script.title, dry_run=dry_run)
    except DeliveryError as exc:
        log.error("pipeline.delivery_failed", error=str(exc))
        if not dry_run:
            return 1
        # In dry-run, log but don't fail.

    # Stage 6 (optional): publish to dashboard layout (<data_dir>/episodes/) and
    # register the episode in the dashboard's sqlite DB. Fail-soft: if the web
    # extras aren't installed or DATA_DIR isn't configured, this stage is a no-op.
    try:
        from datetime import date as _date_cls
        ep_date = episode_date.date() if hasattr(episode_date, "date") else _date_cls.today()
        episodes_dir = settings.episodes_dir()
        episodes_dir.mkdir(parents=True, exist_ok=True)
        canonical_mp3 = episodes_dir / f"{ep_date.isoformat()}.mp3"
        canonical_md = episodes_dir / f"{ep_date.isoformat()}.md"
        # Copy (don't move) so OUTPUT_DIR keeps its existing layout.
        try:
            import shutil
            if mp3_path.resolve() != canonical_mp3.resolve():
                shutil.copy2(mp3_path, canonical_mp3)
            if shownotes_path.resolve() != canonical_md.resolve():
                shutil.copy2(shownotes_path, canonical_md)
        except Exception as copy_exc:  # noqa: BLE001
            log.warning("pipeline.episode_copy_failed", error=str(copy_exc))
        try:
            from .web.db import register_episode
            register_episode(
                ep_date,
                canonical_mp3,
                canonical_md,
                title=script.title,
                items=brief.get("_items") or None,
            )
        except Exception as reg_exc:  # noqa: BLE001
            log.warning("pipeline.register_episode_skipped", error=str(reg_exc))
    except Exception as outer_exc:  # noqa: BLE001
        log.warning("pipeline.publish_skipped", error=str(outer_exc))

    log.info(
        "pipeline.complete",
        dry_run=dry_run,
        title=script.title,
        mp3=str(mp3_path),
        shownotes=str(shownotes_path),
    )
    return 0


def cli() -> int:
    """Parse CLI args and run the pipeline."""
    parser = argparse.ArgumentParser(
        prog="podcaster-ai",
        description="Daily bug-bounty podcast generator.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Telegram delivery; save artifacts to OUTPUT_DIR.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )
    args = parser.parse_args()

    try:
        return main(dry_run=args.dry_run)
    except (LLMConfigError, LLMCallError) as exc:
        log.error("pipeline.llm_error", error=str(exc))
        return 1
    except KeyboardInterrupt:
        log.warning("pipeline.interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.error("pipeline.unexpected_error", error=str(exc), exc_info=True)
        return 1
