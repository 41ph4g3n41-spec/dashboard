"""BSE corporate announcements + annual reports + transcript fetcher.

BSE's AnnGetData API works with the right User-Agent and Referer.
Categories of interest (strCat): A=AGM, B=Board Meeting, C=Corp. Action,
R=Results, plus 'Company Update'. We pull all categories and filter
client-side on subject.

`AnnSubCategory` for transcripts: "Analysts/Institutional Investor Meet/Con. Call
Updates" usually contains transcripts.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Iterable

import requests

from ..config import UNIVERSE
from ..db import insert_update
from .util import make_session

log = logging.getLogger("fetchers.bse")

BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
BSE_PDF_PREFIX = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
BSE_ANNUAL_REPORT_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnualReport_New/w"


def _safe_get_json(session: requests.Session, url: str, params: dict,
                   tries: int = 3) -> dict | None:
    last = None
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            log.warning("BSE GET fail (%s) %s — retry", e, url)
            time.sleep(1.5 * (attempt + 1))
    log.error("BSE giving up %s: %s", url, last)
    return None


def fetch_announcements(days: int = 1) -> int:
    """Insert BSE announcements for universe tickers. Returns # new rows."""
    session = make_session()
    session.headers["Referer"] = "https://www.bseindia.com/"
    session.headers["Origin"] = "https://www.bseindia.com"

    inserted = 0
    today = datetime.now().strftime("%Y%m%d")
    prev = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    for ticker, meta in UNIVERSE.items():
        code = meta.get("bse_code")
        if not code:
            continue
        params = {
            "pageno": 1,
            "strCat": -1,
            "strPrevDate": prev,
            "strToDate": today,
            "strScrip": code,
            "strSearch": "P",
            "strType": "C",
        }
        data = _safe_get_json(session, BSE_ANN_URL, params)
        if not data:
            continue
        rows: Iterable[dict] = data.get("Table", []) if isinstance(data, dict) else []
        for r in rows:
            headline = (r.get("HEADLINE") or r.get("NEWSSUB") or "").strip()
            body = (r.get("MORE") or r.get("CATEGORYNAME") or "").strip()
            atth = r.get("ATTACHMENTNAME") or ""
            url = BSE_PDF_PREFIX + atth if atth else r.get("NSURL") or ""
            type_ = "announcement"
            sub = (r.get("SUBCATNAME") or "").lower()
            if "transcript" in headline.lower() or "transcript" in sub:
                type_ = "transcript"
            elif "annual report" in headline.lower():
                type_ = "annual_report"
            elif "results" in (r.get("CATEGORYNAME") or "").lower():
                type_ = "results"
            if not headline:
                continue
            new_id = insert_update(
                ticker=ticker,
                source="bse",
                type_=type_,
                headline=f"[{meta['nse']}] {headline}",
                body=body,
                url=url,
            )
            if new_id:
                inserted += 1
        time.sleep(0.3)

    log.info("BSE announcements: %d new rows", inserted)
    return inserted


def fetch_annual_reports() -> int:
    """Pull annual reports list from BSE for all universe tickers with bse_code."""
    session = make_session()
    session.headers["Referer"] = "https://www.bseindia.com/"
    inserted = 0
    for ticker, meta in UNIVERSE.items():
        code = meta.get("bse_code")
        if not code:
            continue
        data = _safe_get_json(session, BSE_ANNUAL_REPORT_URL, {"scripcode": code})
        if not data:
            continue
        rows = data.get("Table", []) if isinstance(data, dict) else []
        for r in rows:
            year = r.get("FY") or r.get("Year") or ""
            pdf = r.get("AttachLink") or r.get("AttachmentName") or ""
            if pdf and not pdf.startswith("http"):
                pdf = "https://www.bseindia.com/bseplus/AnnualReport/" + code + "/" + pdf
            headline = f"[{meta['nse']}] Annual Report {year}"
            new_id = insert_update(
                ticker=ticker,
                source="bse",
                type_="annual_report",
                headline=headline,
                body=(r.get("Description") or "").strip(),
                url=pdf,
            )
            if new_id:
                inserted += 1
        time.sleep(0.3)
    log.info("BSE annual reports: %d new rows", inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = fetch_announcements(days=3)
    print(f"BSE announcements inserted: {n}")
    n2 = fetch_annual_reports()
    print(f"BSE annual reports inserted: {n2}")
