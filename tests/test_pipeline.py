from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from jobradar import config, deadline, filters, scorer
from jobradar.models import Job
from jobradar.sources.ats import slug_candidates
from jobradar.textutil import word_match

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = yaml.safe_load((ROOT / "profiles/aditya/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "profiles/aditya/keywords.yaml").read_text())
COMPANIES = yaml.safe_load((ROOT / "profiles/aditya/companies.yaml").read_text())["companies"]
N_SETTINGS = yaml.safe_load((ROOT / "profiles/nishi/settings.yaml").read_text())
N_KEYWORDS = yaml.safe_load((ROOT / "profiles/nishi/keywords.yaml").read_text())
PROFILES = sorted(p.name for p in (ROOT / "profiles").iterdir() if p.is_dir())


def J(title="Strategy Manager", company="Acme", location="Mumbai, India", **kw):
    return Job(title=title, company=company, location=location, url="u", source="test", **kw)


def test_word_match_is_whole_word():
    assert word_match("tax", "Tax Manager")
    assert not word_match("tax", "Taxonomy Lead")
    assert word_match("vp", "VP, Strategy")
    assert not word_match("sap", "Sapient Strategy")


def test_title_exclusions():
    assert filters.title_excluded(J("SAP FICO Consultant"), KEYWORDS)
    assert filters.title_excluded(J("Associate Director, Strategy"), KEYWORDS)
    assert filters.title_excluded(J("Summer Intern - Strategy"), KEYWORDS)
    assert not filters.title_excluded(J("Manager, Founder's Office"), KEYWORDS)
    assert not filters.title_excluded(J("Consultant - Commercial Excellence"), KEYWORDS)


def test_locations():
    assert filters.location_ok(J(location="Gurugram, Haryana"), SETTINGS)
    assert filters.location_ok(J(location="Bangalore Urban"), SETTINGS)
    assert filters.location_ok(J(location="Remote"), SETTINGS)
    assert not filters.location_ok(J(location="Remote - United States"), SETTINGS)
    assert not filters.location_ok(J(location="London, UK"), SETTINGS)


def test_priority_annotation():
    j = J("Manager, Founder's Office")
    filters.annotate(j, KEYWORDS)
    assert j.priority_tier == "tier1" and j.keyword_boost == 1.0
    j = J("Key Account Manager")
    filters.annotate(j, KEYWORDS)
    assert j.keyword_boost < 0


def test_experience_hint_not_drop():
    j = J("Strategy Consultant", description="Roles from 0-2 years (Analyst) to 4-6 years (Consultant)")
    filters.annotate(j, KEYWORDS)
    assert any("experience" in h for h in j.hints)


def test_company_matching_and_boomerang():
    c = filters.match_company(J(company="ZS"), COMPANIES)
    assert c is None or c["name"] == "ZS Associates"
    c = filters.match_company(J(company="ZS Associates India"), COMPANIES)
    assert c and "boomerang" in c["tags"]
    assert filters.match_company(J(company="Kearney"), COMPANIES)["bucket"] == "consulting"


def test_dedupe_prefers_description():
    a = J("Manager, Founder's Office", "Zepto", "Mumbai, India", description="")
    b = J("Manager, Founder's Office", "Zepto", "Mumbai, Maharashtra", description="full JD")
    out = filters.dedupe([a, b])
    assert len(out) == 1 and out[0].description == "full JD"


def test_slug_candidates():
    assert "zsassociates" in slug_candidates("ZS Associates")
    assert "razorpay" in slug_candidates("Razorpay")
    assert "lek" in slug_candidates("L.E.K. Consulting")
    assert "eli" not in slug_candidates("Eli Lilly")       # no first-word-only slugs


def test_keyword_fallback_and_tags():
    j = J("Chief of Staff", company_tags=["boomerang"])
    filters.annotate(j, KEYWORDS)
    scorer.keyword_score(j, SETTINGS)
    j.score = 8.5
    tag = scorer.make_tag(j, SETTINGS)
    assert "🔥" in tag and "🔁" in tag


def test_dry_run_with_fixtures(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PROFILE", "aditya")
    import argparse
    from jobradar import main, state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    import json
    from datetime import date
    data = json.loads((ROOT / "tests/fixtures/sample_jobs.json").read_text())
    for d in data:  # keep fixtures "fresh" regardless of when the test runs (except the stale one)
        if not d["posted"].startswith("2025"):
            d["posted"] = date.today().isoformat()
    fx = tmp_path / "jobs.json"
    fx.write_text(json.dumps(data))
    args = argparse.Namespace(dry_run=True, fixtures=str(fx))
    assert main.run(args) == 0
    csv_text = (ROOT / "output/jobs.csv").read_text()
    assert "Axtria" in csv_text and "Zepto" in csv_text
    assert "SAP FICO" not in csv_text          # excluded title
    assert "Acme Corp" not in csv_text         # New York
    assert "Associate Director" not in csv_text
    assert csv_text.count("Founder's Office") == 1   # deduped across sources
    assert "Kearney" not in csv_text           # posted too long ago


def test_sheet_key_accepts_url_or_id():
    from jobradar.sheets import sheet_key
    sid = "1AbC_dEf-123456789012345678901234567890XyZ"
    assert sheet_key(sid) == sid
    assert sheet_key(f"https://docs.google.com/spreadsheets/d/{sid}/edit?gid=0#gid=0") == sid
    assert sheet_key(f" {sid}/edit ") == sid


# ───────────────────────── profiles ─────────────────────────

@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_is_complete(profile, monkeypatch):
    monkeypatch.setenv("PROFILE", profile)
    s = config.load_yaml("settings.yaml")
    keys = config.bucket_keys(s)
    assert keys and "none" not in keys and len(set(keys)) == len(keys)
    for b in config.buckets(s):
        assert b["label"] and b["description"] and b["color"].startswith("#") and b["bg"].startswith("#")
    comp = config.load_yaml("companies.yaml")["companies"]
    assert all(c["bucket"] in [*keys, "any"] for c in comp)
    kw = config.load_yaml("keywords.yaml")
    assert kw["priority"]["tier1"] and kw["exclude_title"]
    prompt = scorer.instructions(s)
    assert all(f"- {k}:" in prompt for k in keys)
    assert scorer.result_schema(keys)["properties"]["results"]["items"]["properties"]["bucket"]["enum"] == [*keys, "none"]
    assert "@" not in config.read_text("profile.md")          # no email addresses in the public repo
    assert not __import__("re").search(r"\+?\d[\d\s-]{9,}\d", config.read_text("profile.md"))  # no phone numbers


def test_nishi_titles():
    assert not filters.title_excluded(J("Finance Business Partner"), N_KEYWORDS)
    assert not filters.title_excluded(J("Senior Analyst - Financial Planning and Analysis"), N_KEYWORDS)
    assert not filters.title_excluded(J("Financial Planning Specialist"), N_KEYWORDS)
    assert not filters.title_excluded(J("PMO Lead"), N_KEYWORDS)
    assert filters.title_excluded(J("Accounts Payable Specialist"), N_KEYWORDS)
    assert filters.title_excluded(J("Senior Accountant - R2R"), N_KEYWORDS)
    assert filters.title_excluded(J("Director, FP&A"), N_KEYWORDS)
    for t in ("Finance Business Partner", "Senior Analyst FP&A", "Financial Planning Specialist", "PMO Manager"):
        j = J(t)
        filters.annotate(j, N_KEYWORDS)
        assert j.priority_tier == "tier1", t


def test_nishi_excludes_current_employer():
    assert filters.company_excluded(J(company="Infosys"), N_SETTINGS)
    assert filters.company_excluded(J(company="Infosys BPM Limited"), N_SETTINGS)
    assert not filters.company_excluded(J(company="Accenture"), N_SETTINGS)
    assert not filters.company_excluded(J(company="Infosys"), SETTINGS)


def test_nishi_locations_pan_india():
    assert filters.location_ok(J(location="Kolkata"), N_SETTINGS)
    assert filters.location_ok(J(location="Jaipur, Rajasthan"), N_SETTINGS)
    assert filters.location_ok(J(location="Bhubaneswar, Odisha, India"), N_SETTINGS)
    assert not filters.location_ok(J(location="Dubai, UAE"), N_SETTINGS)


def test_nishi_keyword_fallback_buckets():
    for title, bucket in (("Finance Business Partner", "finance"), ("PMO Analyst", "pmo"), ("Store Manager", "none")):
        j = J(title, bucket_hint="any")
        scorer.keyword_score(j, N_SETTINGS)
        assert j.bucket == bucket, title


# ───────────────────────── application deadlines ─────────────────────────

def test_deadline_from_text():
    t = date(2026, 11, 4)
    assert deadline.find_in_text("Last date to apply: 15th November 2026", t) == "2026-11-15"
    assert deadline.find_in_text("Apply by Nov 20, 2026.", t) == "2026-11-20"
    assert deadline.find_in_text("Applications close on 30/11/2026", t) == "2026-11-30"
    assert deadline.find_in_text("Deadline: 10 Dec", t) == "2026-12-10"
    assert deadline.find_in_text("3-6 years of FP&A experience, posted 15/11/2026", t) == ""
    assert deadline.find_in_text("", t) == ""


def test_deadline_normalize_and_tags():
    today = date.today()
    assert deadline.normalize("2020-01-01") == ""          # implausible
    assert deadline.normalize("not a date") == ""
    soon = (today + timedelta(days=2)).isoformat()
    past = (today - timedelta(days=1)).isoformat()
    assert deadline.normalize(soon) == soon
    j = J("FP&A Lead", deadline=soon)
    j.score = 8
    assert "⏳" in scorer.make_tag(j, N_SETTINGS)
    j.deadline = past
    assert "⌛" in scorer.make_tag(j, N_SETTINGS)


def test_sheet_columns():
    from jobradar.sheets import JOB_COLS, ADDED_COLS, L
    assert JOB_COLS.index("Apply by") == JOB_COLS.index("Posted") + 1
    assert all(c in JOB_COLS for c in ADDED_COLS)
    assert L("Score") == "A" and L("Status") == "L" and L("URL") == "Q"


def test_dry_run_nishi(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PROFILE", "nishi")
    import argparse
    import csv
    import json
    from jobradar import main, state
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    today = date.today()
    soon = (today + timedelta(days=10)).strftime("%d %B %Y")
    jobs = [
        {"title": "Finance Business Partner", "company": "American Express", "location": "Gurugram, Haryana, India",
         "description": f"4-6 years FP&A. Last date to apply: {soon}."},
        {"title": "PMO Lead", "company": "Accenture", "location": "Kolkata, West Bengal"},
        {"title": "Senior Analyst FP&A", "company": "Infosys", "location": "Bengaluru, India"},
        {"title": "Accounts Payable Executive", "company": "Genpact", "location": "Noida, India"},
    ]
    for i, d in enumerate(jobs):
        d.update(url=f"https://example.com/{i}", source="linkedin", posted=today.isoformat())
    fx = tmp_path / "jobs.json"
    fx.write_text(json.dumps(jobs))
    assert main.run(argparse.Namespace(dry_run=True, fixtures=str(fx))) == 0
    rows = list(csv.DictReader(open(ROOT / "output/jobs.csv")))
    by_title = {r["title"]: r for r in rows}
    assert set(by_title) == {"Finance Business Partner", "PMO Lead"}   # Infosys + AP dropped
    assert by_title["Finance Business Partner"]["bucket"] == "finance"
    assert by_title["PMO Lead"]["bucket"] == "pmo"
    assert by_title["Finance Business Partner"]["apply_by"] == (today + timedelta(days=10)).isoformat()
    assert by_title["PMO Lead"]["apply_by"] == ""
    html = (ROOT / "output/preview.html").read_text()
    assert "Nishi Kumari" in html and "Apply by" in html
