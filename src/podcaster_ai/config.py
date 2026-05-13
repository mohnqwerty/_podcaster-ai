"""Configuration loader.

All runtime settings come from environment variables (loaded via pydantic-settings).
A `.env` file in the working directory is read automatically when present.
No defaults reference real credentials.
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProvider = Literal["deepseek", "openrouter", "kimi", "qwen", "gemini", "groq"]
TTSProvider = Literal["edge", "elevenlabs"]


class Settings(BaseSettings):
    """Strongly-typed runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- LLM ----------
    llm_provider: LLMProvider = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_base_url: Optional[str] = None
    llm_temperature: float = 0.4
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 4

    deepseek_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    moonshot_api_key: Optional[str] = None
    moonshot_api_base: Optional[str] = None
    dashscope_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None

    # ---------- TTS ----------
    tts_provider: TTSProvider = "edge"
    maya_voice: str = "en-US-AriaNeural"
    arjun_voice: str = "en-US-GuyNeural"

    elevenlabs_api_key: Optional[str] = None
    elevenlabs_maya_voice_id: Optional[str] = None
    elevenlabs_arjun_voice_id: Optional[str] = None
    elevenlabs_model: str = "eleven_turbo_v2_5"

    # ---------- Telegram ----------
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # ---------- Sources ----------
    vendor_rss_feeds: str = ""
    youtube_channel_ids: str = "UCS90qS2YOo6HQC3uH9_95MA,UC6Om9kAkl32dWlDS_lX9W3Q"
    youtube_lookback_days: int = 14
    nvd_min_cvss: float = 7.0
    nvd_lookback_hours: int = 72
    max_items_per_source: int = 8
    max_total_items: int = 40

    # ---------- Podcast metadata ----------
    podcast_title: str = "Daily Recon"
    host_maya_name: str = "Maya"
    host_arjun_name: str = "Arjun"
    timezone: str = "Asia/Kolkata"

    # ---------- Output / runtime ----------
    output_dir: Path = Path("/app/out")
    log_level: str = "INFO"
    # When set, episode artifacts are also/instead written under <data_dir>/episodes/.
    data_dir: Optional[Path] = None

    # ---------- Web dashboard (PART B) ----------
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    # 32+ random characters; required for the dashboard to start.
    session_secret: Optional[str] = None
    session_cookie_secure: bool = True
    session_cookie_name: str = "podcaster_session"
    csrf_cookie_name: str = "podcaster_csrf"
    bootstrap_admin_user: Optional[str] = None
    bootstrap_admin_password: Optional[str] = None
    # Defaults to sqlite under <data_dir>/podcaster.db when unset.
    database_url: Optional[str] = None

    # --- helpers ---

    @field_validator("vendor_rss_feeds", "youtube_channel_ids", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    def vendor_feed_list(self) -> list[str]:
        return [u.strip() for u in self.vendor_rss_feeds.split(",") if u.strip()]

    def youtube_channels(self) -> list[str]:
        return [c.strip() for c in self.youtube_channel_ids.split(",") if c.strip()]

    def llm_api_key(self) -> Optional[str]:
        return {
            "deepseek": self.deepseek_api_key,
            "openrouter": self.openrouter_api_key,
            "kimi": self.moonshot_api_key,
            "qwen": self.dashscope_api_key,
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
        }[self.llm_provider]

    def effective_data_dir(self) -> Path:
        """Where shared/persistent state lives (sqlite db, episodes/).

        Falls back to OUTPUT_DIR for backwards compat with the v1/v2 layout.
        """
        return Path(self.data_dir) if self.data_dir else Path(self.output_dir)

    def episodes_dir(self) -> Path:
        return self.effective_data_dir() / "episodes"

    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = self.effective_data_dir() / "podcaster.db"
        return f"sqlite+aiosqlite:///{db_path}"

    def llm_endpoint(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url
        return {
            "deepseek": "https://api.deepseek.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "kimi": self.moonshot_api_base or "https://api.moonshot.cn/v1",
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "groq": "https://api.groq.com/openai/v1",
        }[self.llm_provider]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


def configure_logging(level: Optional[str] = None) -> None:
    """Configure structlog + stdlib logging with a JSON renderer.

    Safe to call multiple times.
    """
    lvl = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    log_level = getattr(logging, lvl, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
