"""Main entry point: orchestrate the full pipeline end-to-end."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .config import configure_logging, get_settings
from .deliver import DeliveryError, send_sync
from .llm import LLMCallError, LLMConfigError
from .research import build_research_brief
from .script import ScriptResult, write_script
from .shownotes import generate_pdf, generate_shownotes
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
    try:
        os.chmod(brief_path, 0o666)
    except PermissionError:
        pass
    log.info("pipeline.brief_saved", path=str(brief_path))

    # Stage 2: Script — turn brief into two-host dialogue.
    try:
        script = write_script(brief)
    except Exception as exc:  # noqa: BLE001
        log.error("pipeline.script_failed", error=str(exc))
        return 1

    script_path = output_dir / f"{date_str}_script.txt"
    script_path.write_text(script.dialogue)
    try:
        os.chmod(script_path, 0o666)
    except PermissionError:
        pass
    log.info("pipeline.script_saved", path=str(script_path))

    # Stage 3: TTS — render dialogue to MP3.
    mp3_path = output_dir / f"{date_str}_episode.mp3"
    try:
        render_async(script.turns, mp3_path)
        try:
            mp3_path.chmod(0o666)
        except PermissionError:
            pass
    except TTSError as exc:
        log.error("pipeline.tts_failed", error=str(exc))
        return 1

    log.info("pipeline.audio_rendered", path=str(mp3_path), size_bytes=mp3_path.stat().st_size)

    # Stage 4: Show notes — generate markdown + PDF.
    try:
        shownotes_md = generate_shownotes(script, brief, episode_date)
    except Exception as exc:  # noqa: BLE001
        log.error("pipeline.shownotes_failed", error=str(exc))
        return 1

    md_path = output_dir / f"{date_str}_shownotes.md"
    md_path.write_text(shownotes_md, encoding="utf-8")
    try:
        os.chmod(md_path, 0o666)
    except PermissionError:
        pass
    log.info("pipeline.shownotes_saved", path=str(md_path))

    pdf_path = output_dir / f"{date_str}_shownotes.pdf"
    try:
        generate_pdf(script, brief, pdf_path, episode_date)
        if pdf_path.exists():
            try:
                os.chmod(pdf_path, 0o666)
            except PermissionError:
                pass
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.pdf_failed", error=str(exc), path=str(pdf_path))

    # Stage 5: Deliver — send to Telegram (or dry-run).
    try:
        send_sync(
            mp3_path,
            shownotes_md,
            pdf_path,
            script.title,
            dry_run=dry_run,
            md_path=md_path,
        )
    except DeliveryError as exc:
        log.error("pipeline.delivery_failed", error=str(exc))
        if not dry_run:
            return 1
        # In dry-run, log but don't fail.

    log.info(
        "pipeline.complete",
        dry_run=dry_run,
        title=script.title,
        mp3=str(mp3_path),
        shownotes=str(md_path),
        pdf=str(pdf_path) if pdf_path.exists() else None,
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


if __name__ == "__main__":
    sys.exit(cli())
