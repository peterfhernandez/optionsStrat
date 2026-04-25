"""
strategies/summary.py
=====================
Cross-sheet performance summary for the Crypto Options Strategy Tool.

Reads trade data from all sheets in the workbook and prints a unified
stats dashboard. Intentionally has no dependency on any single strategy —
it works at the workbook level.

Public API
----------
show_summary(wb)    Print win rate, premium totals, and trade counts
                    for Paper Trades, Live Trades, and Strangles.
"""

from display import hdr, sub, inf


# Sheet name → (col_result, col_prem) — zero-based index into iter_rows tuple.
# Column A = index 0 (always empty); data starts at column B = index 1.
#
# Trade sheets  (📝 Paper Trades, 📋 Live Trades):
#   B=Date C=Type D=Stage E=Days F=Strike G=SpotOpen H=SpotClose I=Premium J=PnL K=Result
#   Premium → I = index 8    Result → K = index 10
#
# Strangles sheet (🔀 Strangles):
#   B=Date C=Type D=PutK E=CallK F=SpotOpen G=SpotClose H=Days I=Premium J=PnL
#   K=LowerBE L=UpperBE M=Result
#   Premium → I = index 8    Result → M = index 12
_SHEET_CONFIGS = {
    "📝 Paper Trades": {"col_result": 10, "col_prem": 8},
    "📋 Live Trades":  {"col_result": 10, "col_prem": 8},
    "🔀 Strangles":    {"col_result": 12, "col_prem": 8},
}


def show_summary(wb) -> None:
    """
    Print a performance summary across all trade sheets in the workbook.

    For each sheet, reports trade count, wins/losses, win rate,
    total premium collected, and average premium per trade.

    Parameters
    ----------
    wb : openpyxl.Workbook  The open workbook returned by setup_excel()
    """
    hdr("Performance Summary")

    for sheet_name, cols in _SHEET_CONFIGS.items():
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
        inf("Total Premium", f"${sum(prems):.2f}"            if prems else "$0")
        inf("Avg Premium",   f"${sum(prems) / len(prems):.2f}" if prems else "N/A")