"""
portfolio.py
============
Open position aggregation and P&L helpers for the Crypto Options Strategy Tool.

Public API
----------
collect_open_positions()
    Query the database for open state and return unified position summaries.
"""

from datetime import date, datetime
from typing import Any

from config import (
    SUPPORTED_ASSETS,
    BUDGET_USD,
    RISK_FREE_RATE,
    IV_FALLBACK,
)
from market.market_data import get_spot_price, get_deribit_iv
from market.pricing import bs_put, bs_call
from database.wheel_db import load_wheel_state
from database.strangle_db import load_strangle_state
from database.calendar_db import load_calendar_state
from database.spread_db import load_spread_state, get_open_spreads, get_spread_history


def _parse_expiry(expiry_str: str) -> date | None:
    expiry_str = str(expiry_str).strip()
    if not expiry_str:
        return None

    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except ValueError:
            continue
    return None


def _days_remaining(expiry_str: str, fallback_days: int = 0) -> int:
    expiry = _parse_expiry(expiry_str)
    if expiry:
        return max((expiry - date.today()).days, 0)
    return max(int(fallback_days or 0), 0)


def _market_data(asset: str, days: int) -> tuple[float | None, float | None]:
    spot = get_spot_price(asset)
    if spot is None:
        return None, None

    iv = get_deribit_iv(asset, spot, days)
    if iv is None:
        iv = IV_FALLBACK

    return spot, iv


def _wheel_position_pnl(position: dict, spot: float, iv: float) -> tuple[float, float]:
    K = float(position.get("strike", 0))
    p0 = float(position.get("premium", 0.0))
    qty = float(position.get("qty") or (BUDGET_USD / K if K else 0.0))
    days = int(position.get("days", 7))
    T = max(days / 365.0, 1 / 365.0)

    if str(position.get("type", "")).lower() == "put":
        current_value = bs_put(spot, K, T, RISK_FREE_RATE, iv) * qty
        description = "Short Put"
    else:
        current_value = bs_call(spot, K, T, RISK_FREE_RATE, iv) * qty
        description = "Short Call"

    return p0 - current_value, current_value, description


def _strangle_position_pnl(position: dict, spot: float, iv: float) -> tuple[float, float]:
    Kp = float(position.get("put_strike", 0))
    Kc = float(position.get("call_strike", 0))
    p0 = float(position.get("total_premium", 0.0))
    qty = float(position.get("qty") or (BUDGET_USD / position.get("spot_open", 1) if position.get("spot_open") else 0.0))
    days = int(position.get("days", 7))
    T = max(days / 365.0, 1 / 365.0)

    current_value = (
        bs_put(spot, Kp, T, RISK_FREE_RATE, iv) * qty
        + bs_call(spot, Kc, T, RISK_FREE_RATE, iv) * qty
    )
    return p0 - current_value, current_value


def _calendar_position_pnl(position: dict, spot: float, iv: float) -> tuple[float, float, str]:
    K = float(position.get("strike", 0))
    opt_type = str(position.get("option_type", "Put"))
    net_debit = float(position.get("net_debit", 0.0))
    qty = float(position.get("qty") or (BUDGET_USD / position.get("spot_open", 1) if position.get("spot_open") else 0.0))
    near_left = _days_remaining(position.get("expiry_near", ""), position.get("near_days", 0))
    far_left = _days_remaining(position.get("expiry_far", ""), position.get("far_days", 0))
    Tn = max(near_left / 365.0, 1 / 365.0)
    Tf = max(far_left / 365.0, 1 / 365.0)

    if opt_type.lower() == "put":
        near_value = bs_put(spot, K, Tn, RISK_FREE_RATE, iv) * qty
        far_value = bs_put(spot, K, Tf, RISK_FREE_RATE, iv) * qty
    else:
        near_value = bs_call(spot, K, Tn, RISK_FREE_RATE, iv) * qty
        far_value = bs_call(spot, K, Tf, RISK_FREE_RATE, iv) * qty

    spread_value = far_value - near_value
    description = f"{opt_type} Calendar"
    return net_debit - spread_value, spread_value, description


def _spread_position_pnl(position: dict, spot: float, iv: float) -> tuple[float, float]:
    short_strike = float(position.get("short_strike", 0))
    long_strike = float(position.get("long_strike", 0))
    net_credit = float(position.get("net_credit", 0.0))
    qty = float(position.get("qty") or 1.0)
    days = int(position.get("days", 7))
    T = max(days / 365.0, 1 / 365.0)
    spread_type = str(position.get("spread_type", "BPS")).upper()

    if spread_type == "BCS":
        short_val = bs_call(spot, short_strike, T, RISK_FREE_RATE, iv)
        long_val = bs_call(spot, long_strike, T, RISK_FREE_RATE, iv)
    else:  # BPS (Bull Put Spread)
        short_val = bs_put(spot, short_strike, T, RISK_FREE_RATE, iv)
        long_val = bs_put(spot, long_strike, T, RISK_FREE_RATE, iv)

    current_value = (short_val - long_val) * qty
    return net_credit - current_value, current_value


