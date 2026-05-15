"""
strategies/spread.py
====================
Credit Spread paper trading simulator.

Two spread types:
  BPS (Bull Put Spread)  — sell OTM put + buy further OTM put.
                           Profit if spot stays above the short put strike.
  BCS (Bear Call Spread) — sell OTM call + buy further OTM call.
                           Profit if spot stays below the short call strike.

In both cases:
  Max profit = net credit received
  Max loss   = (strike width × qty) − net credit
  Breakeven  = short strike ± net credit/qty

Public API
----------
show_spread_analysis(asset, spot, iv, days)
    Display a strike-pair analysis table for both BPS and BCS.

spread_paper_menu(asset, spot, iv, days)
    Interactive paper trading simulator for credit spreads.

Internal helpers
----------------
_spread_pnl(spot_at_expiry, ...)    P&L at expiry for a credit spread
_spread_breakeven(...)              Breakeven price
check_spread_status(spot, iv, days, op)
                                    Evaluate current position status
"""

from datetime import date

from config import (
    BUDGET_USD, RISK_FREE_RATE, OTM_LEVELS, SPREAD_WIDTH_PCT,
)
from database.spread_db import (
    load_spread_state,
    save_spread_state,
    close_spread_trade,
)
from market.pricing import bs_put, bs_call, prob_otm_put, prob_otm_call, round_strike
from trading.executor import enter_trade
from ui.display import (
    hdr, sub, inf, ok, warn,
    GR, RD, CY, YL, GY, WH, R,
)


# ── P&L helpers ───────────────────────────────────────────────────────────────

def _spread_pnl(
    spot_at_expiry: float,
    spread_type: str,
    short_strike: float,
    long_strike: float,
    net_credit: float,
    qty: float,
) -> float:
    """P&L of a credit spread at expiry for a given underlying price."""
    if spread_type == "BPS":
        short_loss = max(short_strike - spot_at_expiry, 0) * qty
        long_gain  = max(long_strike  - spot_at_expiry, 0) * qty
        return net_credit - short_loss + long_gain
    else:  # BCS
        short_loss = max(spot_at_expiry - short_strike, 0) * qty
        long_gain  = max(spot_at_expiry - long_strike,  0) * qty
        return net_credit - short_loss + long_gain


def _spread_breakeven(spread_type: str, short_strike: float, net_credit: float, qty: float) -> float:
    """Breakeven price at expiry."""
    credit_per_unit = net_credit / qty if qty > 0 else 0.0
    if spread_type == "BPS":
        return short_strike - credit_per_unit
    return short_strike + credit_per_unit


def _current_spread_value(
    spot: float,
    iv: float,
    days_left: int,
    spread_type: str,
    short_strike: float,
    long_strike: float,
    qty: float,
) -> float:
    """Current mark-to-market cost to close the spread (what we owe to close)."""
    T = max(days_left / 365.0, 1 / 365.0)
    if spread_type == "BPS":
        short_val = bs_put(spot, short_strike, T, RISK_FREE_RATE, iv) * qty
        long_val  = bs_put(spot, long_strike,  T, RISK_FREE_RATE, iv) * qty
    else:
        short_val = bs_call(spot, short_strike, T, RISK_FREE_RATE, iv) * qty
        long_val  = bs_call(spot, long_strike,  T, RISK_FREE_RATE, iv) * qty
    # We are short the spread: cost to close = short_val - long_val
    return max(short_val - long_val, 0.0)


# ── Status checker ────────────────────────────────────────────────────────────

