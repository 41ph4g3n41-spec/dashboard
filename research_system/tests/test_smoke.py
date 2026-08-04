"""Offline smoke tests — run with:

    PYTHONPATH=. ./research_system/.venv/bin/python -m unittest \
        research_system.tests.test_smoke -v

No network calls. Verifies schema, dedupe, time windows, prompt builders,
JSON extraction, alias matcher, sector matcher, telegram dispatcher,
and dashboard module load.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# Silence streamlit "missing ScriptRunContext" noise during unit tests.
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")


def _fresh_db_env():
    """Return a temp DB path + monkey-patch the global DB_PATH."""
    tmp = Path(tempfile.mkdtemp(prefix="rs_test_"))
    db_file = tmp / "test.db"
    return tmp, db_file


class DBSchemaAndDedupeTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.db = _fresh_db_env()
        from research_system import db
        self._patch = mock.patch.object(db, "DB_PATH", self.db)
        self._patch.start()
        db.ensure_db()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_insert_and_dedupe(self):
        from research_system import db
        uid = db.insert_update(ticker="HATSUN", source="t", type_="news",
                               headline="H", body="b", url="u")
        self.assertIsNotNone(uid)
        dup = db.insert_update(ticker="HATSUN", source="t", type_="news",
                               headline="H", body="b", url="u")
        self.assertIsNone(dup)

    def test_recent_updates_time_window(self):
        from research_system import db
        # Insert 3 rows manually with controlled fetched_at
        old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        mid = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        new = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with db.connect() as cx:
            cx.execute("INSERT INTO updates(source,headline,dedupe_key,fetched_at) VALUES (?,?,?,?)",
                       ("t","old","k_old",old))
            cx.execute("INSERT INTO updates(source,headline,dedupe_key,fetched_at) VALUES (?,?,?,?)",
                       ("t","mid","k_mid",mid))
            cx.execute("INSERT INTO updates(source,headline,dedupe_key,fetched_at) VALUES (?,?,?,?)",
                       ("t","new","k_new",new))
        self.assertEqual(len(db.recent_updates(hours=1)), 1)
        self.assertEqual(len(db.recent_updates(hours=24)), 2)
        self.assertEqual(len(db.recent_updates(hours=24*7)), 3)

    def test_analysis_roundtrip(self):
        from research_system import db
        uid = db.insert_update(ticker="HATSUN", source="t", type_="news",
                               headline="H", body="b", url="u")
        aid = db.insert_analysis(update_id=uid, ticker="HATSUN",
                                 impact="positive", urgency="high",
                                 thesis_effect="strengthens", action="hold",
                                 reasoning="r", follow_ups=["q1","q2"])
        self.assertIsNotNone(aid)
        rows = db.recent_analyses(hours=24)
        self.assertEqual(len(rows), 1)
        fu = json.loads(rows[0]["follow_ups"])
        self.assertEqual(fu, ["q1","q2"])
        # feed_rows joins both
        feed = db.feed_rows(limit=10)
        self.assertEqual(feed[0]["impact"], "positive")
        # mark_processed
        self.assertEqual(len(db.unprocessed_updates()), 1)
        db.mark_processed(uid)
        self.assertEqual(len(db.unprocessed_updates()), 0)

    def test_prices_upsert(self):
        from research_system import db
        db.upsert_price("HATSUN", "2026-05-13", 100, 105, 99, 103, 1000)
        db.upsert_price("HATSUN", "2026-05-13", 100, 105, 99, 104, 1100)
        prices = db.latest_prices()
        self.assertEqual(prices["HATSUN"]["close"], 104)
        self.assertEqual(prices["HATSUN"]["volume"], 1100)


class ConfigTest(unittest.TestCase):
    def test_universe_sizes(self):
        from research_system.config import PORTFOLIO, WATCHLIST, UNIVERSE
        self.assertEqual(len(PORTFOLIO), 9)
        self.assertEqual(len(WATCHLIST), 9)
        self.assertEqual(len(UNIVERSE), 18)

    def test_aliases_resolve(self):
        from research_system.config import all_aliases
        al = all_aliases()
        self.assertEqual(al["zomato"], "ETERNAL")
        self.assertEqual(al["blinkit"], "ETERNAL")
        self.assertEqual(al["hatsun"], "HATSUN")
        self.assertEqual(al["paytm"], "PAYTM")
        self.assertEqual(al["indri"], "PICCADIL")

    def test_every_holding_has_yahoo(self):
        from research_system.config import UNIVERSE
        for tk, m in UNIVERSE.items():
            self.assertTrue(m.get("yahoo"), f"{tk} missing yahoo symbol")


class ThesesTest(unittest.TestCase):
    def test_all_portfolio_have_thesis(self):
        from research_system.config import PORTFOLIO
        from research_system.theses import THESES
        self.assertEqual(set(PORTFOLIO.keys()), set(THESES.keys()))

    def test_thesis_required_fields(self):
        from research_system.theses import THESES
        for tk, t in THESES.items():
            for k in ("name", "thesis", "watch", "breaks", "catalyst"):
                self.assertIn(k, t, f"{tk} missing {k}")
            self.assertIsInstance(t["watch"], list)
            self.assertIsInstance(t["breaks"], list)


class FetcherUtilTest(unittest.TestCase):
    def test_match_ticker(self):
        from research_system.fetchers.util import match_ticker
        self.assertEqual(match_ticker("Blinkit launches in Pune"), "ETERNAL")
        self.assertEqual(match_ticker("Paytm shares jump 5%"), "PAYTM")
        self.assertIsNone(match_ticker("Reliance announces capex"))

    def test_matched_sectors(self):
        from research_system.fetchers.util import matched_sectors
        s = matched_sectors("RBI tightens UPI norms")
        self.assertIn("fintech", s)
        s = matched_sectors("USFDA approves vaccine")
        self.assertIn("pharma", s)


class AnalyzerTest(unittest.TestCase):
    def test_prompt_includes_thesis(self):
        from research_system.analyzer import _build_user_prompt
        p = _build_user_prompt(ticker="HATSUN", source="nse",
                               headline="Hatsun Q4 results", body="profit up 30%")
        self.assertIn("Hatsun", p)
        self.assertIn("Investment thesis", p)
        self.assertIn("Q4 results", p)
        self.assertIn("JSON", p)

    def test_prompt_handles_unknown_ticker(self):
        from research_system.analyzer import _build_user_prompt
        p = _build_user_prompt(ticker=None, source="rss",
                               headline="Sectoral note on consumption", body="")
        self.assertIn("sector signal only", p)

    def test_json_extractor_handles_fences(self):
        from research_system.analyzer import _extract_json
        cases = [
            '{"impact":"positive","urgency":"low","thesis_effect":"unchanged","action":"hold","reasoning":"r","follow_ups":[]}',
            'Sure! Here:\n{"impact":"negative","urgency":"high","thesis_effect":"weakens","action":"trim","reasoning":"r","follow_ups":["q1"]}\nDone.',
            '```json\n{"impact":"neutral","urgency":"low","thesis_effect":"unchanged","action":"no_action","reasoning":"r","follow_ups":[]}\n```',
        ]
        for c in cases:
            d = _extract_json(c)
            self.assertIn("impact", d)
            self.assertIn("urgency", d)


class TelegramDispatcherTest(unittest.TestCase):
    def test_help_works(self):
        from research_system.telegram_bot import _handle
        out = _handle("/help")
        self.assertIn("/brief", out)

    def test_unknown_command(self):
        from research_system.telegram_bot import _handle
        self.assertIn("Unknown command", _handle("/nope"))

    def test_thesis_unknown_ticker(self):
        from research_system.telegram_bot import _handle
        self.assertIn("No thesis card", _handle("/thesis ZZZZ"))

    def test_thesis_known_ticker(self):
        from research_system.telegram_bot import _handle
        out = _handle("/thesis HATSUN")
        self.assertIn("Hatsun", out)


class AlertsTest(unittest.TestCase):
    def test_telegram_disabled_without_env(self):
        from research_system import alerts
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "",
                                          "TELEGRAM_CHAT_ID": ""}, clear=False):
            self.assertFalse(alerts._telegram_enabled())
            self.assertFalse(alerts.telegram_send("hi"))

    def test_email_disabled_without_env(self):
        from research_system import alerts
        keep = {k: os.environ.get(k) for k in
                ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EMAIL_TO")}
        for k in keep:
            os.environ.pop(k, None)
        try:
            self.assertFalse(alerts._email_enabled())
            self.assertFalse(alerts.email_send("s", "h"))
        finally:
            for k, v in keep.items():
                if v is not None:
                    os.environ[k] = v


class DashboardImportTest(unittest.TestCase):
    def test_dashboard_loads(self):
        """Just import; Streamlit warnings are fine here."""
        import importlib
        mod = importlib.import_module("research_system.dashboard")
        self.assertTrue(hasattr(mod, "TABS"))
        self.assertEqual(len(mod.TABS), 10)
        self.assertIn("Portfolio (Upstox)", mod.TABS)

    def test_inr_uses_indian_grouping(self):
        """Lakh/crore grouping, not the western 3-digit one."""
        import importlib
        _inr = importlib.import_module("research_system.dashboard")._inr
        self.assertEqual(_inr(0), "₹0.00")
        self.assertEqual(_inr(567.5), "₹567.50")
        self.assertEqual(_inr(1234.5), "₹1,234.50")
        self.assertEqual(_inr(100000), "₹1,00,000.00")
        self.assertEqual(_inr(1234567.89), "₹12,34,567.89")
        self.assertEqual(_inr(123456789.0), "₹12,34,56,789.00")
        self.assertEqual(_inr(-1234.5), "-₹1,234.50")
        self.assertEqual(_inr(None), "—")


class LibsqlBackendTest(unittest.TestCase):
    """Exercise the libsql (Turso embedded-replica) backend in local-only
    mode — same DB API surface, must dedupe + time-window correctly."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="libsql_test_"))
        self._patches: list[mock._patch] = []
        try:
            import libsql_experimental as libsql
        except Exception as e:
            self.skipTest(f"libsql-experimental not installed: {e}")

        from research_system import db as db_mod
        self.db = db_mod
        # Monkeypatch DB_PATH + _open_libsql + TURSO env
        self._patches += [
            mock.patch.object(db_mod, "DB_PATH", self.tmp / "test.db"),
            mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": "libsql://fake.test"}),
        ]
        def _local():
            raw = libsql.connect(str(self.tmp / "test.db"), isolation_level=None)
            return db_mod._LibsqlConn(raw)
        self._patches.append(mock.patch.object(db_mod, "_open_libsql", _local))
        for p in self._patches:
            p.start()
        db_mod.ensure_db()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_insert_dedupe_roundtrip(self):
        uid = self.db.insert_update(ticker="HATSUN", source="t", type_="news",
                                    headline="H", body="b", url="u")
        self.assertIsNotNone(uid)
        dup = self.db.insert_update(ticker="HATSUN", source="t", type_="news",
                                    headline="H", body="b", url="u")
        self.assertIsNone(dup, "libsql backend should dedupe on UNIQUE constraint")

    def test_time_window(self):
        new = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db.connect() as cx:
            cx.execute("INSERT INTO updates(source,headline,dedupe_key,fetched_at) VALUES (?,?,?,?)",
                       ("t","new","k_new",new))
            cx.execute("INSERT INTO updates(source,headline,dedupe_key,fetched_at) VALUES (?,?,?,?)",
                       ("t","old","k_old",old))
        self.assertEqual(len(self.db.recent_updates(hours=1)), 1)
        self.assertEqual(len(self.db.recent_updates(hours=24*30)), 2)

    def test_column_name_access(self):
        uid = self.db.insert_update(ticker="HATSUN", source="t", type_="news",
                                    headline="Q4 results", body="", url=None)
        self.db.insert_analysis(update_id=uid, ticker="HATSUN",
                                impact="positive", urgency="high",
                                thesis_effect="strengthens", action="hold",
                                reasoning="r", follow_ups=[])
        rows = self.db.feed_rows(limit=5)
        # Critical: dict-style column access must keep working
        self.assertEqual(rows[0]["headline"], "Q4 results")
        self.assertEqual(rows[0]["impact"], "positive")
        self.assertEqual(rows[0]["ticker"], "HATSUN")


