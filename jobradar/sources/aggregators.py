"""Job aggregators with official APIs: Adzuna (India) and Google Jobs via SerpAPI. Both optional."""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from ..http import get
from ..models import Job
from ..textutil import strip_html

log = logging.getLogger(__name__)


def adzuna(query: str, where: str, max_days: int) -> list[Job] | None:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return None
    r = get("https://api.adzuna.com/v1/api/jobs/in/search/1", params={
        "app_id": app_id, "app_key": app_key, "what": query, "where": where,
        "max_days_old": max_days, "results_per_page": 50, "content-type": "application/json",
    })
    if r is None or r.status_code != 200:
        log.warning("Adzuna failed for %r: %s", query, getattr(r, "status_code", "no response"))
        return []
    out = []
    for j in r.json().get("results", []):
        out.append(Job(
            title=strip_html(j.get("title", "")),
            company=(j.get("company") or {}).get("display_name", ""),
            location=(j.get("location") or {}).get("display_name", ""),
            url=j.get("redirect_url", ""), source="adzuna",
            description=strip_html(j.get("description", "")),
            posted=(j.get("created") or "")[:10], external_id=f"az-{j.get('id')}",
        ))
    return out


def _posted_from_text(t: str) -> str:
    t = (t or "").lower()
    n = "".join(ch for ch in t if ch.isdigit())
    if not n:
        return date.today().isoformat() if ("hour" in t or "just" in t) else ""
    n = int(n)
    if "hour" in t or "minute" in t:
        return date.today().isoformat()
    if "day" in t:
        return (date.today() - timedelta(days=n)).isoformat()
    if "week" in t:
        return (date.today() - timedelta(weeks=n)).isoformat()
    if "month" in t:
        return (date.today() - timedelta(days=30 * n)).isoformat()
    return ""


def google_jobs(query: str, location: str = "India") -> list[Job] | None:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        return None
    r = get("https://serpapi.com/search.json", params={
        "engine": "google_jobs", "q": query, "location": location, "hl": "en", "gl": "in",
        "chips": "date_posted:3days", "api_key": key,
    }, timeout=40)
    if r is None or r.status_code != 200:
        log.warning("SerpAPI failed for %r: %s", query, getattr(r, "status_code", "no response"))
        return []
    out = []
    for j in r.json().get("jobs_results", []):
        apply = (j.get("apply_options") or [{}])[0].get("link") or j.get("share_link", "")
        ext = j.get("detected_extensions") or {}
        out.append(Job(
            title=j.get("title", ""), company=j.get("company_name", ""),
            location=j.get("location", ""), url=apply, source=f"google:{j.get('via', '')}".rstrip(":"),
            description=j.get("description", ""), posted=_posted_from_text(ext.get("posted_at", "")),
            external_id=f"gj-{(j.get('job_id') or '')[:40]}",
        ))
    return out
