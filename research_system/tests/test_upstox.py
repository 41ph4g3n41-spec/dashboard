"""Offline tests for the Upstox live market data stack.

    PYTHONPATH=. python -m unittest research_system.tests.test_upstox -v

No network. The protobuf frames below are real wire bytes produced by the
official `upstox-python-sdk` 2.28.0 encoder, so they pin our dependency-free
decoder to the generated parser's behaviour. The decoder was additionally
fuzzed against that parser over 400 randomised frames (30-level depth,
negative int64 timestamps, empty map keys, every feed shape) with exact
agreement; these fixtures are the regression floor for that work.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from research_system.fetchers import upstox_feed, upstox_proto, upstox_rest, upstox_universe

# --- real FeedResponse frames, hex-encoded -----------------------------
LTPC_FRAME = bytes.fromhex(
    "080112310a124e53455f494e4445587c4e69667479203530121b0a190933333333"
    "1341d8401098c6b6a3873321333333330322d84018fbceb6a38733"
)
INDEX_FULL_FRAME = bytes.fromhex(
    "0801129f010a144e53455f494e4445587c4e696674792042616e6b128601128101"
    "127f0a1909cdcccccc4cf5ea4010b0beb6a38733216666666686d8ea4012620a2f"
    "0a023164110000000040dcea4019333333338300eb40210000000090ceea4029cd"
    "cccccc4cf5ea4038c0b4b99087330a2f0a024931110000000000f4ea4019000000"
    "00e0f5ea40210000000060f3ea4029cdcccccc4cf5ea4038e0b1b5a38733200118"
    "e8d5b6a38733"
)
EQUITY_FULL_FRAME = bytes.fromhex(
    "08011294020a134e53455f45517c494e453030324130313031381"
    "2fc0112f7010af4010a1b096666666666629640"
    "10c8b6b6a387331837210000000000309640127d0a17086411000000000062964018c8"
    "0121cdcccccccc6296400a17086511cdcccccccc61964018c90121000000000063964"
    "00a170866119a9999999961964018ca01213333333333639640"
    "0a170867116666666666619640"
    "18cb012167666666666396400a17086811333333333361964018cc01219a999999996"
    "3964022360a340a02316411000000000044964019000000000080964021cdcccccccc"
    "34964029666666666662964030c8b9c60138c0b4b990873329cdcccccccc5c9640"
    "30c8b9c6014900000000004cfd40510000000000edf740200118d0ddb6a38733"
)
GREEKS_FRAME = bytes.fromhex(
    "08011289010a0c4e53455f464f7c343638333312791a750a140966666666668e60"
    "40184b219a99999999b95d40121808ee0511cdcccccccc8c604018e50821333333"
    "33339360401a2d0982734694f606e13f11d7a3703d0ad720c019745e6397a8de3a"
    "3f21cdcccccccccc294029e17a14ae47e1084020c7b9702900000080df17504131"
    "05c58f31772dc13f200218b8e5b6a38733"
)
MARKET_INFO_FRAME = bytes.fromhex(
    "080218a0edb6a3873322240a0a0a064d43585f464f10000a0a0a064e53455f464f"
    "10020a0a0a064e53455f45511002"
)
MULTI_FRAME = bytes.fromhex(
    "12250a0d4d43585f464f7c34323631393512140a12090000000000feb740210000"
    "000000d4b740122a0a124e53455f494e4445587c4e696674792035301214"
    "0a1209333333331341d84021333333330322d84012280a104253455f494e444558"
    "7c53454e53455812140a12090000000028d5f340219a99999941c0f3401888f5b6"
    "a38733"
)


class ProtoDecodeTest(unittest.TestCase):
    def test_ltpc_frame(self):
        d = upstox_proto.decode_feed_response(LTPC_FRAME)
        self.assertEqual(d["type"], "live_feed")
        self.assertEqual(d["currentTs"], 1754300000123)
        feed = d["feeds"]["NSE_INDEX|Nifty 50"]
        self.assertAlmostEqual(feed["ltpc"]["ltp"], 24836.3, places=4)
        self.assertAlmostEqual(feed["ltpc"]["cp"], 24712.05, places=4)
        self.assertEqual(feed["ltpc"]["ltt"], 1754299999000)
        # requestMode ltpc == 0, the proto3 default, so it is omitted.
        self.assertNotIn("requestMode", feed)
        # ltq was 0 -> omitted, same as the generated parser's JSON mapping.
        self.assertNotIn("ltq", feed["ltpc"])

    def test_index_full_frame_has_both_ohlc_intervals(self):
        d = upstox_proto.decode_feed_response(INDEX_FULL_FRAME)
        idx = d["feeds"]["NSE_INDEX|Nifty Bank"]["fullFeed"]["indexFF"]
        self.assertAlmostEqual(idx["ltpc"]["ltp"], 55210.4, places=3)
        bars = idx["marketOHLC"]["ohlc"]
        self.assertEqual([b["interval"] for b in bars], ["1d", "I1"])
        self.assertAlmostEqual(bars[0]["high"], 55300.1, places=3)
        self.assertEqual(d["feeds"]["NSE_INDEX|Nifty Bank"]["requestMode"], "full_d5")

    def test_equity_full_frame_depth_and_volume(self):
        d = upstox_proto.decode_feed_response(EQUITY_FULL_FRAME)
        m = d["feeds"]["NSE_EQ|INE002A01018"]["fullFeed"]["marketFF"]
        self.assertAlmostEqual(m["ltpc"]["ltp"], 1432.6, places=3)
        depth = m["marketLevel"]["bidAskQuote"]
        self.assertEqual(len(depth), 5)
        self.assertAlmostEqual(depth[0]["bidP"], 1432.5, places=3)
        self.assertEqual(depth[0]["bidQ"], 100)
        self.assertEqual(depth[4]["askQ"], 204)
        self.assertEqual(m["vtt"], 3251400)
        self.assertAlmostEqual(m["atp"], 1431.2, places=3)
        self.assertEqual(m["marketOHLC"]["ohlc"][0]["vol"], 3251400)

    def test_greeks_frame(self):
        d = upstox_proto.decode_feed_response(GREEKS_FRAME)
        g = d["feeds"]["NSE_FO|46833"]["firstLevelWithGreeks"]
        self.assertAlmostEqual(g["optionGreeks"]["delta"], 0.5321, places=6)
        self.assertAlmostEqual(g["optionGreeks"]["theta"], -8.42, places=6)
        self.assertAlmostEqual(g["iv"], 0.1342, places=6)
        self.assertEqual(g["vtt"], 1842375)
        self.assertAlmostEqual(g["oi"], 4218750.0, places=1)
        self.assertEqual(g["firstDepth"]["bidQ"], 750)

    def test_market_info_frame(self):
        d = upstox_proto.decode_feed_response(MARKET_INFO_FRAME)
        self.assertEqual(d["type"], "market_info")
        seg = d["marketInfo"]["segmentStatus"]
        self.assertEqual(seg["NSE_EQ"], "NORMAL_OPEN")
        self.assertEqual(seg["NSE_FO"], "NORMAL_OPEN")
        # PRE_OPEN_START is ordinal 0 — the map entry must survive the
        # proto3 default-omission rule rather than vanishing.
        self.assertEqual(seg["MCX_FO"], "PRE_OPEN_START")

    def test_multi_instrument_frame(self):
        d = upstox_proto.decode_feed_response(MULTI_FRAME)
        self.assertNotIn("type", d)  # initial_feed == 0, omitted
        self.assertEqual(len(d["feeds"]), 3)
        self.assertAlmostEqual(
            d["feeds"]["BSE_INDEX|SENSEX"]["ltpc"]["ltp"], 81234.5, places=3
        )

    def test_unknown_fields_are_skipped(self):
        """A newer server schema must not break decoding."""
        def tag(field_no: int, wire_type: int) -> bytes:
            key, out = (field_no << 3) | wire_type, bytearray()
            while True:
                b = key & 0x7F
                key >>= 7
                out.append(b | (0x80 if key else 0))
                if not key:
                    return bytes(out)

        # One unknown field of every wire type appended to a valid frame.
        extra = (
            LTPC_FRAME
            + tag(99, 0) + b"\x01"                        # varint
            + tag(100, 1) + b"\x00" * 8                   # 64-bit
            + tag(101, 2) + b"\x03abc"                    # length-delimited
            + tag(102, 5) + b"\x00" * 4                   # 32-bit
        )
        d = upstox_proto.decode_feed_response(extra)
        self.assertIn("NSE_INDEX|Nifty 50", d["feeds"])
        self.assertEqual(d["currentTs"], 1754300000123)

    def test_bad_input_raises_decode_error(self):
        with self.assertRaises(upstox_proto.DecodeError):
            upstox_proto.decode_feed_response("not bytes")  # type: ignore[arg-type]
        with self.assertRaises(upstox_proto.DecodeError):
            upstox_proto.decode_feed_response(b"\x0a\xff")  # truncated string

    def test_empty_frame_decodes_to_empty_dict(self):
        self.assertEqual(upstox_proto.decode_feed_response(b""), {})


class NormaliseFeedTest(unittest.TestCase):
    """All four feed shapes must collapse onto the same tick keys."""

    def _tick(self, frame: bytes, key: str) -> dict:
        d = upstox_proto.decode_feed_response(frame)
        return upstox_feed.normalise_feed(key, d["feeds"][key])

    def test_ltpc_shape(self):
        t = self._tick(LTPC_FRAME, "NSE_INDEX|Nifty 50")
        self.assertAlmostEqual(t["ltp"], 24836.3, places=3)
        self.assertAlmostEqual(t["prev_close"], 24712.05, places=3)
        self.assertAlmostEqual(t["change"], 124.25, places=2)
        self.assertAlmostEqual(t["pct_change"], 0.5028, places=3)

    def test_index_full_shape_lifts_day_bar(self):
        t = self._tick(INDEX_FULL_FRAME, "NSE_INDEX|Nifty Bank")
        self.assertAlmostEqual(t["ltp"], 55210.4, places=3)
        self.assertAlmostEqual(t["open"], 55010.0, places=3)
        self.assertAlmostEqual(t["high"], 55300.1, places=3)
        self.assertAlmostEqual(t["low"], 54900.5, places=3)
        self.assertIn("I1", t["ohlc"])  # intraday bar retained alongside 1d

    def test_equity_full_shape_lifts_depth(self):
        t = self._tick(EQUITY_FULL_FRAME, "NSE_EQ|INE002A01018")
        self.assertAlmostEqual(t["bid"], 1432.5, places=3)
        self.assertAlmostEqual(t["ask"], 1432.7, places=3)
        self.assertEqual(t["bid_qty"], 100)
        self.assertEqual(len(t["depth"]), 5)
        self.assertEqual(t["volume"], 3251400)
        self.assertEqual(t["vtt"], 3251400)

    def test_greeks_shape(self):
        t = self._tick(GREEKS_FRAME, "NSE_FO|46833")
        self.assertAlmostEqual(t["ltp"], 132.45, places=3)
        self.assertAlmostEqual(t["greeks"]["delta"], 0.5321, places=6)
        self.assertAlmostEqual(t["iv"], 0.1342, places=6)
        self.assertAlmostEqual(t["bid"], 132.4, places=3)
        self.assertEqual(t["mode"], "option_greeks")

    def test_missing_close_leaves_change_absent(self):
        t = upstox_feed.normalise_feed("X", {"ltpc": {"ltp": 10.0}})
        self.assertEqual(t["ltp"], 10.0)
        self.assertNotIn("pct_change", t)


class MarketFeedStoreTest(unittest.TestCase):
    """Tick bookkeeping, exercised without ever opening a socket."""

    def setUp(self):
        self.feed = upstox_feed.MarketFeed()

    def test_on_message_populates_store(self):
        self.feed._on_message(None, MULTI_FRAME)
        snap = self.feed.snapshot()
        self.assertEqual(len(snap), 3)
        self.assertAlmostEqual(snap["MCX_FO|426195"]["ltp"], 6142.0, places=2)
        self.assertEqual(self.feed.status()["messages"], 1)

    def test_partial_frames_merge_not_replace(self):
        """A later quote-only tick must not wipe depth from an earlier full tick."""
        self.feed._on_message(None, EQUITY_FULL_FRAME)
        self.assertIn("depth", self.feed.get("NSE_EQ|INE002A01018"))

        thin = {"feeds": {"NSE_EQ|INE002A01018": {"ltpc": {"ltp": 1440.0, "cp": 1420.0}}}}
        with mock.patch.object(upstox_proto, "decode_feed_response", return_value=thin), \
             mock.patch.object(upstox_feed, "decode_feed_response", return_value=thin):
            self.feed._on_message(None, b"\x00")

        t = self.feed.get("NSE_EQ|INE002A01018")
        self.assertAlmostEqual(t["ltp"], 1440.0, places=3)
        self.assertIn("depth", t, "depth from the earlier full frame was lost")
        self.assertEqual(t["volume"], 3251400)

    def test_market_info_updates_segment_status(self):
        self.feed._on_message(None, MARKET_INFO_FRAME)
        self.assertEqual(self.feed.segment_status()["NSE_EQ"], "NORMAL_OPEN")

    def test_undecodable_frame_is_swallowed(self):
        self.feed._on_message(None, b"\x0a\xff")  # truncated, must not raise
        self.assertEqual(self.feed.status()["messages"], 0)

    def test_listeners_receive_batches(self):
        seen: list[dict] = []
        self.feed.on_ticks(seen.append)
        self.feed._on_message(None, MULTI_FRAME)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 3)

    def test_listener_exception_does_not_break_feed(self):
        def boom(_):
            raise RuntimeError("listener bug")

        self.feed.on_ticks(boom)
        self.feed._on_message(None, MULTI_FRAME)  # must not propagate
        self.assertEqual(len(self.feed.snapshot()), 3)

    def test_store_never_holds_none_values(self):
        """Downstream formatters assume a present key has a usable value."""
        self.feed._on_message(None, MULTI_FRAME)
        for key, tick in self.feed.snapshot().items():
            for field, value in tick.items():
                self.assertIsNotNone(value, f"{key}.{field} is None")

    def test_snapshot_is_a_copy(self):
        self.feed._on_message(None, LTPC_FRAME)
        snap = self.feed.snapshot()
        snap["NSE_INDEX|Nifty 50"]["ltp"] = 1.0
        self.assertNotEqual(self.feed.get("NSE_INDEX|Nifty 50")["ltp"], 1.0)


class SubscriptionTest(unittest.TestCase):
    def setUp(self):
        self.feed = upstox_feed.MarketFeed()

    def test_subscribe_tracks_mode(self):
        self.feed.subscribe(["A", "B"], upstox_feed.MODE_FULL)
        self.assertEqual(self.feed.subscriptions(),
                         {"A": "full", "B": "full"})

    def test_subscribe_dedupes_and_ignores_blanks(self):
        self.feed.subscribe(["A", "A", "", "B"], upstox_feed.MODE_LTPC)
        self.assertEqual(set(self.feed.subscriptions()), {"A", "B"})

    def test_change_mode_moves_key_between_modes(self):
        self.feed.subscribe(["A"], upstox_feed.MODE_LTPC)
        self.feed.change_mode(["A"], upstox_feed.MODE_GREEKS)
        self.assertEqual(self.feed.subscriptions()["A"], "option_greeks")

    def test_unsubscribe_drops_key_and_tick(self):
        self.feed._on_message(None, LTPC_FRAME)
        key = "NSE_INDEX|Nifty 50"
        self.feed.subscribe([key], upstox_feed.MODE_LTPC)
        self.feed.unsubscribe([key])
        self.assertEqual(self.feed.subscriptions(), {})
        self.assertIsNone(self.feed.get(key))

    def test_mode_limit_enforced_client_side(self):
        limit = upstox_feed.MODE_LIMITS[upstox_feed.MODE_D30]
        self.feed.subscribe([f"K{i}" for i in range(limit)], upstox_feed.MODE_D30)
        with self.assertRaises(ValueError) as ctx:
            self.feed.subscribe(["one-too-many"], upstox_feed.MODE_D30)
        self.assertIn("50", str(ctx.exception))

    def test_limit_counts_only_the_target_mode(self):
        """Keys held in other modes must not consume the new mode's budget."""
        self.feed.subscribe([f"L{i}" for i in range(60)], upstox_feed.MODE_LTPC)
        self.feed.subscribe(["D1", "D2"], upstox_feed.MODE_D30)  # must not raise
        self.assertEqual(self.feed.subscriptions()["D1"], "full_d30")

    def test_resubscribing_same_key_does_not_double_count(self):
        limit = upstox_feed.MODE_LIMITS[upstox_feed.MODE_D30]
        keys = [f"K{i}" for i in range(limit)]
        self.feed.subscribe(keys, upstox_feed.MODE_D30)
        self.feed.subscribe(keys, upstox_feed.MODE_D30)  # idempotent, must not raise
        self.assertEqual(len(self.feed.subscriptions()), limit)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.feed.subscribe(["A"], "turbo")

    def test_start_without_token_raises(self):
        with mock.patch.object(upstox_feed, "access_token", return_value=None):
            with self.assertRaises(upstox_feed.FeedNotAvailable):
                self.feed.start()


