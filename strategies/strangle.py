"""
strategies/strangle.py
======================
Short Strangle paper trading simulator.

A short strangle sells an OTM put and an OTM call simultaneously.
Maximum profit is the combined premium received; loss is unlimited on
the call side if the underlying rallies sharply.

Public API
----------
show_strangle_analysis(asset, spot, iv, days)
    Display a strike-pair analysis table + optional profit zone chart.

strangle_paper_menu(asset, spot, iv, days)
    Interactive paper trading simulator for the short strangle.

Internal helpers
----------------
_pnl(spot_at_expiry, ...)       P&L of strangle at expiry
_breakevens(put_k, call_k, ...) Lower and upper breakeven prices
check_stop_loss(spot, iv, days, op)
                                Evaluate stop-loss status for open position
"""

from datetime import date
from types import SimpleNamespace

from config        import (
    BUDGET_USD, RISK_FREE_RATE, OTM_LEVELS,
    STOP_LOSS_MULTIPLIER, STOP_WARN_MULTIPLIER, DERIBIT_PAPER,
)
from database.strangle_db import (
    load_strangle_state,
    save_strangle_state,
    close_strangle_trade,
)
from market.pricing       import bs_put, bs_call, prob_otm_put, prob_otm_call, round_strike
from trading.executor import enter_trade
from trading.fee_calculator import calculate_fee
from access import DeribitClient
from ui.display       import (
    hdr, sub, inf, ok, warn,
    draw_profit_zone, print_stop_loss_status,
    GR, RD, CY, YL, GY, WH, R,
)


# ── P&L helpers ───────────────────────────────────────────────────────────────

def _pnl(
    spot_at_expiry: float,
    put_strike: float,
    call_strike: float,
    total_premium: float,
    qty: float,
) -> float:
    """P&L of a short strangle at expiry for a given underlying price."""
    put_loss  = max(put_strike  - spot_at_expiry, 0) * qty
    call_loss = max(spot_at_expiry - call_strike, 0) * qty
    return total_premium - put_loss - call_loss


def _breakevens(
    put_strike: float,
    call_strike: float,
    premium_per_unit: float,
) -> tuple[float, float]:
    """Return (lower_breakeven, upper_breakeven) for a short strangle."""
    return put_strike - premium_per_unit, call_strike + premium_per_unit


# ── Stop-loss checker ─────────────────────────────────────────────────────────

def check_stop_loss(
    spot: float,
    iv: float,
    days: int,
    op: dict,
) -> tuple[str, float, float, str]:
    """
    Evaluate stop-loss status for an open strangle position.

    Parameters
    ----------
    spot : float  Current underlying price
    iv   : float  Current implied volatility (decimal)
    days : int    Days remaining to expiry
    op   : dict   Open position dict from state (put_strike, call_strike, etc.)

    Returns
    -------
    tuple of (status, current_value, multiplier, message)
        status       : "ok" | "warn" | "stop"
        current_value: float  Current mark-to-market value of the strangle
        multiplier   : float  current_value / premium_received
        message      : str    Human-readable status message
    """
    T      = max(days / 365.0, 1 / 365.0)
    q      = op["qty"]
    p0     = op["total_premium"]
    Kp     = op["put_strike"]
    Kc     = op["call_strike"]

    cur_pp  = bs_put (spot, Kp, T, RISK_FREE_RATE, iv) * q
    cur_cp  = bs_call(spot, Kc, T, RISK_FREE_RATE, iv) * q
    cur_val = cur_pp + cur_cp
    mult    = cur_val / p0 if p0 > 0 else 0.0

    if mult >= STOP_LOSS_MULTIPLIER:
        pnl = p0 - cur_val
        msg = (
            f"STOP-LOSS TRIGGERED  strangle now worth ${cur_val:.2f} "
            f"({mult:.1f}x premium).  Est. loss if closed now: ${pnl:.2f}"
        )
        return "stop", cur_val, mult, msg

    if mult >= STOP_WARN_MULTIPLIER:
        msg = (
            f"Stop WARNING  strangle now worth ${cur_val:.2f} "
            f"({mult:.1f}x premium).  Stop triggers at "
            f"{STOP_LOSS_MULTIPLIER:.1f}x (${p0 * STOP_LOSS_MULTIPLIER:.2f})."
        )
        return "warn", cur_val, mult, msg

    msg = (
        f"OK  {mult:.2f}x premium  "
        f"(warn at {STOP_WARN_MULTIPLIER:.1f}x, "
        f"stop at {STOP_LOSS_MULTIPLIER:.1f}x)"
    )
    return "ok", cur_val, mult, msg


# ── Strike analysis ───────────────────────────────────────────────────────────

