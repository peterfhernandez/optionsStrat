"""
strategies/wheel.py
===================
Wheel Strategy paper trading simulator and strike analysis.

The Wheel sells a cash-secured put; if assigned, holds the underlying
and sells a covered call; if called away, restarts the cycle.

Public API
----------
show_strikes(asset, spot, iv, days)
    Display put and call strike tables with premiums and probabilities.

wheel_paper_menu(asset, spot, iv, wb, days)
    Interactive paper trading simulator for the wheel strategy.

show_summary(wb)
    Print a performance summary across all trade sheets.

Internal helpers
----------------
_state_file(asset)      Asset-specific JSON state file path
_load(asset)            Load wheel state from disk
_save(asset, state)     Persist wheel state to disk
"""

import json
import os
from datetime import date, timedelta

from config        import (
    BUDGET_USD, RISK_FREE_RATE, OTM_LEVELS,
)
from market.pricing       import bs_put, bs_call, prob_otm_put, prob_otm_call
from ui.display       import (
    hdr, sub, inf, ok, warn,
    GR, RD, CY, YL, GY, WH, R,
)
from excel.excel_tracker import append_trade_row


# ── State persistence ─────────────────────────────────────────────────────────

def _state_file(asset: str) -> str:
    """Return asset-specific state file path e.g. 'paper_state_ETH.json'."""
    return f"paper_state_{asset.upper()}.json"


def _load(asset: str) -> dict:
    """Load wheel state from disk, or return a fresh default state."""
    path = _state_file(asset)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "stage":         "no_position",
        "open":          None,
        "asset_held":    0.0,
        "cost_basis":    0.0,
        "total_premium": 0.0,
        "wins":          0,
        "losses":        0,
        "cycles":        0,
    }


def _save(asset: str, state: dict) -> None:
    """Persist wheel state to disk."""
    with open(_state_file(asset), "w") as f:
        json.dump(state, f, indent=2)


# ── Strike analysis ───────────────────────────────────────────────────────────

def show_strikes(
    asset: str,
    spot: float,
    iv: float,
    days: int,
) -> None:
    """
    Display OTM put and call strike tables for the wheel strategy.

    Shows strike price, total premium, annualised yield, and probability
    of expiring worthless for each OTM level in config.OTM_LEVELS.

    Parameters
    ----------
    asset : str   Underlying asset symbol (e.g. "ETH", "BTC", "SOL")
    spot  : float Current spot price
    iv    : float Implied volatility (decimal)
    days  : int   Days to expiry
    """
    T = days / 365.0
    r = RISK_FREE_RATE

    hdr(f"Wheel Strike Analysis — {asset} {days}-day expiry")
    inf(f"{asset} Spot", f"${spot:,.2f}")
    inf("IV",            f"{iv * 100:.1f}%")
    inf("Budget",        f"${BUDGET_USD:.0f}")

    # ── Cash-Secured Puts ─────────────────────────────────────────────────────
    sub(f"Cash-Secured Put Strikes ({days}-day)")
    print(f"\n  {'OTM%':<8}{'Strike':<14}{'Total Prem':<14}{'Yield/yr':<14}{'P(Profit)'}")
    print(f"  {'─' * 55}")

    for otm in OTM_LEVELS:
        K   = round(spot * (1 - otm) / 10) * 10
        qty = BUDGET_USD / K
        tot = bs_put(spot, K, T, r, iv) * qty
        yld = (tot / BUDGET_USD) * (365 / days) * 100
        pp  = prob_otm_put(spot, K, T, r, iv) * 100
        c   = GR if otm == 0.15 else WH
        print(f"  {c}{otm*100:.0f}%{'':6}${K:>10,.0f}  ${tot:>7.2f}  {yld:>6.1f}%/yr  {pp:.0f}%{R}")

    # ── Covered Calls ─────────────────────────────────────────────────────────
    sub(f"Covered Call Strikes ({days}-day)")
    print(f"\n  {'OTM%':<8}{'Strike':<14}{'Total Prem':<14}{'Yield/yr':<14}{'P(Profit)'}")
    print(f"  {'─' * 55}")

    qty = BUDGET_USD / spot
    for otm in OTM_LEVELS:
        K   = round(spot * (1 + otm) / 10) * 10
        tot = bs_call(spot, K, T, r, iv) * qty
        yld = (tot / BUDGET_USD) * (365 / days) * 100
        pp  = prob_otm_call(spot, K, T, r, iv) * 100
        c   = GR if otm == 0.15 else WH
        print(f"  {c}{otm*100:.0f}%{'':6}${K:>10,.0f}  ${tot:>7.2f}  {yld:>6.1f}%/yr  {pp:.0f}%{R}")

    print(f"\n  {GY}IV: {iv*100:.0f}%  |  Budget: ${BUDGET_USD:.0f}  |  Black-Scholes estimate{R}")


