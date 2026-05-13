"""RSS news fetcher — Moneycontrol, ET Markets, Mint, Business Standard.

Filters to universe via alias matching. Sector keyword matches are
also recorded (ticker stays None for those, body carries sector tag).
"""

from __future__ import annotations

import logging
from typing import Iterable

import feedparser

from ..db import insert_update
from .util import match_ticker, matched_sectors

log = logging.getLogger("fetchers.rss")

FEEDS = [
    ("moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
    ("moneycontrol_markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("moneycontrol_results", "https://www.moneycontrol.com/rss/results.xml"),
    ("et_markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("mint_markets", "https://www.livemint.com/rss/markets"),
    ("bs_markets", "https://www.business-standard.com/rss/markets-106.rss"),
]


def fetch_all() -> int:
    inserted = 0
    for name, url in FEEDS:
        try:
            f = feedparser.parse(
                url,
                request_headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
            )
        except Exception as e:
            log.warning("RSS %s parse error: %s", name, e)
            continue
        for entry in f.entries:
            head = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            link = entry.get("link") or ""
            text = head + " " + summary
            ticker = match_ticker(text)
            sectors = matched_sectors(text)
            if not ticker and not sectors:
                continue
            type_ = "news"
            body = summary
            if not ticker and sectors:
                body = f"[sectors: {','.join(sectors)}] " + summary
            new_id = insert_update(
                ticker=ticker,
                source=f"rss:{name}",
                type_=type_,
                headline=head,
                body=body,
                url=link,
            )
            if new_id:
                inserted += 1
        log.info("RSS %s: parsed %d entries", name, len(f.entries))
    log.info("RSS total inserted: %d", inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = fetch_all()
    print(f"RSS inserted: {n}")