def show_strangle_analysis(
    asset: str,
    spot: float,
    iv: float,
    days: int,
) -> None:
    """
    Display a table of OTM strike pairs with premiums, breakevens, and P(profit).

    Highlights the 15% OTM row as the suggested trade. Offers to show the
    ASCII profit zone chart for that strike pair.

    Parameters
    ----------
    asset : str   Underlying asset symbol (e.g. "ETH", "BTC", "SOL")
    spot  : float Current spot price
    iv    : float Implied volatility (decimal)
    days  : int   Days to expiry
    """
    T   = days / 365.0
    r   = RISK_FREE_RATE
    qty = BUDGET_USD / spot

    hdr(f"Short Strangle Analysis — {asset} {days}-day expiry")
    inf(f"{asset} Spot", f"${spot:,.2f}")
    inf("IV",            f"{iv * 100:.1f}%")
    inf("Budget",        f"${BUDGET_USD:.0f}")

    sub("Strike Pairs (Put OTM% / Call OTM% — symmetric)")
    print(
        f"\n  {'Strikes':<22}{'Put Prem':<11}{'Call Prem':<11}"
        f"{'Combined':<12}{'Yield/yr':<12}{'Lower B/E':<13}{'Upper B/E':<13}{'P(Profit)'}"
    )
    print(f"  {'─' * 100}")

    best_row = None
    for otm in OTM_LEVELS:
        Kp  = round_strike(spot * (1 - otm), spot)
        Kc  = round_strike(spot * (1 + otm), spot)
        pp  = bs_put (spot, Kp, T, r, iv) * qty
        cp  = bs_call(spot, Kc, T, r, iv) * qty
        tot = pp + cp

        # Account for open and estimated close fees for both legs
        open_fee_pp = calculate_fee(spot, pp / qty, asset) * qty
        open_fee_cp = calculate_fee(spot, cp / qty, asset) * qty
        close_fee_est_pp = calculate_fee(spot, 0.01, asset) * qty
        close_fee_est_cp = calculate_fee(spot, 0.01, asset) * qty
        effective_tot = tot - open_fee_pp - open_fee_cp - close_fee_est_pp - close_fee_est_cp
        yld = (effective_tot / BUDGET_USD) * (365 / days) * 100 if effective_tot > 0 else 0.0

        prem_per_unit = effective_tot / qty if qty else 0
        be_lo = Kp - prem_per_unit
        be_hi = Kc + prem_per_unit

        p_lo = prob_otm_put (spot, Kp, T, r, iv)
        p_hi = prob_otm_call(spot, Kc, T, r, iv)
        pop  = max(0.0, (p_lo + p_hi - 1) * 100)

        c = GR if otm == 0.15 else WH
        print(
            f"  {c}${Kp:>7,.0f} / ${Kc:>7,.0f}  "
            f"${pp:>7.2f}  ${cp:>7.2f}  "
            f"${effective_tot:>8.2f}  {yld:>7.1f}%/yr  "
            f"${be_lo:>8,.0f}  ${be_hi:>8,.0f}  {pop:.0f}%{R}"
        )
        if otm == 0.15:
            best_row = (Kp, Kc, pp, cp, effective_tot, qty)

    print(f"\n  {GY}* Premiums estimated via Black-Scholes | qty ≈ ${BUDGET_USD:.0f}/spot{R}")
    warn("Short strangles have unlimited loss potential on the call side — size carefully.")

    if best_row:
        Kp, Kc, pp, cp, tot, qty2 = best_row
        print()
        resp = input(
            f"  {YL}Show profit zone chart for 15% OTM strangle? (y/n): {R}"
        ).strip().lower()
        if resp == "y":
            draw_profit_zone(spot, Kp, Kc, tot, qty2, days, iv)


# ── Paper trading simulator ───────────────────────────────────────────────────

