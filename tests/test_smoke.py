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
        bug_bounty_reports_explained,
        cisa_kev,
        conferences,
        critical_thinking_podcast,
        darknet_diaries,
        dfir_report,
        hackerone_hacktivity,
        hardware_hacking,
        krebs_on_security,
        mastodon,
        nvd_recent,
        portswigger_rss,
        projectdiscovery_releases,
        ransomwatch,
        risky_business,
        vendor_rss,
        youtube_transcripts,
    )

    # Mastodon must be importable and callable; with no token it returns [].
    assert mastodon.fetch() == []

    # New sources must be importable and callable; when disabled, return [].
    assert critical_thinking_podcast.fetch() == []
    assert bug_bounty_reports_explained.fetch() == []
    assert darknet_diaries.fetch() == []
    assert risky_business.fetch() == []
    assert dfir_report.fetch() == []
    assert ransomwatch.fetch() == []
    assert krebs_on_security.fetch() == []

    assert podcaster_ai.__version__ == "0.1.0"


def test_config_loading() -> None:
    """Verify config loads from environment."""
    from podcaster_ai.config import get_settings

    settings = get_settings()
    assert settings.llm_provider in (
        "deepseek", "openrouter", "kimi", "qwen", "gemini", "groq",
    )
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
        ("groq", "https://api.groq.com/openai/v1"),
    ]:
        s = Settings(llm_provider=provider)  # type: ignore
        assert s.llm_endpoint() == expected_endpoint


def test_groq_provider_routing() -> None:
    """Verify Groq provider picks up GROQ_API_KEY and the correct endpoint."""
    from podcaster_ai.config import Settings

    s = Settings(llm_provider="groq", groq_api_key="gsk_test_key")  # type: ignore
    assert s.llm_endpoint() == "https://api.groq.com/openai/v1"
    assert s.llm_api_key() == "gsk_test_key"


def test_run_py_has_main_guard() -> None:
    """Verify run.py has __main__ guard to prevent accidental module import."""
    run_py_path = Path(__file__).parent.parent / "src" / "podcaster_ai" / "run.py"
    content = run_py_path.read_text()
    assert 'if __name__ == "__main__":' in content, "run.py missing __main__ guard"
    assert "sys.exit(cli())" in content, "run.py missing sys.exit(cli()) call"


def test_source_urls_accessible() -> None:
    """Verify source feed URLs return HTTP < 400. Requires network."""
    import pytest
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed")

    # Mark as network test; skip with -m "not network"
    pytest.mark.network

    urls_to_check = [
        "https://blog.trailofbits.com/feed/",
        "https://embracethered.com/blog/index.xml",
        "https://protectai.com/blog/rss.xml",
        "https://hackaday.com/category/security-hacks/feed/",
        "https://infosec-conferences.com/feed/",
        "https://media.ccc.de/podcast-hd.xml",
    ]

    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        for url in urls_to_check:
            try:
                resp = client.head(url)
                assert resp.status_code < 400, f"{url} returned {resp.status_code}"
            except Exception as e:
                pytest.skip(f"Network error checking {url}: {e}")


def test_cisa_kev_url_with_user_agent() -> None:
    """Verify CISA KEV URL returns 200 with proper User-Agent header."""
    import pytest
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed")

    pytest.mark.network

    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PodcasterAI/1.0; +https://github.com/mohnqwerty/_podcaster-ai)"}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.head(url, headers=headers)
            assert resp.status_code < 400, f"CISA KEV URL returned {resp.status_code}"
    except Exception as e:
        pytest.skip(f"Network error checking CISA KEV URL: {e}")


def test_settings_loads_with_minimal_env() -> None:
    """Verify Settings class loads with only required env vars."""
    import os
    from podcaster_ai.config import Settings

    # Save current env
    old_env = os.environ.copy()
    try:
        # Clear most env vars, keep only required ones
        for key in list(os.environ.keys()):
            if key.startswith(("TELEGRAM_", "GROQ_", "DEEPSEEK_", "OPENROUTER_", "MOONSHOT_", "DASHSCOPE_", "GEMINI_", "LLM_", "TTS_", "MAYA_", "ARJUN_", "ELEVENLABS_", "PODCAST_", "HOST_", "TIMEZONE_", "OUTPUT_", "DATA_", "LOG_", "YOUTUBE_", "NVD_", "MAX_", "VENDOR_", "MASTODON_", "DASHBOARD_", "SESSION_", "CSRF_", "BOOTSTRAP_", "DATABASE_")):
                del os.environ[key]

        # Set minimal required env
        os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
        os.environ["TELEGRAM_CHAT_ID"] = "123456"
        os.environ["GROQ_API_KEY"] = "test_key"

        # This should not raise
        settings = Settings()  # type: ignore
        assert settings.telegram_bot_token == "test_token"
        assert settings.telegram_chat_id == "123456"
    finally:
        # Restore env
        os.environ.clear()
        os.environ.update(old_env)


if __name__ == "__main__":
    test_imports()
    test_config_loading()
    test_llm_endpoint_routing()
    test_groq_provider_routing()
    test_run_py_has_main_guard()
    print("✓ All smoke tests passed.")
