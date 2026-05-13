"""Shared fetcher utilities: requests session, alias matching, logging."""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from ..config import HTTP_HEADERS, SECTOR_KEYWORDS, all_aliases

log = logging.getLogger("fetchers")

_ALIASES = all_aliases()
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(_ALIASES.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    return s


def match_ticker(text: str) -> Optional[str]:
    """Return universe ticker if text mentions any company alias."""
    if not text:
        return None
    m = _ALIAS_RE.search(text)
    if m:
        return _ALIASES[m.group(1).lower()]
    return None


def matched_sectors(text: str) -> list[str]:
    """Return list of sector keys whose keywords appear in `text`."""
    if not text:
        return []
    low = text.lower()
    hits: list[str] = []
    for sector, kws in SECTOR_KEYWORDS.items():
        if any(kw.lower() in low for kw in kws):
            hits.append(sector)
    return hits
