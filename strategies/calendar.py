"""
strategies/calendar.py
======================
Calendar Spread paper trading simulator.

A calendar spread (time spread / horizontal spread) buys a longer-dated option
and sells a shorter-dated option at the same strike.  The maximum loss is the
net debit paid at entry; the maximum profit is achieved when the underlying is
near the strike at near-leg expiry (short leg expires worthless, long leg
retains maximum remaining time value).

Public API
----------
show_calendar_analysis(asset, spot, iv, days)
    Display a table of strike options with net debit, max profit, profit range,
    and estimated probability of profit.  Offers to draw an ASCII P&L chart.

calendar_paper_menu(asset, spot, iv, wb, days)
    Interactive paper trading simulator for the calendar spread strategy.

Internal helpers
----------------
_state_file(asset)                  Asset-specific JSON state file path
_load(asset)                        Load calendar state from disk
_save(asset, state)                 Persist calendar state to disk
_spread_value(spot, ...)            Current mark value of the calendar spread
_pnl_at_near_expiry(spot_close, ...) P&L given a closing price at near expiry
_find_breakevens(...)               Numerically locate lower/upper breakeven prices
check_calendar_status(...)          Evaluate stop / take-profit / warn conditions
"""

import json
import os
from datetime import date, timedelta

from config import (
    BUDGET_USD, RISK_FREE_RATE, OTM_LEVELS,
    CALENDAR_NEAR_DAYS, CALENDAR_FAR_DAYS, CALENDAR_STOP_PCT,
)
from market.pricing import bs_put, bs_call, prob_otm_put, prob_otm_call
from ui.display import (
    hdr, sub, inf, ok, warn,
    draw_calendar_zone,
    GR, RD, CY, YL, GY, WH, R,
)
from excel.excel_tracker import append_calendar_row


# ── State persistence ─────────────────────────────────────────────────────────

def _state_file(asset: str) -> str:
    """Return asset-specific state file path e.g. 'calendar_state_ETH.json'."""
    return f"calendar_state_{asset.upper()}.json"


def _load(asset: str) -> dict:
    """Load calendar state from disk, or return a fresh default state."""
    path = _state_file(asset)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"open": None, "total_pnl": 0.0, "wins": 0, "losses": 0, "trades": 0}


def _save(asset: str, state: dict) -> None:
    """Persist calendar state to disk."""
    with open(_state_file(asset), "w") as f:
        json.dump(state, f, indent=2)


# ── P&L helpers ───────────────────────────────────────────────────────────────

def _spread_value(
    spot: float,
    strike: float,
    T_near: float,
    T_far: float,
    r: float,
    iv: float,
    qty: float,
    option_type: str,
) -> float:
    """
    Current mark-to-market value of an open calendar spread.

    Returns (far_leg_value - near_leg_value) in USD.
    Positive when the far leg is worth more than the short near leg (normal).
    """
    if option_type == "Call":
        far_val  = bs_call(spot, strike, T_far,  r, iv) * qty
        near_val = bs_call(spot, strike, T_near, r, iv) * qty
    else:
        far_val  = bs_put(spot, strike, T_far,  r, iv) * qty
        near_val = bs_put(spot, strike, T_near, r, iv) * qty
    return far_val - near_val


def _pnl_at_near_expiry(
    spot_close: float,
    strike: float,
    near_days: int,
    far_days: int,
    r: float,
    iv: float,
    qty: float,
    net_debit: float,
    option_type: str,
) -> float:
    """
    Estimated P&L at near-leg expiry for a given closing spot price.

    Near leg is settled at intrinsic value (if ITM) or zero (if OTM).
    Far leg is sold at Black-Scholes with the remaining time (far - near days).
    """
    T_remaining = max(far_days - near_days, 1) / 365.0
    if option_type == "Call":
        near_cost = max(spot_close - strike, 0) * qty
        far_val   = bs_call(spot_close, strike, T_remaining, r, iv) * qty
    else:
        near_cost = max(strike - spot_close, 0) * qty
        far_val   = bs_put(spot_close, strike, T_remaining, r, iv) * qty
    return far_val - near_cost - net_debit


