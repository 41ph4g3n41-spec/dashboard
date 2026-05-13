# India Research Monitor

Fully-automated equity-research monitoring system for your portfolio + watchlist.
Runs locally, free data sources only, Claude Sonnet 4.5 for analysis.

## What's inside

| Layer | File |
|---|---|
| Universe + BSE codes | `config.py` |
| Investment theses | `theses.py` |
| SQLite + helpers | `db.py` |
| NSE corp announcements + results calendar | `fetchers/nse_fetcher.py` |
| BSE corp announcements + transcripts + annual reports | `fetchers/bse_fetcher.py` |
| RSS (Moneycontrol, ET, Mint, BS) | `fetchers/rss_fetcher.py` |
| PIB government press releases | `fetchers/pib_fetcher.py` |
| Yahoo Finance prices + macro | `fetchers/price_fetcher.py` |
| Claude analyzer + "why is X moving?" | `analyzer.py` |
| Morning brief (7:30 IST) | `morning_brief.py` |
| Telegram + email alerts | `alerts.py` |
| APScheduler cron | `scheduler.py` |
| Streamlit dashboard | `dashboard.py` |

## One-time setup

```bash
cd research_system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, put your ANTHROPIC_API_KEY in it
python -m research_system.db          # creates SQLite at data/research.db
```

## Daily use

Start the scheduler (keeps fetching + analyzing in the background):

```bash
python -m research_system.scheduler
```

In a second terminal, launch the dashboard:

```bash
streamlit run research_system/dashboard.py
# open http://localhost:8501
```

## Force a one-shot pull (without the scheduler)

```bash
python -m research_system.fetchers.rss_fetcher
python -m research_system.fetchers.pib_fetcher
python -m research_system.fetchers.nse_fetcher
python -m research_system.fetchers.bse_fetcher
python -m research_system.fetchers.price_fetcher
python -m research_system.analyzer          # drains unprocessed + analyses
python -m research_system.morning_brief     # builds + stores today's brief
```

Every dashboard sidebar button also runs these fetchers on demand.

## Telegram alerts (optional)

1. Talk to `@BotFather` on Telegram, create a bot, copy the token.
2. Start a chat with your bot, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Paste both into `.env`. High-urgency alerts + the morning brief will push
   to that chat automatically.

## Email morning brief (optional)

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO` in `.env`.
Gmail users: generate an app-password (with 2FA on) and use that.

## Where things live

- `data/research.db` — all updates, analyses, briefs, prices
- `logs/scheduler.log` — rotating log file
- `.env` — your secrets (gitignored)

## Editing the universe

Open `config.py`, edit `PORTFOLIO` / `WATCHLIST`. Open `theses.py`, edit thesis
text for each holding — that text gets injected into every Claude analysis call.
Restart the scheduler / dashboard.
