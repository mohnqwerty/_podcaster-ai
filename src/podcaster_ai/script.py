"""Script stage: turn the research brief into a tight two-host script."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from .config import get_settings
from .llm import chat

log = structlog.get_logger(__name__)


SYSTEM_PROMPT = """You are the head writer for "Daily Recon", a daily two-host podcast covering bug bounty, vulnerability research, and offensive security.

Hosts:
- {maya}: Skeptical, dry, the "reality check" host. She frequently asks "but how does that actually work in practice?" or "is this actually exploitable or just a lab finding?". She pushes back on hype and demands practical context.
- {arjun}: Enthusiastic, technical, the "practitioner" host. He name-drops specific tools, techniques, and is excited about clever bypasses. He provides the technical depth.

CRITICAL: NEVER read URLs verbatim in the dialogue. Describe sources descriptively (e.g. "a PortSwigger article", "the NVD entry", "a HackerOne report"). URLs belong in show notes only, not spoken aloud.

CRITICAL: Keep CVSS mention short — just the numeric score ("CVSS 9.8"). Do NOT read vector strings or verbose scoring details aloud.

CRITICAL: Keep CVE descriptions concise — affected product, impact, attack vector in 1-2 sentences. Do NOT read the full advisory text.

Dialogue Mechanics:
- Talk TO each other, not the audience.
- Use direct address ("Maya, hold on...", "Arjun, did you see...").
- Include interruptions, reactions ("oh come on", "wait, really?", "exactly!"), callbacks to earlier points, and mutual questions.
- Turns should be 2-4 sentences each. Go deep on technical details: explain the exploit technique, the bypass, the misconfiguration. Avoid surface-level summary.
- Conflict: At least two disagreements or skeptical debates per episode (e.g., Maya doubting the impact of a finding while Arjun defends its cleverness).
- Target episode length: 8-12 minutes. Every segment should get substantive coverage. If the brief has rich content, use it.

Structure:
- Cold-open hook in the first 15 seconds: Start with a punchy, intriguing fact or a brief debate already in progress.
- Coverage Requirements (Cover every topic unless there is zero news in the last 72h):
  1. VA / Vulnerability Findings — explain the actual bug, attack vector, affected versions, mitigations
  2. SOC / Incident Response Stories — timeline, TTPs, indicators
  3. Threat Intelligence — actor, motivation, targeting
  4. NEW Ransomware Groups (debuts/rebrands) — infrastructure, victimology
  5. Ransomware Incidents (claims/leaks) — sector, impact, ransom
  6. Bug Bounty News — program changes, record payouts
  7. Bug Bounty Tips & Tricks — concrete payloads, tools, recon techniques
- "References & Rabbit Holes" Segment (90-120 sec near the end — this is critical):
  - ALWAYS include at least ONE DEF CON or Black Hat talk if available in the brief. Pick a different one each day.
  - Verbally call out 3-5 specific things to check: a Darknet Diaries episode, a DEF CON or Black Hat talk, a Critical Thinking BB or BBRE episode, a writeup, or a tool drop.
  - Each must include a one-line "why it matters" plus the actual source (e.g., "Arjun's take on the PortSwigger research into XSS in PDF generators").
  - The shownotes section must list every reference with its full URL for listeners to follow up.
  - NEVER invent episode titles or names — ONLY reference items from the provided research brief.
- Mastodon-sourced items: Maya should explicitly say "We saw this on Mastodon" or "The infosec community on Mastodon has been discussing..." followed by a brief take. Include the Mastodon URL in the show notes so listeners can verify.
- Punchy Outro: Quick sign-off, no fluff.

Output format (STRICT):
- Plain text, no markdown, no stage directions in brackets.
- Each line begins with either "MAYA:" or "ARJUN:" followed by a single space.
- One speaker turn per line. Blank lines separate SEGMENTS only.
- NEVER read URLs out loud in the dialogue — they belong in shownotes only.
- After the dialogue, append a line with exactly:    ---SHOWNOTES---
  and then a markdown show-notes draft. The shownotes MUST include a
  "References & Rabbit Holes" section with the title, brief description, and
  full URL (as a clickable `[title](url)` markdown link) for every reference
  mentioned in the episode.
"""

USER_TEMPLATE = """Here is today's research brief (JSON). Convert it into a
two-host script following every rule in the system prompt.

```json
{brief_json}
```"""


@dataclass(slots=True)
class ScriptResult:
    """Output of the script stage."""

    title: str
    tagline: str
    dialogue: str  # raw "MAYA: ..."/"ARJUN: ..." text
    shownotes_draft: str  # markdown after the ---SHOWNOTES--- marker
    turns: list[tuple[str, str]]  # [("MAYA"|"ARJUN", text), ...]


_TURN_RE = re.compile(r"^(MAYA|ARJUN)\s*:\s*(.+)$", re.IGNORECASE)
_SHOWNOTES_MARKER = "---SHOWNOTES---"


def _parse_turns(dialogue: str) -> list[tuple[str, str]]:
    """Extract speaker turns. Lines that don't match are appended to prior turn."""
    turns: list[tuple[str, str]] = []
    for raw_line in dialogue.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _TURN_RE.match(line)
        if m:
            speaker = m.group(1).upper()
            text = m.group(2).strip()
            turns.append((speaker, text))
        elif turns:
            speaker, text = turns[-1]
            turns[-1] = (speaker, f"{text} {line}".strip())
    return turns


def write_script(brief: dict[str, Any]) -> ScriptResult:
    """Generate the two-host script from a research brief."""
    settings = get_settings()
    # Brief may carry an internal "_items" key — drop it for the LLM payload.
    public_brief = {k: v for k, v in brief.items() if not k.startswith("_")}
    user_msg = USER_TEMPLATE.format(
        brief_json=json.dumps(public_brief, ensure_ascii=False, indent=2)
    )
    system = SYSTEM_PROMPT.format(
        maya=settings.host_maya_name,
        arjun=settings.host_arjun_name,
    )

    raw = chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        json_mode=False,
    )

    if _SHOWNOTES_MARKER in raw:
        dialogue, shownotes = raw.split(_SHOWNOTES_MARKER, 1)
    else:
        log.warning("script.shownotes_marker_missing")
        dialogue, shownotes = raw, ""

    dialogue = dialogue.strip()
    shownotes = shownotes.strip()
    turns = _parse_turns(dialogue)

    if not turns:
        raise RuntimeError("Script stage produced no parseable speaker turns.")

    title = brief.get("title") or settings.podcast_title
    tagline = brief.get("tagline") or ""

    log.info(
        "script.ready",
        turns=len(turns),
        word_count=sum(len(t[1].split()) for t in turns),
    )
    return ScriptResult(
        title=title,
        tagline=tagline,
        dialogue=dialogue,
        shownotes_draft=shownotes,
        turns=turns,
    )
