from pathlib import Path

import yaml

from jobradar import filters, scorer
from jobradar.models import Job
from jobradar.sources.ats import slug_candidates
from jobradar.textutil import word_match

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
KEYWORDS = yaml.safe_load((ROOT / "config/keywords.yaml").read_text())
COMPANIES = yaml.safe_load((ROOT / "config/companies.yaml").read_text())["companies"]


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


def test_keyword_fallback_and_tags():
    j = J("Chief of Staff", company_tags=["boomerang"])
    filters.annotate(j, KEYWORDS)
    scorer.keyword_score(j)
    j.score = 8.5
    tag = scorer.make_tag(j, SETTINGS)
    assert "🔥" in tag and "🔁" in tag


def test_dry_run_with_fixtures(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