class RestClientTest(unittest.TestCase):
    def test_missing_token_raises_auth_error(self):
        with mock.patch.object(upstox_rest, "access_token", return_value=None):
            self.assertFalse(upstox_rest.is_configured())
            with self.assertRaises(upstox_rest.UpstoxAuthError):
                upstox_rest._headers()

    def test_empty_key_list_short_circuits(self):
        """No network call should happen for an empty universe."""
        with mock.patch.object(upstox_rest, "_get", side_effect=AssertionError):
            self.assertEqual(upstox_rest.ltp([]), {})
            self.assertEqual(upstox_rest.ohlc([]), {})
            self.assertEqual(upstox_rest.full_quote([]), {})
            self.assertEqual(upstox_rest.option_greeks([]), {})

    def test_long_key_lists_are_chunked_and_merged(self):
        keys = [f"NSE_EQ|K{i}" for i in range(1100)]
        calls: list[int] = []

        def fake_get(path, params=None):
            n = len(params["instrument_key"].split(","))
            calls.append(n)
            return {"data": {f"S{len(calls)}_{i}": {"last_price": i} for i in range(n)}}

        with mock.patch.object(upstox_rest, "_get", side_effect=fake_get):
            out = upstox_rest.ltp(keys)

        self.assertEqual(calls, [500, 500, 100])
        self.assertEqual(len(out), 1100)

    def test_ohlc_passes_interval_through(self):
        seen = {}

        def fake_get(path, params=None):
            seen.update(params)
            return {"data": {}}

        with mock.patch.object(upstox_rest, "_get", side_effect=fake_get):
            upstox_rest.ohlc(["NSE_INDEX|Nifty 50"], interval="I1")
        self.assertEqual(seen["interval"], "I1")

    def test_filter_instruments(self):
        rows = [
            {"segment": "NSE_EQ", "instrument_type": "EQ",
             "trading_symbol": "HATSUN", "name": "Hatsun Agro",
             "instrument_key": "NSE_EQ|INE473B01035"},
            {"segment": "NSE_FO", "instrument_type": "FUTIDX",
             "trading_symbol": "NIFTY25AUGFUT", "name": "NIFTY",
             "instrument_key": "NSE_FO|1"},
            {"segment": "NSE_FO", "instrument_type": "CE",
             "trading_symbol": "NIFTY25AUG24800CE", "name": "NIFTY",
             "instrument_key": "NSE_FO|2"},
        ]
        eq = upstox_rest.filter_instruments(rows, segments=["NSE_EQ"])
        self.assertEqual(len(eq), 1)
        futs = upstox_rest.filter_instruments(rows, instrument_types=["FUTIDX"])
        self.assertEqual(futs[0]["trading_symbol"], "NIFTY25AUGFUT")
        named = upstox_rest.filter_instruments(rows, name_contains="hatsun")
        self.assertEqual(len(named), 1)

    def test_index_by_symbol(self):
        rows = [{"trading_symbol": "HATSUN", "instrument_key": "NSE_EQ|X"},
                {"trading_symbol": None, "instrument_key": "NSE_EQ|Y"}]
        self.assertEqual(upstox_rest.index_by_symbol(rows), {"HATSUN": "NSE_EQ|X"})

    def test_unknown_exchange_rejected(self):
        with self.assertRaises(ValueError):
            upstox_rest.download_instruments("NASDAQ")


