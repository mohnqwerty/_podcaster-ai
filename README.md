# Daily Recon — Self-Hosted Bug-Bounty Podcast Generator

A plug-and-play daily podcast generator that fetches the latest bug-bounty, vulnerability research, and security releases, synthesizes them with an LLM into a tight two-host script, renders audio via TTS, and delivers episodes to Telegram — all from a single Docker container on your VPS.

**What you get**: A ~10-minute daily podcast with hosts Maya (analyst) and Arjun (practitioner) discussing the day's top findings. The pipeline covers general bug bounty, **AI Security** (MITRE ATLAS, LLM vulnerabilities), **Hardware Hacking** (firmware, side-channels), and **Security Conferences** (Black Hat, DEF CON, Hack.lu). It prioritizes primary resources like Black Hat and DEF CON YouTube transcripts for deep-dive analysis. Scheduled via systemd timer or cron. No external SaaS dependencies beyond your LLM and TTS providers.

---

## Quickstart

### Prerequisites

- A VPS or local machine with Docker and Docker Compose installed
- An LLM API key (DeepSeek, OpenRouter, Moonshot Kimi, Qwen, Google Gemini, or Groq)
- A TTS provider (edge-tts is free; ElevenLabs is optional)
- A Telegram bot token and chat ID

### Setup (5 minutes)

1. **Clone the repo:**
   ```bash
   git clone https://github.com/mohnqwerty/_podcaster-ai.git
   cd _podcaster-ai
   ```

2. **Copy and configure the environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and settings
   nano .env
   ```

3. **Smoke test (dry-run):**
   ```bash
   docker compose run --rm podcaster --dry-run
   ```
   This will generate a podcast episode without sending to Telegram. Check `./out/` for the MP3, script, and show notes.

4. **Enable the daily timer (on the VPS):**
   ```bash
   sudo cp systemd/podcaster-ai.service /etc/systemd/system/
   sudo cp systemd/podcaster-ai.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now podcaster-ai.timer
   sudo systemctl status podcaster-ai.timer
   ```

5. **Verify it's scheduled:**
   ```bash
   sudo systemctl list-timers podcaster-ai.timer
   ```

---

## Configuration

### Required Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `LLM_PROVIDER` | Which LLM backend to use | `deepseek` |
| `LLM_MODEL` | Model name (provider-specific) | `deepseek-chat` |
| `DEEPSEEK_API_KEY` | DeepSeek API key (if using DeepSeek) | (your key) |
| `OPENROUTER_API_KEY` | OpenRouter API key (if using OpenRouter) | (your key) |
| `MOONSHOT_API_KEY` | Moonshot Kimi API key (if using Kimi) | (your key) |
| `DASHSCOPE_API_KEY` | Qwen API key (if using Qwen) | (your key) |
| `GEMINI_API_KEY` | Google Gemini API key (if using Gemini) | (your key) |
| `GROQ_API_KEY` | Groq API key (if using Groq) | (your key) |
| `TTS_PROVIDER` | Which TTS backend to use | `edge` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (from BotFather) | (your token) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID to receive episodes | (your chat ID) |

### Optional Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_TEMPERATURE` | `0.4` | LLM creativity (0.0–1.0) |
| `LLM_TIMEOUT_SECONDS` | `120` | Timeout for LLM calls |
| `LLM_MAX_RETRIES` | `4` | Retry attempts for LLM failures |
| `TTS_PROVIDER` | `edge` | `edge` (free) or `elevenlabs` (paid) |
| `MAYA_VOICE` | `en-US-AriaNeural` | Voice for Maya (edge-tts voice name) |
| `ARJUN_VOICE` | `en-IN-PrabhatNeural` | Voice for Arjun (edge-tts voice name) |
| `TTS_RATE` | `+25%` | Speed of the TTS output (e.g. +25% or -10%) |
| `ELEVENLABS_API_KEY` | — | ElevenLabs API key (if using ElevenLabs) |
| `ELEVENLABS_MAYA_VOICE_ID` | — | ElevenLabs voice ID for Maya |
| `ELEVENLABS_ARJUN_VOICE_ID` | — | ElevenLabs voice ID for Arjun |
| `VENDOR_RSS_FEEDS` | — | Comma-separated vendor advisory RSS URLs |
| `YOUTUBE_CHANNEL_IDS` | — | Comma-separated YouTube channel IDs to fetch |
| `YOUTUBE_LOOKBACK_DAYS` | `14` | How many days back to pull YouTube episodes |
| `NVD_MIN_CVSS` | `7.0` | Minimum CVSS score for NVD CVEs |
| `NVD_LOOKBACK_HOURS` | `72` | How many hours back to pull NVD CVEs |
| `MAX_ITEMS_PER_SOURCE` | `8` | Max items per source after ranking |
| `MAX_TOTAL_ITEMS` | `40` | Hard cap on total items fed to research |
| `PODCAST_TITLE` | `Daily Recon` | Podcast name |
| `HOST_MAYA_NAME` | `Maya` | Name of the analyst host |
| `HOST_ARJUN_NAME` | `Arjun` | Name of the practitioner host |
| `TIMEZONE` | `Asia/Kolkata` | Timezone for scheduling (systemd uses local time) |
| `OUTPUT_DIR` | `/app/out` | Where to save artifacts inside container |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## LLM Providers

