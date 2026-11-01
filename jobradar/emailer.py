"""HTML email digest via Gmail SMTP (app password)."""
from __future__ import annotations

import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .models import Job

log = logging.getLogger(__name__)

BUCKETS = [("lifesciences", "🧬 Life-Science / Pharma Consulting", "#1E7A3C"),
           ("consulting", "📈 Consulting / Strategy", "#1A4FA0"),
           ("startup", "🚀 Startups", "#6A2FB0"),
           ("none", "Other", "#666666")]


def _card(j: Job, tag: str) -> str:
    e = html.escape
    tag_html = f'<span style="font-size:12px;color:#C2410C;font-weight:600">{e(tag)}</span><br>' if tag else ""
    meta = " · ".join(x for x in [j.location, j.experience, f"posted {j.posted}" if j.posted else ""] if x)
    return f"""
    <tr><td style="padding:12px 14px;border-bottom:1px solid #EEF1F6">
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="52" valign="top"><div style="background:{'#57BB8A' if j.score >= 8 else '#FCE8B2'};color:#1F2A44;
             font-weight:700;font-size:16px;border-radius:8px;text-align:center;padding:8px 0;width:44px">{j.score:.1f}</div></td>
        <td valign="top" style="padding-left:10px;font-family:Arial,sans-serif">
          {tag_html}
          <a href="{e(j.url)}" style="font-size:15px;font-weight:700;color:#1F2A44;text-decoration:none">{e(j.title)}</a><br>
          <span style="font-size:13px;color:#333;font-weight:600">{e(j.company)}</span>
          <span style="font-size:12px;color:#777"> · {e(meta)}</span><br>
          <span style="font-size:13px;color:#444">{e(j.fit_reason)}</span>
        </td></tr></table>
    </td></tr>"""


def build(jobs_with_tags: list[tuple[Job, str]], total_new: int, sheet_url: str, run_label: str) -> tuple[str, str]:
    high = sum(1 for j, _ in jobs_with_tags if j.score >= 8)
    subject = f"🔥 {high} high-fit + {total_new} new roles | Job Radar, {run_label}" if high else \
              f"{total_new} new roles | Job Radar, {run_label}"
    sections = []
    for key, label, color in BUCKETS:
        items = [(j, t) for j, t in jobs_with_tags if (j.bucket or "none") == key]
        if not items:
            continue
        rows = "".join(_card(j, t) for j, t in items)
        sections.append(f"""
        <tr><td style="padding:18px 14px 6px;font-family:Arial,sans-serif;font-size:14px;font-weight:700;
             color:{color};border-bottom:2px solid {color}">{label} ({len(items)})</td></tr>{rows}""")
    body = "".join(sections) or """<tr><td style="padding:20px;font-family:Arial">No new roles above the
        threshold this run.</td></tr>"""
    html_doc = f"""<html><body style="margin:0;background:#F4F6FA">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6FA;padding:20px 0"><tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;overflow:hidden">
      <tr><td style="background:#1F2A44;padding:18px 20px;font-family:Arial,sans-serif;color:#fff">
        <div style="font-size:20px;font-weight:700">🎯 Job Radar</div>
        <div style="font-size:12px;color:#AFC3E8">{html.escape(run_label)} · {total_new} new relevant roles · {high} high fit</div>
      </td></tr>
      {body}
      <tr><td style="padding:18px;text-align:center;font-family:Arial">
        <a href="{html.escape(sheet_url)}" style="background:#1F2A44;color:#fff;padding:10px 18px;border-radius:6px;
           text-decoration:none;font-size:13px;font-weight:600">Open the tracker sheet →</a>
      </td></tr>
    </table></td></tr></table></body></html>"""
    return subject, html_doc


def send(subject: str, html_doc: str) -> bool:
    user, pwd = os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD")
    to = os.getenv("EMAIL_TO") or user
    if not (user and pwd):
        log.warning("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — skipping email")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"Job Radar <{user}>", to
    msg.attach(MIMEText(html_doc, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pwd)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    return True
