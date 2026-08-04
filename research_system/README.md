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
| **Upstox live feed** — websocket V3 streamer | `fetchers/upstox_feed.py` |
| **Upstox** — protobuf decoder (no deps) | `fetchers/upstox_proto.py` |
| **Upstox** — REST quotes / option chain / instruments | `fetchers/upstox_rest.py` |
| **Upstox** — instrument-key resolution | `fetchers/upstox_universe.py` |
| **Global tape** — indices, futures, FX, commodities | `fetchers/global_feed.py` |
| **Live Markets page** | `live_markets.py` |
| Claude analyzer + "why is X moving?" | `analyzer.py` |
| Morning brief (7:30 IST) | `morning_brief.py` |
| Telegram + email alerts (outbound) | `alerts.py` |
| Interactive Telegram bot (inbound commands) | `telegram_bot.py` |
| APScheduler cron | `scheduler.py` |
| Streamlit dashboard (10 tabs) | `dashboard.py` |
| Offline test suite | `tests/test_smoke.py`, `tests/test_upstox.py` |

## Pick an LLM provider

Set `LLM_PROVIDER` in `.env` (or Streamlit secrets):

| Provider | Cost | Quality | Free tier | Get a key |
|---|---|---|---|---|
| `anthropic` (default) | pay-per-use | best | $5 trial credit | https://console.anthropic.com |
| `gemini` | **free** | excellent | 500 req/day, 250K tokens/min, no card | https://aistudio.google.com/apikey |

The whole system runs on either — analyzer, morning brief, "why is X moving",
ad-hoc analyze panel. Switch any time, no code changes.

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

## Four ways to run this

| Mode | Setup time | Cost | Always-on | History | Telegram bot |
|---|---|---|---|---|---|
| **Streamlit Cloud only** | 5 min | ₹0 | sleeps after 7d | ephemeral | no |
| **Local laptop only** | 5 min | ₹0 | only when awake | yes | yes |
| **$5 VPS only** | 15 min | ~₹500/mo | ✓ | yes | yes |
| **VPS + Cloud + Turso** ⭐ | 20 min | ~₹500/mo | ✓ | yes | yes |

The starred row is the **recommended setup**: VPS runs scheduler + Telegram
bot 24/7, Streamlit Cloud hosts the public dashboard, both share a free
Turso database. Phone-friendly, no SSH tunnel needed for the dashboard.

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

## Live market data (Upstox)

The **Live Markets** tab streams real-time Indian market data over the
[Upstox Market Data Feed V3](https://upstox.com/developer/api-documentation/websocket)
websocket, and pairs it with a global tape from Yahoo Finance.

| Board | Source | Latency |
|---|---|---|
| NSE / BSE indices | Upstox websocket | real time |
| Cash equities (your portfolio + watchlist) | Upstox websocket | real time |
| Index & stock futures (NSE), commodities (MCX) | Upstox websocket | real time |
| Option chains with greeks, IV, OI | Upstox websocket | real time |
| US / Europe / Asia indices, US futures, FX, metals, energy, rates, crypto | Yahoo | futures & FX ~live, cash indices ~15 min delayed |

Upstox is an Indian broker and its instrument master carries **no non-Indian
instruments** — that is why the global board is Yahoo-sourced. MCX commodity
futures (crude, gold, silver, copper) are the closest live proxy for
international benchmarks that trades on an Indian exchange.

### Setup

```bash
# .env  (local)                 |  Streamlit Cloud: Settings ▸ Secrets
UPSTOX_ACCESS_TOKEN=...         |  UPSTOX_ACCESS_TOKEN = "..."
```

Generate a token via the OAuth flow at
<https://account.upstox.com/developer/apps>.

> **Check your token's lifetime — there are two kinds.** A standard access
> token expires at **03:30 IST the next day**. An *extended* token (JWT claim
> `isExtended: true`, offered on paid plans for read-only market data) lasts
> about a **year**, also expiring at 03:30 IST. Decode the JWT's `exp` claim to
> see which you hold. Either way an expired token fails loudly with an auth
> error rather than serving stale prices — nothing is cached across expiry.

Verify a token from the shell without opening the dashboard:

```bash
python -m research_system.fetchers.upstox_feed                       # Nifty 50 + Bank
python -m research_system.fetchers.upstox_feed --mode ltpc --seconds 10 \
    "NSE_INDEX|Nifty 50" "NSE_EQ|INE002A01018"
```

### Subscription modes

Each instrument is subscribed in exactly one mode; the caps are Upstox's and
are enforced client-side so an over-subscription fails clearly.

| Mode | Cap | Payload |
|---|---|---|
| `ltpc` | 5,000 | last price, last traded qty/time, prev close |
| `full` | 2,000 | + 5-level depth, day & intraday OHLC, OI, IV, ATP |
| `option_greeks` | 3,000 | + best bid/ask, delta/gamma/theta/vega/rho, IV |
| `full_d30` | 50 | as `full` with 30-level market depth |

### Notes on the implementation

* **No protobuf dependency.** The V3 feed is protobuf-encoded; rather than
  pull in `protobuf` (native extension) and `upstox-python-sdk`, the frozen
  `MarketDataFeedV3` schema is decoded by `fetchers/upstox_proto.py` in pure
  Python. It was fuzzed against the official generated parser over 400
  randomised frames with exact agreement, and `tests/test_upstox.py` pins that
  behaviour with real wire bytes.
* **TLS verification stays on.** The official SDK connects with
  `cert_reqs=CERT_NONE`; this client does not, since the handshake carries a
  bearer token.
* **The socket survives Streamlit reruns.** The feed is a process singleton
  and the price boards are `st.fragment`s, so reruns reattach rather than
  re-handshake.
* **The instruments master is cached** under `data/upstox/` for 20 hours
  (Upstox regenerates it daily). That directory is gitignored — the NSE file
  alone is tens of MB.

## Run the tests

```bash
PYTHONPATH=. python -m unittest discover -s research_system/tests -t .
```

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
