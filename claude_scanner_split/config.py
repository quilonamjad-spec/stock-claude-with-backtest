"""
config.py
---------
Shared constants used across the app: timezone helper, the NSE 500 source
URL, sector-index definitions, and default ranking weights. No Streamlit
UI code lives here — just data every other module needs.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

NSE500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
LOCAL_FALLBACK = "nifty500_fallback.csv"  # optional: ship a cached copy in your repo
IST_TZ = "Asia/Kolkata"
IST = ZoneInfo(IST_TZ)


def now_ist() -> datetime:
    """Server-timezone-proof 'now'. Never use bare datetime.now() for
    anything market-related — it returns the HOST's local clock (often
    UTC on cloud servers), not IST, and silently produces a wrong as_of."""
    return datetime.now(IST)


DEFAULT_WEIGHTS = {
    "Trend_strength": 0.20,       # trend      -> EMA9 vs EMA21 separation
    "Breakout_strength": 0.20,    # momentum   -> distance past 10-bar high/low
    "Volatility_strength": 0.15,  # volatility -> ATR(14) expansion vs its own avg
    "Volume_strength": 0.20,      # volume     -> volume vs 20-period avg
    "MoneyFlow_strength": 0.10,   # volume     -> CMF magnitude
    "VWAP_strength": 0.15,        # trend      -> distance from VWAP
}

# --------------------------------------------------------------------------
# SECTOR INDICES (for the stock-vs-sector alignment column)
# --------------------------------------------------------------------------
# Verified Yahoo Finance tickers as of Aug 2026 — a few gotchas worth noting:
#   - NIFTY FIN SERVICE uses a plain .NS ticker, NOT the ^CNX-style prefix
#     like most others (^CNXFIN is a DIFFERENT, narrower index — the 25/50
#     variant — don't swap it in).
#   - NIFTY BANK (^NSEBANK, 12 large banks) is NOT the same universe as
#     NIFTY PRIVATE BANK or NIFTY PSU BANK — a stock like BANDHANBNK sits
#     in Private Bank, not the main Bank index, so both are listed
#     separately below rather than folded into BANK.
#   - CHEMICALS' Yahoo ticker (NIFTY_CHEMICALS.NS) follows the same
#     "NIFTY_<NAME>.NS" pattern as Healthcare/Oil&Gas/Consumer Durables but
#     wasn't directly confirmed on a live quote page — if it turns out to
#     be wrong, that one sector just won't populate (fails gracefully,
#     falls back to NIFTY 50) rather than breaking anything.
SECTOR_INDICES = {
    "BANK":         {"csv": "https://archives.nseindia.com/content/indices/ind_niftybanklist.csv",            "yahoo": "^NSEBANK",             "label": "NIFTY BANK"},
    "PVT_BANK":     {"csv": "https://archives.nseindia.com/content/indices/ind_niftyprivatebanklist.csv",     "yahoo": "NIFTY_PVT_BANK.NS",     "label": "NIFTY PRIVATE BANK"},
    "PSU_BANK":     {"csv": "https://archives.nseindia.com/content/indices/ind_niftypsubanklist.csv",         "yahoo": "^CNXPSUBANK",           "label": "NIFTY PSU BANK"},
    "AUTO":         {"csv": "https://archives.nseindia.com/content/indices/ind_niftyautolist.csv",            "yahoo": "^CNXAUTO",              "label": "NIFTY AUTO"},
    "IT":           {"csv": "https://archives.nseindia.com/content/indices/ind_niftyitlist.csv",              "yahoo": "^CNXIT",                "label": "NIFTY IT"},
    "PHARMA":       {"csv": "https://archives.nseindia.com/content/indices/ind_niftypharmalist.csv",          "yahoo": "^CNXPHARMA",            "label": "NIFTY PHARMA"},
    "HEALTHCARE":   {"csv": "https://archives.nseindia.com/content/indices/ind_niftyhealthcarelist.csv",      "yahoo": "NIFTY_HEALTHCARE.NS",   "label": "NIFTY HEALTHCARE"},
    "FMCG":         {"csv": "https://archives.nseindia.com/content/indices/ind_niftyfmcglist.csv",            "yahoo": "^CNXFMCG",              "label": "NIFTY FMCG"},
    "CONSR_DURBL":  {"csv": "https://archives.nseindia.com/content/indices/ind_niftyconsumerdurableslist.csv","yahoo": "NIFTY_CONSR_DURBL.NS",  "label": "NIFTY CONSUMER DURABLES"},
    "METAL":        {"csv": "https://archives.nseindia.com/content/indices/ind_niftymetallist.csv",           "yahoo": "^CNXMETAL",             "label": "NIFTY METAL"},
    "CHEMICALS":    {"csv": "https://archives.nseindia.com/content/indices/ind_niftychemicalslist.csv",       "yahoo": "NIFTY_CHEMICALS.NS",    "label": "NIFTY CHEMICALS"},
    "MEDIA":        {"csv": "https://archives.nseindia.com/content/indices/ind_niftymedialist.csv",           "yahoo": "^CNXMEDIA",             "label": "NIFTY MEDIA"},
    "REALTY":       {"csv": "https://archives.nseindia.com/content/indices/ind_niftyrealtylist.csv",          "yahoo": "^CNXREALTY",            "label": "NIFTY REALTY"},
    "OIL_GAS":      {"csv": "https://archives.nseindia.com/content/indices/ind_niftyoilgaslist.csv",          "yahoo": "NIFTY_OIL_AND_GAS.NS",  "label": "NIFTY OIL AND GAS"},
    "ENERGY":       {"csv": "https://archives.nseindia.com/content/indices/ind_niftyenergylist.csv",          "yahoo": "^CNXENERGY",            "label": "NIFTY ENERGY"},
    "FIN_SERVICE":  {"csv": "https://archives.nseindia.com/content/indices/ind_niftyfinservicelist.csv",      "yahoo": "NIFTY_FIN_SERVICE.NS",  "label": "NIFTY FIN SERVICE"},
}
DEFAULT_SECTOR = "NIFTY50"
DEFAULT_INDEX_YAHOO = "^NSEI"
DEFAULT_INDEX_LABEL = "NIFTY 50"

# More specific sectors first, so a stock that appears in more than one list
# (e.g. a bank in both BANK and the broader FIN_SERVICE list) keeps its most
# specific mapping — first match wins wherever this is consumed.
SECTOR_PRIORITY = [
    "BANK", "PVT_BANK", "PSU_BANK", "AUTO", "IT", "PHARMA", "HEALTHCARE",
    "FMCG", "CONSR_DURBL", "METAL", "CHEMICALS", "MEDIA", "REALTY",
    "OIL_GAS", "ENERGY", "FIN_SERVICE",
]