class LLMProviderTest(unittest.TestCase):
    """Verify llm.complete dispatches to the right backend without making
    a real API call. We patch the underlying client factories."""

    def setUp(self):
        from research_system import llm
        self.llm = llm
        # Clear lru_caches so each test gets a fresh client
        llm._anthropic_client.cache_clear()
        llm._gemini_client.cache_clear()

    def test_default_is_anthropic(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "",
                                          "ANTHROPIC_API_KEY": "k"}, clear=False):
            self.assertEqual(self.llm.provider_info()["provider"], "anthropic")

    def test_gemini_when_set(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini",
                                          "GEMINI_API_KEY": "k"}, clear=False):
            info = self.llm.provider_info()
            self.assertEqual(info["provider"], "gemini")
            self.assertTrue(info["model"].startswith("gemini"))

    def test_anthropic_dispatch(self):
        fake = mock.MagicMock()
        fake.messages.create.return_value.content = [
            mock.MagicMock(text='{"impact":"positive","urgency":"low"}')
        ]
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "anthropic",
                                          "ANTHROPIC_API_KEY": "k"}, clear=False), \
             mock.patch.object(self.llm, "_anthropic_client", return_value=fake):
            out = self.llm.complete(system="s", prompt="p", max_tokens=100)
            self.assertIn("impact", out)
            fake.messages.create.assert_called_once()

    def test_gemini_dispatch(self):
        fake = mock.MagicMock()
        fake.models.generate_content.return_value.text = '{"impact":"neutral"}'
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini",
                                          "GEMINI_API_KEY": "k"}, clear=False), \
             mock.patch.object(self.llm, "_gemini_client", return_value=fake):
            out = self.llm.complete(system="s", prompt="p", max_tokens=100)
            self.assertIn("neutral", out)
            fake.models.generate_content.assert_called_once()

    def test_anthropic_missing_key_raises(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "anthropic",
                                          "ANTHROPIC_API_KEY": ""}, clear=False):
            self.llm._anthropic_client.cache_clear()
            with self.assertRaises(RuntimeError):
                self.llm.complete(system="s", prompt="p")

    def test_gemini_missing_key_raises(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini",
                                          "GEMINI_API_KEY": ""}, clear=False):
            self.llm._gemini_client.cache_clear()
            with self.assertRaises(RuntimeError):
                self.llm.complete(system="s", prompt="p")


