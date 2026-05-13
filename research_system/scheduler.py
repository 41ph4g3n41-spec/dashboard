"""APScheduler that runs the pipeline.

Schedule (IST):
  - Fetchers every 15 min, Mon-Fri 09:00-16:00
  - RSS + PIB every 30 min, all day (news doesn't sleep)
  - Price snapshot at 16:15 daily (post-close)
  - Annual reports / results calendar refresh weekly (Sun 06:00)
  - Morning brief 07:30, Mon-Sat
  - Analyzer drain every 5 min (decoupled from fetchers)
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .analyzer import run as run_analyzer
from .config import LOG_DIR
from .db import ensure_db
from .fetchers import bse_fetcher, nse_fetcher, pib_fetcher, price_fetcher, rss_fetcher
from .morning_brief import build_and_store as build_brief

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("scheduler")


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # console
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # rotating file
    fh = RotatingFileHandler(
        LOG_DIR / "scheduler.log", maxBytes=5_000_000, backupCount=5
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


def job_fetch_market_hours() -> None:
    log.info(">> fetch (market hours): NSE+BSE announcements")
    try:
        nse_fetcher.fetch_announcements(days=1)
    except Exception as e:
        log.exception("nse fetch failed: %s", e)
    try:
        bse_fetcher.fetch_announcements(days=1)
    except Exception as e:
        log.exception("bse fetch failed: %s", e)


def job_fetch_news() -> None:
    log.info(">> fetch news: RSS + PIB")
    try:
        rss_fetcher.fetch_all()
    except Exception as e:
        log.exception("rss fetch failed: %s", e)
    try:
        pib_fetcher.fetch_all()
    except Exception as e:
        log.exception("pib fetch failed: %s", e)


def job_analyze() -> None:
    try:
        n = run_analyzer(batch=25)
        if n:
            log.info("<< analyser processed %d updates", n)
    except Exception as e:
        log.exception("analyser failed: %s", e)


def job_price_snapshot() -> None:
    log.info(">> price snapshot")
    try:
        price_fetcher.snapshot_universe()
    except Exception as e:
        log.exception("price snapshot failed: %s", e)


def job_weekly_refresh() -> None:
    log.info(">> weekly refresh: annual reports + results calendar")
    try:
        bse_fetcher.fetch_annual_reports()
    except Exception as e:
        log.exception("annual reports failed: %s", e)
    try:
        nse_fetcher.fetch_results_calendar()
    except Exception as e:
        log.exception("results calendar failed: %s", e)


def job_morning_brief() -> None:
    log.info(">> morning brief")
    try:
        build_brief()
    except Exception as e:
        log.exception("morning brief failed: %s", e)


def main() -> None:
    _configure_logging()
    ensure_db()
    sched = BlockingScheduler(timezone=IST)

    # Market-hours corp announcements: every 15 min, Mon-Fri 09:00-16:00 IST
    sched.add_job(
        job_fetch_market_hours,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/15"),
        id="fetch_corp", max_instances=1, coalesce=True,
    )
    sched.add_job(
        job_fetch_market_hours,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        id="fetch_corp_close", max_instances=1, coalesce=True,
    )

    # News (RSS+PIB): every 30 min, all day
    sched.add_job(
        job_fetch_news,
        CronTrigger(minute="*/30"),
        id="fetch_news", max_instances=1, coalesce=True,
    )

    # Analyzer drain: every 5 min
    sched.add_job(
        job_analyze, CronTrigger(minute="*/5"),
        id="analyze", max_instances=1, coalesce=True,
    )

    # Price snapshot: 16:15 IST Mon-Fri
    sched.add_job(
        job_price_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15),
        id="prices",
    )

    # Weekly refresh: Sunday 06:00 IST
    sched.add_job(
        job_weekly_refresh, CronTrigger(day_of_week="sun", hour=6, minute=0),
        id="weekly",
    )

    # Morning brief: 07:30 IST, Mon-Sat
    sched.add_job(
        job_morning_brief, CronTrigger(day_of_week="mon-sat", hour=7, minute=30),
        id="brief",
    )

    log.info("scheduler started. jobs:")
    for j in sched.get_jobs():
        log.info("  %s  next=%s", j.id, j.next_run_time)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopping")
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