def strangle_paper_menu(
    asset: str,
    spot: float,
    iv: float,
    days: int,
) -> None:
    """
    Interactive paper trading simulator for the short strangle strategy.

    Manages state across sessions via the database. Supports opening,
    monitoring (with stop-loss bar), expiring, and early-closing positions.

    Parameters
    ----------
    asset : str              Underlying asset symbol
    spot  : float            Current spot price
    iv    : float            Implied volatility (decimal)
    days  : int              Days to expiry
    """
    T   = days / 365.0
    s   = load_strangle_state(asset)
    qty = BUDGET_USD / spot

    hdr(f"Short Strangle — {asset} Paper Trading")

    total = s["wins"] + s["losses"]
    inf("Trades completed", str(total))
    inf("Wins / Losses",    f"{s['wins']} / {s['losses']}")
    inf("Win Rate",         f"{s['wins'] / total * 100:.1f}%" if total else "N/A")
    inf("Total Premium",    f"${s['total_premium']:.2f}")

    # ── Show open position details if one exists ──────────────────────────────
    if s["open"]:
        op     = s["open"]
        Kp     = op["put_strike"]
        Kc     = op["call_strike"]
        p0     = op["total_premium"]
        q      = op["qty"]
        cur_pp = bs_put (spot, Kp, T, RISK_FREE_RATE, iv) * q
        cur_cp = bs_call(spot, Kc, T, RISK_FREE_RATE, iv) * q
        cur_val = cur_pp + cur_cp
        unreal  = p0 - cur_val
        colour  = GR if unreal >= 0 else RD

        prem_per_unit = p0 / q
        be_lo, be_hi  = _breakevens(Kp, Kc, prem_per_unit)

        sub("Open Strangle Position")
        inf("  Put Strike",        f"${Kp:,.0f}")
        inf("  Call Strike",       f"${Kc:,.0f}")
        inf("  Expiry",            op.get("expiry", ""))
        inf("  Premium Received",  f"${p0:.2f}")
        inf("  Current Value",     f"${cur_val:.2f}")
        inf("  Unrealised P&L",    f"{colour}${unreal:.2f}{R}")
        inf("  Lower Breakeven",   f"${be_lo:,.0f}")
        inf("  Upper Breakeven",   f"${be_hi:,.0f}")
        inf(f"  {asset} needs to stay", f"${be_lo:,.0f} — ${be_hi:,.0f}")

        sl_status, sl_val, sl_mult, sl_msg = check_stop_loss(spot, iv, days, op)
        print_stop_loss_status(sl_status, sl_val, sl_mult, sl_msg, p0)

    # ── Menu ──────────────────────────────────────────────────────────────────
    print(f"""
  {CY}[1]{R}  Open new strangle
  {CY}[2]{R}  Show profit zone chart  {GY}(open position){R}
  {CY}[3]{R}  Close / expire at expiry price
  {CY}[4]{R}  Close NOW at current price  {RD}(stop-loss / early exit){R}
  {CY}[5]{R}  Adjust stop-loss multiplier  {GY}(currently {STOP_LOSS_MULTIPLIER:.1f}x){R}
  {CY}[6]{R}  Back
""")
    choice = input(f"  {YL}Choice: {R}").strip()

    # [1] Open new strangle
    if choice == "1":
        if s["open"]:
            warn("Close existing position first.")
            return

        sub("Suggested strikes (15% OTM each side)")
        Kp_sug = round_strike(spot * 0.85, spot)
        Kc_sug = round_strike(spot * 1.15, spot)
        pp_sug = bs_put (spot, Kp_sug, T, RISK_FREE_RATE, iv) * qty
        cp_sug = bs_call(spot, Kc_sug, T, RISK_FREE_RATE, iv) * qty
        inf("  Put strike  (15% below)", f"${Kp_sug:,.0f}  →  ${pp_sug:.2f} premium")
        inf("  Call strike (15% above)", f"${Kc_sug:,.0f}  →  ${cp_sug:.2f} premium")
        inf("  Combined premium",        f"${pp_sug + cp_sug:.2f}")

        Kp  = float(input(f"\n  Put strike  [enter for ${Kp_sug:,.0f}]: $") or Kp_sug)
        Kc  = float(input(f"  Call strike [enter for ${Kc_sug:,.0f}]: $") or Kc_sug)
        pp  = bs_put (spot, Kp, T, RISK_FREE_RATE, iv) * qty
        cp  = bs_call(spot, Kc, T, RISK_FREE_RATE, iv) * qty
        tot = pp + cp

        c = SimpleNamespace(
            strategy="Strangle",
            asset=asset,
            spot=spot,
            iv=iv,
            days=days,
            put_strike=Kp,
            call_strike=Kc,
            prob_profit=0,
            yield_ann=0,
            liquidity_tag="manual",
        )

        broker = DeribitClient(paper=DERIBIT_PAPER)
        if not broker.asset_has_options(asset):
            warn(f"No {asset} options available on Deribit. Try BTC or ETH instead.")
            return

        enter_trade(c)
        s = load_strangle_state(asset)

        ok(f"Strangle opened: Put ${Kp:,.0f} / Call ${Kc:,.0f} | Premium: ${tot:.2f}")
        draw_profit_zone(spot, Kp, Kc, tot, qty, days, iv)

    # [2] Show profit zone chart
    elif choice == "2":
        if not s["open"]:
            warn("No open position.")
            return
        op = s["open"]
        draw_profit_zone(spot, op["put_strike"], op["call_strike"],
                         op["total_premium"], op["qty"], days, iv)

    # [3] Close / expire at expiry price
    elif choice == "3":
        if not s["open"]:
            warn("No open position.")
            return
        op  = s["open"]
        Kp  = op["put_strike"];  Kc  = op["call_strike"]
        tot = op["total_premium"]; q = op["qty"]

        spot_close = float(
            input(f"  {asset} price at expiry [~${spot:,.0f}]: $") or spot
        )
        pnl = _pnl(spot_close, Kp, Kc, tot, q)

        if pnl >= 0:
            result = "Win";  s["wins"]   += 1
            ok(f"Expired in profit!  P&L: +${pnl:.2f}")
        else:
            result = "Loss"; s["losses"] += 1
            side = "PUT side (price dropped)" if spot_close < Kp else "CALL side (price rallied)"
            warn(f"Expired in loss.  P&L: ${pnl:.2f}  ({side})")

        trade_id = op.get("trade_id")
        if trade_id:
            close_strangle_trade(
                trade_id,
                date_close=date.today(),
                spot_close=spot_close,
                pnl=round(pnl, 4),
                result=result,
                notes=f"{asset} held to expiry",
            )

        s["open"] = None
        save_strangle_state(asset, s)

    # [4] Early close at current market price
    elif choice == "4":
        if not s["open"]:
            warn("No open position.")
            return
        op  = s["open"]
        Kp  = op["put_strike"];  Kc  = op["call_strike"]
        p0  = op["total_premium"]; q = op["qty"]

        sl_status, cur_val, mult, sl_msg = check_stop_loss(spot, iv, days, op)
        pnl = p0 - cur_val

        sub("Early Close — Mark-to-Market")
        inf("  Cost to buy back now",          f"${cur_val:.2f}  ({mult:.2f}x premium)")
        inf("  Premium originally collected",   f"${p0:.2f}")
        colour = GR if pnl >= 0 else RD
        inf("  Net P&L if closed now",          f"{colour}${pnl:.2f}{R}")

        if sl_status == "stop":
            print(f"\n  {RD}⛔  Stop-loss level reached — strongly recommended to close.{R}")
        elif sl_status == "warn":
            print(f"\n  {YL}⚠  Warning level — consider closing before it gets worse.{R}")
        else:
            print(f"\n  {GY}Position within normal range — early close is optional.{R}")

        confirm = input(f"\n  {YL}Confirm close at current price? (y/n): {R}").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

        if pnl >= 0:
            result = "Win";  s["wins"]   += 1
        else:
            result = "Loss (Stop)" if sl_status == "stop" else "Loss (Early)"
            s["losses"] += 1

        note = (
            f"Stop-loss at {mult:.1f}x — loss ${abs(pnl):.2f}"
            if sl_status == "stop"
            else f"Early close at {mult:.1f}x — P&L ${pnl:.2f}"
        )
        ok(f"Closed.  P&L: {'+'if pnl>=0 else ''}${pnl:.2f}")

        trade_id = op.get("trade_id")
        if trade_id:
            close_strangle_trade(
                trade_id,
                date_close=date.today(),
                spot_close=spot,
                pnl=round(pnl, 4),
                result=result,
                notes=f"{asset} {note}",
            )

        s["open"] = None
        save_strangle_state(asset, s)

    # [5] Adjust stop-loss multipliers
    elif choice == "5":
        sub("Adjust Stop-Loss Settings")
        inf("Current stop multiplier", f"{STOP_LOSS_MULTIPLIER:.1f}x")
        inf("Current warn multiplier", f"{STOP_WARN_MULTIPLIER:.1f}x")
        print(f"""
  {GY}Common rules used by options sellers:{R}
  {CY}2x{R}  stop = lose at most 1x your premium  {GY}(conservative — recommended for beginners){R}
  {CY}3x{R}  stop = lose at most 2x your premium  {GY}(moderate){R}
  {CY}5x{R}  stop = lose at most 4x your premium  {GY}(aggressive — not recommended){R}
""")
        new_stop = input(f"  New stop multiplier [{STOP_LOSS_MULTIPLIER:.1f}]: ").strip()
        new_warn = input(f"  New warn multiplier [{STOP_WARN_MULTIPLIER:.1f}]: ").strip()
        if new_stop:
            import config
            config.STOP_LOSS_MULTIPLIER = float(new_stop)
        if new_warn:
            import config
            config.STOP_WARN_MULTIPLIER = float(new_warn)
        ok(f"Updated: warn at {STOP_WARN_MULTIPLIER:.1f}x, stop at {STOP_LOSS_MULTIPLIER:.1f}x")
        if STOP_LOSS_MULTIPLIER > 3.0:
            warn("Stop > 3x is aggressive. One bad move can wipe weeks of gains.")
