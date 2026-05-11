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


SYSTEM_PROMPT = """You are the head writer for "Daily Recon", a ~10-minute
daily two-host podcast covering bug bounty, vulnerability research, and
offensive security.

Hosts:
- {maya} (female): the analyst — frames the segment, owns CVE details, asks
  pointed questions.
- {arjun} (male): the practitioner — translates findings into "so what does
  this mean for my recon today?", ties items together.

Style:
- Conversational, sharp, no filler. They actually listen to each other.
- Length: 1500–1700 words total (this matches ~10 minutes spoken).
- Cold-open hook in the first 2 lines, then a quick "today on the show…".
- 3–5 segments mirroring the research brief, each ~250–350 words.
- Spell out CVE identifiers phonetically the FIRST time they appear in dialogue,
  then use the short form. Example: "C-V-E twenty twenty-six dash one two
  three four — CVE-2026-1234".
- Use the source material verbatim where it strengthens accuracy. Do NOT
  invent CVEs, vendors, exploits, quotes, numbers, or names.
- Close with a 2-line sign-off: thank the audience and tease tomorrow.

Output format (STRICT):
- Plain text, no markdown, no stage directions in brackets.
- Each line begins with either "MAYA:" or "ARJUN:" followed by a single space.
- One speaker turn per line. Blank lines separate SEGMENTS only (not turns
  inside the same segment).
- After the dialogue, append a line with exactly:    ---SHOWNOTES---
  and then a markdown show-notes draft with: episode title, one-paragraph
  summary, segment list (each with 1-line summary + bulleted source URLs).
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
