"""
market_data.py
==============
Live market data fetching for the Crypto Options Strategy Tool.

Functions
---------
get_eth_price()                 Fetch current ETH/USD spot price from CoinGecko
get_deribit_iv(spot, days)      Fetch ATM implied volatility from Deribit

Internal helpers
----------------
_expiry_date(days)              Resolve expiry date for daily or weekly options
_deribit_instrument(spot, days, option_type)  Build a Deribit instrument name
_fetch_mark_iv(instrument)      Fetch mark IV for a single Deribit instrument
"""

import requests
from datetime import datetime, timedelta


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


def _deribit_instrument(spot: float, days: int, option_type: str) -> str:
    """
    Build a Deribit instrument name for an ATM ETH option.

    Format: ETH-{DDMMMYY}-{STRIKE}-{P|C}
    e.g.    ETH-25APR25-1800-P

    Parameters
    ----------
    spot        : float   Current ETH spot price
    days        : int     Days to expiry (1 = daily, 7 = weekly)
    option_type : str     "P" for put, "C" for call
    """
    expiry_str = _expiry_date(days).strftime("%d%b%y").upper()
    atm_strike = round(spot / 100) * 100
    return f"ETH-{expiry_str}-{int(atm_strike)}-{option_type}"


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

def get_eth_price() -> float | None:
    """
    Fetch the current ETH/USD spot price from CoinGecko.

    Returns the price as a float, or None if the request fails.
    CoinGecko's free tier requires no API key.
    """
    try:
        response = requests.get(
            _COINGECKO_URL,
            params={"ids": "ethereum", "vs_currencies": "usd"},
            timeout=_REQUEST_TIMEOUT,
        )
        return float(response.json()["ethereum"]["usd"])
    except Exception as e:
        print(f"  ⚠ ETH price fetch failed: {e}")
        return None


def get_deribit_iv(spot: float, days: int) -> float | None:
    """
    Fetch the ATM implied volatility for ETH options from Deribit.

    Tries the put first, then the call, for the nearest ATM strike.
    Returns IV as a decimal (e.g. 0.80 for 80%), or None if both fail.

    Parameters
    ----------
    spot : float  Current ETH spot price (used to determine ATM strike)
    days : int    Days to expiry — 1 for daily, 7 for weekly
    """
    try:
        for option_type in ("P", "C"):
            instrument = _deribit_instrument(spot, days, option_type)
            iv = _fetch_mark_iv(instrument)
            if iv is not None:
                return iv
    except Exception as e:
        print(f"  ⚠ IV fetch failed: {e}")
    return None
