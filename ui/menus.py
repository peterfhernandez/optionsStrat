"""
ui/menus.py
===========
Menu system for the Crypto Options Strategy Tool.

Handles main menu, strategies submenu, portfolio view, and live/paper mode toggle.
"""

import sys
from typing import Tuple

from config import (
    IV_FALLBACK,
    DAILY_DAYS, WEEKLY_DAYS,
    CALENDAR_NEAR_DAYS, CALENDAR_FAR_DAYS,
    CALENDAR_NEAR_OPTIONS, CALENDAR_FAR_OPTIONS,
    DEFAULT_ASSET, SUPPORTED_ASSETS,
    TRADING_MODE,
)
from market.market_data import get_spot_price, get_deribit_iv
from ui.display import hdr, sub, inf, ok, warn, err, GR, RD, R, show_trade_history
import strategies.scanner as scanner
from strategies.wheel import show_strikes, wheel_paper_menu
from strategies.strangle import show_strangle_analysis, strangle_paper_menu
from strategies.calendar import show_calendar_analysis, calendar_paper_menu
from strategies.summary import show_summary
from automation.monitor import run_monitor
from trading.portfolio import collect_open_positions
from automation.automator import run_automation, DEFAULT_MIN_PROB


# ── Color codes ───────────────────────────────────────────────────────────────
R  = "\033[0m"
B  = "\033[1m"
CY = "\033[96m"
YL = "\033[93m"
GY = "\033[90m"
WH = "\033[97m"


# ── Asset selection ───────────────────────────────────────────────────────────

def select_asset() -> str:
    """
    Prompt the user to choose an underlying asset from SUPPORTED_ASSETS.

    Displays a numbered menu of available assets. Pressing Enter without
    a selection defaults to DEFAULT_ASSET. Returns the chosen asset symbol
    as an uppercase string e.g. "ETH", "BTC", "SOL".
    """
    assets = list(SUPPORTED_ASSETS.keys())

    print(f"\n{B}{CY}  Select underlying asset{R}")
    for i, asset in enumerate(assets, 1):
        default_label = f"  {GY}← default{R}" if asset == DEFAULT_ASSET else ""
        print(f"  {CY}[{i}]{R}  {WH}{asset}{R}{default_label}")
    print()

    while True:
        raw = input(
            f"  {YL}Choice [1–{len(assets)}, Enter for {DEFAULT_ASSET}]: {R}"
        ).strip()

        if raw == "":
            return DEFAULT_ASSET

        if raw.isdigit() and 1 <= int(raw) <= len(assets):
            return assets[int(raw) - 1]

        # Allow typing the asset symbol directly e.g. "BTC"
        if raw.upper() in SUPPORTED_ASSETS:
            return raw.upper()

        print(f"  Invalid choice — enter a number between 1 and {len(assets)}")


# ── Yield filter ──────────────────────────────────────────────────────────────

def _next_calendar_near(current: int, far: int) -> int:
    """Cycle the near-leg calendar horizon to the next valid value."""
    for value in CALENDAR_NEAR_OPTIONS:
        if value > current and value < far:
            return value
    for value in CALENDAR_NEAR_OPTIONS:
        if value < far:
            return value
    return current


def _next_calendar_far(current: int, near: int) -> int:
    """Cycle the far-leg calendar horizon to the next valid value."""
    for value in CALENDAR_FAR_OPTIONS:
        if value > current and value > near:
            return value
    for value in CALENDAR_FAR_OPTIONS:
        if value > near:
            return value
    return current


def set_yield_filter() -> None:
    """Prompt user to update the scanner minimum yield filter."""
    current = scanner.MIN_YIELD_PCT
    raw = input(
        f"  {YL}Min yield %/yr for recommendations [{current:.0f}%]: {R}"
    ).strip()
    if raw:
        try:
            scanner.set_min_yield(float(raw))
            ok(f"Min yield filter set to {scanner.MIN_YIELD_PCT:.0f}%/yr")
        except ValueError:
            warn("Invalid value — enter a number e.g. 15")


# ── Portfolio view ────────────────────────────────────────────────────────────

def show_portfolio() -> None:
    """
    Display all open positions across all strategies with live P&L.
    """
    hdr("Open Portfolio Positions")

    positions = collect_open_positions()
    if not positions:
        inf("Open positions", "None found")
        return

    print(
        "\n  Asset  Strategy    Position             Strike(s)          Days  Premium     Value       Unrealised P&L"
    )
    print("  " + "─" * 102)

    total_pnl = 0.0
    for pos in positions:
        pnl = pos["unrealised_pnl"]
        pnl_text = f"${pnl:,.2f}" if pnl is not None else "N/A"
        pnl_col = GR if pnl is not None and pnl >= 0 else RD
        value_text = f"${pos['current_value']:,.2f}" if pos["current_value"] is not None else "N/A"

        print(
            f"  {pos['asset']:<5}  {pos['strategy']:<10}  {pos['position']:<20}  "
            f"{pos['strike']:<18}  {pos['days_left']:>4}  "
            f"${pos['premium']:>8.2f}  {value_text:>11}  {pnl_col}{pnl_text:>14}{R}"
        )
        if pnl is not None:
            total_pnl += pnl

    print()
    inf("Open positions", str(len(positions)))
    inf("Total unrealised P&L", f"{GR if total_pnl >= 0 else RD}${total_pnl:,.2f}{R}")


