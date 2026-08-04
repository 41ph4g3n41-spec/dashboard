"""Pure-Python decoder for the Upstox `MarketDataFeedV3` protobuf feed.

Why hand-rolled instead of `protobuf` + generated `_pb2`?

The repo's deploy target is Streamlit Community Cloud, and the root
`requirements.txt` deliberately keeps to wheels with no native build step.
`protobuf` ships a C++ extension and drags `upstox-python-sdk` (which pulls
its own pinned `requests`/`urllib3`) behind it. The V3 feed schema is small,
frozen, and fully known, so decoding it directly costs ~200 lines and zero
dependencies.

The schema below is transcribed from `MarketDataFeedV3.proto` as shipped in
the official `upstox-python-sdk` 2.28.0 wheel
(`upstox_client/feeder/proto/MarketDataFeedV3_pb2.py`):

    enum Type        { initial_feed=0, live_feed=1, market_info=2 }
    enum RequestMode { ltpc=0, full_d5=1, option_greeks=2, full_d30=3 }
    enum MarketStatus{ PRE_OPEN_START=0, PRE_OPEN_END=1, NORMAL_OPEN=2,
                       NORMAL_CLOSE=3, CLOSING_START=4, CLOSING_END=5 }

    LTPC          { double ltp=1; int64 ltt=2; int64 ltq=3; double cp=4; }
    Quote         { int64 bidQ=1; double bidP=2; int64 askQ=3; double askP=4; }
    OptionGreeks  { double delta=1,theta=2,gamma=3,vega=4,rho=5; }
    OHLC          { string interval=1; double open=2,high=3,low=4,close=5;
                    int64 vol=6; int64 ts=7; }
    MarketLevel   { repeated Quote bidAskQuote=1; }
    MarketOHLC    { repeated OHLC ohlc=1; }
    MarketFullFeed{ LTPC ltpc=1; MarketLevel marketLevel=2;
                    OptionGreeks optionGreeks=3; MarketOHLC marketOHLC=4;
                    double atp=5; int64 vtt=6; double oi=7; double iv=8;
                    double tbq=9; double tsq=10; }
    IndexFullFeed { LTPC ltpc=1; MarketOHLC marketOHLC=2; }
    FullFeed      { MarketFullFeed marketFF=1; IndexFullFeed indexFF=2; }
    FirstLevelWithGreeks { LTPC ltpc=1; Quote firstDepth=2;
                    OptionGreeks optionGreeks=3; int64 vtt=4;
                    double oi=5; double iv=6; }
    Feed          { LTPC ltpc=1; FullFeed fullFeed=2;
                    FirstLevelWithGreeks firstLevelWithGreeks=3;
                    RequestMode requestMode=4; }
    MarketInfo    { map<string, MarketStatus> segmentStatus=1; }
    FeedResponse  { Type type=1; map<string, Feed> feeds=2;
                    int64 currentTs=3; MarketInfo marketInfo=4; }

Output shape matches `google.protobuf.json_format.MessageToDict` closely
enough to be a drop-in: camelCase keys, proto3 default values omitted,
enums as their name strings. It differs deliberately in one place — 64-bit
ints stay Python `int` rather than becoming strings, because every consumer
here does arithmetic on them. `tests/test_upstox_proto.py` round-trips this
decoder against the official generated parser to keep the two in step.
"""

from __future__ import annotations

import struct
from typing import Any

__all__ = ["decode_feed_response", "DecodeError"]


class DecodeError(ValueError):
    """Raised when a frame is not valid MarketDataFeedV3 wire data."""


# --- wire types ---------------------------------------------------------
_WT_VARINT = 0
_WT_I64 = 1
_WT_LEN = 2
_WT_I32 = 5

# --- field kinds used by the schema table -------------------------------
# ("d", None)          double
# ("i", None)          int64  (varint)
# ("s", None)          string
# ("e", <enum dict>)   enum   (varint -> name)
# ("m", <spec>)        nested message (singular)
# ("r", <spec>)        nested message (repeated)
# ("map", <spec>)      map<string, X> — X described by the entry spec