class UniverseTest(unittest.TestCase):
    def test_expiry_parses_epoch_millis_and_iso(self):
        self.assertEqual(
            upstox_universe._expiry_date({"expiry": 1756233000000}),
            date.fromtimestamp(1756233000),
        )
        self.assertEqual(
            upstox_universe._expiry_date({"expiry": "2026-08-28"}),
            date(2026, 8, 28),
        )
        self.assertIsNone(upstox_universe._expiry_date({}))
        self.assertIsNone(upstox_universe._expiry_date({"expiry": "garbage"}))

    def test_headline_indices_fall_back_without_master(self):
        with mock.patch.object(upstox_universe, "_load", return_value=[]):
            idx = upstox_universe.headline_indices()
        self.assertEqual(idx["Nifty 50"], "NSE_INDEX|Nifty 50")
        self.assertEqual(idx["Sensex"], "BSE_INDEX|SENSEX")

    def test_option_strikes_centres_on_spot(self):
        rows = []
        for strike in range(24000, 25100, 100):
            for opt in ("CE", "PE"):
                rows.append({
                    "segment": "NSE_FO", "instrument_type": opt, "name": "NIFTY",
                    "asset_symbol": "NIFTY", "strike_price": strike,
                    "expiry": "2026-08-27",
                    "trading_symbol": f"NIFTY{strike}{opt}",
                    "instrument_key": f"NSE_FO|{strike}{opt}",
                })
        with mock.patch.object(upstox_universe, "_load", return_value=rows):
            got = upstox_universe.option_strikes(
                "NIFTY", "2026-08-27", spot=24550.0, around=2
            )
        strikes = sorted({r["strike_price"] for r in got})
        # ATM rounds to 24500; two strikes either side.
        self.assertEqual(strikes, [24300, 24400, 24500, 24600, 24700])
        self.assertEqual(len(got), 10)  # CE + PE per strike

    def test_expired_contracts_filtered_out(self):
        rows = [
            {"segment": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
             "asset_symbol": "NIFTY", "expiry": "2020-01-30",
             "trading_symbol": "OLD", "instrument_key": "NSE_FO|old"},
            {"segment": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
             "asset_symbol": "NIFTY", "expiry": "2099-01-30",
             "trading_symbol": "NEW", "instrument_key": "NSE_FO|new"},
        ]
        with mock.patch.object(upstox_universe, "_load", return_value=rows):
            futs = upstox_universe.futures("NSE", ["NIFTY"])
        self.assertEqual([f["trading_symbol"] for f in futs], ["NEW"])

    def test_near_month_picks_one_per_underlying(self):
        rows = [
            {"segment": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
             "asset_symbol": "NIFTY", "expiry": "2099-02-27",
             "trading_symbol": "NIFTY-FAR", "instrument_key": "NSE_FO|far"},
            {"segment": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
             "asset_symbol": "NIFTY", "expiry": "2099-01-30",
             "trading_symbol": "NIFTY-NEAR", "instrument_key": "NSE_FO|near"},
            {"segment": "NSE_FO", "instrument_type": "FUTIDX", "name": "BANKNIFTY",
             "asset_symbol": "BANKNIFTY", "expiry": "2099-01-30",
             "trading_symbol": "BANK-NEAR", "instrument_key": "NSE_FO|bank"},
        ]
        with mock.patch.object(upstox_universe, "_load", return_value=rows):
            got = upstox_universe.near_month_futures("NSE", ["NIFTY", "BANKNIFTY"])
        self.assertEqual({r["trading_symbol"] for r in got},
                         {"NIFTY-NEAR", "BANK-NEAR"})

    def test_keys_and_labels(self):
        rows = [{"instrument_key": "NSE_FO|1", "trading_symbol": "NIFTYFUT"},
                {"instrument_key": "NSE_FO|2"}]
        self.assertEqual(upstox_universe.keys_of(rows), ["NSE_FO|1", "NSE_FO|2"])
        self.assertEqual(upstox_universe.label_map(rows),
                         {"NSE_FO|1": "NIFTYFUT", "NSE_FO|2": "NSE_FO|2"})

    def test_spot_key_bridges_contract_root_to_index_name(self):
        """The F&O root and the index's own name differ — see UNDERLYING_SPOT_KEY."""
        self.assertEqual(upstox_universe.spot_key("NIFTY"), "NSE_INDEX|Nifty 50")
        self.assertEqual(upstox_universe.spot_key("banknifty"), "NSE_INDEX|Nifty Bank")
        self.assertIsNone(upstox_universe.spot_key("CRUDEOIL"))

    def test_every_index_underlying_maps_to_a_known_index(self):
        for root, key in upstox_universe.UNDERLYING_SPOT_KEY.items():
            self.assertIn(key, set(upstox_universe.FALLBACK_INDICES.values()), root)


