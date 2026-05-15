"""
strategies/summary.py
=====================
Cross-strategy performance summary for the Crypto Options Strategy Tool.

Reads trade data from SQLite and prints a unified stats dashboard,
including currently open positions from state.

Public API
----------
show_summary(broker)    Print win rate, premium totals, and trade counts
                        for Wheel, Strangles, and Calendars, plus any
                        currently open positions.
"""

from config import DERIBIT_PAPER, SUPPORTED_ASSETS
from database import get_wheel_stats, get_strangle_stats, get_calendar_stats
from database import load_wheel_state
from database.strangle_db import load_strangle_state
from database.calendar_db import load_calendar_state
from database.spread_db import load_spread_state, get_spread_stats
from ui.display import hdr, sub, inf
from access import BrokerBase, DeribitClient


def _show_open_positions() -> None:
    """Print a summary of all currently open positions from state."""
    sub("Open Positions")
    any_open = False

    for asset in SUPPORTED_ASSETS:
        w = load_wheel_state(asset)
        if w.get("open"):
            op = w["open"]
            broker = w.get("broker", "—")
            inf(
                f"  {asset} Wheel ({op.get('type', '?')})",
                f"strike ${op.get('strike', 0):,.0f}  "
                f"expiry {op.get('expiry', '?')}  "
                f"prem ${op.get('premium', 0):.2f}  "
                f"broker {broker}",
            )
            any_open = True

        s = load_strangle_state(asset)
        if s.get("open"):
            op = s["open"]
            broker = s.get("broker", "—")
            inf(
                f"  {asset} Strangle",
                f"put ${op.get('put_strike', 0):,.0f} / "
                f"call ${op.get('call_strike', 0):,.0f}  "
                f"expiry {op.get('expiry', '?')}  "
                f"prem ${op.get('total_premium', 0):.2f}  "
                f"broker {broker}",
            )
            any_open = True

        c = load_calendar_state(asset)
        if c.get("open"):
            op = c["open"]
            broker = c.get("broker", "—")
            inf(
                f"  {asset} Calendar ({op.get('option_type', '?')})",
                f"strike ${op.get('strike', 0):,.0f}  "
                f"near {op.get('expiry_near', '?')} / "
                f"far {op.get('expiry_far', '?')}  "
                f"debit ${op.get('net_debit', 0):.2f}  "
                f"broker {broker}",
            )
            any_open = True

        sp = load_spread_state(asset)
        if sp.get("open"):
            op     = sp["open"]
            broker = sp.get("broker", "—")
            inf(
                f"  {asset} {op.get('spread_type', 'Spread')}",
                f"short ${op.get('short_strike', 0):,.2f} / "
                f"long ${op.get('long_strike', 0):,.2f}  "
                f"expiry {op.get('expiry', '?')}  "
                f"credit ${op.get('net_credit', 0):.2f}  "
                f"broker {broker}",
            )
            any_open = True

    if not any_open:
        inf("No open positions", "")


def show_summary(broker: BrokerBase | None = None) -> None:
    """
    Print a performance summary across all strategies from the database,
    plus any currently open positions.

    Parameters
    ----------
    broker : BrokerBase adapter. Defaults to DeribitClient(paper=DERIBIT_PAPER).
             Used to display the active broker name in the header.
    """
    if broker is None:
        broker = DeribitClient(paper=DERIBIT_PAPER)

    hdr("Performance Summary")
    inf("Broker", broker.broker_name)

    _show_open_positions()

    sub("Closed Trade Statistics")
    sections = [
        ("Wheel (Singles)", get_wheel_stats()),
        ("Strangles",       get_strangle_stats()),
        ("Calendars",       get_calendar_stats()),
        ("Credit Spreads",  get_spread_stats()),
    ]

    for label, stats in sections:
        sub(label)
        if stats["trades"] == 0:
            inf("No trades yet", "")
            continue
        if "total_pnl" in stats:
            pnl_key, pnl_label, avg_key, avg_label = "total_pnl",    "Total P&L",     "avg_pnl",    "Avg P&L"
        elif "total_credit" in stats:
            pnl_key, pnl_label, avg_key, avg_label = "total_credit", "Total Credit",  "avg_credit", "Avg Credit"
        else:
            pnl_key, pnl_label, avg_key, avg_label = "total_premium","Total Premium", "avg_premium","Avg Premium"
        inf("Trades",        str(stats["trades"]))
        inf("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
        inf("Win Rate",      f"{stats['win_rate']:.1f}%" if stats["trades"] else "N/A")
        inf(pnl_label,       f"${stats[pnl_key]:.2f}")
        inf(avg_label,       f"${stats[avg_key]:.2f}" if stats["trades"] else "N/A")
