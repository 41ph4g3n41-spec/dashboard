"""Optional Telegram + email alert delivery.

All env-driven and gracefully no-ops if not configured.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("alerts")


def _telegram_enabled() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def telegram_send(text: str, parse_mode: str | None = None) -> bool:
    """Send Telegram message. Plain text by default — Markdown breaks on
    common chars (_ * [ ] in tickers/URLs). Pass parse_mode='HTML' if needed."""
    if not _telegram_enabled():
        return False
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = {"chat_id": chat, "text": text[:4000],
               "disable_web_page_preview": "true"}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, data=payload, timeout=15)
        ok = r.status_code == 200
        if not ok:
            log.warning("telegram send %s: %s", r.status_code, r.text[:200])
        return ok
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False


def send_high_alert(update_row, analysis: dict) -> None:
    ticker = update_row["ticker"] or "—"
    head = update_row["headline"]
    src = update_row["source"]
    url = update_row["url"] or ""
    msg = (
        f"⚠️ HIGH URGENCY — {ticker}\n"
        f"{head}\n\n"
        f"Impact: {analysis['impact']}  "
        f"Thesis: {analysis['thesis_effect']}  "
        f"Action: {analysis['action']}\n"
        f"{analysis['reasoning']}\n\n"
        f"source: {src}\n{url}"
    )
    telegram_send(msg)


def _email_enabled() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EMAIL_TO"))


def email_send(subject: str, html: str) -> bool:
    if not _email_enabled():
        return False
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASS")
    to = os.getenv("EMAIL_TO")
    msg = MIMEMultipart("alternative")
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
        return True
    except Exception as e:
        log.warning("email send failed: %s", e)
        return False
