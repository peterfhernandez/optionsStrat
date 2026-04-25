"""
market_data.py
==============
Live market data fetching for the Crypto Options Strategy Tool.

Functions
---------
get_eth_price()                 Fetch current ETH/USD spot price from CoinGecko
get_btc_price()                 Fetch current BTC/USD spot price from CoinGecko
get_sol_price()                 Fetch current SOL/USD spot price from CoinGecko
get_deribit_iv(spot, days)      Fetch ATM implied volatility from Deribit

Internal helpers
----------------
_expiry_date(days)              Resolve expiry date for daily or weekly options
_deribit_instrument(spot, days, option_type)  Build a Deribit instrument name
_fetch_mark_iv(instrument)      Fetch mark IV for a single Deribit instrument
"""

import requests
from datetime import datetime, timedelta

from config import SUPPORTED_ASSETS

# ── Constants ─────────────────────────────────────────────────────────────────

_COINGECKO_URL  = "https://api.coingecko.com/api/v3/simple/price"
_DERIBIT_URL    = "https://www.deribit.com/api/v2/public/get_order_book"
_REQUEST_TIMEOUT = 8  # seconds


# ── Internal helpers ──────────────────────────────────────────────────────────

def _expiry_date(days: int) -> datetime:
    """
    Resolve the target expiry date for a given number of days.

    - days == 1  → tomorrow (daily expiry)
    - days >= 2  → next Friday (standard weekly expiry on Deribit)
    """
    today = datetime.utcnow()
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
    expiry_str = _expiry_date(days).strftime("%d%b%y").upper()
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


# ── Public API ────────────────────────────────────────────────────────────────
def get_spot_price(asset: str) -> float | None:
    """
    Fetch the current USD spot price for any supported asset.
 
    Parameters
    ----------
    asset : str  Asset symbol — must be a key in config.SUPPORTED_ASSETS
                 e.g. "ETH", "BTC", "SOL"
 
    Returns
    -------
    float | None  Spot price in USD, or None if the request fails.
    """
    asset = asset.upper()
    if asset not in SUPPORTED_ASSETS:
        raise ValueError(
            f"Unsupported asset '{asset}'. "
            f"Choose from: {', '.join(SUPPORTED_ASSETS)}"
        )
    coingecko_id = SUPPORTED_ASSETS[asset]["coingecko_id"]
    try:
        response = requests.get(
            _COINGECKO_URL,
            params={"ids": coingecko_id, "vs_currencies": "usd"},
            timeout=_REQUEST_TIMEOUT,
        )
        return float(response.json()[coingecko_id]["usd"])
    except Exception as e:
        print(f"  ⚠ {asset} price fetch failed: {e}")
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
            instrument = _deribit_instrument(asset, spot, days, cfg['strike_round'], option_type)
            iv = _fetch_mark_iv(instrument)
            if iv is not None:
                return iv
    except Exception as e:
        print(f"  ⚠ IV fetch failed: {e}")
    return None