_TYPE_ENUM = {0: "initial_feed", 1: "live_feed", 2: "market_info"}
_REQUEST_MODE_ENUM = {0: "ltpc", 1: "full_d5", 2: "option_greeks", 3: "full_d30"}
_MARKET_STATUS_ENUM = {
    0: "PRE_OPEN_START",
    1: "PRE_OPEN_END",
    2: "NORMAL_OPEN",
    3: "NORMAL_CLOSE",
    4: "CLOSING_START",
    5: "CLOSING_END",
}

_LTPC = {
    1: ("ltp", "d", None),
    2: ("ltt", "i", None),
    3: ("ltq", "i", None),
    4: ("cp", "d", None),
}

_QUOTE = {
    1: ("bidQ", "i", None),
    2: ("bidP", "d", None),
    3: ("askQ", "i", None),
    4: ("askP", "d", None),
}

_OPTION_GREEKS = {
    1: ("delta", "d", None),
    2: ("theta", "d", None),
    3: ("gamma", "d", None),
    4: ("vega", "d", None),
    5: ("rho", "d", None),
}

_OHLC = {
    1: ("interval", "s", None),
    2: ("open", "d", None),
    3: ("high", "d", None),
    4: ("low", "d", None),
    5: ("close", "d", None),
    6: ("vol", "i", None),
    7: ("ts", "i", None),
}

_MARKET_LEVEL = {1: ("bidAskQuote", "r", _QUOTE)}
_MARKET_OHLC = {1: ("ohlc", "r", _OHLC)}

_MARKET_FULL_FEED = {
    1: ("ltpc", "m", _LTPC),
    2: ("marketLevel", "m", _MARKET_LEVEL),
    3: ("optionGreeks", "m", _OPTION_GREEKS),
    4: ("marketOHLC", "m", _MARKET_OHLC),
    5: ("atp", "d", None),
    6: ("vtt", "i", None),
    7: ("oi", "d", None),
    8: ("iv", "d", None),
    9: ("tbq", "d", None),
    10: ("tsq", "d", None),
}

_INDEX_FULL_FEED = {
    1: ("ltpc", "m", _LTPC),
    2: ("marketOHLC", "m", _MARKET_OHLC),
}

_FULL_FEED = {
    1: ("marketFF", "m", _MARKET_FULL_FEED),
    2: ("indexFF", "m", _INDEX_FULL_FEED),
}

_FIRST_LEVEL_WITH_GREEKS = {
    1: ("ltpc", "m", _LTPC),
    2: ("firstDepth", "m", _QUOTE),
    3: ("optionGreeks", "m", _OPTION_GREEKS),
    4: ("vtt", "i", None),
    5: ("oi", "d", None),
    6: ("iv", "d", None),
}

_FEED = {
    1: ("ltpc", "m", _LTPC),
    2: ("fullFeed", "m", _FULL_FEED),
    3: ("firstLevelWithGreeks", "m", _FIRST_LEVEL_WITH_GREEKS),
    4: ("requestMode", "e", _REQUEST_MODE_ENUM),
}

# map<string, Feed> — synthetic entry message: key=1, value=2
_FEEDS_ENTRY = {1: ("key", "s", None), 2: ("value", "m", _FEED)}
_SEGMENT_STATUS_ENTRY = {1: ("key", "s", None), 2: ("value", "e", _MARKET_STATUS_ENUM)}

_MARKET_INFO = {1: ("segmentStatus", "map", _SEGMENT_STATUS_ENTRY)}

_FEED_RESPONSE = {
    1: ("type", "e", _TYPE_ENUM),
    2: ("feeds", "map", _FEEDS_ENTRY),
    3: ("currentTs", "i", None),
    4: ("marketInfo", "m", _MARKET_INFO),
}


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    """Return (value, next_index). Raises DecodeError on truncation."""
    result = 0
    shift = 0
    n = len(buf)
    while True:
        if i >= n:
            raise DecodeError("truncated varint")
        if shift > 63:
            raise DecodeError("varint overflows 64 bits")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _to_signed64(v: int) -> int:
    """Protobuf int64 arrives as an unsigned varint; reinterpret the sign."""
    return v - (1 << 64) if v >= (1 << 63) else v