All providers expose an OpenAI-compatible REST interface. Switch providers by changing `LLM_PROVIDER` and the corresponding API key.

| Provider | Endpoint | Default Model | API Key Env | Notes |
|----------|----------|---------------|-------------|-------|
| **DeepSeek** | `https://api.deepseek.com/v1` | `deepseek-chat` | `DEEPSEEK_API_KEY` | Cheap, fast, good quality |
| **OpenRouter** | `https://openrouter.ai/api/v1` | (varies) | `OPENROUTER_API_KEY` | Aggregates many models; use `deepseek/deepseek-chat` or `moonshotai/kimi-k2` |
| **Moonshot Kimi** | `https://api.moonshot.cn/v1` | `moonshot-v1-32k` | `MOONSHOT_API_KEY` | Chinese LLM; excellent context window |
| **Qwen** | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `DASHSCOPE_API_KEY` | Alibaba; good for Chinese content |
| **Google Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` | `GEMINI_API_KEY` | Multimodal, fast, free tier available |
| **Groq** | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | Extremely fast LPU inference; OpenAI-compatible; great latency for daily runs |

**Example configurations:**

```bash
# DeepSeek (recommended for cost)
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-...

# OpenRouter with Kimi
LLM_PROVIDER=openrouter
LLM_MODEL=moonshotai/kimi-k2
OPENROUTER_API_KEY=sk-or-...

# Gemini
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
GEMINI_API_KEY=AIza...

# Groq (fast LPU inference)
LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=gsk_...
```

---

## TTS Providers

### edge-tts (Free, Default)

No API key required. Uses Microsoft's edge-tts package to synthesize speech.

```bash
TTS_PROVIDER=edge
MAYA_VOICE=en-US-AriaNeural
ARJUN_VOICE=en-IN-PrabhatNeural
TTS_RATE=+25%
```

**Available voices** (edge-tts supports many; here are common ones):
- Female: `en-US-AriaNeural`, `en-US-JennyNeural`, `en-GB-SoniaNeural`
- Male: `en-IN-PrabhatNeural`, `en-US-GuyNeural`, `en-US-AmberNeural`, `en-GB-RyanNeural`

### Audio Quality & Continuity
- **TTS Rate**: Configurable via `TTS_RATE` (default `+25%`).
- **Voice Continuity**: Maya is pinned to `en-US-AriaNeural` and Arjun to `en-IN-PrabhatNeural` in code for consistency.
- **Inter-turn Silence**: Tightened to 100ms between speaker changes.
- **Crossfade**: 40ms linear crossfade applied between turns to eliminate pops.
- **Normalization**: Loudness is normalized to -20 dBFS across all turns before final stitching.

Run `edge-tts --list-voices` inside the container to see all available voices.

### ElevenLabs (Paid, Premium Quality)

Requires an ElevenLabs account and API key.

```bash
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=sk_...
ELEVENLABS_MAYA_VOICE_ID=21m00Tcm4TlvDq8ikWAM
ELEVENLABS_ARJUN_VOICE_ID=EXAVITQu4vr4xnSDxMaL
ELEVENLABS_MODEL=eleven_turbo_v2_5
```

Get voice IDs from your ElevenLabs dashboard.

---

## Telegram Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. BotFather will give you a **bot token** (e.g., `123456789:ABCdefGHIjklmnoPQRstuvWXYZ`)
4. Copy this token to `TELEGRAM_BOT_TOKEN` in `.env`

### 2. Get Your Chat ID

**Option A: Using @userinfobot**
1. Open Telegram and search for **@userinfobot**
2. Send any message; it will reply with your user ID
3. Copy this to `TELEGRAM_CHAT_ID` in `.env`

**Option B: Using your bot**
1. Add your bot to a chat or start a direct message with it
2. Send any message to the bot
3. Run:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
   ```
