# 🎯 Job Radar (offcampus)

Finds new job openings twice a day and scores each one 0–10 with Claude against a candidate profile.
Results land in a styled **Google Sheet** and an **email digest**. It runs separately for each person in
`profiles/`, with their own buckets, keywords, companies, sheet and email:

| Profile | Buckets | Sheet / email secrets |
|---|---|---|
| `aditya` | Consulting/Strategy · Life-Science/Pharma Consulting · Startups (Series B+) | `SHEET_ID`, `EMAIL_TO` |
| `nishi` | Finance (FP&A / Business Finance) · PMO | `SHEET_ID_NISHI`, `EMAIL_TO_NISHI` |

```
GitHub Actions (07:30 & 17:30 IST)
  ├─ Fetch    company ATS feeds (Greenhouse / Lever / Ashby / SmartRecruiters / Workday)
  │           + LinkedIn public job search + Adzuna + Google Jobs (SerpAPI)
  ├─ Filter   dedupe · already-seen · India/remote · posted ≤7 days · excluded titles / companies
  ├─ Score    Claude: bucket, 0–10 fit, experience, application deadline, one-line reason (+ keyword boosts)
  └─ Deliver  Google Sheet (Dashboard / New / All Jobs / Tracker / …) + Gmail digest
```

## What's where

| Path | What it is |
|---|---|
| `profiles/<name>/profile.md` | Candidate profile the AI scores against (no phone / email: the repo is public) |
| `profiles/<name>/scoring.md` | How the AI should score: fit rules, experience range, notice period |
| `profiles/<name>/settings.yaml` | Buckets, name on the dashboard, locations, search queries, excluded companies, sources, thresholds |
| `profiles/<name>/companies.yaml` | Seed company list (copied to the sheet's 🏢 Companies tab on the first run) |
| `profiles/<name>/keywords.yaml` | Priority / exclusion keywords (copied to the 🔑 Keywords tab on the first run) |
| `jobradar/` | The code, shared by everyone |
| `state/<name>/` | Seen-job IDs + ATS probe cache, committed back by the workflow |
| `.github/workflows/job-radar.yml` | Schedule |

After the first run, **edit companies and keywords in the Google Sheet**. The sheet becomes the source of
truth, so you don't need to touch code.

## Google Sheet tabs

- **📊 Dashboard**: KPIs (new today, new this week, high-fit open, applied, interviews, offers), per-bucket counts, top open high-fit roles
- **🔥 New**: just this run's roles, sorted by score
- **📋 All Jobs**: every relevant role. **Apply by** shows the application deadline when the posting states
  one (red when it closes within 3 days, struck through once passed; blank when no date is given). **Update the Status column here** (New → Shortlisted → Applied → Interview → Offer / Rejected / Not interested)
- **✅ Tracker**: live view of everything Shortlisted / Applied / Interview / Offer
- **🗂 Others**: low-score roles, kept so nothing is lost
- **🏢 Companies**, **🔑 Keywords**: your editable lists
- **🧾 Run Log**: per-run counts, AI token usage, warnings (e.g. a broken feed)

Tags: 🔥 High fit (≥8) · ⭐ Good fit (6–7) · 🔁 Boomerang (ex-employer: ZS, Blinkit/Eternal, Zupee, Virohan) ·
⚡ Immediate joiner (posting needs someone sooner than the candidate can join) · ⏳ Closing soon (deadline within
3 days) · ⌛ Closed (deadline passed; moved to 🗂 Others) · 💎 Dream (add `dream` in the Companies tab's Tags column).

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
**Actions → Job Radar → Run workflow** (choose `all`, or one person). The first run builds and styles the sheet and scores the backlog,
up to 250 jobs. After that it runs on its own twice a day.

## Adding a person (e.g. how `nishi` was set up)

Everything except their Google Sheet and email address is shared: API key, service account, Gmail sender,
Adzuna / SerpAPI keys, schedule.

1. Copy `profiles/aditya/` to `profiles/<name>/` and edit all five files: `profile.md` (from their resume,
   leaving out phone/email), `scoring.md`, `settings.yaml` (buckets, `display`, locations, queries,
   `exclude_companies`), `profiles/<name>/companies.yaml`, `keywords.yaml`.
2. Add `<name>` to the workflow's `matrix.profile` list and `inputs.profile` options, and add lines for
   their `SHEET_ID_<NAME>` / `EMAIL_TO_<NAME>` secrets (the `SHEET_ID:` / `EMAIL_TO:` lines in the workflow).
3. Create a blank Google Sheet for them, **share it as Editor with the same service account email**
   (inside `GOOGLE_SERVICE_ACCOUNT_JSON`, `client_email`). Share it with the person too.
4. Add repository secrets `SHEET_ID_<NAME>` (sheet ID or URL) and `EMAIL_TO_<NAME>`.
5. **Actions → Job Radar → Run workflow → profile: `<name>`** for their first run.

SerpAPI's free tier is 100 searches/month across everyone: Aditya runs 1 per run (≈60/month) and Nishi 1 every
other run (`serpapi_every_n_runs: 2`, ≈30/month).

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
     `X-GitHub-Api-Version: 2022-11-28`; request body `{"ref":"main"}` (runs everyone; add
     `"inputs":{"profile":"nishi"}` to run one person)
3. Save, then click *Test run*. A new run should appear in the repo's Actions tab within seconds.

If GitHub's own (late) scheduled run also fires, it just finds nothing new or only a few new jobs.

## Running locally

```bash
pip install -r requirements.txt
python -m pytest -q
python -m jobradar.main --dry-run --profile nishi   # live fetch, no Sheet/email → output/preview.html + output/jobs.csv
python -m jobradar.main --dry-run --fixtures tests/fixtures/sample_jobs.json   # fully offline (profile aditya)
```

## Cost

Only the AI scoring costs money. Jobs are deduplicated and pre-filtered, so only new, plausible roles reach Claude,
batched 10 per call.

Costs below are per person; two profiles cost about twice as much.

| Model (`CLAUDE_MODEL`) | Rough cost at ~100 new jobs/day |
|---|---|
| `claude-opus-5-5` (default, best judgment) | ≈ $0.75/day ≈ $20–25/month |
| `claude-haiku-5-5` (cheapest) | ≈ $0.02/day ≈ under $1/month |

Actual token usage per run is logged in the 🧾 Run Log tab. Without an API key the radar still runs, using
keyword-only scoring.

## Notes & limits

- **LinkedIn**: uses LinkedIn's public, logged-out job search at low volume. LinkedIn may rate-limit or block
  automated access, and its terms restrict scraping. If you'd rather not, set `sources.linkedin: false` in
  `profiles/<name>/settings.yaml`. Many of the same roles also come in via Adzuna and Google Jobs.
- **ATS feeds**: companies without an `ats` value are auto-probed on Greenhouse, Lever, Ashby and
  SmartRecruiters (cached for 14 days). Big consulting and pharma firms mostly use their own career sites,
  so they're covered through LinkedIn and aggregator search. Workday URLs in `profiles/<name>/companies.yaml` are best-known
  values; broken ones appear in the Run Log. Fix them in the 🏢 Companies tab.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. The state commits each run
  normally keep it active.
