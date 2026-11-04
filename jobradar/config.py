"""Which candidate profile this run is for, and where its files live.

Each person has a folder under profiles/<name>/ (profile.md, scoring.md, settings.yaml, companies.yaml,
keywords.yaml) and their own state under state/<name>/. Pick one with the PROFILE env var (default: aditya).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = "aditya"

OTHER = {"key": "none", "label": "Other", "email_label": "Other", "color": "#666666", "bg": "#EEEEEE",
         "keywords": []}


def name() -> str:
    n = (os.getenv("PROFILE") or DEFAULT_PROFILE).strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", n):
        raise SystemExit(f"Invalid PROFILE {n!r}: use letters, digits, '-' or '_'")
    return n


def profile_dir(profile: str | None = None) -> Path:
    d = ROOT / "profiles" / (profile or name())
    if not d.is_dir():
        raise SystemExit(f"No profile folder {d} (set PROFILE to one of: "
                         f"{', '.join(sorted(p.name for p in (ROOT / 'profiles').iterdir()))})")
    return d


def load_yaml(fname: str, profile: str | None = None):
    return yaml.safe_load((profile_dir(profile) / fname).read_text())


def read_text(fname: str, profile: str | None = None) -> str:
    return (profile_dir(profile) / fname).read_text()


def buckets(settings: dict, with_other: bool = False) -> list[dict]:
    out = [dict(b) for b in settings.get("buckets") or []]
    for b in out:
        b.setdefault("email_label", b["label"])
        b.setdefault("keywords", [])
    return out + ([dict(OTHER)] if with_other else [])


def bucket_keys(settings: dict) -> list[str]:
    return [b["key"] for b in buckets(settings)]


def bucket_label(settings: dict, key: str) -> str:
    return next((b["label"] for b in buckets(settings, with_other=True) if b["key"] == key), "Other")