4. Look for `"chat":{"id":<YOUR_CHAT_ID>}` in the response
5. Copy the chat ID to `TELEGRAM_CHAT_ID` in `.env`

### 3. Restrict the Bot (Security)

To prevent others from using your bot, set a default chat ID in BotFather:
1. Send `/setdefaultadministratorrights` to @BotFather
2. Or manually check the chat ID in each message before processing (the code already does this)

---

## Scheduling

### Option 1: systemd Timer (Recommended on VPS)

The repo includes systemd unit files. On your VPS:

```bash
sudo cp systemd/podcaster-ai.service /etc/systemd/system/
sudo cp systemd/podcaster-ai.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now podcaster-ai.timer
```

**Change the fire time:**
```bash
sudo systemctl edit podcaster-ai.timer
```

Edit the `OnCalendar` line. Examples:
- `*-*-* 07:30:00` — 7:30 AM daily
- `Mon-Fri *-*-* 09:00:00` — 9:00 AM on weekdays
- `*-*-* 00,12:00:00` — midnight and noon daily

**View logs:**
```bash
sudo journalctl -u podcaster-ai.service -f
```

### Option 2: Cron (Alternative)

On your VPS, add to `crontab -e`:

```bash
# Run daily at 7:30 AM (Asia/Kolkata)
30 7 * * * cd /opt/_podcaster-ai && docker compose run --rm podcaster >> /var/log/podcaster-ai.log 2>&1
```

Adjust the time and path as needed.

### Option 3: Manual Trigger

```bash
cd /path/to/_podcaster-ai
./scripts/run-once.sh
```

Or with dry-run:
```bash
./scripts/run-once.sh --dry-run
```

---

## Source Configuration

### Built-in Sources (Always Active)

- **PortSwigger Research** — RSS feed of security research
- **HackerOne Hacktivity** — Public disclosed bug bounty reports
- **ProjectDiscovery Releases** — Nuclei and nuclei-templates updates
- **NVD Recent CVEs** — High-severity CVEs (CVSS ≥ 7.0 by default)
- **CISA KEV** — Known Exploited Vulnerabilities catalog
- **YouTube Transcripts** (primary) — Recent episodes from Black Hat, DEF CON, and other security channels
- **AI Security News** — MITRE ATLAS and specialized AI security feeds
- **Hardware Hacking** — Firmware, side-channel, and physical security news
- **Conference News** — Upcoming events and news from Black Hat, DEF CON, and Hack.lu (Luxembourg)
- **Mastodon** (optional, Tier 3 leads) — Recent statuses from your home timeline, bookmarks, and configured hashtags on a Mastodon-compatible instance (default: infosec.exchange). Enabled by setting `MASTODON_ACCESS_TOKEN`.

### Mastodon (Optional, Tier 3 Leads)

Mastodon timelines are an excellent leading indicator for in-the-wild exploits,
zero-days, and hot CVEs surfaced by the security community before they hit the
formal advisory channels. Because anyone can post anything, this source is
classified as **Tier 3**: items must always be cross-checked against
authoritative sources (NVD, vendor advisories, PortSwigger, CISA KEV, etc.)
before being asserted as fact. The original Mastodon URL is always cited in
the show notes.

**1. Generate an access token.** On your Mastodon instance (e.g.
`https://infosec.exchange`):