def check_spread_status(
    spot: float,
    iv: float,
    days: int,
    op: dict,
) -> tuple[str, float, float, str]:
    """
    Evaluate current status of an open credit spread position.

    Returns
    -------
    tuple of (status, current_cost, pnl, message)
        status       : "ok" | "warn" | "stop" | "profit"
        current_cost : float  Cost to close the spread now
        pnl          : float  Unrealised P&L
        message      : str    Human-readable status
    """
    spread_type  = op["spread_type"]
    short_strike = op["short_strike"]
    long_strike  = op["long_strike"]
    net_credit   = op["net_credit"]
    qty          = op["qty"]
    max_loss     = op["max_loss"]

    cur_cost = _current_spread_value(spot, iv, days, spread_type, short_strike, long_strike, qty)
    pnl      = net_credit - cur_cost

    # Stop-loss: current loss ≥ 100% of max_loss
    if max_loss > 0 and cur_cost >= max_loss:
        msg = (
            f"STOP-LOSS TRIGGERED  spread now costs ${cur_cost:.2f} to close "
            f"(max loss ${max_loss:.2f}).  Est. loss: ${pnl:.2f}"
        )
        return "stop", cur_cost, pnl, msg

    # Warn at 75% of max loss
    if max_loss > 0 and cur_cost >= max_loss * 0.75:
        msg = (
            f"Stop WARNING  spread costs ${cur_cost:.2f} to close "
            f"({cur_cost/max_loss*100:.0f}% of max loss).  "
            f"Stop triggers at ${max_loss:.2f}."
        )
        return "warn", cur_cost, pnl, msg

    # Take-profit: spread has lost ≥90% of value (keep ≥90% of credit)
    if net_credit > 0 and cur_cost <= net_credit * 0.10:
        msg = (
            f"Take-profit zone  spread now worth only ${cur_cost:.2f} to close.  "
            f"Est. gain: ${pnl:.2f} ({pnl/net_credit*100:.0f}% of credit captured)"
        )
        return "profit", cur_cost, pnl, msg

    msg = (
        f"Spread cost to close: ${cur_cost:.2f}  |  "
        f"Credit: ${net_credit:.2f}  |  P&L: ${pnl:.2f}"
    )
    return "ok", cur_cost, pnl, msg


# ── Analysis display ──────────────────────────────────────────────────────────

def show_spread_analysis(
    asset: str,
    spot: float,
    iv: float,
    days: int,
) -> None:
    """
    Display Bull Put Spread and Bear Call Spread analysis tables.

    Shows net credit, max loss, breakeven, and P(Profit) for each OTM level.
    """
    hdr(f"Credit Spread Analysis — {asset}")
    inf("Spot", f"${spot:,.2f}")
    inf("IV",   f"{iv*100:.0f}%")
    inf("Days", str(days))
    inf("Budget", f"${BUDGET_USD:,.0f}")
    inf("Spread width", f"{SPREAD_WIDTH_PCT*100:.0f}% OTM offset for long leg")
    print()

    T   = days / 365.0
    r   = RISK_FREE_RATE
    qty = BUDGET_USD / spot

    for spread_type, label in (("BPS", "Bull Put Spread"), ("BCS", "Bear Call Spread")):
        sub(label)
        print(
            f"\n  {'OTM%':<7}{'Short K':<12}{'Long K':<12}"
            f"{'Net Credit':<13}{'Max Loss':<11}{'Breakeven':<13}"
            f"{'Yld/yr':<10}{'P(Profit)'}"
        )
        print(f"  {'─' * 87}")

        for otm in OTM_LEVELS:
            if spread_type == "BPS":
                short_k = round_strike(spot * (1 - otm), 1)
                long_k  = round_strike(spot * (1 - otm - SPREAD_WIDTH_PCT), 1)
                short_p = bs_put(spot, short_k, T, r, iv) * qty
                long_p  = bs_put(spot, long_k,  T, r, iv) * qty
                net_cr  = short_p - long_p
                max_ls  = (short_k - long_k) * qty - net_cr
                be      = short_k - net_cr / qty
                pop     = prob_otm_put(spot, short_k, T, r, iv) * 100
            else:
                short_k = round_strike(spot * (1 + otm), 1)
                long_k  = round_strike(spot * (1 + otm + SPREAD_WIDTH_PCT), 1)
                short_p = bs_call(spot, short_k, T, r, iv) * qty
                long_p  = bs_call(spot, long_k,  T, r, iv) * qty
                net_cr  = short_p - long_p
                max_ls  = (long_k - short_k) * qty - net_cr
                be      = short_k + net_cr / qty
                pop     = prob_otm_call(spot, short_k, T, r, iv) * 100

            yld = (net_cr / (max_ls + net_cr)) * (365 / days) * 100 if max_ls + net_cr > 0 else 0.0
            pc  = GR if pop >= 70 else YL if pop >= 55 else WH

            print(
                f"  {otm*100:.0f}%{'':4}{WH}${short_k:>9,.2f}  ${long_k:>9,.2f}  "
                f"{GR}${net_cr:>8.2f}{WH}     ${max_ls:>7.2f}    "
                f"${be:>9,.2f}    {yld:>6.1f}%/yr  "
                f"{pc}{pop:>5.1f}%{R}"
            )
        print()

    print(
        f"  {GY}Net Credit = max profit (received up front)  |  "
        f"Max Loss = strike width × qty − credit{R}"
    )
    print(
        f"  {GY}BPS profits when spot > short put strike at expiry  |  "
        f"BCS profits when spot < short call strike{R}"
    )
    print(f"  {GY}Qty based on ${BUDGET_USD:.0f} budget / spot  |  "
          f"Black-Scholes estimates  |  {days}d to expiry{R}\n")


