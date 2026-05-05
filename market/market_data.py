"""
market_data.py
==============
Live market data fetching for the Crypto Options Strategy Tool.
 
Supports any asset defined in config.SUPPORTED_ASSETS.
To add a new asset, add an entry there — no changes needed here.
 
Spot price source priority
--------------------------
1. Binance public ticker  — no API key, generous rate limits
2. CoinGecko simple price — fallback if Binance fails
 
Public API
----------
get_spot_price(asset)               Fetch current spot price (Binance → CoinGecko)
get_deribit_iv(asset, spot, days)   Fetch ATM implied volatility from Deribit
get_eth_price()                     Convenience wrapper → get_spot_price("ETH")
 
Internal helpers
----------------
_price_from_binance(asset)              Fetch price from Binance
_price_from_coingecko(asset)            Fetch price from CoinGecko (with 429 retry)
_expiry_date(days)                      Resolve expiry date (daily or weekly)
_atm_strike(spot, strike_round)         Round spot to nearest ATM strike increment
_deribit_instrument(ticker, spot, days, Build a Deribit instrument name
                    strike_round, opt_type)
_fetch_mark_iv(instrument)              Fetch mark IV for one Deribit instrument
_fetch_order_book(instrument)           Fetch IV + liquidity metrics for one instrument
"""

import requests
import time
from datetime import datetime, timedelta, timezone

from config import SUPPORTED_ASSETS

# ── Constants ─────────────────────────────────────────────────────────────────

_BINANCE_URL     = "https://api.binance.com/api/v3/ticker/price"
_COINGECKO_URL   = "https://api.coingecko.com/api/v3/simple/price"
_DERIBIT_URL     = "https://www.deribit.com/api/v2/public/get_order_book"
_REQUEST_TIMEOUT = 8  # seconds
_COINGECKO_RETRY = 2  # seconds to wait before retrying after a 429


# ── Internal helpers ──────────────────────────────────────────────────────────

