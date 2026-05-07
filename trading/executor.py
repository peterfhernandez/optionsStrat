"""
trading/executor.py
===================
Paper trade execution logic for all strategies.

Public API
----------
enter_trade(candidate, days)
    Open the paper position described by candidate and persist + log it.

Internal helpers
----------------
_enter_csp(c, T)      Open Cash-Secured Put position
_enter_cc(c, T)       Open Covered Call position
_enter_strangle(c, T) Open Short Strangle position
_enter_calendar(c, T) Open Calendar position
"""

from datetime import date, timedelta

from config import (
    BUDGET_USD, RISK_FREE_RATE, CALENDAR_NEAR_DAYS, CALENDAR_FAR_DAYS,
)
from database import load_wheel_state, save_wheel_state, create_single_trade
from database.strangle_db import load_strangle_state, save_strangle_state, create_strangle_trade
from database.calendar_db import load_calendar_state, save_calendar_state, create_calendar_trade
from market.pricing import bs_put, bs_call


def _enter_csp(c, T: float) -> dict:
    """Open a Cash-Secured Put position in the wheel state."""
    K       = float(c.strike.replace("$", "").replace(",", ""))
    qty     = BUDGET_USD / K
    premium = bs_put(c.spot, K, T, RISK_FREE_RATE, c.iv) * qty
    expiry  = (date.today() + timedelta(days=c.days)).strftime("%d-%b-%Y")

    s = load_wheel_state(c.asset)
    s["stage"] = "short_put"
    s["open"]  = {
        "type":      "Put",
        "strike":    K,
        "expiry":    expiry,
        "premium":   round(premium, 4),
        "spot_open": c.spot,
        "qty":       qty,
        "days":      c.days,
        "asset":     c.asset,
    }
    s["total_premium"] = s.get("total_premium", 0.0) + premium
    save_wheel_state(c.asset, s)

    create_single_trade(
        asset=c.asset,
        date_open=date.today(),
        option_type="Put",
        strike=K,
        expiry=expiry,
        spot_open=c.spot,
        premium=round(premium, 4),
        qty=qty,
        days=c.days,
        stage="short_put",
        notes=(
            f"AUTO {c.asset} CSP, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag}"
        ),
    )
    return s["open"]


def _enter_cc(c, T: float) -> dict:
    """Open a Covered Call position. Requires wheel state in 'holding'."""
    s = load_wheel_state(c.asset)
    if s["stage"] != "holding":
        raise RuntimeError(f"Cannot enter CC for {c.asset}: wheel stage={s['stage']}")

    K       = float(c.strike.replace("$", "").replace(",", ""))
    qty     = s["asset_held"] or (BUDGET_USD / c.spot)
    premium = bs_call(c.spot, K, T, RISK_FREE_RATE, c.iv) * qty
    expiry  = (date.today() + timedelta(days=c.days)).strftime("%d-%b-%Y")

    s["stage"] = "short_call"
    s["open"]  = {
        "type":      "Call",
        "strike":    K,
        "expiry":    expiry,
        "premium":   round(premium, 4),
        "spot_open": c.spot,
        "qty":       qty,
        "days":      c.days,
        "asset":     c.asset,
    }
    s["total_premium"] = s.get("total_premium", 0.0) + premium
    save_wheel_state(c.asset, s)

    create_single_trade(
        asset=c.asset,
        date_open=date.today(),
        option_type="Call",
        strike=K,
        expiry=expiry,
        spot_open=c.spot,
        premium=round(premium, 4),
        qty=qty,
        days=c.days,
        stage="short_call",
        notes=(
            f"AUTO {c.asset} CC, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag}"
        ),
    )
    return s["open"]