class LiveMarketsRenderTest(unittest.TestCase):
    """The page must render end to end without a live socket or network.

    Streamlit in bare mode executes the script body, so this exercises every
    sub-tab: tiles, tables, option-chain centring and the global board.
    """

    FUT = [{"segment": "NSE_FO", "instrument_type": "FUTIDX", "name": "NIFTY",
            "asset_symbol": "NIFTY", "expiry": "2099-01-30",
            "trading_symbol": "NIFTY99JANFUT", "instrument_key": "NSE_FO|1"}]
    OPT = [{"segment": "NSE_FO", "instrument_type": o, "name": "NIFTY",
            "asset_symbol": "NIFTY", "expiry": "2099-01-30", "strike_price": s,
            "trading_symbol": f"N{s}{o}", "instrument_key": f"NSE_FO|{s}{o}"}
           for s in range(24000, 25000, 100) for o in ("CE", "PE")]

    def test_render_with_ticks(self):
        from research_system.fetchers import global_feed

        feed = upstox_feed.MarketFeed()
        feed._on_message(None, MULTI_FRAME)
        feed._on_message(None, EQUITY_FULL_FRAME)
        feed._on_message(None, GREEKS_FRAME)

        rows = [{"symbol": "^GSPC", "name": "S&P 500", "group": "US Indices",
                 "delayed": True, "last": 5500.2, "prev_close": 5480.0,
                 "change": 20.2, "pct_change": 0.37, "day_high": None,
                 "day_low": None, "asof": "x"}]

        with mock.patch.object(upstox_rest, "is_configured", return_value=True), \
             mock.patch.object(upstox_feed, "get_feed", return_value=feed), \
             mock.patch.object(feed, "start"), \
             mock.patch.object(feed, "wait_until_connected", return_value=False), \
             mock.patch.object(upstox_universe, "_load",
                               return_value=self.FUT + self.OPT), \
             mock.patch.object(upstox_universe, "portfolio_keys",
                               return_value={"ETERNAL": "NSE_EQ|INE002A01018"}), \
             mock.patch.object(global_feed, "snapshot", return_value=rows):
            from research_system import live_markets

            live_markets._cached_indices.clear()
            live_markets._cached_futures.clear()
            live_markets._cached_portfolio_keys.clear()
            live_markets._cached_expiries.clear()
            live_markets._cached_strikes.clear()
            live_markets.render()  # must not raise

    def test_render_setup_help_without_token(self):
        with mock.patch.object(upstox_rest, "is_configured", return_value=False):
            from research_system import live_markets

            live_markets.render()  # must not raise, must not touch the network


