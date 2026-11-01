"""LinkedIn public (logged-out) job search — the same pages anyone sees without signing in.

Kept deliberately low-volume (a few dozen requests per run, with pauses). LinkedIn may rate-limit
or block automated access at any time; when that happens this source just returns fewer jobs and
the Run Log says so. Turn it off with `sources.linkedin: false` in config/settings.yaml.
"""
from __future__ import annotations

import logging
import random
import time

from bs4 import BeautifulSoup

from ..http import get
from ..models import Job
from ..textutil import strip_html

log = logging.getLogger(__name__)

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"

# f_E: 3 = Associate, 4 = Mid-Senior level   |   f_TPR: r86400 = past 24h, r604800 = past week
EXPERIENCE_FILTER = "3,4"


def _pause():
    time.sleep(random.uniform(1.5, 3.0))


def search(keywords: str, location: str, *, past_seconds: int = 86400, pages: int = 1) -> tuple[list[Job], bool]:
    """Returns (jobs, blocked)."""
    jobs: list[Job] = []
    for page in range(pages):
        r = get(SEARCH, params={
            "keywords": keywords, "location": location, "f_TPR": f"r{past_seconds}",
            "f_E": EXPERIENCE_FILTER, "start": page * 25,
        })
        _pause()
        if r is None:
            return jobs, True
        if r.status_code in (429, 999, 403):
            return jobs, True
        if r.status_code != 200 or not r.text.strip():
            break
        cards = BeautifulSoup(r.text, "html.parser").select("li")
        if not cards:
            break
        for li in cards:
            card = li.select_one("[data-entity-urn]")
            title = li.select_one(".base-search-card__title")
            comp = li.select_one(".base-search-card__subtitle")
            loc = li.select_one(".job-search-card__location")
            link = li.select_one("a.base-card__full-link") or li.select_one("a")
            when = li.select_one("time")
            if not (card and title and comp):
                continue
            jid = card["data-entity-urn"].rsplit(":", 1)[-1]
            url = (link.get("href") if link else "") or f"https://www.linkedin.com/jobs/view/{jid}"
            jobs.append(Job(
                title=title.get_text(strip=True), company=comp.get_text(strip=True),
                location=loc.get_text(strip=True) if loc else "", url=url.split("?")[0],
                source="linkedin", posted=(when.get("datetime", "") if when else "")[:10],
                external_id=f"li-{jid}",
            ))
    return jobs, False


def description(job: Job) -> str:
    jid = job.external_id.removeprefix("li-")
    r = get(DETAIL.format(id=jid))
    _pause()
    if r is None or r.status_code != 200:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    body = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    crit = " | ".join(
        f"{c.select_one('h3').get_text(strip=True)}: {c.select_one('span').get_text(strip=True)}"
        for c in soup.select(".description__job-criteria-item")
        if c.select_one("h3") and c.select_one("span")
    )
    text = strip_html(str(body)) if body else ""
    return f"{crit}\n{text}".strip()
