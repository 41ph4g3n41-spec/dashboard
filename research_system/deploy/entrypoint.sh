#!/usr/bin/env bash
# Multi-role entrypoint. Usage: <role> [extra args]
set -euo pipefail

ROLE="${1:-scheduler}"
shift || true

cd /app

case "$ROLE" in
  scheduler)
    exec python -m research_system.scheduler "$@"
    ;;
  telegram|bot)
    exec python -m research_system.telegram_bot "$@"
    ;;
  dashboard|streamlit|ui)
    exec streamlit run /app/research_system/dashboard.py \
        --server.address 0.0.0.0 \
        --server.port 8501 \
        --server.headless true \
        --browser.gatherUsageStats false "$@"
    ;;
  brief)
    exec python -m research_system.morning_brief "$@"
    ;;
  pull)
    exec python -c "
from research_system.fetchers import rss_fetcher, pib_fetcher, nse_fetcher, bse_fetcher, price_fetcher
from research_system.analyzer import run
print('rss=',  rss_fetcher.fetch_all())
print('pib=',  pib_fetcher.fetch_all())
print('nse=',  nse_fetcher.fetch_announcements(days=1))
print('bse=',  bse_fetcher.fetch_announcements(days=1))
print('px =',  price_fetcher.snapshot_universe())
print('an =',  run(batch=50))
"
    ;;
  test)
    exec python -m unittest research_system.tests.test_smoke -v
    ;;
  shell|bash)
    exec /bin/bash
    ;;
  *)
    # Anything else: treat as a literal command to run inside the image
    exec "$ROLE" "$@"
    ;;
esac
