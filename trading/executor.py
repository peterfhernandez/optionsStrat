"""
trading/executor.py
===================
Paper trade execution logic for all strategies.

Public API
----------
enter_trade(candidate, wb, days)
    Open the paper position described by candidate and persist + log it.

Internal helpers
----------------
_enter_csp(c, wb, T)     Open Cash-Secured Put position
_enter_cc(c, wb, T)      Open Covered Call position
_enter_strangle(c, wb, T) Open Short Strangle position
_enter_calendar(c, wb, T) Open Calendar position
"""

from datetime import date, timedelta

from config import (
    BUDGET_USD, RISK_FREE_RATE, CALENDAR_NEAR_DAYS, CALENDAR_FAR_DAYS,
)
from market.pricing import bs_put, bs_call
from excel.excel_tracker import (
    append_trade_row, append_strangle_row, append_calendar_row,
)
from strategies import wheel, strangle, calendar


def _enter_csp(c, wb, T: float) -> dict:
    """Open a Cash-Secured Put position in the wheel state."""
    K       = float(c.strike.replace("$", "").replace(",", ""))
    qty     = BUDGET_USD / K
    premium = bs_put(c.spot, K, T, RISK_FREE_RATE, c.iv) * qty
    expiry  = (date.today() + timedelta(days=c.days)).strftime("%d-%b-%Y")

    s = wheel._load(c.asset)
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
    wheel._save(c.asset, s)

    append_trade_row(wb, "📝 Paper Trades", {
        "date":      str(date.today()),
        "type":      "Sell Cash-Secured Put",
        "stage":     "Short Put",
        "days":      c.days,
        "strike":    K,
        "spot_open": c.spot,
        "premium":   round(premium, 4),
        "result":    "Open",
        "notes":     f"AUTO {c.asset} CSP, {c.days}d, "
                     f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
                     f"liq={c.liquidity_tag}",
    })
    return s["open"]


def _enter_cc(c, wb, T: float) -> dict:
    """Open a Covered Call position. Requires wheel state in 'holding'."""
    s = wheel._load(c.asset)
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
    wheel._save(c.asset, s)

    append_trade_row(wb, "📝 Paper Trades", {
        "date":      str(date.today()),
        "type":      "Sell Covered Call",
        "stage":     "Short Call",
        "days":      c.days,
        "strike":    K,
        "spot_open": c.spot,
        "premium":   round(premium, 4),
        "result":    "Open",
        "notes":     f"AUTO {c.asset} CC, {c.days}d, "
                     f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
                     f"liq={c.liquidity_tag}",
    })
    return s["open"]


def _enter_strangle(c, wb, T: float) -> dict:
    """Open a short strangle position."""
    Kp  = c.put_strike
    Kc  = c.call_strike
    qty = BUDGET_USD / c.spot
    pp  = bs_put (c.spot, Kp, T, RISK_FREE_RATE, c.iv) * qty
    cp  = bs_call(c.spot, Kc, T, RISK_FREE_RATE, c.iv) * qty
    tot = pp + cp
    expiry = (date.today() + timedelta(days=c.days)).strftime("%d-%b-%Y")

    s = strangle._load(c.asset)
    s["open"] = {
        "put_strike":    Kp,
        "call_strike":   Kc,
        "total_premium": round(tot, 4),
        "qty":           qty,
        "expiry":        expiry,
        "spot_open":     c.spot,
        "days":          c.days,
        "asset":         c.asset,
    }
    s["total_premium"] = s.get("total_premium", 0.0) + tot
    s["trades"]        = s.get("trades",        0)   + 1
    strangle._save(c.asset, s)

    append_strangle_row(wb, {
        "date":        str(date.today()),
        "type":        "Short Strangle — Open (AUTO)",
        "put_strike":  Kp,
        "call_strike": Kc,
        "spot_open":   c.spot,
        "premium":     round(tot, 4),
        "days":        c.days,
        "result":      "Open",
        "notes":       f"AUTO {c.asset} strangle, {c.days}d, "
                       f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
                       f"liq={c.liquidity_tag or 'N/A'}",
    })
    return s["open"]


def _enter_calendar(c, wb, T: float) -> dict:
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

    s = calendar._load(c.asset)
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
    }
    s["trades"] = s.get("trades", 0) + 1
    calendar._save(c.asset, s)

    append_calendar_row(wb, {
        "date":        str(date.today()),
        "type":        f"{option_type} Calendar — Open (AUTO)",
        "strike":      K,
        "option_type": option_type,
        "spot_open":   c.spot,
        "near_prem":   round(near_prem, 4),
        "far_prem":    round(far_prem,  4),
        "net_debit":   round(net_debit, 4),
        "near_days":   c.days,
        "far_days":    far_days,
        "result":      "Open",
        "notes":       f"AUTO {c.asset} {option_type} calendar, "
                       f"{c.days}d/{far_days}d, "
                       f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr",
    })
    return s["open"]


def enter_trade(c, wb, days: int | None = None) -> dict:
    """
    Open the paper position described by c and persist + log it.

    The strategy tag on the candidate selects the right opener.
    Returns the freshly written open dict.
    """
    days_eff = days or c.days
    T        = days_eff / 365.0

    if c.strategy == "CSP":
        return _enter_csp(c, wb, T)
    if c.strategy == "CC":
        return _enter_cc(c, wb, T)
    if c.strategy == "Strangle":
        return _enter_strangle(c, wb, T)
    if c.strategy in ("Cal-C", "Cal-P"):
        return _enter_calendar(c, wb, T)

    raise ValueError(f"Unsupported strategy '{c.strategy}'")