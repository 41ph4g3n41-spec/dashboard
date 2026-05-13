"""Universe, tickers, BSE scrip mapping, sector keywords."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "research.db"
LOG_DIR = ROOT / "logs"

# ---------------------------------------------------------------
# Portfolio (9) — held positions
# ---------------------------------------------------------------
PORTFOLIO = {
    "ETERNAL": {
        "name": "Eternal (Zomato)",
        "sector": "Quick Commerce / Food Delivery",
        "nse": "ETERNAL",
        "bse_code": "543320",       # Zomato/Eternal
        "yahoo": "ETERNAL.NS",
        "aliases": ["Zomato", "Eternal", "Blinkit"],
    },
    "FIRSTCRY": {
        "name": "Brainbees Solutions (FirstCry)",
        "sector": "E-Commerce / Babycare",
        "nse": "FIRSTCRY",
        "bse_code": "544037",
        "yahoo": "FIRSTCRY.NS",
        "aliases": ["FirstCry", "Brainbees", "Brainbees Solutions"],
    },
    "HATSUN": {
        "name": "Hatsun Agro Product",
        "sector": "FMCG - Dairy",
        "nse": "HATSUN",
        "bse_code": "531531",
        "yahoo": "HATSUN.NS",
        "aliases": ["Hatsun Agro", "Hatsun", "Arun Ice Cream"],
    },
    "PAYTM": {
        "name": "One 97 Communications (Paytm)",
        "sector": "Fintech",
        "nse": "PAYTM",
        "bse_code": "543396",
        "yahoo": "PAYTM.NS",
        "aliases": ["Paytm", "One 97 Communications", "One97"],
    },
    "PANACEA": {
        "name": "Panacea Biotec",
        "sector": "Pharma / Vaccines",
        "nse": "PANACEABIO",
        "bse_code": "531349",
        "yahoo": "PANACEABIO.NS",
        "aliases": ["Panacea Biotec", "Panacea"],
    },
    "PICCADIL": {
        "name": "Piccadily Agro Industries",
        "sector": "Spirits / Liquor",
        "nse": "PICCADIL",
        "bse_code": "530305",
        "yahoo": "PICCADIL.NS",
        "aliases": ["Piccadily Agro", "Piccadily", "Indri", "Indri Whisky"],
    },
    "SAPPHIRE": {
        "name": "Sapphire Foods India",
        "sector": "QSR (KFC, Pizza Hut)",
        "nse": "SAPPHIRE",
        "bse_code": "543397",
        "yahoo": "SAPPHIRE.NS",
        "aliases": ["Sapphire Foods", "Sapphire", "KFC India", "Pizza Hut India"],
    },
    "STARHEAL": {
        "name": "Star Health and Allied Insurance",
        "sector": "Health Insurance",
        "nse": "STARHEALTH",
        "bse_code": "543412",
        "yahoo": "STARHEALTH.NS",
        "aliases": ["Star Health", "Star Health Insurance"],
    },
    "SUNTV": {
        "name": "Sun TV Network",
        "sector": "Media / Broadcasting",
        "nse": "SUNTV",
        "bse_code": "532733",
        "yahoo": "SUNTV.NS",
        "aliases": ["Sun TV Network", "Sun TV", "Sun Pictures"],
    },
}

# ---------------------------------------------------------------
# Watchlist (9)
# ---------------------------------------------------------------
WATCHLIST = {
    "ENRIN": {
        "name": "Enrin / Energy Infra",  # placeholder until clarified
        "sector": "Energy / Infrastructure",
        "nse": "ENRIN",
        "bse_code": None,
        "yahoo": "ENRIN.NS",
        "aliases": ["Enrin"],
    },
    "FRACTAL": {
        "name": "Fractal Analytics",
        "sector": "AI / Analytics",
        "nse": "FRACTAL",
        "bse_code": None,
        "yahoo": "FRACTAL.NS",
        "aliases": ["Fractal Analytics", "Fractal"],
    },
    "JSFB": {
        "name": "Jana Small Finance Bank",
        "sector": "Banking / SFB",
        "nse": "JSFB",
        "bse_code": "544118",
        "yahoo": "JSFB.NS",
        "aliases": ["Jana Small Finance Bank", "Jana SFB", "Jana Bank"],
    },
    "LGEINDIA": {
        "name": "LG Electronics India",
        "sector": "Consumer Durables",
        "nse": "LGEINDIA",
        "bse_code": None,
        "yahoo": "LGEINDIA.NS",
        "aliases": ["LG Electronics India", "LG India"],
    },
    "LOTUSDEV": {
        "name": "Lotus Chocolate / Lotus Developer",
        "sector": "FMCG / Chocolate",
        "nse": "LOTUSDEV",
        "bse_code": None,
        "yahoo": "LOTUSDEV.NS",
        "aliases": ["Lotus Chocolate", "Lotus Developer"],
    },
    "MEESHO": {
        "name": "Meesho (Fashnear Technologies)",
        "sector": "E-Commerce",
        "nse": "MEESHO",
        "bse_code": None,
        "yahoo": "MEESHO.NS",
        "aliases": ["Meesho", "Fashnear"],
    },
    "OBSCP": {
        "name": "OBSCP / Oswal Pumps",  # placeholder
        "sector": "Capital Goods",
        "nse": "OBSCP",
        "bse_code": None,
        "yahoo": "OBSCP.NS",
        "aliases": ["OBSCP"],
    },
    "SHILPAME": {
        "name": "Shilpa Medicare",
        "sector": "Pharma",
        "nse": "SHILPAMED",
        "bse_code": "530549",
        "yahoo": "SHILPAMED.NS",
        "aliases": ["Shilpa Medicare", "Shilpa Pharma"],
    },
    "UNIFIED": {
        "name": "Unified Data-Tech Solutions",
        "sector": "IT Services",
        "nse": "UNIFIED",
        "bse_code": None,
        "yahoo": "UNIFIED.NS",
        "aliases": ["Unified Data-Tech", "Unified"],
    },
}

UNIVERSE = {**PORTFOLIO, **WATCHLIST}

# ---------------------------------------------------------------
# Sector keyword map for cross-reading PIB releases & RSS
# ---------------------------------------------------------------
SECTOR_KEYWORDS = {
    "consumer": [
        "consumption", "FMCG", "dairy", "milk", "ice cream",
        "QSR", "restaurant", "food delivery", "quick commerce",
        "e-commerce", "retail", "GST consumer",
    ],
    "pharma": [
        "pharma", "drug", "vaccine", "API", "CDSCO",
        "DCGI", "USFDA", "WHO PQ", "biosimilar",
    ],
    "infrastructure": [
        "infrastructure", "highway", "NHAI", "logistics", "port",
        "PLI", "capex", "metro", "railway",
    ],
    "defense": [
        "defence", "defense", "MoD", "BEL", "HAL", "missile",
        "indigenisation",
    ],
    "fintech": [
        "UPI", "NPCI", "RBI", "fintech", "payments", "wallet",
        "digital lending", "credit card", "BBPS",
    ],
    "media": [
        "TRAI", "broadcasting", "DTH", "OTT", "I&B Ministry",
    ],
    "insurance": [
        "IRDAI", "insurance", "health insurance", "claims ratio",
        "solvency", "premium",
    ],
}

# ---------------------------------------------------------------
# HTTP headers commonly required by NSE / BSE
# ---------------------------------------------------------------
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def ticker_to_meta(ticker: str) -> dict | None:
    return UNIVERSE.get(ticker.upper())


def all_aliases() -> dict:
    """Returns {alias_lower: ticker} for fuzzy matching news text."""
    out = {}
    for tk, meta in UNIVERSE.items():
        for alias in meta.get("aliases", []) + [tk, meta["nse"]]:
            out[alias.lower()] = tk
    return out