def _find_breakevens(
    spot: float,
    strike: float,
    near_days: int,
    far_days: int,
    r: float,
    iv: float,
    qty: float,
    net_debit: float,
    option_type: str,
    n_steps: int = 800,
) -> tuple[float, float]:
    """
    Numerically locate lower and upper breakeven prices at near-leg expiry.

    Scans spot * [0.50 … 1.50] for sign changes in P&L.
    Returns (be_lo, be_hi); both are 0.0 if no crossings are found.
    """
    lo = spot * 0.50
    hi = spot * 1.50
    step = (hi - lo) / n_steps
    prices = [lo + i * step for i in range(n_steps + 1)]
    pnls = [
        _pnl_at_near_expiry(p, strike, near_days, far_days, r, iv, qty, net_debit, option_type)
        for p in prices
    ]

    be_lo = be_hi = 0.0
    for i in range(len(pnls) - 1):
        if pnls[i] < 0 <= pnls[i + 1]:
            be_lo = prices[i]
        if pnls[i] >= 0 > pnls[i + 1]:
            be_hi = prices[i + 1]
    return be_lo, be_hi


# ── Status checker ────────────────────────────────────────────────────────────

def check_calendar_status(
    spot: float,
    iv: float,
    near_days_left: int,
    far_days_left: int,
    op: dict,
) -> tuple[str, float, float, str]:
    """
    Evaluate the current status of an open calendar spread position.

    Parameters
    ----------
    spot           : float  Current underlying price
    iv             : float  Current implied volatility (decimal)
    near_days_left : int    Days remaining until near-leg expiry
    far_days_left  : int    Days remaining until far-leg expiry
    op             : dict   Open position dict from state

    Returns
    -------
    tuple of (status, spread_val, pct_of_debit, message)
        status        : "ok" | "warn" | "stop" | "tp"
        spread_val    : float  Current spread mark value (USD)
        pct_of_debit  : float  spread_val / net_debit paid at entry
        message       : str    Human-readable status description
    """
    T_near = max(near_days_left / 365.0, 1 / 365.0)
    T_far  = max(far_days_left  / 365.0, 1 / 365.0)
    net_debit   = op["net_debit"]
    qty         = op["qty"]
    strike      = op["strike"]
    option_type = op["option_type"]

    sv  = _spread_value(spot, strike, T_near, T_far, RISK_FREE_RATE, iv, qty, option_type)
    pct = sv / net_debit if net_debit > 0 else 0.0
    pnl = sv - net_debit

    if pct <= CALENDAR_STOP_PCT:
        msg = (
            f"STOP  spread worth ${sv:.2f} ({pct*100:.0f}% of debit paid).  "
            f"Est. loss: ${abs(pnl):.2f}"
        )
        return "stop", sv, pct, msg

    if pct >= 1.50:
        msg = (
            f"TAKE-PROFIT  spread worth ${sv:.2f} ({pct*100:.0f}% of debit paid).  "
            f"Est. gain: ${pnl:.2f}"
        )
        return "tp", sv, pct, msg

    if pct <= 0.70:
        msg = (
            f"WARN  spread worth ${sv:.2f} ({pct*100:.0f}% of debit).  "
            f"Hard stop at {CALENDAR_STOP_PCT*100:.0f}%."
        )
        return "warn", sv, pct, msg

    msg = f"OK  {pct*100:.0f}% of debit  (stop at {CALENDAR_STOP_PCT*100:.0f}%)"
    return "ok", sv, pct, msg


# ── Strike analysis ───────────────────────────────────────────────────────────

