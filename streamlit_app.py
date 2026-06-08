"""Streamlit Community Cloud entry point.

Streamlit Cloud looks for `streamlit_app.py` (or `app.py`) at the repo root.
This thin shim:
  1. ensures the `research_system` package is importable
  2. bridges Streamlit's `st.secrets` into os.environ
  3. auto-detects the LLM provider from whichever key the user set
  4. always renders the dashboard — never calls st.stop() so the user
     gets a usable page even if no API key is set yet (read-only mode)

Public URL after deploy: https://<your-app>.streamlit.app
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- Bridge Streamlit Cloud secrets into os.environ --------------------
_SECRET_KEYS = (
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "GEMINI_API_KEY", "GEMINI_MODEL",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "EMAIL_TO",
    "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "TURSO_SYNC_INTERVAL",
)
try:
    for k in _SECRET_KEYS:
        if k in st.secrets and not os.environ.get(k):
            os.environ[k] = str(st.secrets[k])
except Exception:
    # Local laptop run: st.secrets may not exist — fall back to .env
    pass

# ---- Auto-pick LLM provider from whichever key is present --------------
# Rule: if the user explicitly set LLM_PROVIDER, respect it. Otherwise
# pick whichever key they actually added. Default to "anthropic" only
# if both are absent (so the rest of the code has a deterministic mode).
_explicit = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
_has_anth = bool(os.environ.get("ANTHROPIC_API_KEY"))
_has_gem = bool(os.environ.get("GEMINI_API_KEY"))

if _explicit in ("anthropic", "gemini"):
    _provider = _explicit
elif _has_gem and not _has_anth:
    _provider = "gemini"
elif _has_anth and not _has_gem:
    _provider = "anthropic"
elif _has_anth and _has_gem:
    _provider = "anthropic"          # both → prefer the paid better one
else:
    _provider = "anthropic"          # nothing set; dashboard runs in read-only

os.environ["LLM_PROVIDER"] = _provider

# ---- Always render the dashboard. Show a banner if no key. ------------
_provider_ok = (
    (_provider == "anthropic" and _has_anth) or
    (_provider == "gemini" and _has_gem)
)
if not _provider_ok:
    st.warning(
        "⚠️ No LLM API key detected — dashboard is running in **read-only "
        "mode**. Fetchers + Live Feed + Universe + Thesis Cards work, but "
        "the analyser, morning brief, *Why Is X Moving*, and *Analyze* "
        "panels will fail until you add a key.\n\n"
        "Add one to **App ▸ Settings ▸ Secrets** (TOML):\n\n"
        "```toml\n"
        "# free option — https://aistudio.google.com/apikey\n"
        'GEMINI_API_KEY = "AIza..."\n'
        "```\n"
        "or:\n"
        "```toml\n"
        'ANTHROPIC_API_KEY = "sk-ant-..."\n'
        "```\n"
        "Then **Reboot app** from the top-right ⋮ menu."
    )

# ---- Hand off to the real dashboard module ------------------------------
from research_system import dashboard  # noqa: F401  (executes Streamlit page)
