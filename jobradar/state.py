"""Small JSON state committed back to the repo by the workflow (seen jobs, ATS probe cache)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
KEEP_DAYS = 90


def load(name: str) -> dict:
    p = STATE_DIR / f"{name}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            return {}
    return {}


def save(name: str, data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True))


def prune_seen(seen: dict) -> dict:
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}
