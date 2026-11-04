"""Google Sheet output: tabs, styling, dashboard, and editable config tabs."""
from __future__ import annotations

import json
import logging
import os
import re

import gspread
from http import HTTPStatus
from gspread.http_client import BackOffHTTPClient

from . import config
from .models import Job


class RetryingHTTPClient(BackOffHTTPClient):
    """Retry Google's transient errors (408, 429, 5xx) with backoff: 2s, 4s, … up to 64s (~2 min total)."""
    _HTTP_ERROR_CODES = [
        HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.GATEWAY_TIMEOUT,
    ]
    _MAX_BACKOFF = 64

log = logging.getLogger(__name__)

T_DASH, T_NEW, T_ALL, T_TRACK = "📊 Dashboard", "🔥 New", "📋 All Jobs", "✅ Tracker"
T_OTHERS, T_COMP, T_KW, T_LOG = "🗂 Others", "🏢 Companies", "🔑 Keywords", "🧾 Run Log"
TAB_ORDER = [T_DASH, T_NEW, T_ALL, T_TRACK, T_OTHERS, T_COMP, T_KW, T_LOG]

JOB_COLS = ["Score", "Tag", "Bucket", "Company", "Role", "Location", "Experience", "Posted", "Apply by",
            "Why it fits", "Apply", "Status", "Notes", "Source", "Found on", "Key", "URL"]
WIDTHS = [62, 170, 115, 170, 300, 160, 100, 92, 100, 420, 80, 125, 220, 105, 130, 60, 60]
COL = {c: i for i, c in enumerate(JOB_COLS)}
# Columns added after a sheet may already exist: inserted in place so existing rows stay aligned.
ADDED_COLS = ["Apply by"]
STATUSES = ["New", "Shortlisted", "Applied", "Interview", "Offer", "Rejected", "Not interested"]


def L(c: str) -> str:
    """Column letter of a job-tab column, e.g. L("Status") → "L"."""
    return chr(ord("A") + COL[c])

ALL = f"'{T_ALL}'"


def _rgb(hexs: str) -> dict:
    h = hexs.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


NAVY, WHITE = _rgb("#1F2A44"), _rgb("#FFFFFF")
STATUS_COLORS = {
    "New": ("#F1F3F4", "#3C4043"), "Shortlisted": ("#FEF7E0", "#B06000"),
    "Applied": ("#E8F0FE", "#1967D2"), "Interview": ("#FFE0B2", "#E65100"),
    "Offer": ("#CEEAD6", "#137333"), "Rejected": ("#FCE8E6", "#C5221F"),
    "Not interested": ("#F1F3F4", "#9AA0A6"),
}


def sheet_key(raw: str) -> str:
    """Accept either the bare sheet id or a full Google Sheets URL."""
    raw = (raw or "").strip().strip('"').strip("'")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", raw)
    if m:
        return m.group(1)
    return raw.split("/")[0].split("?")[0].split("#")[0]