def _price_from_binance(asset: str) -> float | None:
    """
    Fetch spot price from Binance public ticker endpoint.
 
    No API key required. Rate limits are generous for public endpoints.
    Returns price in USD, or None if the request fails.
    """
    symbol = SUPPORTED_ASSETS[asset]["binance_symbol"]
    try:
        response = requests.get(
            _BINANCE_URL,
            params={"symbol": symbol},
            timeout=_REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if "price" not in data:
            return None
        return float(data["price"])
    except Exception:
        return None
 
 
def _price_from_coingecko(asset: str) -> float | None:
    """
    Fetch spot price from CoinGecko as a fallback.
 
    Handles 429 rate-limit responses with one automatic retry.
    Returns price in USD, or None if the request fails.
    """
    coingecko_id = SUPPORTED_ASSETS[asset]["coingecko_id"]
    try:
        for _ in range(2):  # one retry on 429
            response = requests.get(
                _COINGECKO_URL,
                params={"ids": coingecko_id, "vs_currencies": "usd"},
                timeout=_REQUEST_TIMEOUT,
            )
            if response.status_code == 429:
                print(f"  ⚠ CoinGecko rate limit — waiting {_COINGECKO_RETRY}s...")
                time.sleep(_COINGECKO_RETRY)
                continue
            data = response.json()
            if coingecko_id not in data:
                return None
            return float(data[coingecko_id]["usd"])
    except Exception:
        return None
    return None

def _expiry_date(days: int) -> datetime:
    """
    Resolve the target expiry date for a given number of days.

    - days == 1  → tomorrow (daily expiry)
    - days >= 2  → next Friday (standard weekly expiry on Deribit)
    """
    today = datetime.now(timezone.utc)
    if days == 1:
        return today + timedelta(days=1)
    days_until_friday = (4 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_until_friday)


def _atm_strike(spot: float, strike_round: int) -> int:
    """
    Round spot price to the nearest ATM strike increment for this asset.
 
    Each asset has its own increment (e.g. ETH=$100, BTC=$500, SOL=$1)
    defined in config.SUPPORTED_ASSETS.
    """
    return round(spot / strike_round) * strike_round


def _deribit_instrument(
    ticker: str,
    spot: float,
    days: int,
    strike_round:int,
    option_type: str,
) -> str:
    """
    Build a Deribit instrument name for an ATM option.
 
    Format: {TICKER}-{DDMMMYY}-{STRIKE}-{P|C}
    e.g.    ETH-25APR25-1800-P
            BTC-25APR25-90000-C
            SOL-25APR25-150-P
 
    Parameters
    ----------
    ticker       : str   Deribit asset ticker (e.g. "ETH", "BTC", "SOL")
    spot         : float Current spot price
    days         : int   Days to expiry (1 = daily, 7 = weekly)
    strike_round : int   ATM strike rounding increment
    option_type  : str   "P" for put, "C" for call
    """
    expiry_dt  = _expiry_date(days)
    expiry_str = str(expiry_dt.day) + expiry_dt.strftime("%b%y").upper()
    strike = _atm_strike(spot, strike_round)
    return f"{ticker}-{expiry_str}-{int(strike)}-{option_type}"


def _fetch_mark_iv(instrument: str) -> float | None:
    """
    Fetch the mark IV for a single Deribit instrument.

    Returns the IV as a decimal (e.g. 0.80 for 80%), or None if unavailable.
    """
    response = requests.get(
        _DERIBIT_URL,
        params={"instrument_name": instrument},
        timeout=_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        return None
    iv = response.json().get("result", {}).get("mark_iv")
    if iv and iv > 0:
        return float(iv) / 100.0
    return None


def _fetch_order_book(instrument: str) -> dict | None:
    """
    Fetch full order book data for a Deribit instrument.
 
    Returns a dict with IV and liquidity metrics, or None if unavailable.
 
    Keys returned
    -------------
    mark_iv      : float  Mark IV as a decimal (e.g. 0.80)
    bid_iv       : float  Bid IV as a decimal
    ask_iv       : float  Ask IV as a decimal
    iv_spread    : float  ask_iv - bid_iv (tighter = more liquid)
    open_interest: float  Open interest in contracts
    volume_usd   : float  24h volume in USD
    best_bid     : float  Best bid price (in BTC/ETH — Deribit convention)
    best_ask     : float  Best ask price
    """
    response = requests.get(
        _DERIBIT_URL,
        params={"instrument_name": instrument},
        timeout=_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        return None
    result = response.json().get("result", {})
 
    mark_iv = result.get("mark_iv")
    bid_iv  = result.get("bid_iv")
    ask_iv  = result.get("ask_iv")
    if not mark_iv or mark_iv <= 0:
        return None
 
    stats = result.get("stats", {})
    return {
        "mark_iv":       float(mark_iv) / 100.0,
        "bid_iv":        float(bid_iv)  / 100.0 if bid_iv  else None,
        "ask_iv":        float(ask_iv)  / 100.0 if ask_iv  else None,
        "iv_spread":     round((ask_iv - bid_iv) / 100.0, 4)
                         if bid_iv and ask_iv else None,
        "open_interest": float(result.get("open_interest", 0)),
        "volume_usd":    float(stats.get("volume_usd", 0)),
        "best_bid":      result.get("best_bid_price"),
        "best_ask":      result.get("best_ask_price"),
    }

# ── Public API ────────────────────────────────────────────────────────────────
def get_spot_price(asset: str) -> float | None:
    """
    Fetch the current USD spot price for any supported asset.
 
    Tries Binance first (no API key, generous rate limits). Falls back
    to CoinGecko if Binance fails. Prints which source was used only
    when the primary fails.
 
    Parameters
    ----------
    asset : str  Asset symbol — must be a key in config.SUPPORTED_ASSETS
 
    Returns
    -------
    float | None  Spot price in USD, or None if both sources fail.
    """
    asset = asset.upper()
    if asset not in SUPPORTED_ASSETS:
        raise ValueError(
            f"Unsupported asset '{asset}'. "
            f"Choose from: {', '.join(SUPPORTED_ASSETS)}"
        )
 
    # Primary: Binance
    price = _price_from_binance(asset)
    if price:
        return price
 
    # Fallback: CoinGecko
    print(f"  ⚠ Binance price fetch failed for {asset} — trying CoinGecko...")
    price = _price_from_coingecko(asset)
    if price:
        return price
 
    print(f"  ⚠ All price sources failed for {asset}.")
    return None
    
    

def get_eth_price() -> float | None:
    """Convenience wrapper for get_spot_price('ETH'). Keeps existing call sites working."""
    return get_spot_price("ETH")


def get_btc_price() -> float | None:
    """Convenience wrapper for get_spot_price('BTC'). Keeps existing call sites working."""
    return get_spot_price("BTC")


def get_sol_price() -> float | None:
    """Convenience wrapper for get_spot_price('SOL'). Keeps existing call sites working."""
    return get_spot_price("SOL")


def get_xrp_price() -> float | None:
    """Convenience wrapper for get_spot_price('XRP'). Keeps existing call sites working."""
    return get_spot_price("XRP")


def get_deribit_iv(asset: str, spot: float, days: int) -> float | None:
    """
    Fetch the ATM implied volatility for any supported asset from Deribit.
 
    Tries the put first, then the call, at the nearest ATM strike.
    Returns IV as a decimal (e.g. 0.80 for 80%), or None if both fail.
 
    Parameters
    ----------
    asset : str   Asset symbol — must be a key in config.SUPPORTED_ASSETS
    spot  : float Current spot price (used to determine ATM strike)
    days  : int   Days to expiry — 1 for daily, 7 for weekly
    """
    asset = asset.upper()
    if asset not in SUPPORTED_ASSETS:
        raise ValueError(
            f"Unsupported asset '{asset}'. "
            f"Choose from: {', '.join(SUPPORTED_ASSETS)}"
        )
    cfg = SUPPORTED_ASSETS[asset]
    try:
        for option_type in ("P", "C"):
            instrument = _deribit_instrument(cfg['deribit_ticker'], spot, days, cfg['strike_round'], option_type)
            iv = _fetch_mark_iv(instrument)
            if iv is not None:
                return iv
    except Exception as e:
        print(f"  ⚠ IV fetch failed: {e}")
    return None