class HoldingsOverrideTest(unittest.TestCase):
    """Holdings can be added/removed via JSON override at data/holdings_override.json."""

    def setUp(self):
        from research_system import config as cfg
        self._cfg = cfg
        # Save & clear any existing override
        self._backup = None
        if cfg.HOLDINGS_OVERRIDE_PATH.exists():
            self._backup = cfg.HOLDINGS_OVERRIDE_PATH.read_text()
            cfg.HOLDINGS_OVERRIDE_PATH.unlink()
        self._orig_p = dict(cfg.PORTFOLIO)
        self._orig_w = dict(cfg.WATCHLIST)

    def tearDown(self):
        # Restore
        try:
            self._cfg.HOLDINGS_OVERRIDE_PATH.unlink()
        except FileNotFoundError:
            pass
        if self._backup is not None:
            self._cfg.HOLDINGS_OVERRIDE_PATH.write_text(self._backup)
        self._cfg.PORTFOLIO.clear()
        self._cfg.PORTFOLIO.update(self._orig_p)
        self._cfg.WATCHLIST.clear()
        self._cfg.WATCHLIST.update(self._orig_w)

    def test_add_and_remove_holding_via_override_file(self):
        import importlib
        from research_system import config as cfg
        cfg.save_overrides(portfolio={
            "RELIANCE": {"name": "Reliance Industries",
                         "sector": "Energy",
                         "nse": "RELIANCE",
                         "bse_code": "500325",
                         "yahoo": "RELIANCE.NS",
                         "aliases": ["Reliance", "RIL"]}
        })
        importlib.reload(cfg)
        self.assertIn("RELIANCE", cfg.PORTFOLIO)
        self.assertIn("RELIANCE", cfg.UNIVERSE)
        # Remove
        cfg.save_overrides(remove=["RELIANCE"])
        importlib.reload(cfg)
        self.assertNotIn("RELIANCE", cfg.PORTFOLIO)


