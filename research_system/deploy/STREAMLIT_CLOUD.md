# Deploy the dashboard to Streamlit Community Cloud (free)

Streamlit Cloud gives you a free public URL hosting the dashboard. Open it
from your laptop **or** your phone browser — no SSH, no VPS.

> **What works on Streamlit Cloud:** Morning brief tab, Live feed, Thesis
> cards, Universe table, Govt tracker, Why-is-X-moving, Analyze (paste),
> Holdings editor, manual fetch buttons in the sidebar.
>
> **What does NOT work on Streamlit Cloud:** the always-on scheduler (the
> app sleeps after inactivity) and the Telegram bot (requires a long-lived
> process). For those, either:
>   - keep a VPS doing scheduler + bot (see DEPLOY.md), OR
>   - just click the sidebar fetch + analyse buttons whenever you open the
>     dashboard — it's still useful as a manual research console.

---

## 5-minute setup

### 1. Push the repo to your own GitHub

```bash
# from your laptop, in the repo
git remote add personal git@github.com:<your-github-handle>/dashboard.git
git push -u personal claude/indian-equity-research-system-ZGTuG
```

(Or fork the existing repo via the GitHub UI.)

### 2. Sign in to Streamlit Cloud

Go to **https://share.streamlit.io** and sign in with your GitHub account.

### 3. Create the app

Click **New app** → pick your repo → fill in:

| Field | Value |
|---|---|
| Repository | `<your-github>/dashboard` |
| Branch | `claude/indian-equity-research-system-ZGTuG` (or `main` if you merged) |
| Main file path | `streamlit_app.py` |
| App URL | pick anything, e.g. `india-research-monitor` |

Click **Advanced settings** → set **Python version** to **3.11** → **Deploy**.

### 4. Add your secrets

Once it boots (will fail with "ANTHROPIC_API_KEY not set" — expected), go to
**App ▸ Settings ▸ Secrets** in the Streamlit Cloud sidebar. Paste:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
ANTHROPIC_MODEL   = "claude-sonnet-4-5"

# optional — Upstox broker feed (read-only). Unlocks the
# "Portfolio (Upstox)" tab with your real demat holdings and P&L, and
# makes Upstox the price source ahead of Yahoo.
# Token: https://account.upstox.com/developer/apps
UPSTOX_ACCESS_TOKEN = ""

# optional — only if you also run the Telegram bot on a VPS and want
# the dashboard to push messages from sidebar buttons
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = ""
```

> Secrets live only in Streamlit's secrets store — never commit any of
> these to the repo. If a token is ever pasted somewhere public, rotate it
> at the provider immediately.

Save. The app auto-reboots.

### 5. Open it

Your URL is `https://<your-app-name>.streamlit.app`. Bookmark it on your
phone home screen — it's now a one-tap research console.

---

## Recommended hybrid setup (Streamlit Cloud + VPS + shared Turso DB)

This is the **best architecture**: phone-friendly free dashboard + always-on
fetchers + bot, all reading from one shared database.

| Component | Where | Why |
|---|---|---|
| Dashboard | **Streamlit Cloud** (free) | free public URL, phone-friendly |
| Scheduler | **VPS** (DEPLOY.md path A or B) | needs to run 24/7 |
| Telegram bot | **VPS** | needs long-lived process |
| Database | **Turso** hosted libSQL (free 5 GB) | shared between Cloud and VPS |

### Turso wiring (one-time, 3 minutes)

1. Go to **https://turso.tech** → sign in with GitHub → free plan.
2. Click **Create Database** → name it `research` → region `bom` (Mumbai) → Create.
3. On the database page click **Connect** → copy the **Database URL**
   (looks like `libsql://research-<your-org>.turso.io`).
4. Click **Generate Token** → copy the **Auth Token**.
5. Add both to your `.env` on the VPS:
   ```
   TURSO_DATABASE_URL=libsql://research-<your-org>.turso.io
   TURSO_AUTH_TOKEN=eyJhbGciOiJF...
   ```
6. Add the same two to your Streamlit Cloud secrets (App ▸ Settings ▸ Secrets):
   ```toml
   TURSO_DATABASE_URL  = "libsql://research-<your-org>.turso.io"
   TURSO_AUTH_TOKEN    = "eyJhbGciOiJF..."
   TURSO_SYNC_INTERVAL = "30"
   ```
7. Restart the VPS scheduler (`docker compose restart` or `systemctl restart`)
   and re-deploy Streamlit Cloud. Both now read/write the same DB.

### How it works under the hood

`libsql-experimental`'s **embedded replica mode** keeps a local SQLite file
on each side and syncs it to Turso every `TURSO_SYNC_INTERVAL` seconds. Reads
hit the local file (fast); writes go to local + queued for sync.

So the **VPS scheduler writes** → Turso → **Streamlit Cloud reads** within
~30 s. The Telegram bot also reads from the same shared state.

If `TURSO_DATABASE_URL` is unset anywhere, that node falls back to a
local-only SQLite file. No code changes needed.

---

## Notes on the free tier

- **Sleeps after ~7 days of no traffic.** Hit the URL once a week to keep alive.
- **1 GB RAM, 1 CPU.** Plenty for this dashboard.
- **Public.** Anyone with the URL can view it. If your theses or holdings
  contain anything sensitive, deploy privately on a VPS instead.
- **Ephemeral filesystem.** SQLite resets on every deploy / restart. Use
  Streamlit Cloud as a manual research console only — for persistent
  history, run the scheduler on a VPS.

---

## Local dev with Streamlit Cloud-style secrets

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, fill it
in, then run:

```bash
streamlit run streamlit_app.py
```

The shim reads `st.secrets` first, then falls back to `.env`. Either works.
