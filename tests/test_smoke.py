"""Smoke tests: verify imports and basic config loading."""

import sys
from pathlib import Path

# Add src to path for testing.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_imports() -> None:
    """Verify all main modules import without error."""
    import podcaster_ai
    import podcaster_ai.config
    import podcaster_ai.llm
    import podcaster_ai.research
    import podcaster_ai.script
    import podcaster_ai.tts
    import podcaster_ai.shownotes
    import podcaster_ai.deliver
    import podcaster_ai.run
    from podcaster_ai.pipeline.sources import (
        ai_security_news,
        cisa_kev,
        conferences,
        hackerone_hacktivity,
        hardware_hacking,
        nvd_recent,
        portswigger_rss,
        projectdiscovery_releases,
        vendor_rss,
        youtube_transcripts,
    )

    assert podcaster_ai.__version__ == "0.1.0"


def test_config_loading() -> None:
    """Verify config loads from environment."""
    from podcaster_ai.config import get_settings

    settings = get_settings()
    assert settings.llm_provider in ("deepseek", "openrouter", "kimi", "qwen", "gemini")
    assert settings.tts_provider in ("edge", "elevenlabs")
    assert settings.podcast_title == "Daily Recon"


def test_llm_endpoint_routing() -> None:
    """Verify LLM endpoint routing is correct."""
    from podcaster_ai.config import Settings

    for provider, expected_endpoint in [
        ("deepseek", "https://api.deepseek.com/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("kimi", "https://api.moonshot.cn/v1"),
        ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ]:
        s = Settings(llm_provider=provider)  # type: ignore
        assert s.llm_endpoint() == expected_endpoint


if __name__ == "__main__":
    test_imports()
    test_config_loading()
    test_llm_endpoint_routing()
    print("✓ All smoke tests passed.")