# ── Paper trading menu ────────────────────────────────────────────────────────

def _days_remaining(expiry_str: str) -> int:
    from datetime import datetime
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return max((datetime.strptime(expiry_str.strip(), fmt).date() - date.today()).days, 0)
        except ValueError:
            continue
    return 0


def _show_status_bar(op: dict, spot: float, iv: float) -> None:
    """Print a coloured status bar for the open spread position."""
    days_left   = _days_remaining(op.get("expiry", ""))
    spread_type = op["spread_type"]
    short_k     = op["short_strike"]
    long_k      = op["long_strike"]
    net_credit  = op["net_credit"]
    max_loss    = op["max_loss"]
    qty         = op["qty"]

    cur_cost = _current_spread_value(spot, iv, days_left, spread_type, short_k, long_k, qty)
    pnl      = net_credit - cur_cost

    pct_of_max = cur_cost / max_loss if max_loss > 0 else 0.0
    bar_len    = 30
    filled     = min(int(pct_of_max * bar_len), bar_len)
    col        = GR if pct_of_max < 0.5 else YL if pct_of_max < 0.75 else RD
    bar        = f"[{col}{'█' * filled}{'░' * (bar_len - filled)}{R}]"

    pnl_col = GR if pnl >= 0 else RD
    label   = f"{spread_type}  Short ${short_k:,.2f} / Long ${long_k:,.2f}"
    print(f"\n  {WH}{label}{R}")
    print(
        f"  Credit: {GR}${net_credit:.2f}{R}  |  "
        f"Close cost: ${cur_cost:.2f}  |  "
        f"P&L: {pnl_col}${pnl:.2f}{R}  |  "
        f"{days_left}d left"
    )
    print(f"  Max-loss bar {bar}  {pct_of_max*100:.0f}% of max loss")


