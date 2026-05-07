"""
strategies/summary.py
=====================
Cross-strategy performance summary for the Crypto Options Strategy Tool.

Reads trade data from SQLite and prints a unified stats dashboard.

Public API
----------
show_summary()    Print win rate, premium totals, and trade counts
                  for Wheel, Strangles, and Calendars.
"""

from database import get_wheel_stats, get_strangle_stats, get_calendar_stats
from ui.display import hdr, sub, inf


def show_summary() -> None:
    """Print a performance summary across all strategies from the database."""
    hdr("Performance Summary")

    sections = [
        ("Wheel (Singles)", get_wheel_stats()),
        ("Strangles",       get_strangle_stats()),
        ("Calendars",       get_calendar_stats()),
    ]

    for label, stats in sections:
        sub(label)
        if stats["trades"] == 0:
            inf("No trades yet", "")
            continue
        inf("Trades",        str(stats["trades"]))
        inf("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
        inf("Win Rate",      f"{stats['win_rate']:.1f}%" if stats["trades"] else "N/A")
        inf("Total Premium", f"${stats['total_premium']:.2f}")
        inf("Avg Premium",   f"${stats['avg_premium']:.2f}" if stats["trades"] else "N/A")