def _fake_jwt(**claims) -> str:
    """Build an unsigned JWT-shaped string for token_info() tests.

    Deliberately synthetic — no real credential belongs in a test file.
    """
    import base64 as _b64

    def seg(d):
        raw = json.dumps(d).encode()
        return _b64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'HS256'})}.{seg(claims)}.signature-not-checked"


class UpstoxTokenTest(unittest.TestCase):
    """Token plumbing: detection, JWT introspection, redaction."""

    def setUp(self):
        from research_system.fetchers import upstox_fetcher as ux
        self.ux = ux

    def test_not_configured_without_env(self):
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}, clear=False):
            self.assertFalse(self.ux.is_configured())
            self.assertIsNone(self.ux.access_token())
            self.assertEqual(self.ux.token_info(), {"configured": False})

    def test_blank_token_is_not_configured(self):
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "   "}, clear=False):
            self.assertFalse(self.ux.is_configured())

    def test_token_info_decodes_claims_and_expiry(self):
        future = int(time.time()) + 30 * 86400
        tok = _fake_jwt(sub="TESTUSER", iss="udapi-gateway-service",
                        exp=future, isPlusPlan=True)
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": tok}, clear=False):
            info = self.ux.token_info()
        self.assertTrue(info["configured"])
        self.assertTrue(info["readable"])
        self.assertEqual(info["user_id"], "TESTUSER")
        self.assertFalse(info["expired"])
        self.assertEqual(info["days_left"], 29)          # floor of 29.99…
        self.assertNotIn(tok, json.dumps(info), "token must not leak into info")

    def test_token_info_flags_expired(self):
        tok = _fake_jwt(sub="X", exp=int(time.time()) - 3600)
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": tok}, clear=False):
            info = self.ux.token_info()
        self.assertTrue(info["expired"])

    def test_token_info_survives_opaque_token(self):
        with mock.patch.dict(os.environ,
                             {"UPSTOX_ACCESS_TOKEN": "not-a-jwt"}, clear=False):
            info = self.ux.token_info()
        self.assertTrue(info["configured"])
        self.assertFalse(info["readable"])

    def test_redact_strips_token(self):
        with mock.patch.dict(os.environ,
                             {"UPSTOX_ACCESS_TOKEN": "SECRET123"}, clear=False):
            self.assertNotIn("SECRET123", self.ux._redact("bearer SECRET123 failed"))

    def test_get_without_token_raises_auth_error(self):
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""}, clear=False):
            with self.assertRaises(self.ux.UpstoxAuthError):
                self.ux._get("/user/profile")

    def test_401_becomes_auth_error(self):
        resp = mock.Mock(status_code=401, text="unauthorised")
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "t"}, clear=False), \
             mock.patch.object(self.ux.requests, "get", return_value=resp):
            with self.assertRaises(self.ux.UpstoxAuthError):
                self.ux._get("/user/profile")

    def test_error_status_payload_raises(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"status": "error",
                                  "errors": [{"message": "bad instrument"}]}
        with mock.patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "t"}, clear=False), \
             mock.patch.object(self.ux.requests, "get", return_value=resp):
            with self.assertRaisesRegex(self.ux.UpstoxError, "bad instrument"):
                self.ux._get("/market-quote/ltp")


