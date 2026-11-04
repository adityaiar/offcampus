# 🎯 Job Radar (offcampus)

Finds new job openings twice a day in three buckets: **Consulting/Strategy**, **Life-Science/Pharma
Consulting**, and **Startups (Series B+)**. Each job gets a 0–10 fit score from Claude against my profile.
Results land in a styled **Google Sheet** and an **email digest**.

```
GitHub Actions (07:30 & 17:30 IST)
  ├─ Fetch    company ATS feeds (Greenhouse / Lever / Ashby / SmartRecruiters / Workday)
  │           + LinkedIn public job search + Adzuna + Google Jobs (SerpAPI)
  ├─ Filter   dedupe · already-seen · India/remote · posted ≤7 days · excluded titles
  ├─ Score    Claude: bucket, 0–10 fit, experience, one-line reason (+ keyword boosts)
  └─ Deliver  Google Sheet (Dashboard / New / All Jobs / Tracker / …) + Gmail digest
```

## What's where

| Path | What it is |
|---|---|
| `config/profile.md` | Candidate profile the AI scores against. Edit it when your goals change |
| `config/settings.yaml` | Locations, search queries, sources on/off, score thresholds, model |
| `config/companies.yaml` | Seed company list (copied to the sheet's 🏢 Companies tab on the first run) |
| `config/keywords.yaml` | Priority / exclusion keywords (copied to the 🔑 Keywords tab on the first run) |
| `jobradar/` | The code |
| `state/` | Seen-job IDs + ATS probe cache, committed back by the workflow |
| `.github/workflows/job-radar.yml` | Schedule |

After the first run, **edit companies and keywords in the Google Sheet**. The sheet becomes the source of
truth, so you don't need to touch code.

## Google Sheet tabs

- **📊 Dashboard**: KPIs (new today, new this week, high-fit open, applied, interviews, offers), per-bucket counts, top open high-fit roles
- **🔥 New**: just this run's roles, sorted by score
- **📋 All Jobs**: every relevant role. **Update the Status column here** (New → Shortlisted → Applied → Interview → Offer / Rejected / Not interested)
- **✅ Tracker**: live view of everything Shortlisted / Applied / Interview / Offer
- **🗂 Others**: low-score roles, kept so nothing is lost
- **🏢 Companies**, **🔑 Keywords**: your editable lists
- **🧾 Run Log**: per-run counts, AI token usage, warnings (e.g. a broken feed)

Tags: 🔥 High fit (≥8) · ⭐ Good fit (6–7) · 🔁 Boomerang (ex-employer: ZS, Blinkit/Eternal, Zupee, Virohan) ·
⚡ Immediate joiner (posting needs someone before Feb 2027) · 💎 Dream (add `dream` in the Companies tab's Tags column).

## One-time setup (about 30 minutes)

### 1. Anthropic API key
At console.anthropic.com → API Keys, create a key. You'll add it as `ANTHROPIC_API_KEY` in step 5.

### 2. Google Sheet + service account
1. Go to console.cloud.google.com and create a project (e.g. `job-radar`).
2. **APIs & Services → Library**: enable **Google Sheets API** and **Google Drive API**.
3. **IAM & Admin → Service Accounts → Create**, name it `job-radar` (no roles needed). Open it, then
   **Keys → Add key → JSON**, which downloads a `.json` file.
4. Signed in as **adityarai.ngh@gmail.com**, create a blank Google Sheet named "Job Radar".
5. **Share** the sheet with the service account's email (`job-radar@<project>.iam.gserviceaccount.com`) as **Editor**.
6. Copy the sheet ID from its URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`.

### 3. Gmail app password (for the digest)
1. Turn on 2-Step Verification on the Google account.
2. Go to myaccount.google.com/apppasswords and create one named "Job Radar". You get a 16-character password.

### 4. Optional extra sources (free tiers)
- **Adzuna** (developer.adzuna.com): `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
- **SerpAPI / Google Jobs** (serpapi.com, 100 free searches a month): `SERPAPI_KEY`

### 5. GitHub secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from step 1 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of the downloaded JSON file |
| `SHEET_ID` | from step 2.6 |
| `GMAIL_ADDRESS` | `adityarai.ngh@gmail.com` |
| `GMAIL_APP_PASSWORD` | from step 3 |
| `EMAIL_TO` | `adityarai.ngh@gmail.com` (comma-separate for more recipients) |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `SERPAPI_KEY` | optional |

To change the AI model without editing code, add a repository **variable** (not a secret) named `CLAUDE_MODEL`,
e.g. `claude-haiku-5-5`.

### 6. First run
**Actions → Job Radar → Run workflow.** The first run builds and styles the sheet and scores the backlog,
up to 250 jobs. After that it runs on its own twice a day.

## Schedule (on-time runs)

The workflow is scheduled for **07:30 and 17:30 IST**. GitHub's free scheduler is best-effort, though,
and often starts runs hours late (we've seen ~7 h). For punctual runs, trigger the workflow from a free
external scheduler as well:

1. **GitHub token**: GitHub → Settings → Developer settings → Fine-grained personal access tokens →
   *Generate new token*. Repository access: **Only `adityaiar/offcampus`**. Permissions: **Actions →
   Read and write**. Set an expiry (e.g. 1 year) and copy the token.
2. **cron-job.org** (free): create an account, then *Create cronjob*:
   - URL: `https://api.github.com/repos/adityaiar/offcampus/actions/workflows/job-radar.yml/dispatches`
   - Schedule: custom, **07:30 and 17:30**, time zone **Asia/Kolkata**
   - Advanced → Request method **POST**; headers
     `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`; request body `{"ref":"main"}`
3. Save, then click *Test run*. A new run should appear in the repo's Actions tab within seconds.

If GitHub's own (late) scheduled run also fires, it just finds nothing new or only a few new jobs.

## Running locally

```bash
pip install -r requirements.txt
python -m pytest -q
python -m jobradar.main --dry-run          # live fetch, no Sheet/email → output/preview.html + output/jobs.csv
python -m jobradar.main --dry-run --fixtures tests/fixtures/sample_jobs.json   # fully offline
```

## Cost

Only the AI scoring costs money. Jobs are deduplicated and pre-filtered, so only new, plausible roles reach Claude,
batched 10 per call.

| Model (`CLAUDE_MODEL`) | Rough cost at ~100 new jobs/day |
|---|---|
| `claude-opus-5-5` (default, best judgment) | ≈ $0.75/day ≈ $20–25/month |
| `claude-haiku-5-5` (cheapest) | ≈ $0.02/day ≈ under $1/month |

Actual token usage per run is logged in the 🧾 Run Log tab. Without an API key the radar still runs, using
keyword-only scoring.

## Notes & limits

- **LinkedIn**: uses LinkedIn's public, logged-out job search at low volume. LinkedIn may rate-limit or block
  automated access, and its terms restrict scraping. If you'd rather not, set `sources.linkedin: false` in
  `config/settings.yaml`. Many of the same roles also come in via Adzuna and Google Jobs.
- **ATS feeds**: companies without an `ats` value are auto-probed on Greenhouse, Lever, Ashby and
  SmartRecruiters (cached for 14 days). Big consulting and pharma firms mostly use their own career sites,
  so they're covered through LinkedIn and aggregator search. Workday URLs in `companies.yaml` are best-known
  values; broken ones appear in the Run Log. Fix them in the 🏢 Companies tab.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. The state commits each run
  normally keep it active.
