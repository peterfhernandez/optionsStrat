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
from trading.executor import enter_trade

from database import load_wheel_state
from database.strangle_db import load_strangle_state
from database.calendar_db import load_calendar_state
from strategies.scanner import Candidate, _build_candidates


# Default automation thresholds — exposed as kwargs on run_automation so
# tests and the CLI can override them.
DEFAULT_MIN_YIELD       = 10.0
DEFAULT_ALLOWED_LIQUIDITY = ("Med", "High")
DEFAULT_MIN_PROB        = 90.0

# ── Candidate selection ──────────────────────────────────────────────────────

def select_best_candidate(
    candidates:         Iterable[Candidate],
    min_yield:          float            = DEFAULT_MIN_YIELD,
    min_prob:           float            = DEFAULT_MIN_PROB,
    allowed_liquidity:  Iterable[str]    = DEFAULT_ALLOWED_LIQUIDITY,
    blocked_strategies: Iterable[str]    = (),
) -> Candidate | None:
    """
    Return the candidate with the highest probability of profit that
    satisfies ``yield_ann >= min_yield``, ``prob_profit > min_prob`` and
    has its ``liquidity_tag`` in ``allowed_liquidity``. Ties are broken by
    ``yield_ann`` desc.

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
        and c.prob_profit > min_prob
        and c.strategy   not in blocked_set
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda c: (c.prob_profit, c.yield_ann), reverse=True)
    return eligible[0]


# ── Determine which strategies are not currently enterable ──────────────────

def _blocked_strategies(asset: str) -> set[str]:
    """
    Inspect database state for ``asset`` and return the set of
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

    # Wheel state from database
    w = load_wheel_state(asset)
    if w["stage"] == "no_position":
        blocked.add("CC")
    elif w["stage"] == "holding":
        blocked.add("CSP")
    else:
        blocked.update({"CSP", "CC"})

    # Strangle state from database
    s = load_strangle_state(asset)
    if s.get("open"):
        blocked.add("Strangle")

    # Calendar state from database
    c = load_calendar_state(asset)
    if c.get("open"):
        blocked.update({"Cal-C", "Cal-P"})

    return blocked


# ── Top-level entry point ────────────────────────────────────────────────────

def run_automation(
    active_spot:       float,
    active_iv:         float,
    active_asset:      str,
    days:              int,
    wb,
    min_yield:         float          = DEFAULT_MIN_YIELD,
    min_prob:          float          = DEFAULT_MIN_PROB,
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
    * prob_profit   >  ``min_prob``                 (default 90 %)
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
        inf("Min prob",         f">{min_prob:.0f}%")
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
        min_prob           = min_prob,
        allowed_liquidity  = allowed_liquidity,
        blocked_strategies = blocked,
    )

    eligible_count = sum(
        1 for c in all_candidates
        if c.liquidity_tag in set(allowed_liquidity)
        and c.yield_ann   >= min_yield
        and c.prob_profit > min_prob
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
