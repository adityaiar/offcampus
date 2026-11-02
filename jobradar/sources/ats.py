"""Public job-board feeds of applicant-tracking systems (no login, no scraping of HTML).

Supported: Greenhouse, Lever, Ashby, SmartRecruiters, Workday.
Companies without an explicit `ats` are auto-probed on Greenhouse/Lever/Ashby/SmartRecruiters
and the result is cached in state/ats_cache.json.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone

from ..http import get, post_json
from ..models import Job
from ..textutil import strip_html

log = logging.getLogger(__name__)

PROBE_PROVIDERS = ["greenhouse", "lever", "ashby", "smartrecruiters"]
REPROBE_DAYS = 14

WORKDAY_SEARCH_TERMS = ["strategy", "commercial", "consult", "incentive", "excellence",
                        "program", "operations", "business", "analytics", "transformation"]


def _iso_from_ms(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


def _iso(s) -> str:
    return (s or "")[:10]


# ───────────────────────── individual providers ─────────────────────────

def greenhouse(slug: str, company: str) -> list[Job] | None:
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    if r is None or r.status_code != 200:
        return None
    jobs = []
    for j in r.json().get("jobs", []):
        jobs.append(Job(
            title=j.get("title", ""), company=company,
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""), source="greenhouse",
            description=strip_html(html.unescape(j.get("content", ""))),
            posted=_iso(j.get("first_published") or j.get("updated_at")),
            external_id=f"gh-{j.get('id')}",
        ))
    return jobs


def lever(slug: str, company: str) -> list[Job] | None:
    for host in ("api.lever.co", "api.eu.lever.co"):
        r = get(f"https://{host}/v0/postings/{slug}", params={"mode": "json"})
        if r is not None and r.status_code == 200 and isinstance(r.json(), list):
            break
    else:
        return None
    jobs = []
    for j in r.json():
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        if j.get("workplaceType") == "remote":
            loc = f"{loc} (Remote)".strip()
        jobs.append(Job(
            title=j.get("text", ""), company=company, location=loc,
            url=j.get("hostedUrl", ""), source="lever",
            description=j.get("descriptionPlain", "") or strip_html(j.get("description", "")),
            posted=_iso_from_ms(j.get("createdAt")), external_id=f"lv-{j.get('id')}",
        ))
    return jobs


def ashby(slug: str, company: str) -> list[Job] | None:
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r is None or r.status_code != 200:
        return None
    jobs = []
    for j in r.json().get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = "; ".join(l for l in locs if l)
        if j.get("isRemote"):
            loc = f"{loc} (Remote)".strip()
        jobs.append(Job(
            title=j.get("title", ""), company=company, location=loc,
            url=j.get("jobUrl", ""), source="ashby",
            description=j.get("descriptionPlain", "") or strip_html(j.get("descriptionHtml", "")),
            posted=_iso(j.get("publishedAt")), external_id=f"ab-{j.get('id')}",
        ))
    return jobs


def smartrecruiters(slug: str, company: str) -> list[Job] | None:
    r = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": 100, "country": "in"})
    if r is None or r.status_code != 200:
        return None
    data = r.json()
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        loc_s = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
        if loc.get("remote"):
            loc_s += " (Remote)"
        jobs.append(Job(
            title=j.get("name", ""), company=company, location=loc_s,
            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", source="smartrecruiters",
            description=(j.get("function") or {}).get("label", ""),
            posted=_iso(j.get("releasedDate")), external_id=f"sr-{j.get('id')}",
        ))
    return jobs


def workday(spec: str, company: str) -> list[Job] | None:
    """spec = '<host>/<tenant>/<site>', e.g. 'novartis.wd3.myworkdayjobs.com/novartis/Novartis_Careers'."""
    try:
        host, tenant, site = spec.split("/", 2)
    except ValueError:
        log.warning("Bad workday spec for %s: %s", company, spec)
        return None
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    seen, jobs, ok = set(), [], False
    for term in WORKDAY_SEARCH_TERMS:
        r = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0,
                                       "searchText": f"{term} India"})
        if r is None or r.status_code != 200:
            continue
        ok = True
        for p in r.json().get("jobPostings", []):
            path = p.get("externalPath", "")
            if not path or path in seen:
                continue
            seen.add(path)
            jobs.append(Job(
                title=p.get("title", ""), company=company,
                location=p.get("locationsText", ""),
                url=f"https://{host}/en-US/{site}{path}", source="workday",
                posted=_workday_posted(p.get("postedOn", "")),
                external_id=f"wd-{tenant}-{path.rsplit('_', 1)[-1]}",
            ))
            jobs[-1]._wd_detail = f"{api}{path}"  # type: ignore[attr-defined]
        time.sleep(0.3)
    return jobs if ok else None


def _workday_posted(text: str) -> str:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' → ISO date."""
    from datetime import date, timedelta
    t = (text or "").lower()
    today = date.today()
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return ""


