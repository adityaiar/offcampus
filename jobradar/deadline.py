"""Application deadlines ("apply by", "last date", "closing date") — only when a posting states one."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, ms in enumerate([
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")], start=1) for m in ms}

_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_CUE = (r"(?:apply\s+(?:by|before|on\s+or\s+before)|last\s+date(?:\s+(?:to|for|of)\s+(?:apply|applying|application|applications|submission))?"
        r"|closing\s+date|applications?\s+(?:close|closes|closing|deadline|due)(?:\s+on)?|application\s+end\s+date"
        r"|deadline(?:\s+to\s+apply|\s+for\s+applications?)?|apply\s+latest\s+by|posting\s+end\s+date)")
_SEP = r"\s*(?:is|:|-|–|by|on)?\s*"
_DATES = [
    (rf"(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]+(?:of\s+)?{_MON}[\s,\-]*(\d{{4}})?", "dmy"),  # 15th Nov 2026 / 15-Nov-26
    (rf"{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?", "mdy"),                     # November 15, 2026
    (r"(\d{4})-(\d{1,2})-(\d{1,2})", "iso"),                                               # 2026-11-15
    (r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", "num"),                                   # 15/11/2026 (Indian d/m/y)
]
_PATTERNS = [(re.compile(_CUE + _SEP + rx, re.I), kind) for rx, kind in _DATES]


def _infer_year(d: int, m: int, today: date) -> date | None:
    for y in (today.year, today.year + 1):
        try:
            cand = date(y, m, d)
        except ValueError:
            return None
        if cand >= today - timedelta(days=60):
            return cand
    return None


def _build(kind: str, g: tuple, today: date) -> date | None:
    try:
        if kind == "dmy":
            d, m, y = int(g[0]), MONTHS[g[1].lower()], g[2]
            return date(int(y), m, d) if y else _infer_year(d, m, today)
        if kind == "mdy":
            m, d, y = MONTHS[g[0].lower()], int(g[1]), g[2]
            return date(int(y), m, d) if y else _infer_year(d, m, today)
        if kind == "iso":
            return date(int(g[0]), int(g[1]), int(g[2]))
        if kind == "num":
            y = int(g[2]) + (2000 if len(g[2]) == 2 else 0)
            return date(y, int(g[1]), int(g[0]))
    except (ValueError, KeyError):
        return None
    return None


def find_in_text(text: str, today: date | None = None) -> str:
    """Deadline stated in a job description, as YYYY-MM-DD, or ''."""
    today = today or date.today()
    if not text:
        return ""
    for rx, kind in _PATTERNS:
        m = rx.search(text)
        if m:
            d = _build(kind, m.groups(), today)
            if d:
                return normalize(d.isoformat(), today)
    return ""


def normalize(value: str, today: date | None = None) -> str:
    """Accept an AI- or feed-provided date; return YYYY-MM-DD if it is plausible, else ''."""
    today = today or date.today()
    v = (value or "").strip()[:10]
    try:
        d = datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return ""
    if not (today - timedelta(days=120) <= d <= today + timedelta(days=366)):
        return ""
    return d.isoformat()


def is_closed(deadline: str, today: date | None = None) -> bool:
    return bool(deadline) and deadline < (today or date.today()).isoformat()


def closing_soon(deadline: str, days: int = 3, today: date | None = None) -> bool:
    today = today or date.today()
    return bool(deadline) and today.isoformat() <= deadline <= (today + timedelta(days=days)).isoformat()