1. Log in, then go to **Preferences → Development → New application**.
2. Give the app any name (e.g. `podcaster-ai`).
3. Tick the following scopes (and only these):
   - `read:statuses`
   - `read:lists`
   - `read:accounts`
   - `read:bookmarks`
   - `read:favourites`
4. Submit, open the app, then copy the **Your access token** value into
   `MASTODON_ACCESS_TOKEN` in `.env`.

**2. Configure the source.** In `.env`:

```bash
MASTODON_BASE_URL=https://infosec.exchange
MASTODON_ACCESS_TOKEN=<your token>
MASTODON_HASHTAGS=infosec,cve,bugbounty,0day,threatintel
MASTODON_INCLUDE_HOME=true
MASTODON_INCLUDE_BOOKMARKS=true
MASTODON_HOURS=48
```

If `MASTODON_ACCESS_TOKEN` is empty the source is silently disabled — the
pipeline still runs cleanly. Each enabled endpoint (home, per-tag, bookmarks)
is fetched fail-soft; a single failing endpoint never breaks the run.

The summary line of every Mastodon item carries an engagement signal
(`[reblogs=R favs=F replies=P]`) so the ranking stage can use it as a
secondary score.

### Vendor Advisory RSS Feeds (Optional)

Add custom vendor advisory feeds via `VENDOR_RSS_FEEDS`:

```bash
VENDOR_RSS_FEEDS=https://www.microsoft.com/security/rss/,https://security.apple.com/rss/
```

Comma-separated list of RSS feed URLs. Each feed is fetched and items are ranked by recency and source weight.

### YouTube Channels (Primary Resources)

Pull recent episode transcripts from primary security channels like Black Hat and DEF CON:

```bash
# Defaults include Black Hat (UCS90qS2YOo6HQC3uH9_95MA) and DEF CON (UC6Om9kAkl32dWlDS_lX9W3Q)
YOUTUBE_CHANNEL_IDS=UCS90qS2YOo6HQC3uH9_95MA,UC6Om9kAkl32dWlDS_lX9W3Q
YOUTUBE_LOOKBACK_DAYS=14
```

The pipeline fetches the last 14 days of videos from each channel, pulls their transcripts (if available), and includes them in the research brief. Transcripts are summarized by the LLM, never embedded raw.

---

## Cost Estimates

Rough order-of-magnitude per daily run (assuming one 10-minute episode):

| Scenario | LLM Cost | TTS Cost | Total |
|----------|----------|----------|-------|
| DeepSeek + edge-tts | ~$0.01 | $0.00 | **~$0.01/day** |
| OpenRouter (Kimi) + edge-tts | ~$0.02 | $0.00 | **~$0.02/day** |
| Gemini (free tier) + edge-tts | $0.00 | $0.00 | **$0.00/day** |
| DeepSeek + ElevenLabs | ~$0.01 | ~$0.30 | **~$0.31/day** |

**Monthly**: DeepSeek + edge-tts ≈ $0.30/month. DeepSeek + ElevenLabs ≈ $9.30/month.

---

## Security Notes

1. **Never commit `.env`** — it contains API keys. The `.gitignore` already excludes it.
2. **Run as non-root** — the Dockerfile creates an unprivileged `app` user.
3. **Rotate tokens regularly** — if you suspect a leak, regenerate your LLM, TTS, and Telegram tokens.
4. **Restrict Telegram bot** — set `TELEGRAM_CHAT_ID` to your chat only; the bot will reject messages from other users.
5. **Use environment variables** — never hardcode secrets in code or config files.
6. **Keep dependencies updated** — periodically rebuild the Docker image to pick up security patches:
   ```bash
   docker compose build --no-cache
   ```

---

## Troubleshooting

### "ffmpeg not found"
The Dockerfile installs ffmpeg via `apt-get`. If you're running locally, install it:
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

### "Telegram chat ID wrong"
Verify your chat ID:
```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
```
Look for `"chat":{"id":<YOUR_CHAT_ID>}`.

### "LLM returns empty response"
- Check your API key is set correctly and has quota
- Verify the LLM provider endpoint is reachable
- Try increasing `LLM_TIMEOUT_SECONDS` if the LLM is slow
- Check logs: `docker compose logs podcaster`