def _skip(buf: bytes, i: int, wire_type: int) -> int:
    """Advance past one field of unknown number, preserving forward-compat."""
    if wire_type == _WT_VARINT:
        _, i = _read_varint(buf, i)
        return i
    if wire_type == _WT_I64:
        return i + 8
    if wire_type == _WT_LEN:
        length, i = _read_varint(buf, i)
        return i + length
    if wire_type == _WT_I32:
        return i + 4
    raise DecodeError(f"unsupported wire type {wire_type}")


def _parse(buf: bytes, spec: dict[int, tuple[str, str, Any]]) -> dict[str, Any]:
    """Decode one length-delimited message body against `spec`."""
    out: dict[str, Any] = {}
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field_no = key >> 3
        wire_type = key & 0x07
        entry = spec.get(field_no)

        if entry is None:
            # Unknown field: a newer server-side schema. Skip, don't fail.
            i = _skip(buf, i, wire_type)
            continue

        name, kind, sub = entry

        if kind == "d":
            if wire_type != _WT_I64:
                i = _skip(buf, i, wire_type)
                continue
            if i + 8 > n:
                raise DecodeError(f"truncated double for field {name}")
            (val,) = struct.unpack_from("<d", buf, i)
            i += 8
            if val != 0.0:  # proto3 omits defaults
                out[name] = val

        elif kind == "i":
            if wire_type != _WT_VARINT:
                i = _skip(buf, i, wire_type)
                continue
            raw, i = _read_varint(buf, i)
            val = _to_signed64(raw)
            if val != 0:
                out[name] = val

        elif kind == "e":
            if wire_type != _WT_VARINT:
                i = _skip(buf, i, wire_type)
                continue
            raw, i = _read_varint(buf, i)
            if raw != 0:
                # Unknown enum ordinals keep their number, as MessageToDict does.
                out[name] = sub.get(raw, raw)

        elif kind == "s":
            if wire_type != _WT_LEN:
                i = _skip(buf, i, wire_type)
                continue
            length, i = _read_varint(buf, i)
            if i + length > n:
                raise DecodeError(f"truncated string for field {name}")
            val = buf[i : i + length].decode("utf-8", errors="replace")
            i += length
            if val:
                out[name] = val

        elif kind in ("m", "r", "map"):
            if wire_type != _WT_LEN:
                i = _skip(buf, i, wire_type)
                continue
            length, i = _read_varint(buf, i)
            if i + length > n:
                raise DecodeError(f"truncated message for field {name}")
            body = buf[i : i + length]
            i += length
            if kind == "m":
                out[name] = _parse(body, sub)
            elif kind == "r":
                out.setdefault(name, []).append(_parse(body, sub))
            else:  # map
                entry_dict = _parse(body, sub)
                # proto3 map entries omit defaulted key/value; restore them so
                # a "" key or a 0-valued entry still lands in the dict.
                k = entry_dict.get("key", "")
                if "value" in entry_dict:
                    v = entry_dict["value"]
                elif sub[2][1] == "e":
                    v = sub[2][2].get(0)
                else:
                    v = {}
                out.setdefault(name, {})[k] = v
        else:  # pragma: no cover - guards the schema table above
            raise DecodeError(f"bad schema kind {kind!r} for field {name}")

    return out


def decode_feed_response(payload: bytes) -> dict[str, Any]:
    """Decode a binary `FeedResponse` frame into a plain dict.

    Shape (defaults omitted, as in protobuf JSON mapping)::

        {
          "type": "live_feed",
          "currentTs": 1712054400123,
          "feeds": {"NSE_INDEX|Nifty 50": {...Feed...}},
          "marketInfo": {"segmentStatus": {"NSE_EQ": "NORMAL_OPEN"}},
        }
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise DecodeError(f"expected bytes, got {type(payload).__name__}")
    return _parse(bytes(payload), _FEED_RESPONSE)
