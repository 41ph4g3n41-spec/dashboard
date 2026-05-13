"""PIB government press release fetcher.

PIB's RSS at ModId=6, Regid=3 returns the main English press-release feed.
We filter to entries that touch our sectors (consumer, pharma,
infrastructure, defense, fintech, media, insurance) or directly mention
a universe ticker.
"""

from __future__ import annotations

import logging

import feedparser

from ..db import insert_update
from .util import match_ticker, matched_sectors

log = logging.getLogger("fetchers.pib")

PIB_URL = "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"


def fetch_all() -> int:
    f = feedparser.parse(
        PIB_URL,
        request_headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36"
        },
    )
    inserted = 0
    for entry in f.entries:
        head = (entry.get("title") or "").strip()
        summary = (entry.get("summary") or entry.get("description") or "").strip()
        link = entry.get("link") or ""
        text = head + " " + summary
        ticker = match_ticker(text)
        sectors = matched_sectors(text)
        if not ticker and not sectors:
            continue
        body = summary
        if sectors:
            body = f"[sectors: {','.join(sectors)}] " + body
        new_id = insert_update(
            ticker=ticker,
            source="pib",
            type_="press_release",
            headline=head,
            body=body,
            url=link,
        )
        if new_id:
            inserted += 1
    log.info("PIB inserted: %d (of %d entries)", inserted, len(f.entries))
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = fetch_all()
    print(f"PIB inserted: {n}")