def spread_paper_menu(
    asset: str,
    spot: float,
    iv: float,
    days: int,
) -> None:
    """
    Interactive paper trading simulator for credit spreads.

    Allows the user to open and manage Bull Put Spread or Bear Call Spread
    positions, simulating credits received and P&L at close.
    """
    state = load_spread_state(asset)
    T     = days / 365.0
    r     = RISK_FREE_RATE
    qty   = BUDGET_USD / spot

    while True:
        op = state.get("open")
        hdr(f"Credit Spread — {asset}  ${spot:,.2f}  IV:{iv*100:.0f}%  {days}d")
        inf("Stats",
            f"Trades: {state.get('trades', 0)}  "
            f"Wins: {state.get('wins', 0)}  "
            f"Losses: {state.get('losses', 0)}  "
            f"Total credit: ${state.get('net_credit', 0.0):.2f}")

        if op:
            _show_status_bar(op, spot, iv)
            print(f"""
  {CY}[1]{R}  Close at expiry  (record full credit as profit/loss)
  {CY}[2]{R}  Early close      (close spread now at current value)
  {CY}[3]{R}  Refresh status
  {CY}[0]{R}  Back
""")
        else:
            print(f"""
  {GY}No open position.{R}

  {CY}[1]{R}  Open Bull Put Spread  (sell put + buy lower put)
  {CY}[2]{R}  Open Bear Call Spread (sell call + buy higher call)
  {CY}[3]{R}  Show analysis
  {CY}[0]{R}  Back
""")

        choice = input(f"  {YL}Choice: {R}").strip()

        # ── No open position ──────────────────────────────────────────────────
        if not op:
            if choice == "1":
                _open_spread_menu(state, asset, spot, iv, days, "BPS", T, r, qty)

            elif choice == "2":
                _open_spread_menu(state, asset, spot, iv, days, "BCS", T, r, qty)

            elif choice == "3":
                show_spread_analysis(asset, spot, iv, days)

            elif choice == "0":
                break
            else:
                warn("Enter 0–3")

        # ── Position open ─────────────────────────────────────────────────────
        else:
            if choice == "1":
                _close_at_expiry(state, asset, spot, iv)

            elif choice == "2":
                _close_early(state, asset, spot, iv)

            elif choice == "3":
                status, cost, pnl, msg = check_spread_status(spot, iv, days, op)
                col = GR if status in ("ok", "profit") else YL if status == "warn" else RD
                print(f"\n  {col}{msg}{R}\n")

            elif choice == "0":
                break
            else:
                warn("Enter 0–3")


def _open_spread_menu(
    state: dict,
    asset: str,
    spot: float,
    iv: float,
    days: int,
    spread_type: str,
    T: float,
    r: float,
    qty: float,
) -> None:
    """Prompt user to choose OTM level and open the spread."""
    from database.spread_db import create_spread_trade
    from datetime import timedelta, datetime

    label = "Bull Put Spread" if spread_type == "BPS" else "Bear Call Spread"
    sub(f"Open {label}")
    print(f"\n  {'#':<4}{'OTM%':<8}{'Short K':<12}{'Long K':<12}{'Net Credit':<13}{'Max Loss'}")
    print(f"  {'─' * 60}")

    options = []
    for i, otm in enumerate(OTM_LEVELS, 1):
        if spread_type == "BPS":
            sk = round_strike(spot * (1 - otm), 1)
            lk = round_strike(spot * (1 - otm - SPREAD_WIDTH_PCT), 1)
            sp = bs_put(spot, sk, T, r, iv) * qty
            lp = bs_put(spot, lk, T, r, iv) * qty
        else:
            sk = round_strike(spot * (1 + otm), 1)
            lk = round_strike(spot * (1 + otm + SPREAD_WIDTH_PCT), 1)
            sp = bs_call(spot, sk, T, r, iv) * qty
            lp = bs_call(spot, lk, T, r, iv) * qty

        net_cr = sp - lp
        width  = abs(sk - lk)
        max_ls = width * qty - net_cr
        options.append((sk, lk, net_cr, max_ls))
        print(
            f"  [{i}]  {otm*100:.0f}%{'':4}${sk:>9,.2f}  ${lk:>9,.2f}  "
            f"{GR}${net_cr:>8.2f}{R}     ${max_ls:>7.2f}"
        )

    print()
    raw = input(f"  {YL}Select level [1–{len(OTM_LEVELS)}] or Enter to cancel: {R}").strip()
    if not raw or not raw.isdigit() or not (1 <= int(raw) <= len(OTM_LEVELS)):
        warn("Cancelled.")
        return

    idx = int(raw) - 1
    sk, lk, net_cr, max_ls = options[idx]

    expiry_dt = date.today() + __import__("datetime").timedelta(days=days)
    expiry    = expiry_dt.strftime("%d-%b-%Y")

    trade = create_spread_trade(
        asset=asset,
        spread_type=spread_type,
        date_open=date.today(),
        short_strike=sk,
        long_strike=lk,
        spot_open=spot,
        net_credit=round(net_cr, 4),
        max_loss=round(max_ls, 4),
        qty=qty,
        days=days,
        expiry=expiry,
        notes=f"Paper {label} @ {asset} {spot:.2f} IV={iv*100:.0f}%",
    )

    state["open"] = {
        "spread_type":  spread_type,
        "short_strike": sk,
        "long_strike":  lk,
        "net_credit":   round(net_cr, 4),
        "max_loss":     round(max_ls, 4),
        "qty":          qty,
        "expiry":       expiry,
        "spot_open":    spot,
        "days":         days,
        "asset":        asset,
        "trade_id":     trade.id,
    }
    state["net_credit"] = state.get("net_credit", 0.0) + net_cr
    state["trades"]     = state.get("trades", 0) + 1
    save_spread_state(asset, state)
    ok(f"{label} opened  |  Short ${sk:,.2f} / Long ${lk:,.2f}  |  Credit: ${net_cr:.2f}")