def workday_description(job: Job) -> str:
    url = getattr(job, "_wd_detail", None)
    if not url:
        return ""
    r = get(url, headers={"Accept": "application/json"})
    if r is None or r.status_code != 200:
        return ""
    return strip_html((r.json().get("jobPostingInfo") or {}).get("jobDescription", ""))


PROVIDERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "smartrecruiters": smartrecruiters, "workday": workday,
}


# ───────────────────────── orchestration ─────────────────────────

def slug_candidates(name: str) -> list[str]:
    base = re.sub(r"\(.*?\)", "", name).replace("&", "and")
    words = re.findall(r"[a-z0-9]+", base.lower())
    if not words:
        return []
    # Full-name slugs only: a first-word slug ("eli" for Eli Lilly) matched other companies' boards.
    cands = ["".join(words), "-".join(words)]
    # drop generic suffixes ("consulting", "india", "group"...)
    generic = {"consulting", "india", "group", "company", "and", "the", "technologies", "labs",
               "energy", "consumer", "intelligence", "health", "pharma", "pharmaceuticals", "laboratories"}
    core = [w for w in words if w not in generic]
    if core:
        cands += ["".join(core), "-".join(core)]
    out = []
    for c in cands:
        if c and c not in out and len(c) >= 3:
            out.append(c)
    return out


INDIA_RX = re.compile(r"india|mumbai|bengaluru|bangalore|gurugram|gurgaon|delhi|noida|hyderabad|pune|chennai",
                      re.I)


def probe(name: str) -> str:
    """Return 'provider:slug' for the first ATS board with at least one India-based job, or ''.

    Requiring an India job guards against same-named companies elsewhere (e.g. a US 'Porter').
    """
    for slug in slug_candidates(name):
        for prov in PROBE_PROVIDERS:
            jobs = PROVIDERS[prov](slug, name)  # smartrecruiters() already filters to country=in
            if jobs and any(INDIA_RX.search(j.location or "") for j in jobs):
                return f"{prov}:{slug}"
    return ""


def fetch_company(company: dict, cache: dict, log_entries: list) -> list[Job]:
    name = company["name"]
    spec = (company.get("ats") or "").strip()
    if not spec:
        cached = cache.get(name)
        fresh = cached and (time.time() - cached.get("ts", 0) < REPROBE_DAYS * 86400)
        if fresh:
            spec = cached.get("spec", "")
        else:
            spec = probe(name)
            cache[name] = {"spec": spec, "ts": time.time()}
        if not spec:
            return []   # covered by LinkedIn company search instead
    prov, _, arg = spec.partition(":")
    fn = PROVIDERS.get(prov)
    if fn is None:
        log_entries.append(f"Unknown ATS '{prov}' for {name}")
        return []
    jobs = fn(arg, name)
    if jobs is None:
        log_entries.append(f"⚠ {name}: {prov} feed not reachable ({arg})")
        if not company.get("ats"):
            cache.pop(name, None)   # force a re-probe next run
        return []
    for j in jobs:
        j.bucket_hint = company.get("bucket", "")
        j.company_tags = list(company.get("tags") or [])
    return jobs


def has_feed(company: dict, cache: dict) -> bool:
    return bool((company.get("ats") or "").strip() or (cache.get(company["name"]) or {}).get("spec"))