def _position_summary(asset: str, strategy: str, state: dict) -> dict | None:
    open_position = state.get("open")
    if not open_position:
        return None

    if strategy == "Wheel":
        position_type = str(open_position.get("type", ""))
    elif strategy == "Strangle":
        position_type = "Short Strangle"
    elif strategy == "Spread":
        spread_type = str(open_position.get("spread_type", "BPS")).upper()
        position_type = "Bull Put Spread" if spread_type == "BPS" else "Bear Call Spread"
    else:
        position_type = f"Calendar {open_position.get('option_type', '')}"

    days_left = None
    if strategy == "Calendar":
        days_left = _days_remaining(open_position.get("expiry_near", ""), open_position.get("near_days", 0))
    else:
        days_left = _days_remaining(open_position.get("expiry", ""), open_position.get("days", 0))

    if strategy == "Strangle":
        description = "Short Strangle"
    elif strategy == "Calendar":
        description = f"{open_position.get('option_type', 'Call')} Calendar"
    elif strategy == "Spread":
        spread_type = str(open_position.get("spread_type", "BPS")).upper()
        description = "Bull Put Spread" if spread_type == "BPS" else "Bear Call Spread"
    else:
        description = f"Short {open_position.get('type', 'Put')}"

    spot, iv = _market_data(asset, open_position.get("days", 7) or 7)
    if spot is None or iv is None:
        pnl = None
        current_value = None
    else:
        if strategy == "Wheel":
            pnl, current_value, description = _wheel_position_pnl(open_position, spot, iv)
        elif strategy == "Strangle":
            pnl, current_value = _strangle_position_pnl(open_position, spot, iv)
        elif strategy == "Spread":
            pnl, current_value = _spread_position_pnl(open_position, spot, iv)
        else:
            pnl, current_value, description = _calendar_position_pnl(open_position, spot, iv)

    if strategy == "Wheel":
        strike_value = f"${open_position.get('strike', ''):,.0f}"
    elif strategy == "Strangle":
        strike_value = (
            f"${open_position.get('put_strike', 0):,.0f}/"
            f"${open_position.get('call_strike', 0):,.0f}"
        )
    elif strategy == "Spread":
        strike_value = (
            f"${open_position.get('short_strike', 0):,.0f}/"
            f"${open_position.get('long_strike', 0):,.0f}"
        )
    else:
        strike_value = f"${open_position.get('strike', 0):,.0f}"

    if strategy == "Wheel":
        premium = float(open_position.get("premium", 0.0))
    elif strategy == "Strangle":
        premium = float(open_position.get("total_premium", 0.0))
    elif strategy == "Spread":
        premium = float(open_position.get("net_credit", 0.0))
    else:
        premium = float(open_position.get("net_debit", 0.0))

    return {
        "asset": asset,
        "strategy": strategy,
        "position": description,
        "strike": strike_value,
        "days_left": days_left,
        "premium": premium,
        "current_value": float(current_value) if current_value is not None else None,
        "unrealised_pnl": float(pnl) if pnl is not None else None,
        "notes": str(open_position.get("asset", "")),
    }


def _spread_row_to_position_dict(row: Any) -> dict:
    return {
        "asset":        row.asset,
        "spread_type":  row.spread_type,
        "short_strike": row.short_strike,
        "long_strike":  row.long_strike,
        "expiry":       row.expiry or "",
        "qty":          row.qty,
        "days":         row.days or 7,
        "spot_open":    row.spot_open,
        "net_credit":   row.net_credit or 0.0,
        "max_loss":     row.max_loss,
        "result":       row.result,
        "notes":        row.notes or "",
        "broker":       row.broker,
        "id":           row.id,
    }


def collect_open_positions() -> list[dict]:
    positions: list[dict] = []

    _loaders = {
        "Wheel":    load_wheel_state,
        "Strangle": load_strangle_state,
        "Calendar": load_calendar_state,
    }

    for asset in SUPPORTED_ASSETS:
        for strategy, loader in _loaders.items():
            try:
                state = loader(asset)
            except Exception:
                continue

            summary = _position_summary(asset, strategy, state)
            if summary:
                positions.append(summary)

        # Spreads: query the spreads table directly for open rows
        try:
            open_spreads = get_open_spreads(asset)
        except Exception:
            open_spreads = []

        for row in open_spreads:
            pos = _spread_row_to_position_dict(row)
            summary = _position_summary(asset, "Spread", {"open": pos})
            if summary:
                positions.append(summary)

    return positions


def collect_spread_history(asset: str | None = None) -> list[dict]:
    """Return closed credit spread trades from the spreads table."""
    assets = [asset] if asset else list(SUPPORTED_ASSETS.keys())
    history: list[dict] = []

    for a in assets:
        try:
            rows = get_spread_history(a)
        except Exception:
            continue

        for row in rows:
            history.append({
                "id":           row.id,
                "asset":        row.asset,
                "spread_type":  row.spread_type,
                "short_strike": row.short_strike,
                "long_strike":  row.long_strike,
                "expiry":       row.expiry,
                "qty":          row.qty,
                "days":         row.days,
                "date_open":    row.date_open,
                "date_close":   row.date_close,
                "spot_open":    row.spot_open,
                "spot_close":   row.spot_close,
                "net_credit":   row.net_credit,
                "max_loss":     row.max_loss,
                "pnl":          row.pnl,
                "result":       row.result,
                "notes":        row.notes,
                "broker":       row.broker,
            })

    return history