class GlobalFeedTest(unittest.TestCase):
    def test_universe_entries_are_well_formed(self):
        from research_system.fetchers import global_feed

        for sym, (name, group, delayed) in global_feed.GLOBAL_UNIVERSE.items():
            self.assertTrue(name and isinstance(name, str), sym)
            self.assertIn(group, global_feed.GROUPS, sym)
            self.assertIsInstance(delayed, bool, sym)

    def test_snapshot_survives_yfinance_failure(self):
        from research_system.fetchers import global_feed

        try:
            import yfinance  # noqa: F401
        except ImportError:
            self.skipTest("yfinance not installed")

        global_feed._cache["rows"] = []
        global_feed._cache["at"] = 0.0
        with mock.patch("yfinance.download", side_effect=RuntimeError("network down")):
            self.assertEqual(global_feed.snapshot(), [])

    def test_snapshot_returns_empty_without_yfinance(self):
        """A missing optional dependency must not take the page down."""
        import builtins

        from research_system.fetchers import global_feed

        global_feed._cache["rows"] = []
        global_feed._cache["at"] = 0.0
        real_import = builtins.__import__

        def no_yfinance(name, *a, **kw):
            if name == "yfinance":
                raise ImportError("yfinance is not installed")
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", no_yfinance):
            self.assertEqual(global_feed.snapshot(), [])

    def test_by_group_preserves_order(self):
        from research_system.fetchers import global_feed

        rows = [
            {"group": "Crypto", "name": "Bitcoin", "last": 1, "pct_change": 0,
             "change": 0, "delayed": False, "symbol": "BTC-USD"},
            {"group": "US Indices", "name": "S&P 500", "last": 1, "pct_change": 0,
             "change": 0, "delayed": True, "symbol": "^GSPC"},
        ]
        self.assertEqual(list(global_feed.by_group(rows)), ["US Indices", "Crypto"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
