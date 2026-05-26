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

wheel_paper_menu(asset, spot, iv, days)
    Interactive paper trading simulator for the wheel strategy.

show_summary()
    Print a performance summary of all wheel trades.
"""

from datetime import date
from types import SimpleNamespace

from config import BUDGET_USD, RISK_FREE_RATE, OTM_LEVELS, DERIBIT_PAPER
from database import (
    load_wheel_state,
    save_wheel_state,
    close_single_trade,
    get_wheel_stats,
)
from market.pricing import bs_put, bs_call, prob_otm_put, prob_otm_call, round_strike
from models import get_session, Single
from trading.executor import enter_trade
from trading.fee_calculator import calculate_fee
from access import DeribitClient
from ui.display import hdr, sub, inf, ok, warn, GR, RD, CY, YL, GY, WH, R


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
        K   = round_strike(spot * (1 - otm), spot)
        qty = BUDGET_USD / K
        tot = bs_put(spot, K, T, r, iv) * qty
        # Account for open and estimated close fees
        open_fee = calculate_fee(spot, tot / qty, asset) * qty
        close_fee_est = calculate_fee(spot, 0.01, asset) * qty
        effective_tot = tot - open_fee - close_fee_est
        yld = (effective_tot / BUDGET_USD) * (365 / days) * 100 if effective_tot > 0 else 0.0
        pp  = prob_otm_put(spot, K, T, r, iv) * 100
        c   = GR if otm == 0.15 else WH
        print(f"  {c}{otm*100:.0f}%{'':6}${K:>10,.0f}  ${effective_tot:>7.2f}  {yld:>6.1f}%/yr  {pp:.0f}%{R}")

    # ── Covered Calls ─────────────────────────────────────────────────────────
    sub(f"Covered Call Strikes ({days}-day)")
    print(f"\n  {'OTM%':<8}{'Strike':<14}{'Total Prem':<14}{'Yield/yr':<14}{'P(Profit)'}")
    print(f"  {'─' * 55}")

    qty = BUDGET_USD / spot
    for otm in OTM_LEVELS:
        K   = round_strike(spot * (1 + otm), spot)
        tot = bs_call(spot, K, T, r, iv) * qty
        # Account for open and estimated close fees
        open_fee = calculate_fee(spot, tot / qty, asset) * qty
        close_fee_est = calculate_fee(spot, 0.01, asset) * qty
        effective_tot = tot - open_fee - close_fee_est
        yld = (effective_tot / BUDGET_USD) * (365 / days) * 100 if effective_tot > 0 else 0.0
        pp  = prob_otm_call(spot, K, T, r, iv) * 100
        c   = GR if otm == 0.15 else WH
        print(f"  {c}{otm*100:.0f}%{'':6}${K:>10,.0f}  ${effective_tot:>7.2f}  {yld:>6.1f}%/yr  {pp:.0f}%{R}")

    print(f"\n  {GY}IV: {iv*100:.0f}%  |  Budget: ${BUDGET_USD:.0f}  |  Black-Scholes estimate{R}")


# ── Paper trading simulator ───────────────────────────────────────────────────

def wheel_paper_menu(asset: str, spot: float, iv: float, days: int) -> None:
    """
    Interactive paper trading simulator for the wheel strategy.

    Manages position state across sessions via the SQLite database.
    Walks through the four wheel stages: no position → short put →
    holding asset → short call → back to no position (one full cycle).

    Parameters
    ----------
    asset : str   Underlying asset symbol
    spot  : float Current spot price
    iv    : float Implied volatility (decimal)
    days  : int   Days to expiry
    """
    T = days / 365.0
    s = load_wheel_state(asset)

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
        if s["open"]:
            warn("Close existing position first.")
            return

        K_sug = round_strike(spot * 0.85, spot)
        qty   = BUDGET_USD / K_sug
        p_sug = bs_put(spot, K_sug, T, RISK_FREE_RATE, iv) * qty
        sub("Suggested strike (15% OTM)")
        inf(f"  Strike", f"${K_sug:,.0f}  →  ${p_sug:.2f} premium")

        K      = float(input(f"\n  Strike [enter for ${K_sug:,.0f}]: $") or K_sug)
        qty    = BUDGET_USD / K
        premium = bs_put(spot, K, T, RISK_FREE_RATE, iv) * qty

        c = SimpleNamespace(
            strategy="CSP",
            asset=asset,
            spot=spot,
            iv=iv,
            days=days,
            strike=str(K),
            prob_profit=0,
            yield_ann=0,
            liquidity_tag="manual",
        )

        broker = DeribitClient(paper=DERIBIT_PAPER)
        if not broker.asset_has_options(asset):
            warn(f"No {asset} options available on Deribit. Try BTC or ETH instead.")
            return

        enter_trade(c)
        ok(f"Sell Put @ ${K:,.0f}  |  Premium: ${premium:.2f}")

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
            pnl = p0
            result = "Win"
            s["wins"] += 1
            ok(f"Expired worthless ✓  P&L: +${pnl:.2f}")
            s["stage"] = "holding" if op["type"] == "Call" else "no_position"
            if op["type"] == "Call":
                s["cycles"] += 1
        else:
            intrinsic = abs(spot_close - K) * qty
            pnl = p0 - intrinsic
            result = "Loss"
            s["losses"] += 1
            warn(f"Expired ITM.  P&L: ${pnl:.2f}")
            s["stage"] = "no_position"

        # Find the open trade in the database and close it
        session = get_session()
        open_trade = (
            session.query(Single)
            .filter(
                Single.asset == asset,
                Single.stage == "short_put" if op["type"] == "Put" else "short_call",
                Single.date_close.is_(None),
            )
            .order_by(Single.date_open.desc())
            .first()
        )
        session.close()

        if open_trade:
            close_single_trade(
                trade_id=open_trade.id,
                date_close=date.today(),
                spot_close=spot_close,
                pnl=round(pnl, 4),
                result=result,
                notes=f"{asset} paper expired {days}d",
            )

        s["open"] = None
        save_wheel_state(asset, s)

    # [3] Assign put — take asset
    elif choice == "3":
        if s["stage"] != "short_put":
            warn("Not in short put stage.")
            return
        op = s["open"]
        s["asset_held"] = op.get("qty", BUDGET_USD / op["strike"])
        s["cost_basis"] = op["strike"]
        s["stage"] = "holding"
        s["open"] = None
        save_wheel_state(asset, s)
        ok(f"Assigned! {s['asset_held']:.4f} {asset} @ ${op['strike']:,.0f}")

    # [4] Sell Covered Call
    elif choice == "4":
        if s["open"]:
            warn("Close existing position first.")
            return
        if s["stage"] != "holding":
            warn(f"Must be holding {asset} first.")
            return

        K_sug   = round_strike(spot * 1.15, spot)
        qty     = s["asset_held"]
        p_sug   = bs_call(spot, K_sug, T, RISK_FREE_RATE, iv) * qty
        sub("Suggested strike (15% OTM)")
        inf(f"  Strike", f"${K_sug:,.0f}  →  ${p_sug:.2f} premium")

        K       = float(input(f"\n  Strike [enter for ${K_sug:,.0f}]: $") or K_sug)
        premium = bs_call(spot, K, T, RISK_FREE_RATE, iv) * qty

        c = SimpleNamespace(
            strategy="CC",
            asset=asset,
            spot=spot,
            iv=iv,
            days=days,
            strike=str(K),
            prob_profit=0,
            yield_ann=0,
            liquidity_tag="manual",
        )

        broker = DeribitClient(paper=DERIBIT_PAPER)
        if not broker.asset_has_options(asset):
            warn(f"No {asset} options available on Deribit. Try BTC or ETH instead.")
            return

        enter_trade(c)
        ok(f"Sell Call @ ${K:,.0f}  |  Premium: ${premium:.2f}")


# ── Performance summary ───────────────────────────────────────────────────────

def show_summary() -> None:
    """Print a performance summary of all wheel (singles) trades from the database."""
    hdr("Wheel Strategy — Performance Summary")
    session = get_session()

    try:
        # Get all singles (wheel trades) by asset
        singles = session.query(Single).filter(Single.result.isnot(None), Single.result != "Open").all()

        if not singles:
            sub("No completed trades yet")
            return

        # Group by asset
        by_asset = {}
        for trade in singles:
            if trade.asset not in by_asset:
                by_asset[trade.asset] = []
            by_asset[trade.asset].append(trade)

        # Display per-asset stats
        for asset in sorted(by_asset.keys()):
            trades = by_asset[asset]
            sub(f"{asset} Wheel Trades")

            wins = sum(1 for t in trades if t.result == "Win")
            losses = sum(1 for t in trades if t.result == "Loss")
            total = len(trades)
            prems = [t.premium for t in trades if t.premium]

            inf("Trades", str(total))
            inf("Wins / Losses", f"{wins} / {losses}")
            inf("Win Rate", f"{wins / total * 100:.1f}%" if total else "N/A")
            inf("Total Premium", f"${sum(prems):.2f}" if prems else "$0")
            inf("Avg Premium", f"${sum(prems) / len(prems):.2f}" if prems else "N/A")
            print()

        # Overall stats
        sub("Overall")
        stats = get_wheel_stats(session=session)
        inf("All Trades", str(stats["trades"]))
        inf("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
        inf("Win Rate", f"{stats['win_rate']:.1f}%" if stats["trades"] else "N/A")
        inf("Total Premium", f"${stats['total_premium']:.2f}")
        inf("Avg Premium", f"${stats['avg_premium']:.2f}" if stats["avg_premium"] else "$0")
    finally:
        session.close()
