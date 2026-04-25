"""
strategies/monitor.py
=====================
Cross-strategy position monitor for the Crypto Options Strategy Tool.

Checks all open positions across all assets and strategies. Triggers
automatic closes when stop-loss, take-profit, or expiry conditions are
met, and logs every auto-close to Excel.

Designed to be extensible — register a new strategy by adding one entry
to _REGISTRY at the bottom of this file.

Public API
----------
run_monitor(spot, iv, wb, days, asset, silent=False)
    Check all open positions and auto-close any that breach thresholds.
    Called silently on every main menu display, or verbosely via menu.

Internal helpers
----------------
_days_remaining(expiry_str)         Days left until expiry date string
_check_strangle(asset, spot, iv,    Evaluate and optionally close a
                wb, silent)          strangle position
_check_wheel(asset, spot, iv,       Evaluate and optionally close a
             wb, silent)             wheel position
_REGISTRY                           List of checker functions to call
"""

import json
import os
from datetime import date, datetime

from config  import (
    SUPPORTED_ASSETS, RISK_FREE_RATE,
    STOP_LOSS_MULTIPLIER, BUDGET_USD,
)
from pricing import bs_put, bs_call
from display import ok, warn, err, hdr, inf, sub, GR, RD, YL, CY, WH, GY, R
from excel_tracker import append_strangle_row, append_trade_row


# ── Thresholds ────────────────────────────────────────────────────────────────

# Take-profit: auto-close when position retains less than this fraction
# of original premium (i.e. nearly worthless — lock in the gain)
TAKE_PROFIT_THRESHOLD = 0.10   # 10% of premium remaining → close


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_remaining(expiry_str: str) -> int:
    """
    Calculate calendar days remaining until an expiry date string.

    Accepts formats: "25-Apr-2026", "25-APR-2026", "2026-04-25".
    Returns 0 if expiry is today or in the past.
    """
    expiry_str = expiry_str.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            expiry_date = datetime.strptime(expiry_str, fmt).date()
            return max((expiry_date - date.today()).days, 0)
        except ValueError:
            continue
    return 0  # unrecognised format → treat as expired