class UpstoxPortfolioTest(unittest.TestCase):
    """Normalisation of holdings / positions / summary, all offline."""

    RAW_HOLDINGS = [
        {"trading_symbol": "HATSUN", "company_name": "Hatsun Agro Product",
         "isin": "INE473B01035", "exchange": "NSE",
         "instrument_token": "NSE_EQ|INE473B01035",
         "quantity": 10, "average_price": 900.0, "last_price": 1000.0,
         "pnl": 1000.0, "day_change": 5.0, "day_change_percentage": 0.5,
         "t1_quantity": 0, "product": "D"},
        # pnl deliberately missing -> must be derived, and a string qty
        {"trading_symbol": "PAYTM", "company_name": "One 97 Communications",
         "isin": "INE982J01020", "exchange": "NSE",
         "instrument_token": "NSE_EQ|INE982J01020",
         "quantity": "5", "average_price": "800", "last_price": "700",
         "day_change": -10.0, "day_change_percentage": -1.4},
    ]

    def setUp(self):
        from research_system.fetchers import upstox_fetcher as ux
        self.ux = ux

    def test_holdings_normalise_and_sort(self):
        with mock.patch.object(self.ux, "_get", return_value=self.RAW_HOLDINGS):
            hs = self.ux.holdings()
        self.assertEqual(len(hs), 2)
        # sorted by current value desc -> HATSUN (10k) before PAYTM (3.5k)
        self.assertEqual(hs[0]["symbol"], "HATSUN")
        self.assertEqual(hs[0]["invested"], 9000.0)
        self.assertEqual(hs[0]["current_value"], 10000.0)
        self.assertEqual(hs[0]["pnl"], 1000.0)
        self.assertAlmostEqual(hs[0]["pnl_pct"], 11.11, places=2)

    def test_missing_pnl_is_derived_and_strings_coerced(self):
        with mock.patch.object(self.ux, "_get", return_value=self.RAW_HOLDINGS):
            paytm = [h for h in self.ux.holdings() if h["symbol"] == "PAYTM"][0]
        self.assertEqual(paytm["quantity"], 5.0)
        self.assertEqual(paytm["invested"], 4000.0)
        self.assertEqual(paytm["current_value"], 3500.0)
        self.assertEqual(paytm["pnl"], -500.0)          # 5 * (700 - 800)
        self.assertAlmostEqual(paytm["pnl_pct"], -12.5, places=2)

    def test_portfolio_summary_aggregates(self):
        with mock.patch.object(self.ux, "_get", return_value=self.RAW_HOLDINGS):
            s = self.ux.portfolio_summary()
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["invested"], 13000.0)
        self.assertEqual(s["current_value"], 13500.0)
        self.assertEqual(s["pnl"], 500.0)

    def test_empty_holdings_summary_does_not_divide_by_zero(self):
        with mock.patch.object(self.ux, "_get", return_value=[]):
            s = self.ux.portfolio_summary()
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["pnl_pct"], 0.0)

    def test_suggestions_skip_names_already_tracked(self):
        with mock.patch.object(self.ux, "_get", return_value=self.RAW_HOLDINGS):
            sugg = self.ux.suggest_universe_overrides()
        # both HATSUN and PAYTM are already in config.PORTFOLIO
        self.assertEqual(sugg, {})

    def test_suggestions_include_untracked_name(self):
        raw = self.RAW_HOLDINGS + [{
            "trading_symbol": "RELIANCE", "company_name": "Reliance Industries",
            "isin": "INE002A01018", "exchange": "NSE",
            "instrument_token": "NSE_EQ|INE002A01018",
            "quantity": 1, "average_price": 100, "last_price": 110,
        }]
        with mock.patch.object(self.ux, "_get", return_value=raw):
            sugg = self.ux.suggest_universe_overrides()
        self.assertIn("RELIANCE", sugg)
        self.assertEqual(sugg["RELIANCE"]["nse"], "RELIANCE")
        self.assertEqual(sugg["RELIANCE"]["yahoo"], "RELIANCE.NS")
        self.assertEqual(sugg["RELIANCE"]["upstox_key"], "NSE_EQ|INE002A01018")