# ── Trading mode toggle ───────────────────────────────────────────────────────

def toggle_trading_mode() -> None:
    """
    Toggle between paper and live trading mode.
    (Placeholder for Phase 6 implementation)
    """
    current = TRADING_MODE
    msg = f"Current mode: {current}. Live trading requires Deribit credentials (Phase 6)."
    warn(msg)


def prompt_min_probability(default: float = DEFAULT_MIN_PROB) -> float:
    """Prompt the user to update the auto-enter minimum probability threshold."""
    raw = input(
        f"  {YL}Minimum probability of profit [{default:.0f}%]: {R}"
    ).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        warn("Invalid probability — enter a number like 91")
        return default
    if value < 0 or value > 100:
        warn("Probability must be between 0 and 100")
        return default
    return value


# ── Strategies submenu ────────────────────────────────────────────────────────

def strategies_menu(
    asset: str,
    spot: float,
    iv: float,
    days: int,
    calendar_near: int,
    calendar_far: int,
) -> None:
    """
    Sub-menu grouping all strategy options.
    Returns to the main menu when the user selects Back.
    """
    while True:
        print(f"""
{CY}{'─' * 54}{R}
{B}{WH}  Strategies — {asset}  ${spot:,.2f}   IV: {iv*100:.0f}%   {days}d{R}
{CY}{'─' * 54}{R}

  {CY}[1]{R}  Wheel — strike & premium analysis
  {CY}[2]{R}  Wheel — paper trading simulator
  {CY}[3]{R}  Strangle — analysis + profit zone chart
  {CY}[4]{R}  Strangle — paper trading simulator
  {CY}[5]{R}  Calendar Spread — analysis + P&L chart
  {CY}[6]{R}  Calendar Spread — paper trading simulator
  {CY}[7]{R}  Record live trade  {GY}(wheel){R}
  {CY}[M]{R}  Monitor all positions
  {CY}[Y]{R}  Set minimum yield filter  {GY}(currently {scanner.MIN_YIELD_PCT:.0f}%/yr){R}
  {CY}[R]{R}  Recommendations scanner
  {CY}[0]{R}  Back
""")
        choice = input(f"  {YL}Choice: {R}").strip().upper()

        if choice == "1":
            show_strikes(asset, spot, iv, days)

        elif choice == "2":
            wheel_paper_menu(asset, spot, iv, days)

        elif choice == "3":
            show_strangle_analysis(asset, spot, iv, days)

        elif choice == "4":
            strangle_paper_menu(asset, spot, iv, days)

        elif choice == "5":
            show_calendar_analysis(
                asset, spot, iv, days,
                near_days=calendar_near,
                far_days=calendar_far,
            )

        elif choice == "6":
            calendar_paper_menu(
                asset, spot, iv, days,
                near_days=calendar_near,
                far_days=calendar_far,
            )

        elif choice == "7":
            warn("Live trade recording not yet wired — use the original tool for now.")

        elif choice == "M":
            run_monitor(spot, iv, days, asset, silent=False)

        elif choice == "Y":
            set_yield_filter()

        elif choice == "R":
            scanner.run_scanner(
                spot, iv, asset, days,
                cal_near_days=calendar_near,
                cal_far_days=calendar_far,
            )

        elif choice == "0":
            break

        else:
            warn("Invalid choice — enter 0–7, Y or R")


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu(
    asset: str,
    spot: float,
    iv: float,
    days: int,
    calendar_near: int,
    calendar_far: int,
) -> Tuple[bool, str, float, float, int, int, int]:
    """
    Display main menu and process user choice.
    
    Returns (should_continue, asset, spot, iv, days, calendar_near, calendar_far)
    """
    print(f"""
{CY}{'─' * 54}{R}
{B}{WH}  {asset}: ${spot:>10,.2f}   IV: {iv*100:.0f}%   Expiry: {days}d{R}
{CY}{'─' * 54}{R}

  {CY}[S]{R}  Strategies
  {CY}[R]{R}  Recommendations scanner
  {CY}[A]{R}  Auto-enter best paper trade  {GY}(prob >90%, yield ≥10%/yr, liq Med/High){R}
  {CY}[M]{R}  Monitor all positions
  {CY}[P]{R}  Performance summary & stats
  {CY}[O]{R}  Portfolio positions & P&L
  {CY}[H]{R}  Trade history & cumulative P&L
  {CY}[L]{R}  Trading mode (paper/live)
  {CY}[Y]{R}  Set min yield filter  {GY}(currently {scanner.MIN_YIELD_PCT:.0f}%/yr){R}
  {CY}[1]{R}  Switch expiry  {GY}(currently {days}d — {'daily' if days == 1 else 'weekly'}){R}
  {CY}[2]{R}  Switch asset   {GY}(currently {asset}){R}
  {CY}[4]{R}  Calendar near leg  {GY}(currently {calendar_near}d){R}
  {CY}[5]{R}  Calendar far leg   {GY}(currently {calendar_far}d){R}
  {CY}[3]{R}  Refresh market data
  {CY}[0]{R}  Exit
""")
    choice = input(f"  {YL}Choice: {R}").strip().upper()

    if choice == "S":
        strategies_menu(
            asset, spot, iv, days,
            calendar_near, calendar_far,
        )

    elif choice == "R":
        scanner.run_scanner(
            spot, iv, asset, days,
            cal_near_days=calendar_near,
            cal_far_days=calendar_far,
        )

    elif choice == "A":
        min_prob = prompt_min_probability()
        run_automation(
            spot, iv, asset, days,
            min_prob=min_prob,
            cal_near_days=calendar_near,
            cal_far_days=calendar_far,
        )

    elif choice == "M":
        run_monitor(spot, iv, days, asset, silent=False)

    elif choice == "P":
        show_summary()

    elif choice == "O":
        show_portfolio()

    elif choice == "H":
        show_trade_history()

    elif choice == "L":
        toggle_trading_mode()

    elif choice == "Y":
        set_yield_filter()

    elif choice == "1":
        days = DAILY_DAYS if days == WEEKLY_DAYS else WEEKLY_DAYS
        ok(f"Switched to {'daily' if days == 1 else 'weekly'} expiry ({days}d)")

    elif choice == "2":
        asset = select_asset()
        spot_new = get_spot_price(asset)
        iv_new = get_deribit_iv(asset, spot_new or spot, days)
        if spot_new:
            spot = spot_new
            ok(f"Switched to {asset} — price: ${spot:,.2f}")
        if iv_new:
            iv = iv_new
            ok(f"IV refreshed: {iv*100:.0f}%")
        else:
            iv = IV_FALLBACK
            warn(f"IV fetch failed for {asset} — using fallback {IV_FALLBACK*100:.0f}%")

    elif choice == "3":
        spot_new = get_spot_price(asset)
        iv_new = get_deribit_iv(asset, spot_new or spot, days)
        if spot_new:
            ok(f"{asset} price refreshed: ${spot_new:,.2f}")
            spot = spot_new
        if iv_new:
            ok(f"IV refreshed: {iv_new*100:.0f}%")
            iv = iv_new
        else:
            warn("IV refresh failed — keeping previous value")

    elif choice == "4":
        calendar_near = _next_calendar_near(calendar_near, calendar_far)
        ok(f"Calendar near leg set to {calendar_near}d")

    elif choice == "5":
        calendar_far = _next_calendar_far(calendar_far, calendar_near)
        ok(f"Calendar far leg set to {calendar_far}d")

    elif choice == "0":
        return (False, asset, spot, iv, days, calendar_near, calendar_far)

    else:
        warn("Invalid choice — enter 0–5, S, Y, L, R, A, M, P, O or H")

    return (True, asset, spot, iv, days, calendar_near, calendar_far)


