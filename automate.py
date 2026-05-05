"""
automate.py
===========
One-shot CLI entry point for the automated strategy runner.

Usage
-----
    python automate.py [--min-yield 10] [--days 7] [--asset ETH]
                       [--liquidity Med,High]

Behaviour
---------
* Fetches live spot + IV for the active asset
* Calls automation.automator.run_automation
* If a candidate qualifies it opens a paper trade and persists state
* If no candidate qualifies it exits cleanly with code 0 — a scheduled
  task wrapper is expected to invoke this every hour

Exit codes
----------
    0  trade entered, or no eligible candidate (the "do nothing, retry
       next hour" case)
    1  fatal error  (price/IV fetch failed, workbook unavailable, etc.)
"""

from __future__ import annotations

import argparse
import sys

from config        import DEFAULT_ASSET, IV_FALLBACK, WEEKLY_DAYS
from market.market_data   import get_spot_price, get_deribit_iv
from excel.excel_tracker import setup_excel
from ui.display       import hdr, inf, ok, warn, err
from automation.automator import (
    run_automation, DEFAULT_MIN_YIELD, DEFAULT_MIN_PROB,
    DEFAULT_ALLOWED_LIQUIDITY,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="automate.py",
        description="Automated paper-trade entry for the options strategy tool.",
    )
    p.add_argument("--asset", default=DEFAULT_ASSET,
                   help=f"Active asset symbol (default {DEFAULT_ASSET})")
    p.add_argument("--days", type=int, default=WEEKLY_DAYS,
                   help=f"Days to near expiry (default {WEEKLY_DAYS})")
    p.add_argument("--min-yield", type=float, default=DEFAULT_MIN_YIELD,
                   help=f"Minimum annualised yield in %% (default {DEFAULT_MIN_YIELD})")
    p.add_argument("--min-probability", type=float, default=DEFAULT_MIN_PROB,
                   help=f"Minimum probability of profit in %% (default {DEFAULT_MIN_PROB})")
    p.add_argument("--liquidity", default=",".join(DEFAULT_ALLOWED_LIQUIDITY),
                   help='Comma-separated liquidity tags allowed '
                        '(default "Med,High")')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    spot = get_spot_price(args.asset)
    if not spot:
        err(f"Could not fetch spot price for {args.asset}.")
        return 1

    iv = get_deribit_iv(args.asset, spot, args.days) or IV_FALLBACK

    wb = setup_excel()
    allowed = tuple(s.strip() for s in args.liquidity.split(",") if s.strip())

    result = run_automation(
        active_spot       = spot,
        active_iv         = iv,
        active_asset      = args.asset,
        days              = args.days,
        wb                = wb,
        min_yield         = args.min_yield,
        min_prob          = args.min_probability,
        allowed_liquidity = allowed,
    )

    if result["status"] == "entered":
        c = result["candidate"]
        ok(
            f"Trade entered: {c.asset} {c.strategy} {c.strike}  "
            f"P(prof)={c.prob_profit:.0f}% yield={c.yield_ann:.0f}%/yr"
        )
    else:
        warn(
            f"No qualifying candidate "
            f"(considered={result['considered']}, eligible={result['eligible']}). "
            f"Will retry on next scheduled run."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
