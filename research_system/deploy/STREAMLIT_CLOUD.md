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

# optional — only if you also run the Telegram bot on a VPS and want
# the dashboard to push messages from sidebar buttons
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = ""
```

Save. The app auto-reboots.

### 5. Open it

Your URL is `https://<your-app-name>.streamlit.app`. Bookmark it on your
phone home screen — it's now a one-tap research console.

---

## Recommended hybrid setup (best of both worlds)

| Component | Where | Why |
|---|---|---|
| Dashboard | **Streamlit Cloud** | free, public URL, phone-friendly |
| Scheduler | **VPS** (DEPLOY.md path A or B) | needs to run 24/7 |
| Telegram bot | **VPS** | needs long-lived process |
| SQLite DB | **VPS** | one source of truth |

In hybrid mode the Streamlit Cloud dashboard runs without a persistent DB
of its own — manual fetches work on-demand but won't see history from the
VPS. To share data, you'd need to swap SQLite for a hosted database
(Turso/libSQL, Supabase, Neon Postgres). Ask if you want that wired up.

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