def show_calendar_analysis(
    asset: str,
    spot: float,
    iv: float,
    days: int                      = None,
    *,
    near_days: int | None          = None,
    far_days:  int | None          = None,
) -> None:
    """
    Display a table of calendar spread options with net debit, max profit,
    profit zone, and estimated probability of profit.

    The calendar has its own near/far horizons (independent of the global
    wheel/strangle expiry). Defaults come from
    ``config.CALENDAR_NEAR_DAYS`` (7) and ``config.CALENDAR_FAR_DAYS`` (30)
    and can be overridden per-call or via main-menu toggles [4] and [5].

    Parameters
    ----------
    asset     : str   Underlying asset symbol (e.g. "ETH")
    spot      : float Current spot price
    iv        : float Implied volatility (decimal)
    days      : int   Backwards-compatible alias for ``near_days``
    near_days : int   Short-leg horizon (overrides ``days`` if given)
    far_days  : int   Long-leg  horizon (defaults to CALENDAR_FAR_DAYS)
    """
    near_days = near_days if near_days is not None else (days if days is not None else CALENDAR_NEAR_DAYS)
    far_days  = far_days  if far_days  is not None else CALENDAR_FAR_DAYS
    days      = near_days  # internal name used throughout the function

    if days >= far_days:
        warn(
            f"Near expiry ({days}d) must be shorter than far expiry ({far_days}d). "
            f"Adjust horizons via main-menu options [4] / [5]."
        )
        return

    T_near = days     / 365.0
    T_far  = far_days / 365.0
    T_rem  = max(far_days - days, 1) / 365.0
    r      = RISK_FREE_RATE
    qty    = BUDGET_USD / spot

    hdr(f"Calendar Spread Analysis — {asset}  {days}d / {far_days}d expiry")
    inf(f"{asset} Spot",  f"${spot:,.2f}")
    inf("IV",             f"{iv * 100:.1f}%")
    inf("Near expiry",    f"{days} days   (short leg — sell this)")
    inf("Far expiry",     f"{far_days} days  (long leg — buy this)")
    inf("Budget",         f"${BUDGET_USD:.0f}   (net debit = max loss)")
    inf("Strategy edge",  "Theta decay differential + IV term-structure")

    best_call = best_put = None

    for option_type in ("Call", "Put"):
        sub(
            f"{option_type} Calendar Spreads  "
            f"{'(neutral-to-bullish)' if option_type == 'Call' else '(neutral-to-bearish)'}"
        )
        print(
            f"\n  {'OTM%':<7}{'Strike':<12}{'Near Prem':<12}{'Far Prem':<12}"
            f"{'Net Debit':<12}{'Max Profit':<13}{'Profit Range':<28}{'P(Profit)'}"
        )
        print(f"  {'─' * 108}")

        for otm in [0.00] + OTM_LEVELS:
            if option_type == "Call":
                K = round(spot * (1 + otm) / 10) * 10
            else:
                K = round(spot * (1 - otm) / 10) * 10

            if option_type == "Call":
                near_prem = bs_call(spot, K, T_near, r, iv) * qty
                far_prem  = bs_call(spot, K, T_far,  r, iv) * qty
                max_far   = bs_call(K,    K, T_rem,  r, iv) * qty   # spot = K at near expiry
            else:
                near_prem = bs_put(spot, K, T_near, r, iv) * qty
                far_prem  = bs_put(spot, K, T_far,  r, iv) * qty
                max_far   = bs_put(K,    K, T_rem,  r, iv) * qty

            net_debit  = far_prem - near_prem
            max_profit = max_far - net_debit

            be_lo, be_hi = _find_breakevens(spot, K, days, far_days, r, iv, qty, net_debit, option_type)

            if be_lo > 0 and be_hi > 0:
                profit_range = f"${be_lo:,.0f} – ${be_hi:,.0f}"
                p_lo = prob_otm_put (spot, be_lo, T_near, r, iv)
                p_hi = prob_otm_call(spot, be_hi, T_near, r, iv)
                pop  = max(0.0, (p_lo + p_hi - 1) * 100)
            else:
                profit_range = "—"
                pop = 0.0

            highlight = (otm == 0.00)
            c = GR if highlight else WH
            otm_str = "ATM" if otm == 0.00 else f"{otm*100:.0f}%"
            print(
                f"  {c}{otm_str:<7}${K:>8,.0f}   "
                f"${near_prem:>8.2f}   ${far_prem:>8.2f}   "
                f"${net_debit:>8.2f}   "
                f"${max_profit:>8.2f}   "
                f"{profit_range:<28}{pop:.0f}%{R}"
            )

            if otm == 0.00:
                if option_type == "Call":
                    best_call = (K, near_prem, far_prem, net_debit, max_profit, qty)
                else:
                    best_put  = (K, near_prem, far_prem, net_debit, max_profit, qty)

    print(f"\n  {GY}* Premiums via Black-Scholes  |  qty ≈ ${BUDGET_USD:.0f}/spot  |  Max loss = net debit{R}")
    print(f"  {GY}* Profit zone computed at near-leg expiry assuming unchanged IV{R}")
    inf("Best case",  "Spot pins the strike at near expiry — near expires worthless")
    inf("Worst case", "Spot moves far from strike — both legs have similar value")

    # ── Offer to show chart ───────────────────────────────────────────────────
    print()
    resp = input(
        f"  {YL}Show P&L chart?  [c]all / [p]ut / [n]o: {R}"
    ).strip().lower()
    if resp == "c" and best_call:
        K, np_, fp_, nd, mp, q2 = best_call
        draw_calendar_zone(spot, K, nd, q2, days, far_days, iv, "Call")
    elif resp == "p" and best_put:
        K, np_, fp_, nd, mp, q2 = best_put
        draw_calendar_zone(spot, K, nd, q2, days, far_days, iv, "Put")