# ── Application entry point ───────────────────────────────────────────────────

def run_app() -> None:
    """
    Initialize market data and run the main menu loop.
    """
    print(f"\n{B}{CY}  Crypto Options Strategy Tool v3.0{R}")
    print(f"  {GY}Crypto option paper trading & planning on Deribit{R}\n")

    # Asset selection
    asset = DEFAULT_ASSET

    # Fetch live market data
    spot = get_spot_price(asset)
    if not spot:
        print(f"  {YL}⚠ Could not fetch {asset} price. Check your connection.{R}")
        sys.exit(1)

    iv = get_deribit_iv(asset, spot, WEEKLY_DAYS)
    if not iv:
        iv = IV_FALLBACK
        print(f"  {YL}⚠ IV fetch failed — using fallback {IV_FALLBACK*100:.0f}%{R}")

    days = DAILY_DAYS  # default expiry; user can switch via menu
    calendar_near = CALENDAR_NEAR_DAYS
    calendar_far = CALENDAR_FAR_DAYS

    # Main loop
    while True:
        # Background monitor check (silent)
        run_monitor(spot, iv, days, asset, silent=True)

        # Show menu and get response
        should_continue, asset, spot, iv, days, calendar_near, calendar_far = main_menu(
            asset, spot, iv, days,
            calendar_near, calendar_far,
        )
        
        if not should_continue:
            print(f"\n  Goodbye.\n")
            break