def _enter_strangle(c, T: float) -> dict:
    """Open a short strangle position."""
    Kp  = c.put_strike
    Kc  = c.call_strike
    qty = BUDGET_USD / c.spot
    pp  = bs_put (c.spot, Kp, T, RISK_FREE_RATE, c.iv) * qty
    cp  = bs_call(c.spot, Kc, T, RISK_FREE_RATE, c.iv) * qty
    tot = pp + cp
    expiry = (date.today() + timedelta(days=c.days)).strftime("%d-%b-%Y")

    trade = create_strangle_trade(
        asset=c.asset,
        date_open=date.today(),
        put_strike=Kp,
        call_strike=Kc,
        spot_open=c.spot,
        total_premium=round(tot, 4),
        qty=qty,
        days=c.days,
        expiry=expiry,
        notes=(
            f"AUTO {c.asset} strangle, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag or 'N/A'}"
        ),
    )

    s = load_strangle_state(c.asset)
    s["open"] = {
        "put_strike":    Kp,
        "call_strike":   Kc,
        "total_premium": round(tot, 4),
        "qty":           qty,
        "expiry":        expiry,
        "spot_open":     c.spot,
        "days":          c.days,
        "asset":         c.asset,
        "trade_id":      trade.id,
    }
    s["total_premium"] = s.get("total_premium", 0.0) + tot
    s["trades"]        = s.get("trades",        0)   + 1
    save_strangle_state(c.asset, s)
    return s["open"]


def _enter_calendar(c, T: float) -> dict:
    """Open a calendar (Cal-C or Cal-P) position."""
    K           = float(c.strike.split()[0].replace("$", "").replace(",", ""))
    far_days    = c.far_days or CALENDAR_FAR_DAYS
    T_far       = far_days / 365.0
    qty         = BUDGET_USD / c.spot
    option_type = "Call" if c.strategy == "Cal-C" else "Put"
    bs_fn       = bs_call if option_type == "Call" else bs_put

    near_prem = bs_fn(c.spot, K, T,     RISK_FREE_RATE, c.iv) * qty
    far_prem  = bs_fn(c.spot, K, T_far, RISK_FREE_RATE, c.iv) * qty
    net_debit = far_prem - near_prem

    expiry_near = (date.today() + timedelta(days=c.days)).strftime("%d-%b-%Y")
    expiry_far  = (date.today() + timedelta(days=far_days)).strftime("%d-%b-%Y")

    trade = create_calendar_trade(
        asset=c.asset,
        date_open=date.today(),
        option_type=option_type,
        strike=K,
        expiry_near=expiry_near,
        expiry_far=expiry_far,
        near_days=c.days,
        far_days=far_days,
        qty=qty,
        spot_open=c.spot,
        near_prem=round(near_prem, 4),
        far_prem=round(far_prem, 4),
        net_debit=round(net_debit, 4),
        notes=(
            f"AUTO {c.asset} {option_type} calendar, "
            f"{c.days}d/{far_days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr"
        ),
    )

    s = load_calendar_state(c.asset)
    s["open"] = {
        "strike":      K,
        "option_type": option_type,
        "near_prem":   round(near_prem, 4),
        "far_prem":    round(far_prem,  4),
        "net_debit":   round(net_debit, 4),
        "qty":         qty,
        "expiry_near": expiry_near,
        "expiry_far":  expiry_far,
        "spot_open":   c.spot,
        "near_days":   c.days,
        "far_days":    far_days,
        "asset":       c.asset,
        "trade_id":    trade.id,
    }
    s["trades"] = s.get("trades", 0) + 1
    save_calendar_state(c.asset, s)
    return s["open"]


def enter_trade(c, days: int | None = None) -> dict:
    """
    Open the paper position described by c and persist + log it.

    The strategy tag on the candidate selects the right opener.
    Returns the freshly written open dict.
    """
    days_eff = days or c.days
    T        = days_eff / 365.0

    if c.strategy == "CSP":
        return _enter_csp(c, T)
    if c.strategy == "CC":
        return _enter_cc(c, T)
    if c.strategy == "Strangle":
        return _enter_strangle(c, T)
    if c.strategy in ("Cal-C", "Cal-P"):
        return _enter_calendar(c, T)

    raise ValueError(f"Unsupported strategy '{c.strategy}'")
