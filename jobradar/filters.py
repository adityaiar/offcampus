"""Deterministic rules applied before (and around) Claude scoring."""
from __future__ import annotations

import re
from datetime import date, timedelta

from .models import Job, _norm
from .textutil import word_match

FOREIGN_HINTS = [
    "united states", "usa", " us", "u.s.", "uk", "united kingdom", "london", "germany", "canada",
    "singapore", "dubai", "uae", "australia", "europe", "emea", "philippines", "poland", "mexico",
    "brazil", "japan", "china", "ireland", "netherlands", "spain", "france", "new york", "san francisco",
]

TIER_BOOST = {"tier1": 1.0, "tier2": 0.5, "tier3": 0.25}


def location_ok(job: Job, settings: dict) -> bool:
    loc = (job.location or "").lower()
    inc = settings["locations"]["include"]
    if any(word_match(t, loc) for t in inc):
        return True
    remote = any(t in loc for t in settings["locations"]["remote_terms"])
    if remote and not any(h.strip() in f" {loc}" for h in FOREIGN_HINTS):
        return True  # remote with no foreign country named → Claude checks India eligibility
    if not loc.strip() and job.bucket_hint:
        return True  # listed company, location unknown → let scorer decide
    return False


def fresh_enough(job: Job, max_age_days: int) -> bool:
    if not job.posted:
        return True
    try:
        return date.fromisoformat(job.posted[:10]) >= date.today() - timedelta(days=max_age_days)
    except ValueError:
        return True


def title_excluded(job: Job, keywords: dict) -> str:
    t = job.title or ""
    for kw in keywords.get("exclude_title", []):
        if word_match(kw, t):
            return kw
    return ""


def annotate(job: Job, keywords: dict) -> None:
    """Attach keyword tier, boost and hints for the scorer."""
    t = job.title or ""
    pr = keywords.get("priority", {})
    for tier in ("tier1", "tier2", "tier3"):
        if any(word_match(k, t) for k in pr.get(tier, [])):
            job.priority_tier = tier
            job.keyword_boost = TIER_BOOST[tier]
            job.hints.append(f"title matches {tier} priority keyword")
            break
    if any(word_match(k, t) for k in keywords.get("deprioritise_title", [])):
        job.keyword_boost -= 1.0
        job.hints.append("title matches a de-prioritised keyword (sales-heavy / junior analyst)")
    desc = job.description or ""
    for rx in keywords.get("exclude_experience_regex", []):
        m = re.search(rx, desc, re.I)
        if m:
            job.hints.append(f"description mentions '{m.group(0)}' — check experience fit")
            break


def company_excluded(job: Job, settings: dict) -> str:
    """Company names to always drop (e.g. the candidate's current employer). Whole-word match."""
    for c in settings.get("exclude_companies") or []:
        if word_match(c, job.company or ""):
            return c
    return ""


def match_company(job: Job, companies: list[dict]) -> dict | None:
    """Match a job's company name (from LinkedIn/aggregators) against the company list."""
    jc = _norm(job.company)
    if not jc:
        return None
    for c in companies:
        cn = _norm(re.sub(r"\(.*?\)", "", c["name"]))
        if not cn:
            continue
        if jc == cn or jc.startswith(cn + " ") or cn.startswith(jc + " ") or jc.replace(" ", "") == cn.replace(" ", ""):
            return c
    return None


def dedupe(jobs: list[Job]) -> list[Job]:
    """Keep one job per key, preferring the version that has a description / comes from an ATS."""
    rank = {"greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0, "smartrecruiters": 1, "linkedin": 2}
    best: dict[str, Job] = {}
    for j in jobs:
        k = j.key
        cur = best.get(k)
        if cur is None:
            best[k] = j
            continue
        score_new = (0 if j.description else 1, rank.get(j.source, 3))
        score_cur = (0 if cur.description else 1, rank.get(cur.source, 3))
        if score_new < score_cur:
            if not j.bucket_hint:
                j.bucket_hint, j.company_tags = cur.bucket_hint, cur.company_tags
            best[k] = j
    return list(best.values())