# ── Paper trading simulator ───────────────────────────────────────────────────

def wheel_paper_menu(
    asset: str,
    spot: float,
    iv: float,
    wb,
    days: int,
) -> None:
    """
    Interactive paper trading simulator for the wheel strategy.

    Manages position state across sessions via a per-asset JSON file.
    Walks through the four wheel stages: no position → short put →
    holding asset → short call → back to no position (one full cycle).

    Parameters
    ----------
    asset : str              Underlying asset symbol
    spot  : float            Current spot price
    iv    : float            Implied volatility (decimal)
    wb    : openpyxl.Workbook
    days  : int              Days to expiry
    """
    T = days / 365.0
    s = _load(asset)

    stage_labels = {
        "no_position": f"No Position — ready to sell a Put",
        "short_put":   f"Short Put open — waiting for expiry",
        "holding":     f"Holding {asset} — ready to sell a Call",
        "short_call":  f"Short Call open — waiting for expiry",
    }

    hdr(f"Wheel Strategy — {asset} Paper Trading")
    inf("Current Stage",   stage_labels.get(s["stage"], "?"))
    inf("Total Premium",   f"${s['total_premium']:.2f}")

    total = s["wins"] + s["losses"]
    inf("Wins / Losses",   f"{s['wins']} / {s['losses']}")
    inf("Win Rate",        f"{s['wins'] / total * 100:.1f}%" if total else "N/A")
    inf("Cycles Completed", str(s["cycles"]))

    # ── Show open position if one exists ──────────────────────────────────────
    if s["open"]:
        op  = s["open"]
        K   = op["strike"]
        p0  = op["premium"]
        qty = op.get("qty", BUDGET_USD / K)
        cur = (
            bs_put (spot, K, T, RISK_FREE_RATE, iv)
            if op["type"] == "Put"
            else bs_call(spot, K, T, RISK_FREE_RATE, iv)
        ) * qty
        unreal = p0 - cur
        colour = GR if unreal >= 0 else RD

        sub("Open Position")
        inf("  Type",             op["type"])
        inf("  Strike",           f"${K:,.0f}")
        inf("  Expiry",           op.get("expiry", ""))
        inf("  Premium Received", f"${p0:.2f}")
        inf("  Unrealised P&L",   f"{colour}${unreal:.2f}{R}")

    # ── Menu ──────────────────────────────────────────────────────────────────
    print(f"""
  {CY}[1]{R}  Sell Put       {GY}(open new position){R}
  {CY}[2]{R}  Expire position  {GY}(enter price at expiry){R}
  {CY}[3]{R}  Assign put      {GY}(take {asset}){R}
  {CY}[4]{R}  Sell Covered Call
  {CY}[5]{R}  Back
""")
    choice = input(f"  {YL}Choice: {R}").strip()

    # [1] Sell Put
    if choice == "1":
        if s["stage"] != "no_position":
            warn("Close existing position first.")
            return

        K_sug = round(spot * 0.85 / 10) * 10
        qty   = BUDGET_USD / K_sug
        p_sug = bs_put(spot, K_sug, T, RISK_FREE_RATE, iv) * qty
        sub("Suggested strike (15% OTM)")
        inf(f"  Strike", f"${K_sug:,.0f}  →  ${p_sug:.2f} premium")

        K      = float(input(f"\n  Strike [enter for ${K_sug:,.0f}]: $") or K_sug)
        qty    = BUDGET_USD / K
        premium = bs_put(spot, K, T, RISK_FREE_RATE, iv) * qty
        expiry  = (date.today() + timedelta(days=days)).strftime("%d-%b-%Y")

        s["stage"]           = "short_put"
        s["open"]            = {
            "type":    "Put",
            "strike":  K,
            "expiry":  expiry,
            "premium": round(premium, 4),
            "spot_open": spot,
            "qty":     qty,
            "days":    days,
            "asset":   asset,
        }
        s["total_premium"]  += premium
        _save(asset, s)
        ok(f"Sell Put @ ${K:,.0f}  |  Premium: ${premium:.2f}")

        append_trade_row(wb, "📝 Paper Trades", {
            "date":      str(date.today()),
            "type":      "Sell Cash-Secured Put",
            "stage":     "Short Put",
            "days":      days,
            "strike":    K,
            "spot_open": spot,
            "premium":   round(premium, 4),
            "result":    "Open",
            "notes":     f"{asset} paper, {days}d",
        })

    # [2] Expire position
    elif choice == "2":
        if not s["open"]:
            warn("No open position.")
            return

        op          = s["open"]
        K           = op["strike"]
        p0          = op["premium"]
        qty         = op.get("qty", BUDGET_USD / K)
        spot_close  = float(
            input(f"  {asset} price at expiry [~${spot:,.0f}]: $") or spot
        )
        expired = spot_close > K if op["type"] == "Put" else spot_close < K

        if expired:
            pnl    = p0
            result = "Win"
            s["wins"] += 1
            ok(f"Expired worthless ✓  P&L: +${pnl:.2f}")
            s["stage"] = "holding" if op["type"] == "Call" else "no_position"
            if op["type"] == "Call":
                s["cycles"] += 1
        else:
            intrinsic = abs(spot_close - K) * qty
            pnl       = p0 - intrinsic
            result    = "Loss"
            s["losses"] += 1
            warn(f"Expired ITM.  P&L: ${pnl:.2f}")
            s["stage"] = "no_position"

        append_trade_row(wb, "📝 Paper Trades", {
            **op,
            "date":        str(date.today()),
            "type":        f"{'Put' if op['type']=='Put' else 'Call'} → Expired",
            "stage":       "Closed",
            "spot_close":  spot_close,
            "pnl":         round(pnl, 4),
            "result":      result,
            "notes":       f"{asset} paper expired {days}d",
        })
        s["open"] = None
        _save(asset, s)

    # [3] Assign put — take asset
    elif choice == "3":
        if s["stage"] != "short_put":
            warn("Not in short put stage.")
            return
        op               = s["open"]
        s["asset_held"]  = op.get("qty", BUDGET_USD / op["strike"])
        s["cost_basis"]  = op["strike"]
        s["stage"]       = "holding"
        s["open"]        = None
        _save(asset, s)
        ok(f"Assigned! {s['asset_held']:.4f} {asset} @ ${op['strike']:,.0f}")

    # [4] Sell Covered Call
    elif choice == "4":
        if s["stage"] != "holding":
            warn(f"Must be holding {asset} first.")
            return

        K_sug   = round(spot * 1.15 / 10) * 10
        qty     = s["asset_held"]
        p_sug   = bs_call(spot, K_sug, T, RISK_FREE_RATE, iv) * qty
        sub("Suggested strike (15% OTM)")
        inf(f"  Strike", f"${K_sug:,.0f}  →  ${p_sug:.2f} premium")

        K       = float(input(f"\n  Strike [enter for ${K_sug:,.0f}]: $") or K_sug)
        premium = bs_call(spot, K, T, RISK_FREE_RATE, iv) * qty
        expiry  = (date.today() + timedelta(days=days)).strftime("%d-%b-%Y")

        s["stage"]          = "short_call"
        s["open"]           = {
            "type":      "Call",
            "strike":    K,
            "expiry":    expiry,
            "premium":   round(premium, 4),
            "spot_open": spot,
            "qty":       qty,
            "days":      days,
            "asset":     asset,
        }
        s["total_premium"] += premium
        _save(asset, s)
        ok(f"Sell Call @ ${K:,.0f}  |  Premium: ${premium:.2f}")

        append_trade_row(wb, "📝 Paper Trades", {
            "date":      str(date.today()),
            "type":      "Sell Covered Call",
            "stage":     "Short Call",
            "days":      days,
            "strike":    K,
            "spot_open": spot,
            "premium":   round(premium, 4),
            "result":    "Open",
            "notes":     f"{asset} paper, {days}d",
        })


