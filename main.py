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
display.py          ANSI colour helpers, ASCII chart              ← TODO
excel_tracker.py    openpyxl workbook setup & row helpers         ← TODO
strategies/
    wheel.py        Wheel paper trading simulator                 ← TODO
    strangle.py     Short strangle paper trading + stop-loss      ← TODO

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
    print("Please run: pip install requests openpyxl")
    sys.exit(1)

try:
    import openpyxl  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl")
    sys.exit(1)

# ── Internal modules (added as each is extracted) ─────────────────────────────
from config      import (
    BUDGET_USD, EXCEL_FILE, RISK_FREE_RATE, OTM_LEVELS, IV_FALLBACK,
    STOP_LOSS_MULTIPLIER, STOP_WARN_MULTIPLIER,
    DAILY_DAYS, WEEKLY_DAYS,
    PAPER_STATE_FILE, STRANGLE_STATE_FILE,
    DEFAULT_ASSET,
)
from pricing     import bs_put, bs_call, prob_otm_put, prob_otm_call  # noqa: F401
from market_data import get_spot_price, get_deribit_iv

# TODO: from display import hdr, sub, inf, ok, warn, err, draw_profit_zone
# TODO: from excel_tracker import setup_excel, append_trade_row, append_strangle_row
# TODO: from strategies.wheel import wheel_paper_menu, show_strikes
# TODO: from strategies.strangle import strangle_paper_menu, show_strangle_analysis

# ── Temporary: import everything from the original file while refactoring ─────
# Remove each import below as its module is extracted and wired up above.
from crypto_options_trade import (
    hdr, sub, inf, ok, warn, err,
    draw_profit_zone,
    show_strangle_analysis,
    strangle_paper_menu,
    show_strikes,
    wheel_paper_menu,
    show_summary,
    setup_excel,
)

# ── Main menu ─────────────────────────────────────────────────────────────────

def main():
    R  = "\033[0m";  B  = "\033[1m"
    CY = "\033[96m"; YL = "\033[93m"; GY = "\033[90m"; WH = "\033[97m"

    print(f"\n{B}{CY}  Crypto Options Strategy Tool v3.0{R}")
    print(f"  {GY}Paper trading & planning for ETH options on Deribit{R}\n")

    # Fetch live market data
    spot = get_eth_price()
    if not spot:
        print(f"  {YL}⚠ Could not fetch ETH price. Check your connection.{R}")
        sys.exit(1)

    iv = get_deribit_iv(DEFAULT_ASSET, spot, WEEKLY_DAYS)
    if not iv:
        iv = IV_FALLBACK
        print(f"  {YL}⚠ IV fetch failed — using fallback 80%{R}")

    days = WEEKLY_DAYS  # default expiry; user can switch via menu

    wb = setup_excel()

    while True:
        print(f"""
{CY}{'─' * 54}{R}
{B}{WH}  ETH: ${spot:>10,.2f}   IV: {iv*100:.0f}%   Expiry: {days}d{R}
{CY}{'─' * 54}{R}

  {CY}[1]{R}  Wheel strike & premium analysis
  {CY}[2]{R}  Wheel paper trading simulator
  {CY}[3]{R}  Strangle analysis + profit zone chart
  {CY}[4]{R}  Strangle paper trading simulator
  {CY}[5]{R}  Record live trade (wheel)
  {CY}[6]{R}  Performance summary & stats
  {CY}[7]{R}  Switch expiry  {GY}(currently {days}d — {'daily' if days == 1 else 'weekly'}){R}
  {CY}[8]{R}  Refresh market data
  {CY}[9]{R}  Exit
""")
        choice = input(f"  {YL}Choice: {R}").strip()

        if choice == "1":
            show_strikes(spot, iv, days)

        elif choice == "2":
            wheel_paper_menu(spot, iv, wb, days)

        elif choice == "3":
            show_strangle_analysis(spot, iv, days)

        elif choice == "4":
            strangle_paper_menu(spot, iv, wb, days)

        elif choice == "5":
            # Live trade recording — delegates to excel_tracker once extracted
            warn("Live trade recording not yet wired to main.py — use the original tool for now.")

        elif choice == "6":
            show_summary(wb)

        elif choice == "7":
            days = DAILY_DAYS if days == WEEKLY_DAYS else WEEKLY_DAYS
            ok(f"Switched to {'daily' if days == 1 else 'weekly'} expiry ({days}d)")

        elif choice == "8":
            spot_new = get_spot_price(DEFAULT_ASSET)
            iv_new   = get_deribit_iv(DEFAULT_ASSET, spot_new or spot, days)
            if spot_new:
                spot = spot_new
                ok(f"ETH price refreshed: ${spot:,.2f}")
            if iv_new:
                iv = iv_new
                ok(f"IV refreshed: {iv*100:.0f}%")
            else:
                warn("IV refresh failed — keeping previous value")

        elif choice == "9":
            print(f"\n  {GY}Goodbye.{R}\n")
            break

        else:
            warn("Invalid choice — enter 1–9")


if __name__ == "__main__":
    main()
