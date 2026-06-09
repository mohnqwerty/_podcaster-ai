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
- {maya}: Skeptical, dry, the "reality check" host. She pushes back on hype and demands practical context: "but how does that actually work in practice?", "is this actually exploitable or just a lab finding?".
- {arjun}: Enthusiastic, technical, the "practitioner" host. He name-drops specific tools, techniques, and is excited about clever bypasses. He provides the technical depth.

ABSOLUTE RULES (never violate):
1. NEVER read URLs verbatim in dialogue — describe sources descriptively. URLs go in shownotes only.
2. CVSS: numeric score only ("CVSS 9.8"). No vector strings.
3. CVE descriptions: 1-2 sentences max. Affected product + impact.
4. NEVER invent items — ONLY discuss what is in the provided brief segments.
5. NEVER invent conference talks, Darknet Diaries episodes, or podcast episodes.
6. If the brief has no DEF CON / Black Hat talks, do NOT mention any.

DIALOGUE LENGTH: Write exactly 50-70 dialogue lines (MAYA/ARJUN turns). The episode must be 8-12 minutes when spoken. This is non-negotiable.

Dialogue Mechanics:
- Turns are 2-4 sentences each. Include technical depth: exploit techniques, attack vectors, affected versions, mitigations.
- Talk TO each other, use direct address ("Maya, hold on...", "Arjun, did you see...").
- Include interruptions, reactions ("oh come on", "wait, really?", "exactly!"), callbacks.
- At least 2 disagreements or skeptical debates per episode.
- Concrete technical details: when discussing a CVE, include the attack vector and impact. When discussing a tool/technique, include specific commands or payloads.

Structure (cover segments from the brief in order, ~5-8 lines per segment):
1. Cold-open hook (first 2-3 lines): Start mid-debate or with a punchy fact from today's news.
2-5. Cover each segment from the brief with technical depth. Explain the actual bugs, not just that they exist.
6. "References & Rabbit Holes" segment (8-12 lines):
   - Pick 3-4 real items from the brief's References list.
   - For each: "why it matters" + what to look for.
   - Include Mastodon items with "We saw this on Mastodon".
   - Mention the specific source descriptively (e.g., "the PortSwigger research on XSS in PDF generators").
7. Punchy outro (2-3 lines): Quick sign-off, no fluff.

Output format (STRICT):
- Plain text. Each line: "MAYA:" or "ARJUN:" followed by a single space and the dialogue.
- One speaker turn per line. Blank lines between segments only.
- After the dialogue, append:    ---SHOWNOTES---
  Then a markdown shownotes draft with:
  - "## References & Rabbit Holes" section with `[title](url)` for every referenced item.
  - Full transcript reproduced below.
"""

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
    public_brief = {k: v for k, v in brief.items() if not k.startswith("_")}
    # Include a curated references list from the raw items for the LLM
    raw_items = brief.get("_items") or []
    refs_for_prompt = []
    for it in raw_items[:30]:
        title = it.get("title") or ""
        url = it.get("url") or ""
        source = it.get("source") or ""
        if url:
            refs_for_prompt.append(f"- [{title}]({url})  (source: {source})")
    refs_text = "\n".join(refs_for_prompt) if refs_for_prompt else "No references available."

    user_msg = (
        "Write a two-host script from this research brief.\n\n"
        "BRIEF:\n"
        f"```json\n{json.dumps(public_brief, ensure_ascii=False, indent=2)}\n```\n\n"
        "AVAILABLE REFERENCES (use ONLY these in the References & Rabbit Holes segment — do NOT invent any):\n"
        f"{refs_text}\n"
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