# ── Paper trading simulator ───────────────────────────────────────────────────

def calendar_paper_menu(
    asset: str,
    spot: float,
    iv: float,
    wb,
    days: int                      = None,
    *,
    near_days: int | None          = None,
    far_days:  int | None          = None,
) -> None:
    """
    Interactive paper trading simulator for the calendar spread strategy.

    Manages state across sessions via a per-asset JSON file.
    Supports opening, monitoring, closing at near expiry, and early-closing.

    Calendar horizons default to ``config.CALENDAR_NEAR_DAYS`` (7d short
    leg) and ``config.CALENDAR_FAR_DAYS`` (30d long leg). The main menu
    [4] / [5] toggles cycle through ``CALENDAR_NEAR_OPTIONS`` and
    ``CALENDAR_FAR_OPTIONS`` and pass the selected values in here.

    Parameters
    ----------
    asset     : str              Underlying asset symbol
    spot      : float            Current spot price
    iv        : float            Implied volatility (decimal)
    wb        : openpyxl.Workbook
    days      : int              Backwards-compatible alias for ``near_days``
    near_days : int              Short-leg horizon (overrides ``days``)
    far_days  : int              Long-leg horizon
    """
    near_days = near_days if near_days is not None else (days if days is not None else CALENDAR_NEAR_DAYS)
    far_days  = far_days  if far_days  is not None else CALENDAR_FAR_DAYS
    days      = near_days

    if days >= far_days:
        warn(
            f"Near expiry ({days}d) ≥ far expiry ({far_days}d).  "
            f"Adjust horizons via main-menu options [4] / [5]."
        )
        return

    T_near = days     / 365.0
    T_far  = far_days / 365.0
    r      = RISK_FREE_RATE
    qty    = BUDGET_USD / spot
    s      = _load(asset)

    hdr(f"Calendar Spread — {asset} Paper Trading  ({days}d / {far_days}d)")

    total = s["wins"] + s["losses"]
    inf("Trades completed", str(total))
    inf("Wins / Losses",    f"{s['wins']} / {s['losses']}")
    inf("Win Rate",         f"{s['wins'] / total * 100:.1f}%" if total else "N/A")
    inf("Total P&L",        f"${s['total_pnl']:.2f}")

    # ── Show open position details ────────────────────────────────────────────
    if s["open"]:
        op          = s["open"]
        K           = op["strike"]
        opt_type    = op["option_type"]
        net_debit   = op["net_debit"]
        q           = op["qty"]
        near_exp    = op["expiry_near"]
        far_exp     = op["expiry_far"]

        from automation.monitor import _days_remaining
        near_left = _days_remaining(near_exp)
        far_left  = _days_remaining(far_exp)

        T_n = max(near_left / 365.0, 1 / 365.0)
        T_f = max(far_left  / 365.0, 1 / 365.0)

        sv   = _spread_value(spot, K, T_n, T_f, r, iv, q, opt_type)
        pnl  = sv - net_debit
        col  = GR if pnl >= 0 else RD

        status, sv2, pct, st_msg = check_calendar_status(spot, iv, near_left, far_left, op)

        sub(f"Open {opt_type} Calendar — Strike ${K:,.0f}")
        inf("  Near expiry",    f"{near_exp}  ({near_left}d left)")
        inf("  Far expiry",     f"{far_exp}  ({far_left}d left)")
        inf("  Net Debit Paid", f"${net_debit:.2f}  (max loss)")
        inf("  Spread Value Now", f"${sv:.2f}  ({pct*100:.0f}% of debit)")
        inf("  Unrealised P&L", f"{col}${pnl:.2f}{R}")

        # Status bar
        bar_w   = 36
        fill    = min(pct / 2.0, 1.0)   # scale 0 → 200% of debit
        filled  = int(fill * bar_w)
        tp_pos  = int((1.50 / 2.0) * bar_w)
        stop_pos = int((CALENDAR_STOP_PCT / 2.0) * bar_w)
        bar = ""
        for i in range(bar_w):
            if i < filled:
                if i >= tp_pos:    bar += f"{GR}█{R}"
                elif i >= bar_w // 2: bar += f"{YL}█{R}"
                else:              bar += f"{RD}█{R}"
            elif i == tp_pos:      bar += f"{GR}|{R}"
            elif i == stop_pos:    bar += f"{RD}|{R}"
            else:                  bar += f"{GY}░{R}"

        print(f"\n  {WH}Spread Value Monitor{R}  {GY}[stop={CALENDAR_STOP_PCT*100:.0f}%  tp=150%]{R}")
        print(f"  {bar}  {WH}{pct*100:.0f}% of debit{R}")

        if status == "stop":
            print(f"\n  {RD}⛔  {st_msg}{R}")
            print(f"  {RD}Consider closing — you have lost ≥50% of the debit paid.{R}")
        elif status == "tp":
            print(f"\n  {GR}🎯  {st_msg}{R}")
            print(f"  {GR}Consider closing to lock in this profit.{R}")
        elif status == "warn":
            print(f"\n  {YL}⚠   {st_msg}{R}")
        else:
            print(f"  {GY}✓   {st_msg}{R}")

    # ── Menu ──────────────────────────────────────────────────────────────────
    print(f"""
  {CY}[1]{R}  Open new calendar spread
  {CY}[2]{R}  Show P&L chart  {GY}(open position){R}
  {CY}[3]{R}  Close at near-leg expiry
  {CY}[4]{R}  Close NOW at current price  {RD}(early exit / stop-loss){R}
  {CY}[5]{R}  Back
""")
    choice = input(f"  {YL}Choice: {R}").strip()

    # [1] Open new calendar spread
    if choice == "1":
        if s["open"]:
            warn("Close the existing position before opening a new one.")
            return

        sub("Suggested ATM calendar spread")
        K_sug = round(spot / 10) * 10

        print(f"""
  {GY}Option type:{R}
  {CY}[1]{R}  Call  {GY}(neutral to bullish — profits if spot stays near or rises slightly){R}
  {CY}[2]{R}  Put   {GY}(neutral to bearish — profits if spot stays near or falls slightly){R}
""")
        ot_choice = input(f"  {YL}Type [1=Call / 2=Put, Enter for Call]: {R}").strip()
        option_type = "Put" if ot_choice == "2" else "Call"

        if option_type == "Call":
            near_prem_sug = bs_call(spot, K_sug, T_near, r, iv) * qty
            far_prem_sug  = bs_call(spot, K_sug, T_far,  r, iv) * qty
        else:
            near_prem_sug = bs_put(spot, K_sug, T_near, r, iv) * qty
            far_prem_sug  = bs_put(spot, K_sug, T_far,  r, iv) * qty

        nd_sug = far_prem_sug - near_prem_sug
        inf(f"  Suggested strike (ATM)", f"${K_sug:,.0f}")
        inf(f"  Near leg ({days}d) premium",  f"${near_prem_sug:.2f}  ← you receive this")
        inf(f"  Far leg  ({far_days}d) premium", f"${far_prem_sug:.2f}  ← you pay this")
        inf(f"  Net debit (max loss)",     f"${nd_sug:.2f}")

        K = float(input(f"\n  Strike [Enter for ${K_sug:,.0f}]: $") or K_sug)

        if option_type == "Call":
            near_prem = bs_call(spot, K, T_near, r, iv) * qty
            far_prem  = bs_call(spot, K, T_far,  r, iv) * qty
        else:
            near_prem = bs_put(spot, K, T_near, r, iv) * qty
            far_prem  = bs_put(spot, K, T_far,  r, iv) * qty

        net_debit = far_prem - near_prem
        expiry_near = (date.today() + timedelta(days=days)).strftime("%d-%b-%Y")
        expiry_far  = (date.today() + timedelta(days=far_days)).strftime("%d-%b-%Y")

        s["open"] = {
            "strike":      K,
            "option_type": option_type,
            "near_prem":   round(near_prem, 4),
            "far_prem":    round(far_prem,  4),
            "net_debit":   round(net_debit, 4),
            "qty":         qty,
            "expiry_near": expiry_near,
            "expiry_far":  expiry_far,
            "spot_open":   spot,
            "near_days":   days,
            "far_days":    far_days,
            "asset":       asset,
        }
        s["trades"] += 1
        _save(asset, s)

        ok(
            f"Calendar opened: {option_type} ${K:,.0f}  "
            f"{days}d/{far_days}d  |  Net debit: ${net_debit:.2f}"
        )
        draw_calendar_zone(spot, K, net_debit, qty, days, far_days, iv, option_type)

        append_calendar_row(wb, {
            "date":        str(date.today()),
            "type":        f"{option_type} Calendar — Open",
            "strike":      K,
            "option_type": option_type,
            "spot_open":   spot,
            "near_prem":   round(near_prem, 4),
            "far_prem":    round(far_prem,  4),
            "net_debit":   round(net_debit, 4),
            "near_days":   days,
            "far_days":    far_days,
            "result":      "Open",
            "notes":       f"{asset} {option_type} calendar, {days}d/{far_days}d",
        })

    # [2] Show P&L chart
    elif choice == "2":
        if not s["open"]:
            warn("No open position.")
            return
        op = s["open"]
        draw_calendar_zone(
            spot, op["strike"], op["net_debit"], op["qty"],
            op["near_days"], op["far_days"], iv, op["option_type"],
        )

    # [3] Close at near-leg expiry
    elif choice == "3":
        if not s["open"]:
            warn("No open position.")
            return
        op          = s["open"]
        K           = op["strike"]
        opt_type    = op["option_type"]
        net_debit   = op["net_debit"]
        q           = op["qty"]

        spot_close = float(
            input(f"  {asset} price at near-leg expiry [~${spot:,.0f}]: $") or spot
        )
        pnl = _pnl_at_near_expiry(
            spot_close, K, op["near_days"], op["far_days"],
            r, iv, q, net_debit, opt_type,
        )

        if pnl >= 0:
            result = "Win";  s["wins"]   += 1
            ok(f"Profitable!  P&L: +${pnl:.2f}")
        else:
            result = "Loss"; s["losses"] += 1
            warn(f"Loss.  P&L: ${pnl:.2f}  "
                 f"({'spot moved away from strike' if abs(spot_close - K) > K * 0.10 else 'narrow miss'})")

        s["total_pnl"] += pnl
        append_calendar_row(wb, {
            "date":        str(date.today()),
            "type":        f"{opt_type} Calendar — Expired",
            "strike":      K,
            "option_type": opt_type,
            "spot_open":   op["spot_open"], "spot_close": spot_close,
            "near_prem":   op["near_prem"],
            "far_prem":    op["far_prem"],
            "net_debit":   net_debit,
            "pnl":         round(pnl, 4),
            "near_days":   op["near_days"],
            "far_days":    op["far_days"],
            "result":      result,
            "notes":       f"{asset} closed at near expiry, spot ${spot_close:,.0f}",
        })
        s["open"] = None
        _save(asset, s)

    # [4] Early close at current mark
    elif choice == "4":
        if not s["open"]:
            warn("No open position.")
            return
        op          = s["open"]
        K           = op["strike"]
        opt_type    = op["option_type"]
        net_debit   = op["net_debit"]
        q           = op["qty"]

        from automation.monitor import _days_remaining
        near_left = _days_remaining(op["expiry_near"])
        far_left  = _days_remaining(op["expiry_far"])

        T_n = max(near_left / 365.0, 1 / 365.0)
        T_f = max(far_left  / 365.0, 1 / 365.0)
        sv  = _spread_value(spot, K, T_n, T_f, r, iv, q, opt_type)
        pnl = sv - net_debit
        pct = sv / net_debit if net_debit > 0 else 0.0

        status, _, _, st_msg = check_calendar_status(spot, iv, near_left, far_left, op)

        sub("Early Close — Mark-to-Market")
        inf("  Net debit paid",          f"${net_debit:.2f}")
        inf("  Spread value now",        f"${sv:.2f}  ({pct*100:.0f}% of debit)")
        col = GR if pnl >= 0 else RD
        inf("  Net P&L if closed now",   f"{col}${pnl:.2f}{R}")

        if status == "stop":
            print(f"\n  {RD}⛔  Stop-loss — {st_msg}{R}")
        elif status == "tp":
            print(f"\n  {GR}🎯  Take-profit — {st_msg}{R}")
        elif status == "warn":
            print(f"\n  {YL}⚠   {st_msg}{R}")
        else:
            print(f"\n  {GY}Position within normal range — early close is optional.{R}")

        confirm = input(f"\n  {YL}Confirm close at current price? (y/n): {R}").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

        if pnl >= 0:
            result = "Win";  s["wins"]   += 1
        else:
            tag = "Stop" if status == "stop" else "Early"
            result = f"Loss ({tag})";  s["losses"] += 1

        s["total_pnl"] += pnl
        note = (
            f"Stop at {pct*100:.0f}% of debit — loss ${abs(pnl):.2f}"
            if status == "stop"
            else f"Early close at {pct*100:.0f}% of debit — P&L ${pnl:.2f}"
        )
        ok(f"Closed.  P&L: {'+'if pnl>=0 else ''}${pnl:.2f}")

        append_calendar_row(wb, {
            "date":        str(date.today()),
            "type":        f"{opt_type} Calendar — Early Close",
            "strike":      K,
            "option_type": opt_type,
            "spot_open":   op["spot_open"], "spot_close": spot,
            "near_prem":   op["near_prem"],
            "far_prem":    op["far_prem"],
            "net_debit":   net_debit,
            "pnl":         round(pnl, 4),
            "near_days":   op["near_days"],
            "far_days":    op["far_days"],
            "result":      result,
            "notes":       f"{asset} {note}",
        })
        s["open"] = None
        _save(asset, s)