def _load_state(path: str) -> dict | None:
    """Load a JSON state file, returning None if it doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _save_state(path: str, state: dict) -> None:
    """Persist a JSON state file."""
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ── Strangle checker ──────────────────────────────────────────────────────────

def _check_strangle(
    asset: str,
    spot: float,
    iv: float,
    wb,
    silent: bool,
) -> bool:
    """
    Evaluate an open strangle position and auto-close if thresholds are met.

    Checks in priority order:
      1. Expiry reached  → close at intrinsic value
      2. Stop-loss hit   → close at current mark price (loss)
      3. Take-profit hit → close at current mark price (gain locked in)

    Returns True if a close was triggered, False otherwise.
    """
    path  = f"strangle_state_{asset.upper()}.json"
    state = _load_state(path)
    if not state or not state.get("open"):
        return False

    op  = state["open"]
    Kp  = op["put_strike"]
    Kc  = op["call_strike"]
    p0  = op["total_premium"]
    qty = op["qty"]
    T   = max(_days_remaining(op.get("expiry", "")) / 365.0, 1 / 365.0)
    days_left = _days_remaining(op.get("expiry", ""))

    cur_pp  = bs_put (spot, Kp, T, RISK_FREE_RATE, iv) * qty
    cur_cp  = bs_call(spot, Kc, T, RISK_FREE_RATE, iv) * qty
    cur_val = cur_pp + cur_cp
    mult    = cur_val / p0 if p0 > 0 else 0.0
    pnl     = p0 - cur_val

    # ── Determine trigger ─────────────────────────────────────────────────────
    trigger = None
    result  = None
    note    = None

    if days_left == 0:
        trigger = "EXPIRY"
        pnl     = p0 - max(Kp - spot, 0) * qty - max(spot - Kc, 0) * qty
        result  = "Win" if pnl >= 0 else "Loss"
        note    = f"Auto-closed at expiry. P&L: ${pnl:.2f}"

    elif mult >= STOP_LOSS_MULTIPLIER:
        trigger = "STOP-LOSS"
        result  = "Loss (Auto Stop)"
        note    = f"Auto stop-loss at {mult:.2f}x premium. P&L: ${pnl:.2f}"

    elif cur_val <= p0 * TAKE_PROFIT_THRESHOLD:
        trigger = "TAKE-PROFIT"
        result  = "Win (Auto TP)"
        note    = f"Auto take-profit — {(1-mult)*100:.0f}% of premium captured. P&L: ${pnl:.2f}"

    if trigger is None:
        if not silent:
            colour = GR if pnl >= 0 else RD
            inf(f"  {asset} Strangle",
                f"val=${cur_val:.2f}  {mult:.2f}x  P&L={colour}${pnl:.2f}{R}  "
                f"{days_left}d left  → No action")
        return False

    # ── Auto-close ────────────────────────────────────────────────────────────
    colour = GR if pnl >= 0 else RD
    print(f"\n  {RD if trigger == 'STOP-LOSS' else YL if trigger == 'EXPIRY' else GR}"
          f"⚡ AUTO-CLOSE [{trigger}] {asset} Strangle{R}")
    print(f"  Put ${Kp:,.0f} / Call ${Kc:,.0f}  |  "
          f"Premium: ${p0:.2f}  |  P&L: {colour}${pnl:.2f}{R}")

    append_strangle_row(wb, {
        "date":        str(date.today()),
        "type":        f"Short Strangle — Auto Close ({trigger})",
        "put_strike":  Kp,
        "call_strike": Kc,
        "spot_open":   op.get("spot_open", spot),
        "spot_close":  spot,
        "premium":     round(p0, 4),
        "pnl":         round(pnl, 4),
        "days":        op.get("days", 7),
        "result":      result,
        "notes":       note,
    })

    if pnl >= 0:
        state["wins"]   += 1
    else:
        state["losses"] += 1
    state["open"] = None
    _save_state(path, state)
    ok(f"{asset} strangle auto-closed and logged to Excel.")
    return True


# ── Wheel checker ─────────────────────────────────────────────────────────────

def _check_wheel(
    asset: str,
    spot: float,
    iv: float,
    wb,
    silent: bool,
) -> bool:
    """
    Evaluate an open wheel position and auto-close if thresholds are met.

    Checks in priority order:
      1. Expiry reached  → expire worthless or at intrinsic value
      2. Stop-loss hit   → close at current mark price (loss)
      3. Take-profit hit → close at current mark price (gain locked in)

    Returns True if a close was triggered, False otherwise.
    """
    path  = f"paper_state_{asset.upper()}.json"
    state = _load_state(path)
    if not state or not state.get("open"):
        return False

    op       = state["open"]
    K        = op["strike"]
    p0       = op["premium"]
    qty      = op.get("qty", BUDGET_USD / K)
    opt_type = op["type"]   # "Put" or "Call"
    days_left = _days_remaining(op.get("expiry", ""))
    T        = max(days_left / 365.0, 1 / 365.0)

    cur = (
        bs_put (spot, K, T, RISK_FREE_RATE, iv)
        if opt_type == "Put"
        else bs_call(spot, K, T, RISK_FREE_RATE, iv)
    ) * qty
    mult = cur / p0 if p0 > 0 else 0.0
    pnl  = p0 - cur

    # ── Determine trigger ─────────────────────────────────────────────────────
    trigger = None
    result  = None
    note    = None

    if days_left == 0:
        trigger   = "EXPIRY"
        expired_otm = (spot > K if opt_type == "Put" else spot < K)
        pnl       = p0 if expired_otm else p0 - abs(spot - K) * qty
        result    = "Win" if expired_otm else "Loss"
        note      = f"Auto-expired {'OTM' if expired_otm else 'ITM'}. P&L: ${pnl:.2f}"

    elif mult >= STOP_LOSS_MULTIPLIER:
        trigger = "STOP-LOSS"
        result  = "Loss (Auto Stop)"
        note    = f"Auto stop-loss at {mult:.2f}x premium. P&L: ${pnl:.2f}"

    elif cur <= p0 * TAKE_PROFIT_THRESHOLD:
        trigger = "TAKE-PROFIT"
        result  = "Win (Auto TP)"
        note    = f"Auto take-profit — {(1-mult)*100:.0f}% captured. P&L: ${pnl:.2f}"

    if trigger is None:
        if not silent:
            colour = GR if pnl >= 0 else RD
            inf(f"  {asset} {opt_type}",
                f"val=${cur:.2f}  {mult:.2f}x  P&L={colour}${pnl:.2f}{R}  "
                f"{days_left}d left  → No action")
        return False

    # ── Auto-close ────────────────────────────────────────────────────────────
    colour = GR if pnl >= 0 else RD
    print(f"\n  {RD if trigger == 'STOP-LOSS' else YL if trigger == 'EXPIRY' else GR}"
          f"⚡ AUTO-CLOSE [{trigger}] {asset} {opt_type}{R}")
    print(f"  Strike ${K:,.0f}  |  Premium: ${p0:.2f}  |  P&L: {colour}${pnl:.2f}{R}")

    append_trade_row(wb, "📝 Paper Trades", {
        "date":        str(date.today()),
        "type":        f"{opt_type} — Auto Close ({trigger})",
        "stage":       "Closed",
        "days":        op.get("days", 7),
        "strike":      K,
        "spot_open":   op.get("spot_open", spot),
        "spot_close":  spot,
        "premium":     round(p0, 4),
        "pnl":         round(pnl, 4),
        "result":      result,
        "notes":       note,
    })

    if pnl >= 0:
        state["wins"]   += 1
    else:
        state["losses"] += 1
    state["open"]  = None
    state["stage"] = "no_position"
    _save_state(path, state)
    ok(f"{asset} {opt_type} auto-closed and logged to Excel.")
    return True


# ── Registry ──────────────────────────────────────────────────────────────────
#
# To add a new strategy, append a checker function here.
# Each checker must have the signature:
#   fn(asset, spot, iv, wb, silent) -> bool
#
_REGISTRY = [
    _check_strangle,
    _check_wheel,
]


# ── Public API ────────────────────────────────────────────────────────────────

def run_monitor(
    spot: float,
    iv: float,
    wb,
    days: int,
    asset: str,
    silent: bool = True,
) -> None:
    """
    Check all open positions across all assets and strategies.

    Runs every checker in _REGISTRY for every asset in SUPPORTED_ASSETS.
    Auto-closes and logs any position that breaches a threshold.

    Parameters
    ----------
    spot   : float  Current spot price for the active asset
    iv     : float  Current IV for the active asset
    wb     : openpyxl.Workbook
    days   : int    Days to expiry (used for IV context only)
    asset  : str    Currently selected asset (used for spot/IV context)
    silent : bool   True = only print on trigger; False = print all statuses
    """
    if not silent:
        hdr("Position Monitor")
        print(f"  {GY}Checking all open positions across all assets...{R}\n")

    any_triggered = False

    for a in SUPPORTED_ASSETS:
        # Use live spot/IV for the active asset; for others we use spot as
        # a proxy — in a future version this could fetch live prices per asset
        a_spot = spot if a == asset else spot
        a_iv   = iv   if a == asset else iv

        for checker in _REGISTRY:
            triggered = checker(a, a_spot, a_iv, wb, silent)
            if triggered:
                any_triggered = True

    if not silent:
        if not any_triggered:
            print(f"\n  {GY}No positions required action.{R}")
        print(f"\n  {GY}Thresholds:  "
              f"Stop-loss {STOP_LOSS_MULTIPLIER:.1f}x  |  "
              f"Take-profit <{TAKE_PROFIT_THRESHOLD*100:.0f}% remaining{R}\n")

    elif any_triggered:
        # Silent mode — just print a brief alert so it's not invisible
        print(f"\n  {YL}⚡ One or more positions were auto-closed. "
              f"Select [M] Monitor for details.{R}")
