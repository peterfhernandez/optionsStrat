"""
strategies/automator.py
=======================
Automated strategy selection and paper-trade entry.

Uses the scanner to enumerate cross-asset candidates, filters by

    * yield_ann       ≥ 10 %/yr   (configurable)
    * liquidity_tag   in {"Med", "High"}
    * prob_profit     descending

and automatically opens a paper trade in the first eligible candidate
(skipping anything that conflicts with an already-open position).

If no candidate qualifies, ``run_automation`` returns ``status="no_candidate"``
and does nothing — the caller (or the scheduled task) should retry in
about an hour.

Public API
----------
select_best_candidate(candidates, min_yield, allowed_liquidity, blocked_strategies)
    Pure filter + ranker. No I/O, easy to unit-test.

enter_trade(candidate, wb, days)
    Open the position in the appropriate state file and log it to Excel.
    Returns the persisted ``open`` dict.

run_automation(active_spot, active_iv, active_asset, days, wb,
               min_yield=10.0, allowed_liquidity=("Med","High"),
               silent=False)
    Top-level entry point. Builds candidates, picks one, enters the trade.

Internal helpers
----------------
_blocked_strategies(active_asset)
    Inspect on-disk state files and return the set of strategies that
    cannot currently be opened (because a position is already in flight).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime    import date, timedelta
from typing      import Iterable

from config       import (
    BUDGET_USD, RISK_FREE_RATE, CALENDAR_NEAR_DAYS, CALENDAR_FAR_DAYS,
    SUPPORTED_ASSETS,
)
from market.pricing      import bs_put, bs_call
from ui.display      import hdr, sub, inf, ok, warn, GR, RD, CY, YL, GY, WH, R
from excel.excel_tracker import (
    append_trade_row, append_strangle_row, append_calendar_row,
)

from strategies.scanner  import Candidate, _build_candidates
from strategies          import wheel, strangle, calendar


# Default automation thresholds — exposed as kwargs on run_automation so
# tests and the CLI can override them.
DEFAULT_MIN_YIELD       = 10.0
DEFAULT_ALLOWED_LIQUIDITY = ("Med", "High")


# ── Candidate selection ──────────────────────────────────────────────────────

def select_best_candidate(
    candidates:         Iterable[Candidate],
    min_yield:          float            = DEFAULT_MIN_YIELD,
    allowed_liquidity:  Iterable[str]    = DEFAULT_ALLOWED_LIQUIDITY,
    blocked_strategies: Iterable[str]    = (),
) -> Candidate | None:
    """
    Return the candidate with the highest probability of profit that
    satisfies ``yield_ann >= min_yield`` and has its ``liquidity_tag``
    in ``allowed_liquidity``. Ties are broken by ``yield_ann`` desc.

    ``blocked_strategies`` lets callers exclude strategies that cannot
    currently be entered (e.g. ``"CSP"`` if the wheel already holds an
    open short put).

    Returns ``None`` when no candidate qualifies.
    """
    allowed_set = set(allowed_liquidity)
    blocked_set = set(blocked_strategies)

    eligible = [
        c for c in candidates
        if c.liquidity_tag in allowed_set
        and c.yield_ann   >= min_yield
        and c.strategy   not in blocked_set
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda c: (c.prob_profit, c.yield_ann), reverse=True)
    return eligible[0]


# ── Determine which strategies are not currently enterable ──────────────────

def _blocked_strategies(asset: str) -> set[str]:
    """
    Inspect the on-disk state files for ``asset`` and return the set of
    strategy tags that cannot be opened right now.

    Rules
    -----
    Wheel:
        stage == "no_position" → CSP allowed, CC blocked
        stage == "holding"     → CC  allowed, CSP blocked
        anything else          → both blocked
    Strangle:
        an "open" entry blocks "Strangle"
    Calendar:
        an "open" entry blocks "Cal-C" and "Cal-P"
    """
    blocked: set[str] = set()

    w = wheel._load(asset)
    if w["stage"] == "no_position":
        blocked.add("CC")
    elif w["stage"] == "holding":
        blocked.add("CSP")
    else:
        blocked.update({"CSP", "CC"})

    s = strangle._load(asset)
    if s.get("open"):
        blocked.add("Strangle")

    c = calendar._load(asset)
    if c.get("open"):
        blocked.update({"Cal-C", "Cal-P"})

    return blocked


# ── Trade entry ──────────────────────────────────────────────────────────────

def _enter_csp(c: Candidate, wb, T: float) -> dict:
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


def _enter_cc(c: Candidate, wb, T: float) -> dict:
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


def _enter_strangle(c: Candidate, wb, T: float) -> dict:
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


def _enter_calendar(c: Candidate, wb, T: float) -> dict:
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


def enter_trade(c: Candidate, wb, days: int | None = None) -> dict:
    """
    Open the paper position described by ``c`` and persist + log it.

    The strategy tag on the candidate selects the right opener.
    Returns the freshly written ``open`` dict.
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