class UpstoxQuotesTest(unittest.TestCase):
    """Instrument resolution + quote mapping + price snapshot."""

    def setUp(self):
        from research_system.fetchers import upstox_fetcher as ux
        self.ux = ux
        self.tmp, self.db_file = _fresh_db_env()
        from research_system import db
        self.db = db
        self._patch = mock.patch.object(db, "DB_PATH", self.db_file)
        self._patch.start()
        db.ensure_db()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_instrument_key_prefers_explicit_override(self):
        from research_system import config as cfg
        meta = dict(cfg.UNIVERSE["HATSUN"], upstox_key="NSE_EQ|OVERRIDE")
        with mock.patch.dict(cfg.UNIVERSE, {"HATSUN": meta}):
            self.assertEqual(self.ux.instrument_key_for("HATSUN"),
                             "NSE_EQ|OVERRIDE")

    def test_instrument_key_from_isin(self):
        from research_system import config as cfg
        meta = dict(cfg.UNIVERSE["HATSUN"], isin="INE473B01035")
        with mock.patch.dict(cfg.UNIVERSE, {"HATSUN": meta}):
            self.assertEqual(self.ux.instrument_key_for("HATSUN"),
                             "NSE_EQ|INE473B01035")

    def test_instrument_key_falls_back_to_master_lookup(self):
        with mock.patch.object(self.ux, "instrument_map",
                               return_value={"HATSUN": "NSE_EQ|FROM_MASTER"}):
            self.assertEqual(self.ux.instrument_key_for("HATSUN"),
                             "NSE_EQ|FROM_MASTER")

    def test_unknown_ticker_resolves_to_none(self):
        self.assertIsNone(self.ux.instrument_key_for("NOSUCHTICKER"))

    def test_quote_map_uses_last_price_as_close(self):
        """Upstox's ohlc.close is the *previous* close intraday, so the
        current price must come from last_price."""
        raw = {"NSE_EQ:HATSUN": {
            "instrument_token": "NSE_EQ|X", "symbol": "HATSUN",
            "ohlc": {"open": 990, "high": 1010, "low": 985, "close": 950},
            "last_price": 1000, "volume": 12345,
            "timestamp": "2026-08-04T15:30:00+05:30",
        }}
        with mock.patch.object(self.ux, "universe_instrument_keys",
                               return_value={"HATSUN": "NSE_EQ|X"}), \
             mock.patch.object(self.ux, "quotes", return_value=raw):
            qm = self.ux.quote_map()
        self.assertEqual(qm["HATSUN"]["close"], 1000)
        self.assertEqual(qm["HATSUN"]["prev_close"], 950)
        self.assertAlmostEqual(qm["HATSUN"]["pct_change"], 5.26, places=2)
        self.assertEqual(qm["HATSUN"]["volume"], 12345)

    def test_snapshot_universe_writes_prices(self):
        qm = {"HATSUN": {"open": 990.0, "high": 1010.0, "low": 985.0,
                         "close": 1000.0, "prev_close": 950.0,
                         "volume": 12345, "timestamp": "2026-08-04T15:30:00+05:30"}}
        with mock.patch.object(self.ux, "quote_map", return_value=qm):
            rows = self.ux.snapshot_universe()
        self.assertEqual(rows, 1)
        prices = self.db.latest_prices()
        self.assertEqual(prices["HATSUN"]["close"], 1000.0)
        self.assertEqual(prices["HATSUN"]["asof_date"], "2026-08-04")

    def test_instrument_map_returns_empty_on_download_failure(self):
        with mock.patch.object(self.ux, "_load_instrument_cache", return_value=None), \
             mock.patch.object(self.ux.requests, "get",
                               side_effect=Exception("network down")):
            self.assertEqual(self.ux.instrument_map(force=True), {})


