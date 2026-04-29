"""
main.py
=======
Entry point for the Crypto Options Strategy Tool.

Orchestrates the CLI menu and delegates to strategy/helper modules.

Current module layout
---------------------
config.py           Configuration file. Contain Global Variables ← DONE
pricing.py          Black-Scholes pricing & probability helpers  ← DONE
market_data.py      ETH price + IV fetching                      ← DONE
display.py          ANSI colour helpers, ASCII chart             ← DONE
excel_tracker.py    openpyxl workbook setup & row helpers        ← DONE
strategies/
    wheel.py        Wheel paper trading simulator                ← DONE
    strangle.py     Short strangle paper trading + stop-loss     ← DONE
    calendar.py     Calendar spread paper trading + monitoring   ← DONE
    summary.py      Cross-sheet reporting                        ← DONE
    monitor.py      Monitor trades and automatically close       ← DONE
    scannner.py     Recommend across strategies and instruments  ← DONE

Run
---
    python main.py
"""

# ── Stdlib ────────────────────────────────────────────────────────────────────
import sys

# ── Third-party ───────────────────────────────────────────────────────────────
try:
    import requests  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl colorama")
    sys.exit(1)

try:
    import openpyxl  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl")
    sys.exit(1)

try:
    import colorama  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl colorama")
    sys.exit(1)

# ── Internal modules (added as each is extracted) ─────────────────────────────
from config      import (
    IV_FALLBACK,
    DAILY_DAYS, WEEKLY_DAYS,
    DEFAULT_ASSET, SUPPORTED_ASSETS,
)
#from pricing     import bs_put, bs_call, prob_otm_put, prob_otm_call  # noqa: F401
from market_data import get_spot_price, get_deribit_iv
from display     import hdr, sub, inf, ok, warn, err, draw_profit_zone
from excel_tracker import setup_excel, append_trade_row, append_strangle_row  # noqa: F401
from strategies.wheel    import show_strikes, wheel_paper_menu
from strategies.strangle import show_strangle_analysis, strangle_paper_menu
from strategies.calendar import show_calendar_analysis, calendar_paper_menu
from strategies.summary  import show_summary
from strategies.monitor  import run_monitor
import strategies.scanner as scanner


# ── Asset selection ───────────────────────────────────────────────────────────
 
def _select_asset() -> str:
    """
    Prompt the user to choose an underlying asset from SUPPORTED_ASSETS.
 
    Displays a numbered menu of available assets. Pressing Enter without
    a selection defaults to DEFAULT_ASSET. Returns the chosen asset symbol
    as an uppercase string e.g. "ETH", "BTC", "SOL".
    """
    assets = list(SUPPORTED_ASSETS.keys())
 
    R  = "\033[0m";  B  = "\033[1m"
    CY = "\033[96m"; YL = "\033[93m"; GY = "\033[90m"; WH = "\033[97m"
 
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

# ── Select yield filter ───────────────────────────────────────────────────────
 
def _set_yield_filter() -> None:
    """Prompt user to update the scanner minimum yield filter."""
    import strategies.scanner as scanner
    YL = "\033[93m"; R = "\033[0m"
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

 # ── Strategies sub-menu ───────────────────────────────────────────────────────
 
def _strategies_menu(asset: str, spot: float, iv: float, wb, days: int) -> None:
    """
    Sub-menu grouping all strategy options.
    Returns to the main menu when the user selects Back.
    """
    R  = "\033[0m";  B  = "\033[1m"
    CY = "\033[96m"; YL = "\033[93m"; GY = "\033[90m"; WH = "\033[97m"
 
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
  {CY}[Y]{R}  Set minimum yield filter  {GY}(currently {scanner.MIN_YIELD_PCT:.0f}%/yr){R}
  {CY}[R]{R}  Recommendations scanner
  {CY}[0]{R}  Back