def esc(v):
    """Stop job titles like '+Growth' or '=Strategy' being parsed as formulas."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


class Sheet:
    def __init__(self, settings: dict):
        self.settings = settings
        self.buckets = config.buckets(settings, with_other=True)
        creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        self.gc = gspread.service_account_from_dict(creds, http_client=RetryingHTTPClient)
        key = sheet_key(os.environ["SHEET_ID"])
        try:
            self.sh = self.gc.open_by_key(key)
        except (gspread.exceptions.APIError, gspread.exceptions.SpreadsheetNotFound) as e:
            raise SystemExit(
                f"Could not open the Google Sheet (id starts '{key[:6]}…', {len(key)} chars).\n"
                f"Check that: (1) SHEET_ID is the id from the URL "
                f"docs.google.com/spreadsheets/d/<THIS PART>/edit, and (2) the sheet is shared as Editor "
                f"with the service account {creds.get('client_email', '?')}.\n"
                f"Also make sure the Google Sheets API and Google Drive API are enabled in the Cloud project.\n"
                f"Underlying error: {str(e)[:300]}") from None
        self.url = self.sh.url
        self._ensure_tabs()
        self._migrate_job_columns()

    # ───────────────────────── setup ─────────────────────────
    def _ensure_tabs(self):
        existing = {ws.title: ws for ws in self.sh.worksheets()}
        sizes = {T_ALL: (3000, len(JOB_COLS)), T_OTHERS: (3000, len(JOB_COLS)), T_NEW: (400, len(JOB_COLS)),
                 T_LOG: (1000, 6), T_COMP: (500, 5), T_KW: (500, 2), T_DASH: (60, 10), T_TRACK: (500, 9)}
        for t in TAB_ORDER:
            r, c = sizes[t]
            if t not in existing:
                existing[t] = self.sh.add_worksheet(title=t, rows=r, cols=c)
            elif existing[t].col_count < c and t not in (T_ALL, T_OTHERS, T_NEW):
                existing[t].add_cols(c - existing[t].col_count)   # job tabs grow via _migrate_job_columns
        for name in ("Sheet1", "Sheet 1"):
            if name in existing and len(existing) > len(TAB_ORDER):
                try:
                    self.sh.del_worksheet(existing.pop(name))
                except gspread.exceptions.APIError:
                    pass
        self.ws = existing
        self.sh.reorder_worksheets([existing[t] for t in TAB_ORDER])

    def _migrate_job_columns(self):
        """Insert columns added in later versions (e.g. "Apply by") into job tabs built with the old layout,
        so existing rows, statuses and notes stay in the right columns."""
        reqs = []
        for t in (T_ALL, T_OTHERS, T_NEW):
            hdr = self.ws[t].row_values(1)
            if not hdr or hdr == JOB_COLS:
                continue
            cols = [c for c in JOB_COLS if c not in ADDED_COLS or c in hdr]
            if hdr[:len(cols)] != cols:
                log.warning("Unexpected header in %s; leaving columns as they are", t)
                continue
            for c in ADDED_COLS:
                if c not in hdr:
                    i = cols.index(JOB_COLS[COL[c] - 1]) + 1
                    cols.insert(i, c)
                    reqs.append({"insertDimension": {"range": {"sheetId": self.ws[t].id, "dimension": "COLUMNS",
                                                               "startIndex": i, "endIndex": i + 1},
                                                     "inheritFromBefore": True}})
                    log.info("Added column %r to %s", c, t)
        if reqs:
            self.sh.batch_update({"requests": reqs})
            for t in (T_ALL, T_OTHERS, T_NEW):
                self.ws[t].update(values=[JOB_COLS], range_name="A1", value_input_option="RAW")

    def ws_id(self, t):
        return self.ws[t].id

    # ───────────────────────── styling ─────────────────────────
    def _header_reqs(self, t, ncols, widths=None):
        sid = self.ws_id(t)
        reqs = [
            {"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": ncols},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": NAVY, "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "textFormat": {"foregroundColor": WHITE, "bold": True, "fontSize": 10, "fontFamily": "Inter"}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"}},
            {"updateSheetProperties": {
                "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"}},
            {"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 34}, "fields": "pixelSize"}},
        ]
        for i, w in enumerate(widths or []):
            reqs.append({"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": w}, "fields": "pixelSize"}})
        return reqs

    def _clear_cf_reqs(self, titles):
        meta = self.sh.fetch_sheet_metadata({"fields": "sheets(properties(sheetId,title),conditionalFormats)"})
        reqs = []
        for s in meta["sheets"]:
            if s["properties"]["title"] in titles:
                for idx in reversed(range(len(s.get("conditionalFormats", [])))):
                    reqs.append({"deleteConditionalFormatRule": {"sheetId": s["properties"]["sheetId"], "index": idx}})
        return reqs

    def _job_tab_reqs(self, t, status_dropdown=True):
        sid = self.ws_id(t)

        def col(c):
            return {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": COL[c], "endColumnIndex": COL[c] + 1}

        body = {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(JOB_COLS)}
        reqs = self._header_reqs(t, len(JOB_COLS), WIDTHS)
        reqs += [
            {"repeatCell": {"range": body, "cell": {"userEnteredFormat": {
                "verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
                "textFormat": {"fontSize": 10, "fontFamily": "Inter"}}},
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat)"}},
            {"repeatCell": {"range": col("Score"), "cell": {"userEnteredFormat": {
                "horizontalAlignment": "CENTER", "numberFormat": {"type": "NUMBER", "pattern": "0.0"},
                "textFormat": {"bold": True, "fontSize": 11}}},
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat,textFormat)"}},
            {"repeatCell": {"range": col("Found on"), "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "DATE_TIME", "pattern": "dd mmm, hh:mm"}}},
                "fields": "userEnteredFormat.numberFormat"}},
            {"repeatCell": {"range": col("Apply by"), "cell": {"userEnteredFormat": {
                "horizontalAlignment": "CENTER", "numberFormat": {"type": "DATE", "pattern": "dd mmm yyyy"}}},
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}},
            {"repeatCell": {"range": col("Company"), "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold"}},
            {"repeatCell": {"range": col("Apply"), "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                            "fields": "userEnteredFormat.horizontalAlignment"}},
            {"repeatCell": {"range": col("Bucket"), "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                            "fields": "userEnteredFormat.horizontalAlignment"}},
            # hide Key + URL helper columns
            {"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": COL["Key"], "endIndex": COL["URL"] + 1},
                "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
            {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0,
                                                     "startColumnIndex": 0, "endColumnIndex": COL["Found on"] + 1}}}},
        ]
        # score colour scale
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [col("Score")],
            "gradientRule": {
                "minpoint": {"type": "NUMBER", "value": "3", "color": _rgb("#F4C7C3")},
                "midpoint": {"type": "NUMBER", "value": "6.5", "color": _rgb("#FCE8B2")},
                "maxpoint": {"type": "NUMBER", "value": "10", "color": _rgb("#57BB8A")}}}}})
        for b in self.buckets:
            label, bg, fg = b["label"], b["bg"], b["color"]
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [col("Bucket")],
                "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": label}]},
                                "format": {"backgroundColor": _rgb(bg), "textFormat": {"foregroundColor": _rgb(fg), "bold": True}}}}}})
        # Apply by: red when closing within 3 days, grey once passed
        a = f"${L('Apply by')}2"
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [col("Apply by")],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [
                {"userEnteredValue": f'=AND(ISNUMBER({a}),{a}>=TODAY(),{a}<=TODAY()+3)'}]},
                "format": {"backgroundColor": _rgb("#FCE8E6"), "textFormat": {"foregroundColor": _rgb("#B3261E"), "bold": True}}}}}})
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [col("Apply by")],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [
                {"userEnteredValue": f'=AND(ISNUMBER({a}),{a}<TODAY())'}]},
                "format": {"textFormat": {"foregroundColor": _rgb("#9AA0A6"), "strikethrough": True}}}}}})
        for label, (bg, fg) in STATUS_COLORS.items():
            fmt = {"backgroundColor": _rgb(bg), "textFormat": {"foregroundColor": _rgb(fg), "bold": label != "New"}}
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [col("Status")],
                "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": label}]}, "format": fmt}}}})
        # whole-row tints: high fit rows get a warm tint; rejected / not interested rows are greyed out
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": COL["Tag"], "endColumnIndex": COL["Tag"] + 1}],
            "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "🔥"}]},
                            "format": {"textFormat": {"bold": True, "foregroundColor": _rgb("#C2410C")}}}}}})
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": COL["Found on"] + 1}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [
                {"userEnteredValue": f'=OR(${L("Status")}2="Rejected",${L("Status")}2="Not interested")'}]},
                "format": {"textFormat": {"foregroundColor": _rgb("#9AA0A6"), "strikethrough": True}}}}}})
        reqs.append({"addBanding": {"bandedRange": {
            "range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": COL["Found on"] + 1},
            "rowProperties": {"headerColor": NAVY, "firstBandColor": WHITE, "secondBandColor": _rgb("#F8FAFD")}}}})
        if status_dropdown:
            reqs.append({"setDataValidation": {"range": col("Status"), "rule": {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": s} for s in STATUSES]},
                "strict": False, "showCustomUi": True}}})
        return reqs

    def style(self):
        """Idempotent: clears and re-applies all formatting on every run."""
        reqs = self._clear_cf_reqs({T_ALL, T_NEW, T_OTHERS, T_TRACK})
        meta = self.sh.fetch_sheet_metadata({"fields": "sheets(properties(sheetId,title),bandedRanges(bandedRangeId))"})
        for s in meta["sheets"]:
            for b in s.get("bandedRanges", []):
                reqs.append({"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}})
        reqs += self._job_tab_reqs(T_ALL)
        reqs += self._job_tab_reqs(T_NEW, status_dropdown=False)
        reqs += self._job_tab_reqs(T_OTHERS)
        reqs += self._header_reqs(T_TRACK, 9, [120, 62, 170, 300, 160, 100, 240, 360, 130])
        sid = self.ws_id(T_TRACK)
        reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 6},
                                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd mmm yyyy"}}},
                                    "fields": "userEnteredFormat.numberFormat"}})
        reqs += self._header_reqs(T_COMP, 5, [220, 120, 320, 220, 260])
        reqs += self._header_reqs(T_KW, 2, [160, 280])
        reqs += self._header_reqs(T_LOG, 6, [140, 380, 90, 90, 160, 520])
        reqs += self._dashboard_style_reqs()
        self.sh.batch_update({"requests": reqs})

    # ───────────────────────── config tabs ─────────────────────────
    def companies(self, seed: list[dict]) -> list[dict]:
        ws = self.ws[T_COMP]
        rows = ws.get_all_values()
        if len(rows) <= 1:
            values = [["Company", "Bucket", "ATS (optional)", "Tags (comma-separated)", "Notes"]]
            for c in seed:
                values.append([c["name"], c["bucket"], c.get("ats", ""), ", ".join(c.get("tags") or []), ""])
            ws.update(values=values, range_name="A1", value_input_option="RAW")
            self.sh.batch_update({"requests": [{"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 2},
                "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                    {"userEnteredValue": b} for b in [*config.bucket_keys(self.settings), "any"]]},
                    "strict": False, "showCustomUi": True}}}]})
            return seed
        out = []
        for r in rows[1:]:
            r = (r + [""] * 5)[:5]
            if not r[0].strip():
                continue
            out.append({"name": r[0].strip(),
                        "bucket": r[1].strip().lower() or self.settings.get("default_company_bucket", "any"),
                        "ats": r[2].strip(), "tags": [t.strip().lower() for t in r[3].split(",") if t.strip()]})
        return out

    def keywords(self, seed: dict) -> dict:
        ws = self.ws[T_KW]
        rows = ws.get_all_values()
        kinds = [("tier1", "🔥 Tier 1"), ("tier2", "⭐ Tier 2"), ("tier3", "✓ Tier 3"),
                 ("exclude_title", "⛔ Exclude"), ("deprioritise_title", "↓ Deprioritise")]
        label_to_kind = {lbl: k for k, lbl in kinds}
        if len(rows) <= 1:
            values = [["Type", "Keyword (matched on job title)"]]
            for k, lbl in kinds:
                lst = seed["priority"].get(k) if k.startswith("tier") else seed.get(k, [])
                values += [[lbl, kw] for kw in lst or []]
            ws.update(values=values, range_name="A1", value_input_option="RAW")
            self.sh.batch_update({"requests": [{"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
                "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": l} for _, l in kinds]},
                         "strict": True, "showCustomUi": True}}}]})
            return seed
        out = {"priority": {"tier1": [], "tier2": [], "tier3": []}, "exclude_title": [], "deprioritise_title": [],
               "exclude_experience_regex": seed.get("exclude_experience_regex", [])}
        for r in rows[1:]:
            if len(r) < 2 or not r[1].strip():
                continue
            k = label_to_kind.get(r[0].strip())
            if k is None:
                continue
            (out["priority"][k] if k.startswith("tier") else out[k]).append(r[1].strip())
        return out

    # ───────────────────────── jobs ─────────────────────────
    def existing_keys(self) -> set[str]:
        keys = set()
        for t in (T_ALL, T_OTHERS):
            keys |= set(self.ws[t].col_values(COL["Key"] + 1)[1:])
        return keys

    def job_row(self, j: Job, found_on: str, tag: str) -> list:
        return [
            round(j.score, 1), tag, config.bucket_label(self.settings, j.bucket), esc(j.company), esc(j.title),
            esc(j.location), esc(j.experience), j.posted, j.deadline, esc(j.fit_reason),
            f'=HYPERLINK("{j.url.replace(chr(34), "%22")}","Apply ↗")' if j.url else "",
            "New", "", j.source, found_on, j.key, j.url,
        ]

    def _ensure_header(self, t):
        ws = self.ws[t]
        if ws.row_values(1) != JOB_COLS:
            ws.update(values=[JOB_COLS], range_name="A1", value_input_option="RAW")

    def add_jobs(self, rows: list[list], low_rows: list[list]):
        for t in (T_ALL, T_OTHERS):
            self._ensure_header(t)
        if rows:
            self.ws[T_ALL].insert_rows(rows, row=2, value_input_option="USER_ENTERED")
        if low_rows:
            self.ws[T_OTHERS].insert_rows(low_rows, row=2, value_input_option="USER_ENTERED")

    def write_new(self, rows: list[list], run_label: str):
        ws = self.ws[T_NEW]
        ws.clear()
        ws.update(values=[JOB_COLS], range_name="A1", value_input_option="RAW")
        if rows:
            ws.update(values=rows, range_name="A2", value_input_option="USER_ENTERED")
        else:
            ws.update(values=[["", "", "", "", f"No new relevant roles in this run ({run_label})."]],
                      range_name="A2", value_input_option="RAW")
        ws.update_note(f"{L('Status')}1", "This tab is refreshed every run. Update Status in 📋 All Jobs so it is kept.")

    def write_tracker(self):
        ws = self.ws[T_TRACK]
        ws.clear()
        hdr = [["Status", "Score", "Company", "Role", "Location", "Apply by", "Notes", "Link", "Found on"]]
        cols = ["Status", "Score", "Company", "Role", "Location", "Apply by", "Notes", "URL", "Found on"]
        arr = ",".join(f"{ALL}!{L(c)}2:{L(c)}" for c in cols)
        st = f"{ALL}!{L('Status')}2:{L('Status')}"
        f = (f'=IFERROR(SORT(FILTER({{{arr}}},REGEXMATCH({st},"^(Shortlisted|Applied|Interview|Offer)$")),2,FALSE),'
             f'"Mark jobs as Shortlisted / Applied / Interview / Offer in 📋 All Jobs and they will appear here.")')
        ws.update(values=hdr + [[f]], range_name="A1", value_input_option="USER_ENTERED")

    def log_run(self, row: list):
        ws = self.ws[T_LOG]
        if ws.row_values(1) == []:
            ws.update(values=[["Run", "Fetched by source", "New jobs", "🔥 High fit", "AI tokens (in/out)", "Warnings"]],
                      range_name="A1", value_input_option="RAW")
        ws.insert_rows([row], row=2, value_input_option="USER_ENTERED")

    # ───────────────────────── dashboard ─────────────────────────
    def _dashboard_style_reqs(self):
        sid = self.ws_id(T_DASH)

        def rng(r0, r1, c0, c1):
            return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

        reqs = [
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True, "frozenRowCount": 0}},
                                       "fields": "gridProperties(hideGridlines,frozenRowCount)"}},
            {"repeatCell": {"range": rng(0, 60, 0, 10), "cell": {"userEnteredFormat": {
                "textFormat": {"fontFamily": "Inter", "fontSize": 10}, "verticalAlignment": "MIDDLE"}},
                "fields": "userEnteredFormat(textFormat,verticalAlignment)"}},
            {"repeatCell": {"range": rng(0, 1, 0, 10), "cell": {"userEnteredFormat": {
                "backgroundColor": NAVY, "textFormat": {"foregroundColor": WHITE, "bold": True, "fontSize": 18, "fontFamily": "Inter"}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
            {"repeatCell": {"range": rng(1, 2, 0, 10), "cell": {"userEnteredFormat": {
                "backgroundColor": NAVY, "textFormat": {"foregroundColor": _rgb("#AFC3E8"), "fontSize": 10, "fontFamily": "Inter"}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
            {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                                           "properties": {"pixelSize": 48}, "fields": "pixelSize"}},
            # KPI labels row 4, values row 5
            {"repeatCell": {"range": rng(3, 4, 0, 6), "cell": {"userEnteredFormat": {
                "backgroundColor": _rgb("#F1F4F9"), "horizontalAlignment": "CENTER",
                "textFormat": {"foregroundColor": _rgb("#5F6B7A"), "fontSize": 9, "bold": True}}},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"}},
            {"repeatCell": {"range": rng(4, 5, 0, 6), "cell": {"userEnteredFormat": {
                "backgroundColor": _rgb("#F1F4F9"), "horizontalAlignment": "CENTER",
                "textFormat": {"foregroundColor": NAVY, "fontSize": 24, "bold": True}}},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"}},
            {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 4, "endIndex": 5},
                                           "properties": {"pixelSize": 56}, "fields": "pixelSize"}},
            # section headers
            *[{"repeatCell": {"range": rng(r, r + 1, 0, 6), "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": NAVY},
                "borders": {"bottom": {"style": "SOLID_MEDIUM", "color": NAVY}}}},
                "fields": "userEnteredFormat(textFormat,borders)"}} for r in (6, 13)],
            *[{"repeatCell": {"range": rng(r, r + 1, 0, 6), "cell": {"userEnteredFormat": {
                "backgroundColor": _rgb("#E8EDF5"), "textFormat": {"bold": True}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"}} for r in (7, 14)],
            {"repeatCell": {"range": rng(15, 30, 5, 6), "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "DATE", "pattern": "dd mmm yyyy"}}}, "fields": "userEnteredFormat.numberFormat"}},
        ]
        for i, w in enumerate([150, 150, 300, 160, 150, 150, 120, 120, 120, 120]):
            reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                       "properties": {"pixelSize": w}, "fields": "pixelSize"}})
        return reqs

    def write_dashboard(self):
        ws = self.ws[T_DASH]
        ws.clear()
        a = ALL

        def c(name):
            return f"{a}!{L(name)}2:{L(name)}"

        score, status, bucket, found = c("Score"), c("Status"), c("Bucket"), c("Found on")
        kpi_labels = ["NEW TODAY", "NEW THIS WEEK", "🔥 HIGH-FIT OPEN", "APPLIED", "INTERVIEWS", "OFFERS"]
        kpis = [
            f'=COUNTIFS({found},">="&TODAY())',
            f'=COUNTIFS({found},">="&(TODAY()-7))',
            f'=COUNTIFS({score},">=8",{status},"New")',
            f'=COUNTIF({status},"Applied")+COUNTIF({status},"Interview")+COUNTIF({status},"Offer")',
            f'=COUNTIF({status},"Interview")',
            f'=COUNTIF({status},"Offer")',
        ]
        bucket_rows = []
        for b in config.buckets(self.settings):
            lb = b["label"]
            bucket_rows.append([
                lb,
                f'=COUNTIFS({bucket},"{lb}",{found},">="&(TODAY()-7))',
                f'=COUNTIFS({bucket},"{lb}",{score},">=8")',
                f'=COUNTIFS({bucket},"{lb}",{status},"Applied")',
                f'=COUNTIFS({bucket},"{lb}")',
            ])
        top_cols = ["Score", "Company", "Role", "Location", "Bucket", "Apply by", "URL"]
        arr = ",".join(c(x) for x in top_cols)
        top = (f'=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({{{arr}}},'
               f'{score}>=8,{status}="New"),1,FALSE),12,{len(top_cols)}),'
               f'"No open high-fit roles yet — check back after the next run.")')
        d = self.settings.get("display", {})
        tagline = d.get("tagline") or " · ".join(b["label"] for b in config.buckets(self.settings))
        avail = f'&"   |   {d["availability"]}"' if d.get("availability") else ""
        values = [
            [f"🎯 Job Radar · {d.get('name', '')}".rstrip(" ·")],
            [f'="{tagline}   |   Last updated: "&IFERROR(TEXT(MAX({found}),"dd mmm yyyy, hh:mm"),"—"){avail}'],
            [""],
            kpi_labels,
            kpis,
            [""],
            ["By bucket"],
            ["Bucket", "New (7 days)", "🔥 High fit (all)", "Applied", "Total tracked"],
            *bucket_rows,
        ]
        values += [[""]] * (13 - len(values))   # top-roles section starts on row 14 (styled in _dashboard_style_reqs)
        values += [
            ["🔥 Top open high-fit roles"],
            ["Score", "Company", "Role", "Location", "Bucket", "Apply by", "Link"],
            [top],
        ]
        ws.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
