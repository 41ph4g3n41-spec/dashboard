# India Research Monitor

Fully-automated equity-research monitoring for your portfolio + watchlist.
Runs locally, free data sources only, Claude Sonnet 4.5 for analysis,
Streamlit dashboard on your laptop, interactive Telegram bot for your phone.

## What's inside

| Layer | File |
|---|---|
| Universe + BSE codes + holdings overrides | `config.py` |
| Investment theses | `theses.py` |
| SQLite + helpers | `db.py` |
| NSE corp announcements + results calendar | `fetchers/nse_fetcher.py` |
| BSE corp announcements + transcripts + annual reports | `fetchers/bse_fetcher.py` |
| RSS (Moneycontrol, ET, Mint, BS) | `fetchers/rss_fetcher.py` |
| PIB government press releases | `fetchers/pib_fetcher.py` |
| Yahoo Finance prices + macro | `fetchers/price_fetcher.py` |
| Claude analyzer + "why is X moving?" | `analyzer.py` |
| Morning brief (7:30 IST) | `morning_brief.py` |
| Telegram + email alerts (outbound) | `alerts.py` |
| Interactive Telegram bot (inbound commands) | `telegram_bot.py` |
| APScheduler cron | `scheduler.py` |
| Streamlit dashboard (9 tabs) | `dashboard.py` |
| Offline test suite (23 tests) | `tests/test_smoke.py` |

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

## Three ways to run this

| Mode | Setup time | Cost | Always-on | Persistent history | Telegram bot |
|---|---|---|---|---|---|
| **Streamlit Cloud** (free) | 5 min | ₹0 | sleeps after 7d | no | no |
| **Local laptop** | 5 min | ₹0 | only when laptop awake | yes | yes |
| **$5 VPS** (Docker / systemd) | 15 min | ~₹500/mo | ✓ | yes | yes |

- **Free public phone/laptop URL** → see **[deploy/STREAMLIT_CLOUD.md](deploy/STREAMLIT_CLOUD.md)**
- **24/7 with scheduler + bot** → see **[deploy/DEPLOY.md](deploy/DEPLOY.md)**

## 24/7 cloud deploy

See **[deploy/DEPLOY.md](deploy/DEPLOY.md)** for the full guide.
One-liner on a fresh Ubuntu VPS:

```bash
git clone <your fork> && cd dashboard
bash research_system/deploy/install.sh
```

The script installs Docker, asks for your secrets, and starts three
always-on containers: scheduler, Telegram bot, dashboard. SSH-tunnel
the dashboard to your laptop (port 8501).

## Run on your laptop (3 terminals)

```bash
# 1) scheduler — fetches, dedupes, analyses every 5–30 min
python -m research_system.scheduler

# 2) dashboard — open http://localhost:8501
streamlit run research_system/dashboard.py

# 3) Telegram bot for your phone (optional but recommended)
python -m research_system.telegram_bot
```

## Force a one-shot pull (no scheduler)

```bash
python -m research_system.fetchers.rss_fetcher
python -m research_system.fetchers.pib_fetcher
python -m research_system.fetchers.nse_fetcher
python -m research_system.fetchers.bse_fetcher
python -m research_system.fetchers.price_fetcher
python -m research_system.analyzer
python -m research_system.morning_brief
```

Every fetcher is also one-click in the dashboard sidebar.

## Telegram setup (phone interface)

1. Talk to `@BotFather`, create a bot, copy the token.
2. Start a chat with your bot. Open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Add both to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...   # comma-separated if multiple chats
   ```
4. Run `python -m research_system.telegram_bot`.

Then text the bot:

| Command | What it does |
|---|---|
| `/help` | Command list |
| `/brief` | Today's morning brief |
| `/feed 15` | Last 15 analysed items |
| `/high` | Last 24h high-urgency items only |
| `/why HATSUN` | "Why is X moving today?" with Claude |
| `/thesis ETERNAL` | Investment thesis card |
| `/universe` | Portfolio + watchlist + last prices |
| `/price PAYTM` | Last OHLCV + % change |
| `/pull` | Force-run all fetchers + analyser |

The bot also receives **automatic high-urgency push alerts** (any high-urgency
item the scheduler analyses) plus the daily morning brief.

## Editing holdings without code

Two options:

1. **Dashboard "Holdings" tab** — fill the form, hit Save. Writes to
   `data/holdings_override.json`. Restart the scheduler & dashboard.
2. **Edit `config.py` directly** for permanent changes.

Edit `theses.py` to update your investment thesis per holding (this text is
injected into every Claude analysis).

## Email morning brief (optional)

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO` in `.env`.
Gmail: enable 2FA and create an app-password.

## Run the tests

```bash
PYTHONPATH=. python -m unittest research_system.tests.test_smoke -v
```

23 offline tests cover DB roundtrip, dedupe, time windows, alias matching,
sector matching, Claude prompt building, JSON extraction (incl. fenced /
multi-object cases), thesis-card schema, holdings-override flow, alerts
config, Telegram dispatcher, scheduler cron, and dashboard module load.

## Where things live

- `data/research.db` — all updates, analyses, briefs, prices
- `data/holdings_override.json` — dashboard-side holdings edits
- `logs/scheduler.log` — rotating log file
- `.env` — your secrets (gitignored)