# ── Performance summary ───────────────────────────────────────────────────────

def show_summary(wb) -> None:
    """
    Print a performance summary across all trade sheets in the workbook.

    Covers Paper Trades (wheel), Live Trades (wheel), and Strangles.
    """
    hdr("Performance Summary")

    sheet_configs = {
        "📝 Paper Trades": {"col_result": 9,  "col_prem": 7},
        "📋 Live Trades":  {"col_result": 9,  "col_prem": 7},
        "🔀 Strangles":    {"col_result": 11, "col_prem": 7},
    }

    for sheet_name, cols in sheet_configs.items():
        ws = wb[sheet_name]
        sub(sheet_name)

        rows = [
            r for r in ws.iter_rows(min_row=4, values_only=True)
            if r[0] and "←" not in str(r[0])
        ]
        if not rows:
            inf("No trades yet", "")
            continue

        col_result = cols["col_result"]
        col_prem   = cols["col_prem"]

        wins   = sum(1 for t in rows if t[col_result] == "Win")
        losses = sum(1 for t in rows if t[col_result] == "Loss")
        total  = wins + losses
        prems  = [t[col_prem] for t in rows if isinstance(t[col_prem], (int, float))]

        inf("Trades",        str(len(rows)))
        inf("Wins / Losses", f"{wins} / {losses}")
        inf("Win Rate",      f"{wins / total * 100:.1f}%" if total else "N/A")
        inf("Total Premium", f"${sum(prems):.2f}"         if prems  else "$0")
        inf("Avg Premium",   f"${sum(prems)/len(prems):.2f}" if prems else "N/A")