### "edge-tts voice not found"
List available voices:
```bash
docker compose run --rm podcaster python -c "import edge_tts; import asyncio; asyncio.run(edge_tts.list_voices())"
```
Update `MAYA_VOICE` and `ARJUN_VOICE` to valid voice names.

### "No items fetched from sources"
- Check internet connectivity inside the container
- Verify RSS feeds are still active (some may have moved or gone offline)
- Try a dry-run with verbose logging:
  ```bash
  LOG_LEVEL=DEBUG docker compose run --rm podcaster --dry-run
  ```

### "Podcast is too short or too long"
The script stage targets 1500–1700 words (~10 minutes). If episodes are consistently off:
- Adjust `LLM_TEMPERATURE` (lower = more consistent)
- Increase/decrease `MAX_TOTAL_ITEMS` to feed more/fewer items to the LLM
- Tweak the system prompt in `src/podcaster_ai/script.py`

---

## Deployment Checklist

- [ ] Clone repo and copy `.env.example` to `.env`
- [ ] Fill in all required env vars (LLM key, TTS provider, Telegram token + chat ID)
- [ ] Test with `docker compose run --rm podcaster --dry-run`
- [ ] Verify MP3, script, and show notes in `./out/`
- [ ] Set up systemd timer (or cron) on the VPS
- [ ] Enable the timer: `sudo systemctl enable --now podcaster-ai.timer`
- [ ] Check logs: `sudo journalctl -u podcaster-ai.service -f`
- [ ] Receive first episode on Telegram
- [ ] Celebrate! 🎙️

---

## Project Structure

```
_podcaster-ai/
├── .env.example              # Template for environment variables
├── .gitignore                # Excludes .env, __pycache__, etc.
├── Dockerfile                # Single-stage, runs as non-root
├── docker-compose.yml        # One-shot service, mounts ./out
├── LICENSE                   # MIT
├── README.md                 # This file
├── pyproject.toml            # setuptools config
├── requirements.txt          # Pinned dependencies
├── systemd/
│   ├── podcaster-ai.service  # systemd service unit
│   └── podcaster-ai.timer    # systemd timer (daily, 7:30 AM default)
├── scripts/
│   └── run-once.sh           # Helper to run manually
├── src/podcaster_ai/
│   ├── __init__.py
│   ├── __main__.py           # Entry point for `python -m podcaster_ai`
│   ├── config.py             # Pydantic-settings, env loading
│   ├── llm.py                # Provider-agnostic OpenAI-compat client
│   ├── research.py           # Gather, dedupe, rank, build brief
│   ├── script.py             # Two-host script generation
│   ├── tts.py                # edge-tts + ElevenLabs rendering
│   ├── shownotes.py          # Markdown show notes
│   ├── deliver.py            # Telegram delivery
│   ├── run.py                # Main pipeline orchestrator
│   └── pipeline/
│       ├── __init__.py
│       └── sources/
│           ├── __init__.py
│           ├── base.py       # Item dataclass, helpers
│           ├── portswigger_rss.py
│           ├── hackerone_hacktivity.py
│           ├── projectdiscovery_releases.py
│           ├── nvd_recent.py
│           ├── cisa_kev.py
│           ├── vendor_rss.py
│           └── youtube_transcripts.py
└── tests/
    └── test_smoke.py         # Import + config smoke tests
```

---

## License

MIT. See `LICENSE` for details.

---

## Disclaimer

This tool aggregates publicly available security research and vulnerability information. The user is responsible for:

- **Accuracy verification**: Always cross-check findings with original sources.
- **Ethical use**: Use this tool only for legitimate security research and bug-bounty activities.
- **Compliance**: Ensure your use complies with all applicable laws and regulations.
- **Attribution**: When sharing podcast content, credit the original researchers and sources.

The hosts, developers, and maintainers are not liable for any misuse, inaccuracies, or damages arising from the use of this tool.

---

## Contributing & Support

This is a personal project. For issues or suggestions, open a GitHub issue or contact the maintainer.

Happy podcasting! 🎙️