# ── Top-level entry point ────────────────────────────────────────────────────

def run_automation(
    active_spot:       float,
    active_iv:         float,
    active_asset:      str,
    days:              int,
    wb,
    min_yield:         float          = DEFAULT_MIN_YIELD,
    allowed_liquidity: Iterable[str]  = DEFAULT_ALLOWED_LIQUIDITY,
    silent:            bool           = False,
    *,
    cal_near_days:     int | None      = None,
    cal_far_days:      int | None      = None,
) -> dict:
    """
    Build candidates across all supported assets, pick the best eligible one,
    and open the paper trade automatically.

    Filters applied
    ---------------
    * liquidity_tag in ``allowed_liquidity``       (default Med + High)
    * yield_ann     >= ``min_yield``                (default 10 %/yr)
    * strategy not currently blocked by an open position

    Ranking: highest probability of profit, ties broken by annualised yield.

    Returns
    -------
    dict with keys:
        status     : "entered" | "no_candidate"
        candidate  : Candidate (only when status == "entered")
        position   : dict      (only when status == "entered")
        considered : int       — how many candidates were generated
        eligible   : int       — how many passed the filters before ranking
    """
    from market.market_data import get_spot_price, get_deribit_iv

    if not silent:
        hdr("Auto-Strategy Runner")
        inf("Min yield",        f"{min_yield:.0f}%/yr")
        inf("Liquidity filter", "/".join(allowed_liquidity))
        inf("Active asset",     active_asset)
        inf("Days to expiry",   f"{days}d")
        inf("Calendar legs",    f"{cal_near_days or CALENDAR_NEAR_DAYS}d/{cal_far_days or CALENDAR_FAR_DAYS}d")

    all_candidates: list[Candidate] = []
    for asset in SUPPORTED_ASSETS:
        if asset == active_asset:
            spot = active_spot
            iv   = active_iv
        else:
            spot = get_spot_price(asset)
            if not spot:
                if not silent:
                    warn(f"Skipping {asset} — price fetch failed")
                continue
            iv = get_deribit_iv(asset, spot, days) or active_iv
        all_candidates.extend(
            _build_candidates(
                asset, spot, iv, days,
                cal_near_days=cal_near_days,
                cal_far_days=cal_far_days,
            )
        )

    blocked = set()
    for asset in SUPPORTED_ASSETS:
        blocked.update(_blocked_strategies(asset))

    pick = select_best_candidate(
        all_candidates,
        min_yield          = min_yield,
        allowed_liquidity  = allowed_liquidity,
        blocked_strategies = blocked,
    )

    eligible_count = sum(
        1 for c in all_candidates
        if c.liquidity_tag in set(allowed_liquidity)
        and c.yield_ann   >= min_yield
        and c.strategy   not in blocked
    )

    if pick is None:
        if not silent:
            warn(
                f"No candidate met the criteria  "
                f"(considered {len(all_candidates)}, "
                f"eligible {eligible_count}). "
                f"Try again in 1 hour."
            )
        return {
            "status":     "no_candidate",
            "candidate":  None,
            "position":   None,
            "considered": len(all_candidates),
            "eligible":   eligible_count,
        }

    position = enter_trade(pick, wb, days)

    if not silent:
        ok(
            f"Auto-entered {pick.strategy} on {pick.asset}  "
            f"strike {pick.strike}  prem ${pick.premium:.2f}  "
            f"P(prof) {pick.prob_profit:.0f}%  yield {pick.yield_ann:.0f}%/yr  "
            f"liq {pick.liquidity_tag}"
        )

    return {
        "status":     "entered",
        "candidate":  pick,
        "position":   position,
        "considered": len(all_candidates),
        "eligible":   eligible_count,
    }
