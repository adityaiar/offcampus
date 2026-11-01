from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str                      # e.g. "greenhouse", "linkedin", "adzuna"
    description: str = ""
    posted: str = ""                 # ISO date (YYYY-MM-DD) when known
    external_id: str = ""            # id from the source, if any
    bucket_hint: str = ""            # from the company list
    company_tags: list[str] = field(default_factory=list)

    # Filled by filters / scorer
    priority_tier: str = ""          # tier1 | tier2 | tier3 | ""
    keyword_boost: float = 0.0
    hints: list[str] = field(default_factory=list)
    bucket: str = ""
    score: float = 0.0
    tag: str = ""
    experience: str = ""
    fit_reason: str = ""
    stage: str = ""
    immediate_joiner: bool = False

    @property
    def key(self) -> str:
        """Dedup key: same company + same title + same city = same job across sources."""
        city = _norm(self.location).split(" ")[0] if self.location else ""
        raw = f"{_norm(self.company)}|{_norm(self.title)}|{city}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d
