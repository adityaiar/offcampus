"""Job Radar pipeline: fetch → filter → dedupe → score → Google Sheet + email.

Usage:
    python -m jobradar.main                 # full run (needs secrets, see README)
    python -m jobradar.main --dry-run       # no Sheet/email; writes output/preview.html + output/jobs.csv
    python -m jobradar.main --fixtures tests/fixtures/sample_jobs.json --dry-run   # offline test
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from . import filters, scorer, state
from .models import Job
from .sources import aggregators, ats, linkedin

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))
log = logging.getLogger("jobradar")


def load_yaml(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())


def run_label(now: datetime) -> str:
    return now.strftime("%d %b ") + ("AM" if now.hour < 13 else "PM")


# ───────────────────────── fetching ─────────────────────────

def fetch_all(companies, settings, ats_cache, warnings, run_no: int) -> tuple[list[Job], Counter]:
    src = settings["sources"]
    counts: Counter = Counter()
    jobs: list[Job] = []

    if src.get("ats", True):
        for c in companies:
            got = ats.fetch_company(c, ats_cache, warnings)
            if got:
                counts["ats"] += len(got)
                jobs += got
        log.info("ATS feeds: %d jobs", counts["ats"])

    if src.get("linkedin", True):
        blocked = False
        for q in settings["discovery_queries"]:
            for loc in settings["discovery_locations"]:
                if blocked:
                    break
                got, blocked = linkedin.search(q, loc, past_seconds=86400)
                counts["linkedin"] += len(got)
                jobs += got
        # rotate through listed companies that have no ATS feed
        no_feed = [c for c in companies if not ats.has_feed(c, ats_cache)]
        n = int(src.get("linkedin_company_slice", 30))
        if no_feed and not blocked:
            start = (run_no * n) % len(no_feed)
            slice_ = (no_feed + no_feed)[start:start + min(n, len(no_feed))]
            for c in slice_:
                got, blocked = linkedin.search(c["name"], "India", past_seconds=604800)
                for j in got:
                    if filters.match_company(j, [c]):
                        j.bucket_hint, j.company_tags = c["bucket"], list(c.get("tags") or [])
                counts["linkedin"] += len(got)
                jobs += got
                if blocked:
                    break
        if blocked:
            warnings.append("LinkedIn rate-limited this run (partial results)")
        log.info("LinkedIn: %d jobs", counts["linkedin"])

    if src.get("adzuna", True):
        for q in settings["discovery_queries"]:
            got = aggregators.adzuna(q, "India", src.get("max_age_days", 7))
            if got is None:
                break  # no keys configured
            counts["adzuna"] += len(got)
            jobs += got

    if src.get("serpapi", True):
        qs = settings["discovery_queries"]
        k = int(src.get("serpapi_queries_per_run", 1))
        for i in range(k):
            q = qs[(run_no * k + i) % len(qs)]
            got = aggregators.google_jobs(q)
            if got is None:
                break
            counts["google"] += len(got)
            jobs += got
    return jobs, counts


def enrich_descriptions(jobs: list[Job], limit: int = 120) -> None:
    n = 0
    for j in jobs:
        if j.description or n >= limit:
            continue
        if j.source == "linkedin":
            j.description = linkedin.description(j)
            n += 1
        elif j.source == "workday":
            j.description = ats.workday_description(j)
            n += 1


# ───────────────────────── pipeline ─────────────────────────

def run(args) -> int:
    now = datetime.now(IST)
    label = run_label(now)
    settings = load_yaml("settings.yaml")
    companies = load_yaml("companies.yaml")["companies"]
    keywords = load_yaml("keywords.yaml")
    warnings: list[str] = []

    sheet = None
    if not args.dry_run:
        from .sheets import Sheet
        sheet = Sheet()
        sheet.style()
        companies = sheet.companies(companies)
        keywords = sheet.keywords(keywords)

    seen = state.load("seen")
    ats_cache = state.load("ats_cache")
    run_no = int(now.timestamp() // 43200)  # changes every 12h → rotates query/company slices

    if args.fixtures:
        raw = [Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
               for d in json.loads(Path(args.fixtures).read_text())]
        counts = Counter({"fixtures": len(raw)})
    else:
        raw, counts = fetch_all(companies, settings, ats_cache, warnings, run_no)

    # attach company-list info to jobs discovered on LinkedIn/aggregators
    for j in raw:
        if not j.bucket_hint:
            c = filters.match_company(j, companies)
            if c:
                j.bucket_hint, j.company_tags = c["bucket"], list(c.get("tags") or [])

    known = set(seen) | (sheet.existing_keys() if sheet else set())
    stage = Counter()
    kept: list[Job] = []
    for j in filters.dedupe(raw):
        if j.key in known:
            stage["already seen"] += 1
        elif not filters.location_ok(j, settings):
            stage["location"] += 1
        elif not filters.fresh_enough(j, settings["sources"].get("max_age_days", 7)):
            stage["too old"] += 1
        elif filters.title_excluded(j, keywords):
            stage["excluded title"] += 1
        else:
            kept.append(j)
    log.info("Fetched %d, new after filters %d, dropped %s", len(raw), len(kept), dict(stage))

    cap = int(settings["scoring"].get("max_jobs_per_run", 250))
    if len(kept) > cap:
        kept.sort(key=lambda j: not j.bucket_hint)  # listed companies first
        warnings.append(f"{len(kept) - cap} jobs over the per-run cap were deferred")
        kept = kept[:cap]

    if not args.fixtures:
        enrich_descriptions(kept)
    for j in kept:
        filters.annotate(j, keywords)

    stats = scorer.score_jobs(kept, settings)
    sc = settings["scoring"]

    def sort_key(j):
        return ("dream" not in j.company_tags, -j.score)

    kept.sort(key=sort_key)
    found_on = now.strftime("%Y-%m-%d %H:%M")
    good = [j for j in kept if j.score >= sc["good_fit"] or "dream" in j.company_tags or
            ("boomerang" in j.company_tags and j.score >= sc["good_fit"] - 1)]
    low = [j for j in kept if j not in good]
    tags = {j.key: scorer.make_tag(j, settings) for j in kept}

    # remember everything we scored so it isn't re-scored next run
    for j in kept:
        seen[j.key] = now.date().isoformat()
    state.save("seen", state.prune_seen(seen))
    state.save("ats_cache", ats_cache)

    ecfg = settings["email"]
    email_items = [(j, tags[j.key]) for j in good
                   if j.score >= ecfg["min_score"] or "dream" in j.company_tags][: ecfg["max_items"]]
    high = sum(1 for j in good if j.score >= sc["high_fit"])

    from . import emailer
    if sheet:
        from .sheets import Sheet
        rows = [Sheet.job_row(j, found_on, tags[j.key]) for j in good]
        low_rows = [Sheet.job_row(j, found_on, tags[j.key]) for j in low]
        sheet.add_jobs(rows, low_rows)
        sheet.write_new(rows, label)
        sheet.write_tracker()
        sheet.write_dashboard()
        src_summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
        sheet.log_run([found_on, src_summary, len(good), high,
                       f"{stats['input_tokens']}/{stats['output_tokens']}",
                       "; ".join(warnings)[:45000]])
        if good or ecfg.get("send_when_empty", False):
            subject, body = emailer.build(email_items, len(good), sheet.url, label)
            try:
                if emailer.send(subject, body):
                    log.info("Email sent: %s", subject)
            except Exception as e:  # never fail the run because of email
                log.error("Email failed: %s", e)
    else:
        out = ROOT / "output"
        out.mkdir(exist_ok=True)
        subject, body = emailer.build(email_items, len(good), "https://docs.google.com/spreadsheets", label)
        (out / "preview.html").write_text(body)
        with open(out / "jobs.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["score", "tag", "bucket", "company", "title", "location", "experience",
                        "posted", "reason", "url", "source"])
            for j in kept:
                w.writerow([j.score, tags[j.key], j.bucket, j.company, j.title, j.location,
                            j.experience, j.posted, j.fit_reason, j.url, j.source])
        log.info("Dry run: wrote %s and %s (subject: %s)", out / "preview.html", out / "jobs.csv", subject)

    for w_ in warnings:
        log.warning(w_)
    log.info("Done: %d good (%d high fit), %d low. AI-scored %d, keyword-scored %d.",
             len(good), high, len(low), stats["scored_ai"], stats["scored_keyword"])
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="no Google Sheet / email")
    p.add_argument("--fixtures", help="load jobs from a JSON file instead of fetching")
    args = p.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