""")
        choice = input(f"  {YL}Choice: {R}").strip().upper()

        if choice == "1":
            show_strikes(asset, spot, iv, days)

        elif choice == "2":
            wheel_paper_menu(asset, spot, iv, wb, days)

        elif choice == "3":
            show_strangle_analysis(asset, spot, iv, days)

        elif choice == "4":
            strangle_paper_menu(asset, spot, iv, wb, days)

        elif choice == "5":
            show_calendar_analysis(asset, spot, iv, days)

        elif choice == "6":
            calendar_paper_menu(asset, spot, iv, wb, days)

        elif choice == "7":
            warn("Live trade recording not yet wired — use the original tool for now.")

        elif choice == "Y":
            _set_yield_filter()

        elif choice == "R":
            scanner.run_scanner(spot, iv, asset, days)

        elif choice == "0":
            break

        else:
            warn("Invalid choice — enter 0–7, Y or R")
 

# ── Main menu ─────────────────────────────────────────────────────────────────

def main():
    R  = "\033[0m";  B  = "\033[1m"
    CY = "\033[96m"; YL = "\033[93m"; GY = "\033[90m"; WH = "\033[97m"

    print(f"\n{B}{CY}  Crypto Options Strategy Tool v3.0{R}")
    print(f"  {GY}Crypto option paper trading & planning on Deribit{R}\n")

    # Asset selection
    asset = DEFAULT_ASSET
    #asset = _select_asset()
    #ok(f"Asset selected: {asset}")
 
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

    wb = setup_excel()

    while True:
        # ── Background monitor check (silent) ─────────────────────────────────
        run_monitor(spot, iv, wb, days, asset, silent=True)
 
       
        print(f"""
{CY}{'─' * 54}{R}
{B}{WH}  {asset}: ${spot:>10,.2f}   IV: {iv*100:.0f}%   Expiry: {days}d{R}
{CY}{'─' * 54}{R}

  {CY}[S]{R}  Strategies
  {CY}[R]{R}  Recommendations scanner
  {CY}[M]{R}  Monitor all positions
  {CY}[P]{R}  Performance summary & stats
  {CY}[Y]{R}  Set min yield filter  {GY}(currently {scanner.MIN_YIELD_PCT:.0f}%/yr){R}
  {CY}[1]{R}  Switch expiry  {GY}(currently {days}d — {'daily' if days == 1 else 'weekly'}){R}
  {CY}[2]{R}  Switch asset   {GY}(currently {asset}){R}
  {CY}[3]{R}  Refresh market data
  {CY}[0]{R}  Exit
""")
        choice = input(f"  {YL}Choice: {R}").strip().upper()

        if choice == "S":
            _strategies_menu(asset, spot, iv, wb, days)

        elif choice == "R":
            scanner.run_scanner(spot, iv, asset, days)

        elif choice == "M":
            run_monitor(spot, iv, wb, days, asset, silent=False)

        elif choice == "P":
            show_summary(wb)

        elif choice == "Y":
            _set_yield_filter()

        elif choice == "1":
            days = DAILY_DAYS if days == WEEKLY_DAYS else WEEKLY_DAYS
            ok(f"Switched to {'daily' if days == 1 else 'weekly'} expiry ({days}d)")

        elif choice == "2":
            asset    = _select_asset()
            spot_new = get_spot_price(asset)
            iv_new   = get_deribit_iv(asset, spot_new or spot, days)
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
            iv_new   = get_deribit_iv(asset, spot_new or spot, days)
            if spot_new: ok(f"{'{'}asset{'}'} price refreshed: ${'{'}spot:,.2f{'}'}")
            if iv_new:   ok(f"IV refreshed: {'{'}iv*100:.0f{'}'}%")
            else:        warn("IV refresh failed — keeping previous value")
        
        elif choice == "0":
            print(f"\n  Goodbye.\n")
            break
        
        else:
            warn("Invalid choice — enter 1–3, S, Y,     R, M or P")


if __name__ == "__main__":
    main()