class PriceSourceFallbackTest(unittest.TestCase):
    """price_fetcher must prefer Upstox when configured and degrade to
    yfinance quietly when it isn't, or when the token is dead."""

    def setUp(self):
        from research_system.fetchers import price_fetcher as pf
        from research_system.fetchers import upstox_fetcher as ux
        self.pf, self.ux = pf, ux

    def test_uses_yfinance_when_upstox_unconfigured(self):
        with mock.patch.object(self.ux, "is_configured", return_value=False), \
             mock.patch.object(self.pf, "_snapshot_universe_yf",
                               return_value=7) as yf_call:
            self.assertEqual(self.pf.snapshot_universe(), 7)
        yf_call.assert_called_once()

    def test_prefers_upstox_when_configured(self):
        with mock.patch.object(self.ux, "is_configured", return_value=True), \
             mock.patch.object(self.ux, "snapshot_universe", return_value=18), \
             mock.patch.object(self.pf, "_snapshot_universe_yf",
                               return_value=7) as yf_call:
            self.assertEqual(self.pf.snapshot_universe(), 18)
        yf_call.assert_not_called()

    def test_falls_back_when_token_rejected(self):
        with mock.patch.object(self.ux, "is_configured", return_value=True), \
             mock.patch.object(self.ux, "snapshot_universe",
                               side_effect=self.ux.UpstoxAuthError("expired")), \
             mock.patch.object(self.pf, "_snapshot_universe_yf",
                               return_value=7) as yf_call:
            self.assertEqual(self.pf.snapshot_universe(), 7)
        yf_call.assert_called_once()

    def test_falls_back_when_upstox_returns_nothing(self):
        with mock.patch.object(self.ux, "is_configured", return_value=True), \
             mock.patch.object(self.ux, "snapshot_universe", return_value=0), \
             mock.patch.object(self.pf, "_snapshot_universe_yf",
                               return_value=7) as yf_call:
            self.assertEqual(self.pf.snapshot_universe(), 7)
        yf_call.assert_called_once()

    def test_intraday_move_prefers_upstox(self):
        move = {"ticker": "HATSUN", "source": "upstox", "close": 1000}
        with mock.patch.object(self.ux, "is_configured", return_value=True), \
             mock.patch.object(self.ux, "intraday_move", return_value=move), \
             mock.patch.object(self.pf, "_intraday_move_yf") as yf_call:
            self.assertEqual(self.pf.intraday_move("HATSUN")["source"], "upstox")
        yf_call.assert_not_called()

    def test_intraday_move_falls_back_on_error(self):
        with mock.patch.object(self.ux, "is_configured", return_value=True), \
             mock.patch.object(self.ux, "intraday_move",
                               side_effect=self.ux.UpstoxError("boom")), \
             mock.patch.object(self.pf, "_intraday_move_yf",
                               return_value={"source": "yfinance"}) as yf_call:
            self.assertEqual(self.pf.intraday_move("HATSUN")["source"], "yfinance")
        yf_call.assert_called_once()


