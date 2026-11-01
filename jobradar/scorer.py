"""Relevance scoring with Claude (structured JSON output), with a keyword fallback."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .models import Job

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Models that accept the server-side refusal fallback parameter.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "bucket": {"type": "string", "enum": ["consulting", "lifesciences", "startup", "none"]},
                    "score": {"type": "integer", "description": "0-10 overall fit"},
                    "experience_required": {"type": "string", "description": "e.g. '3-6 yrs', 'unclear'"},
                    "india_eligible": {"type": "boolean"},
                    "startup_stage": {"type": "string", "description": "e.g. 'Series C', 'Public', 'n/a', 'unknown'"},
                    "immediate_joiner": {"type": "boolean", "description": "true if the posting needs someone to join well before Feb 2027"},
                    "reason": {"type": "string", "description": "one line, <= 140 chars, why it fits or not"},
                },
                "required": ["id", "bucket", "score", "experience_required", "india_eligible",
                             "startup_stage", "immediate_joiner", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You screen job postings for one candidate (profile below) and score how good a fit each is.

Buckets (pick one):
- consulting: management/strategy consulting firms (MBB, Tier-2, Big-4 strategy arms, boutiques) and
  corporate/business strategy roles at large companies.
- lifesciences: life-science / pharma consulting & analytics firms (ZS, IQVIA, Axtria, Indegene, Clarivate,
  Putnam, Trinity, Beghou, EVERSANA, ...) and commercial / strategy roles at pharma companies or their India GCCs.
- startup: VC-backed startups / new-age tech companies at roughly Series B or later (including post-IPO
  new-age companies like Eternal, Swiggy, Nykaa). Any function that suits a strategy/ops/consulting profile.
- none: does not fit any bucket (IT services, pure tech, non-commercial pharma, early-stage < Series B, etc.).

Score 0-10, adding up:
- Bucket fit (0-3): clearly inside one of the three buckets.
- Experience fit (0-2): role suits ~3-6 years of experience (candidate has 4.6). Too junior (0-2 yrs, analyst
  intern-like) or too senior (8+ yrs, Director/VP) → 0.
- Domain match with the candidate's background (0-3): pharma commercial / SFE / incentive compensation highest;
  then quick commerce / logistics / supply chain; then consumer internet growth, gaming, pricing, promotions,
  loyalty; then ed-tech / health-tech; other sectors lower.
- Function match (0-2): strategy, founder's office, chief of staff, BizOps, program management, growth,
  transformation, consulting delivery. Sales quotas, IT implementation, engineering → 0.
Set india_eligible=false if the role is outside India and not remote-open-to-India; such roles get score <= 2.
If bucket is "none", score <= 4.
The candidate is available from 1 Feb 2027; flag immediate_joiner only if the posting explicitly needs an
immediate / within-30-days joiner. Do not reduce the score for it.
Hints from keyword rules are provided per job; treat them as signals, not verdicts.
Return one result per job id, in any order. Keep reasons concrete (mention the matching experience)."""


def _profile() -> str:
    return (ROOT / "config" / "profile.md").read_text()


def _job_block(i: int, j: Job) -> str:
    desc = (j.description or "").strip()
    if len(desc) > 3500:
        desc = desc[:3500] + " …"
    hints = "; ".join(j.hints) or "none"
    listed = f"on target list as '{j.bucket_hint}'" if j.bucket_hint else "not on target list"
    return (f"<job id=\"{i}\">\nTitle: {j.title}\nCompany: {j.company} ({listed})\n"
            f"Location: {j.location}\nPosted: {j.posted or 'unknown'}\nHints: {hints}\n"
            f"Description: {desc or '(not available — judge from title/company)'}\n</job>")


def keyword_score(j: Job) -> None:
    """Fallback when no API key is configured or a batch fails."""
    base = {"tier1": 7, "tier2": 6, "tier3": 5}.get(j.priority_tier, 4)
    if j.bucket_hint:
        base += 1
    j.bucket = j.bucket or j.bucket_hint or "none"
    j.score = float(base)
    j.experience = j.experience or ""
    j.fit_reason = j.fit_reason or "Keyword-based score (AI scoring unavailable for this job)"


def score_jobs(jobs: list[Job], settings: dict) -> dict:
    """Mutates jobs in place. Returns usage stats."""
    stats = {"scored_ai": 0, "scored_keyword": 0, "input_tokens": 0, "output_tokens": 0}
    if not jobs:
        return stats
    if not os.getenv("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set — using keyword scoring only")
        for j in jobs:
            keyword_score(j)
        stats["scored_keyword"] = len(jobs)
        return stats

    import anthropic

    cfg = settings["scoring"]
    model = os.getenv("CLAUDE_MODEL") or cfg["model"]
    client = anthropic.Anthropic(max_retries=4)
    system = [
        {"type": "text", "text": INSTRUCTIONS},
        {"type": "text", "text": "<candidate_profile>\n" + _profile() + "\n</candidate_profile>",
         "cache_control": {"type": "ephemeral"}},
    ]
    bs = int(cfg.get("batch_size", 10))
    for start in range(0, len(jobs), bs):
        batch = jobs[start:start + bs]
        prompt = "Score these jobs:\n\n" + "\n\n".join(_job_block(i, j) for i, j in enumerate(batch))
        kwargs = dict(
            model=model, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": cfg.get("effort", "low"),
                           "format": {"type": "json_schema", "schema": RESULT_SCHEMA}},
        )
        try:
            if model in FALLBACK_MODELS:
                resp = client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            log.warning("Rate limited by Claude API, keyword-scoring this batch: %s", e)
            resp = None
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e.message)
            resp = None
        except anthropic.APIConnectionError as e:
            log.error("Claude API connection error: %s", e)
            resp = None

        parsed = None
        if resp is not None:
            stats["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
            stats["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0
            if resp.stop_reason in ("refusal", "max_tokens"):
                log.warning("Claude stopped with %s for a batch; keyword-scoring it", resp.stop_reason)
            else:
                text = next((b.text for b in resp.content if b.type == "text"), "")
                try:
                    parsed = json.loads(text)["results"]
                except (ValueError, KeyError) as e:
                    log.warning("Could not parse Claude output: %s", e)

        got = set()
        for r in parsed or []:
            i = r.get("id")
            if not isinstance(i, int) or not (0 <= i < len(batch)):
                continue
            j = batch[i]
            got.add(i)
            s = float(r["score"])
            if not r.get("india_eligible", True):
                s = min(s, 2)
            j.score = max(0.0, min(10.0, s + j.keyword_boost)) if s >= 4 else s
            j.bucket = r["bucket"]
            j.experience = r["experience_required"]
            j.stage = r["startup_stage"]
            j.immediate_joiner = bool(r["immediate_joiner"])
            j.fit_reason = r["reason"]
            stats["scored_ai"] += 1
        for i, j in enumerate(batch):
            if i not in got:
                keyword_score(j)
                stats["scored_keyword"] += 1
    return stats


def make_tag(j: Job, settings: dict) -> str:
    sc = settings["scoring"]
    parts = []
    if "dream" in j.company_tags:
        parts.append("💎 Dream")
    if j.score >= sc["high_fit"]:
        parts.append("🔥 High fit")
    elif j.score >= sc["good_fit"]:
        parts.append("⭐ Good fit")
    if "boomerang" in j.company_tags:
        parts.append("🔁 Boomerang")
    if j.immediate_joiner:
        parts.append("⚡ Immediate joiner")
    return " · ".join(parts)