def _close_at_expiry(state: dict, asset: str, spot: float, iv: float) -> None:
    """Close spread at expiry: intrinsic value determines final P&L."""
    op          = state["open"]
    spread_type = op["spread_type"]
    short_k     = op["short_strike"]
    long_k      = op["long_strike"]
    net_credit  = op["net_credit"]
    qty         = op["qty"]

    pnl    = _spread_pnl(spot, spread_type, short_k, long_k, net_credit, qty)
    result = "Win" if pnl >= 0 else "Loss"
    note   = f"Closed at expiry. Spot=${spot:.2f}  P&L=${pnl:.2f}"

    _finalise_close(state, asset, spot, pnl, result, note)
    col = GR if pnl >= 0 else RD
    ok(f"Closed at expiry  |  P&L: {col}${pnl:.2f}{R}")


def _close_early(state: dict, asset: str, spot: float, iv: float) -> None:
    """Close spread early at current mark-to-market value."""
    op          = state["open"]
    spread_type = op["spread_type"]
    short_k     = op["short_strike"]
    long_k      = op["long_strike"]
    net_credit  = op["net_credit"]
    qty         = op["qty"]
    days_left   = _days_remaining(op.get("expiry", ""))

    cur_cost = _current_spread_value(spot, iv, days_left, spread_type, short_k, long_k, qty)
    pnl      = net_credit - cur_cost
    result   = "Win" if pnl >= 0 else "Loss"
    note     = f"Early close. Spot=${spot:.2f}  Cost=${cur_cost:.2f}  P&L=${pnl:.2f}"

    col = GR if pnl >= 0 else RD
    confirm = input(
        f"  Close now?  Cost: ${cur_cost:.2f}  P&L: {col}${pnl:.2f}{R}  [y/N]: "
    ).strip().lower()
    if confirm != "y":
        warn("Cancelled.")
        return

    _finalise_close(state, asset, spot, pnl, result, note)
    ok(f"Early close recorded  |  P&L: {col}${pnl:.2f}{R}")


def _finalise_close(
    state: dict, asset: str, spot: float, pnl: float, result: str, note: str
) -> None:
    """Common close logic: update DB, update state."""
    op       = state["open"]
    trade_id = op.get("trade_id")

    if trade_id:
        close_spread_trade(
            trade_id=trade_id,
            date_close=date.today(),
            spot_close=spot,
            pnl=round(pnl, 4),
            result=result,
            notes=note,
        )

    if pnl >= 0:
        state["wins"]   = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1

    state["open"] = None
    save_spread_state(asset, state)