class NoCommittedSecretsTest(unittest.TestCase):
    """Guard-rail: example/config files must never carry a real token."""

    def test_examples_have_empty_upstox_token(self):
        from research_system.config import ROOT
        repo = ROOT.parent
        for rel in (".env.example", ".streamlit/secrets.toml.example"):
            path = (ROOT / rel) if rel.startswith(".env") else (repo / rel)
            text = path.read_text()
            self.assertIn("UPSTOX_ACCESS_TOKEN", text,
                          f"{rel} should document the token")
            for line in text.splitlines():
                if line.strip().startswith("UPSTOX_ACCESS_TOKEN"):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    self.assertEqual(value, "",
                                     f"{rel} must not ship a real token")

    def test_no_jwt_literal_in_source(self):
        """A pasted Upstox JWT would start with this header segment."""
        from research_system.config import ROOT
        marker = "eyJ0eXAiOiJKV1Qi"
        for path in list(ROOT.rglob("*.py")) + list(ROOT.parent.glob("*.py")):
            if "tests" in path.parts:
                continue
            self.assertNotIn(marker, path.read_text(),
                             f"possible hard-coded JWT in {path}")


class SchedulerImportTest(unittest.TestCase):
    def test_scheduler_jobs_register(self):
        """Build the scheduler in BackgroundScheduler mode and inspect jobs."""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from research_system import scheduler as sm

        sched = BackgroundScheduler(timezone=sm.IST)
        # Re-add the same jobs (don't start scheduler) to verify cron strings
        sched.add_job(sm.job_fetch_market_hours,
                      CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/15"))
        sched.add_job(sm.job_fetch_news, CronTrigger(minute="*/30"))
        sched.add_job(sm.job_analyze, CronTrigger(minute="*/5"))
        sched.add_job(sm.job_morning_brief,
                      CronTrigger(day_of_week="mon-sat", hour=7, minute=30))
        # if cron expression invalid, add_job would have raised
        self.assertEqual(len(sched.get_jobs()), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
