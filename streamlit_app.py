"""Streamlit Community Cloud entry point.

Streamlit Cloud looks for `streamlit_app.py` (or `app.py`) at the repo root.
This thin shim:
  1. ensures the `research_system` package is importable
  2. bridges Streamlit's `st.secrets` into os.environ so the rest of the
     code (which reads from os.environ via python-dotenv) Just Works
  3. runs the same dashboard module the local laptop version uses

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
# Set these in your Streamlit Cloud app: Settings → Secrets (TOML format)
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
    # Local laptop run: st.secrets may not exist — fall back to .env via python-dotenv
    pass

_provider = (os.environ.get("LLM_PROVIDER") or "anthropic").lower()
_provider_ok = (
    (_provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY")) or
    (_provider == "gemini" and os.environ.get("GEMINI_API_KEY"))
)
if not _provider_ok:
    st.error(
        f"No API key set for LLM_PROVIDER={_provider!r}.\n\n"
        "**On Streamlit Cloud** — App ▸ Settings ▸ Secrets, paste either:\n\n"
        "```toml\nLLM_PROVIDER = \"anthropic\"\n"
        "ANTHROPIC_API_KEY = \"sk-ant-...\"\n```\n\n"
        "or (free):\n\n"
        "```toml\nLLM_PROVIDER = \"gemini\"\n"
        "GEMINI_API_KEY = \"...\"  # https://aistudio.google.com/apikey\n```"
    )
    st.stop()

# ---- Hand off to the real dashboard module ------------------------------
from research_system import dashboard  # noqa: F401  (executes Streamlit page)
