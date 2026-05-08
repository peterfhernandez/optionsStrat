"""
display.py
==========
Terminal display helpers for the Crypto Options Strategy Tool.

All ANSI colours, print formatters, the stop-loss monitor bar, and the
ASCII profit zone chart live here. No pricing or market logic — pure UI.

Public API
----------
Print helpers
    hdr(title)                      Section header with cyan rule
    sub(title)                      Sub-section label
    inf(label, value)               Key/value info line
    ok(text)                        Green success message
    warn(text)                      Yellow warning message
    err(text)                       Red error message

Stop-loss display
    print_stop_loss_status(status, cur_val, mult, msg, p0)
                                    Render the stop-loss progress bar + message

Profit zone chart
    draw_profit_zone(spot, put_strike, call_strike,
                     total_premium, qty, days, iv)
                                    ASCII P&L chart for a short strangle
"""

import shutil
import os

try:
    from colorama import just_fix_windows_console
    just_fix_windows_console()  # no-op on Mac/Linux; enables ANSI on Windows
except ImportError:
    pass  # colorama missing — colours may not render on Windows

from market.pricing import prob_otm_put, prob_otm_call
from config  import RISK_FREE_RATE, STOP_LOSS_MULTIPLIER, STOP_WARN_MULTIPLIER


# ── ANSI colour palette ───────────────────────────────────────────────────────

R      = "\033[0m"   # reset
B      = "\033[1m"   # bold
CY     = "\033[96m"  # cyan
GR     = "\033[92m"  # green
YL     = "\033[93m"  # yellow
RD     = "\033[91m"  # red
GY     = "\033[90m"  # grey
WH     = "\033[97m"  # white
BG_GR  = "\033[42m"  # background green
BG_RD  = "\033[41m"  # background red
BG_YL  = "\033[43m"  # background yellow


# ── Print helpers ─────────────────────────────────────────────────────────────

def hdr(title: str) -> None:
    """Print a prominent section header with a full-width cyan rule."""
    print(f"\n{CY}{'─' * 60}{R}\n{B}{CY} {title}{R}\n{CY}{'─' * 60}{R}")


def sub(title: str) -> None:
    """Print a sub-section label with a yellow arrow."""
    print(f"\n{YL} ▸ {title}{R}")


def inf(label: str, value: str) -> None:
    """Print a key/value info line, left-aligned label, white value."""
    print(f" {GY}{label:<34}{R}{WH}{value}{R}")


def ok(text: str) -> None:
    """Print a green success message with a checkmark."""
    print(f" {GR}✓ {text}{R}")


def warn(text: str) -> None:
    """Print a yellow warning message."""
    print(f" {YL}⚠ {text}{R}")


def err(text: str) -> None:
    """Print a red error message with an X."""
    print(f" {RD}✗ {text}{R}")


# ── Stop-loss monitor ─────────────────────────────────────────────────────────

def print_stop_loss_status(
    status: str,
    cur_val: float,
    mult: float,
    msg: str,
    p0: float,
) -> None:
    """
    Render the stop-loss progress bar and status message.

    The bar spans 0x → 3x premium, colour-coded green / yellow / red.
    Vertical markers show the warn and stop thresholds.

    Parameters
    ----------
    status  : str    "ok", "warn", or "stop"
    cur_val : float  Current market value of the strangle
    mult    : float  Current value as a multiple of premium received
    msg     : str    Pre-formatted status message from check_stop_loss()
    p0      : float  Original premium received
    """
    bar_width = 36
    fill_pct  = min(mult / 3.0, 1.0)
    stop_pos  = int((STOP_LOSS_MULTIPLIER / 3.0) * bar_width)
    warn_pos  = int((STOP_WARN_MULTIPLIER  / 3.0) * bar_width)
    filled    = int(fill_pct * bar_width)

    bar = ""
    for i in range(bar_width):
        if i < filled:
            if i >= stop_pos:   bar += f"{RD}█{R}"
            elif i >= warn_pos: bar += f"{YL}█{R}"
            else:               bar += f"{GR}█{R}"
        elif i == stop_pos:     bar += f"{RD}|{R}"
        elif i == warn_pos:     bar += f"{YL}|{R}"
        else:                   bar += f"{GY}░{R}"

    print(
        f"\n  {WH}Stop-Loss Monitor{R} "
        f"{GY}[warn={STOP_WARN_MULTIPLIER:.1f}x  stop={STOP_LOSS_MULTIPLIER:.1f}x]{R}"
    )
    print(f"  {bar}  {WH}{mult:.2f}x premium{R}")

    if status == "stop":
        print(f"\n  {RD}{'━' * 54}{R}")
        print(f"  {RD} ⛔  {msg}{R}")
        print(f"  {RD}{'━' * 54}{R}")
        print(
            f"\n  {WH}Recommended action:{R} Close the position now "
            f"(option {CY}[3]{R})."
        )
        print(
            f"  {WH}Why:{R} Closing at {STOP_LOSS_MULTIPLIER:.0f}x caps your "
            f"loss at ~1x your premium."
        )
        print(
            f"  Holding exposes you to much larger losses "
            f"if the market keeps moving.\n"
        )
    elif status == "warn":
        print(f"\n  {YL} ⚠  {msg}{R}")
        print(
            f"  {GY} Plan your exit. "
            f"Hard stop triggers at ${p0 * STOP_LOSS_MULTIPLIER:.2f}.\n{R}"
        )
    else:
        print(f"  {GY} ✓  {msg}{R}\n")


# ── ASCII Profit Zone chart ───────────────────────────────────────────────────

def _strangle_pnl(
    spot_at_expiry: float,
    put_strike: float,
    call_strike: float,
    total_premium: float,
    qty: float,
) -> float:
    """P&L of a short strangle at expiry for a given underlying price."""
    put_loss  = max(put_strike  - spot_at_expiry, 0) * qty
    call_loss = max(spot_at_expiry - call_strike, 0) * qty
    return total_premium - put_loss - call_loss


def _strangle_breakevens(
    put_strike: float,
    call_strike: float,
    premium_per_unit: float,
) -> tuple[float, float]:
    """Return (lower_breakeven, upper_breakeven) for a short strangle."""
    return put_strike - premium_per_unit, call_strike + premium_per_unit


def draw_profit_zone(
    spot: float,
    put_strike: float,
    call_strike: float,
    total_premium: float,
    qty: float,
    days: int,
    iv: float,
) -> None:
    """
    Draw an ASCII P&L chart for a short strangle across a ±35% price range.

    Overlays current spot, strike markers, breakeven lines, and an estimated
    probability-of-profit bar at the bottom.

    Parameters
    ----------
    spot          : float  Current underlying price
    put_strike    : float  Put leg strike price
    call_strike   : float  Call leg strike price
    total_premium : float  Total premium received (USD)
    qty           : float  Notional quantity (ETH / units)
    days          : int    Days to expiry
    iv            : float  Implied volatility as a decimal
    """
    width = shutil.get_terminal_size((80, 20)).columns - 4
    width = max(60, min(width, 100))

    prem_per_unit = total_premium / qty if qty > 0 else 0
    be_lo, be_hi  = _strangle_breakevens(put_strike, call_strike, prem_per_unit)

    lo_price = spot * 0.65
    hi_price = spot * 1.35
    prices   = [lo_price + (hi_price - lo_price) * i / (width - 1) for i in range(width)]
    pnls     = [_strangle_pnl(p, put_strike, call_strike, total_premium, qty) for p in prices]

    max_pnl   = total_premium
    min_pnl   = min(pnls)
    pnl_range = max_pnl - min_pnl if max_pnl != min_pnl else 1
    tick      = (hi_price - lo_price) / width * 1.5   # proximity threshold

    chart_height = 12
    rows = []
    for row in range(chart_height):
        threshold = max_pnl - (row / (chart_height - 1)) * pnl_range
        line = ""
        for price, pnl in zip(prices, pnls):
            in_profit = pnl >= 0
            is_be_lo  = abs(price - be_lo)       < tick
            is_be_hi  = abs(price - be_hi)       < tick
            is_spot   = abs(price - spot)         < tick
            is_strike = (abs(price - put_strike)  < tick or
                         abs(price - call_strike) < tick)
            at_level  = pnl >= threshold - pnl_range / (chart_height * 2)

            if at_level:
                if is_be_lo or is_be_hi: line += f"{YL}|{R}"
                elif is_spot:            line += f"{CY}◆{R}"
                elif is_strike:          line += f"{GY}┊{R}"
                elif in_profit:          line += f"{GR}█{R}"
                else:                    line += f"{RD}█{R}"
            else:
                line += " "
        rows.append(line)

    # ── Chart header
    print(
        f"\n  {B}{WH}P&L at Expiry — Short Strangle{R}  "
        f"{GY}({days}d, spot=${spot:,.0f}){R}"
    )
    print(f"  {GY}{'─' * width}{R}")

    # ── Rows with Y-axis labels
    for row_idx, line in enumerate(rows):
        if row_idx == 0:
            label = f"{GR}+${max_pnl:>6.2f}{R}"
        elif row_idx == chart_height // 2:
            label = f"   {WH}$0.00{R} "
        elif row_idx == chart_height - 1:
            label = f"{RD}${min_pnl:>7.2f}{R}"
        else:
            label = "         "
        print(f"  {label}  {line}")

    print(f"  {GY}{'─' * width}{R}")

    # ── X-axis price labels
    n_labels  = 6
    label_row = ""
    prev_end  = 0
    for i in range(n_labels + 1):
        price = lo_price + (hi_price - lo_price) * i / n_labels
        pos   = int(i * (width - 1) / n_labels)
        lbl   = f"${price:,.0f}"
        pad   = max(0, pos - prev_end)
        label_row += " " * pad + lbl
        prev_end = pos + len(lbl)
    print(f"  {GY}{label_row}{R}")

    # ── Legend
    print(f"""
  {GR}█{R} Profit  {RD}█{R} Loss  {CY}◆{R} Spot  {YL}|{R} Breakeven  {GY}┊{R} Strike

  {WH}Put strike : {YL}${put_strike:>8,.0f}{R}    {WH}Call strike : {YL}${call_strike:>8,.0f}{R}
  {WH}Lower B/E  : {GR}${be_lo:>8,.0f}{R}    {WH}Upper B/E   : {GR}${be_hi:>8,.0f}{R}
  {WH}Total prem : {GR}${total_premium:>7.2f}{R}    {WH}Max loss    : {RD}Unlimited{R}
  {WH}Profit range: {GR}${be_lo:,.0f} — ${be_hi:,.0f}{R}  ({(be_hi - be_lo) / spot * 100:.1f}% wide)
""")

    # ── Probability-of-profit bar
    T     = days / 365.0
    p_lo  = prob_otm_put (spot, put_strike,  T, RISK_FREE_RATE, iv)
    p_hi  = prob_otm_call(spot, call_strike, T, RISK_FREE_RATE, iv)
    pop   = max(0.0, (p_lo + p_hi - 1) * 100)

    bar_w  = 40
    filled = int(pop / 100 * bar_w)
    bar    = f"{GR}{'█' * filled}{GY}{'░' * (bar_w - filled)}{R}"
    print(f"  {WH}Est. Prob of Profit: {bar}  {GR}{pop:.0f}%{R}\n")


# ── Calendar Spread P&L chart ─────────────────────────────────────────────────

def draw_calendar_zone(
    spot: float,
    strike: float,
    net_debit: float,
    qty: float,
    near_days: int,
    far_days: int,
    iv: float,
    option_type: str = "Call",
) -> None:
    """
    Draw an ASCII P&L chart for a calendar spread at near-leg expiry.

    Shows the bell-shaped profit zone centred on the strike.  Near leg is
    settled at intrinsic; far leg is valued with remaining time (far - near).

    Parameters
    ----------
    spot        : float  Current underlying price
    strike      : float  Strike used for both legs
    net_debit   : float  Net debit paid at entry (max loss)
    qty         : float  Notional quantity (units of underlying)
    near_days   : int    Near-leg expiry in days
    far_days    : int    Far-leg expiry in days
    iv          : float  Implied volatility (decimal)
    option_type : str    "Call" or "Put"
    """
    from market.pricing import bs_call as _bs_call, bs_put as _bs_put

    T_rem = max(far_days - near_days, 1) / 365.0

    width = shutil.get_terminal_size((80, 20)).columns - 4
    width = max(60, min(width, 100))

    lo_price = spot * 0.65
    hi_price = spot * 1.35
    prices   = [lo_price + (hi_price - lo_price) * i / (width - 1) for i in range(width)]

    def _pnl(price: float) -> float:
        if option_type == "Call":
            near_cost = max(price - strike, 0) * qty
            far_val   = _bs_call(price, strike, T_rem, RISK_FREE_RATE, iv) * qty
        else:
            near_cost = max(strike - price, 0) * qty
            far_val   = _bs_put(price, strike, T_rem, RISK_FREE_RATE, iv) * qty
        return far_val - near_cost - net_debit

    pnls = [_pnl(p) for p in prices]

    max_pnl   = max(pnls)
    min_pnl   = min(pnls)
    pnl_range = max_pnl - min_pnl if max_pnl != min_pnl else 1
    tick      = (hi_price - lo_price) / width * 1.5

    # Locate breakevens
    be_lo = be_hi = 0.0
    for i in range(len(pnls) - 1):
        if pnls[i] < 0 <= pnls[i + 1]:
            be_lo = prices[i]
        if pnls[i] >= 0 > pnls[i + 1]:
            be_hi = prices[i + 1]

    chart_height = 12
    rows = []
    for row in range(chart_height):
        threshold = max_pnl - (row / (chart_height - 1)) * pnl_range
        line = ""
        for price, pnl in zip(prices, pnls):
            in_profit = pnl >= 0
            is_be_lo  = be_lo > 0 and abs(price - be_lo)  < tick
            is_be_hi  = be_hi > 0 and abs(price - be_hi)  < tick
            is_spot   = abs(price - spot)   < tick
            is_strike = abs(price - strike) < tick
            at_level  = pnl >= threshold - pnl_range / (chart_height * 2)

            if at_level:
                if is_be_lo or is_be_hi: line += f"{YL}|{R}"
                elif is_spot:            line += f"{CY}◆{R}"
                elif is_strike:          line += f"{GY}┊{R}"
                elif in_profit:          line += f"{GR}█{R}"
                else:                    line += f"{RD}█{R}"
            else:
                line += " "
        rows.append(line)

    print(
        f"\n  {B}{WH}P&L at Near Expiry — {option_type} Calendar Spread{R}  "
        f"{GY}({near_days}d/{far_days}d, spot=${spot:,.0f}){R}"
    )
    print(f"  {GY}{'─' * width}{R}")

    for row_idx, line in enumerate(rows):
        if row_idx == 0:
            label = f"{GR}+${max_pnl:>6.2f}{R}"
        elif row_idx == chart_height // 2:
            label = f"   {WH}$0.00{R} "
        elif row_idx == chart_height - 1:
            label = f"{RD}${min_pnl:>7.2f}{R}"
        else:
            label = "         "
        print(f"  {label}  {line}")

    print(f"  {GY}{'─' * width}{R}")

    # X-axis price labels
    n_labels  = 6
    label_row = ""
    prev_end  = 0
    for i in range(n_labels + 1):
        price = lo_price + (hi_price - lo_price) * i / n_labels
        pos   = int(i * (width - 1) / n_labels)
        lbl   = f"${price:,.0f}"
        pad   = max(0, pos - prev_end)
        label_row += " " * pad + lbl
        prev_end   = pos + len(lbl)
    print(f"  {GY}{label_row}{R}")

    # Legend and key stats
    be_str   = f"${be_lo:,.0f} — ${be_hi:,.0f}" if be_lo > 0 and be_hi > 0 else "—"
    be_width = (f"  {(be_hi - be_lo) / spot * 100:.1f}% wide" if be_lo > 0 and be_hi > 0 else "")
    print(f"""
  {GR}█{R} Profit  {RD}█{R} Loss  {CY}◆{R} Spot  {YL}|{R} Breakeven  {GY}┊{R} Strike

  {WH}Strike     : {YL}${strike:>8,.0f}{R}    {WH}Option type : {YL}{option_type}{R}
  {WH}Net debit  : {RD}${net_debit:>7.2f}{R}    {WH}(= max loss){R}
  {WH}Max profit : {GR}${max_pnl:>7.2f}{R}    {WH}(when spot ≈ strike at near expiry){R}
  {WH}Profit zone: {GR}{be_str}{R}{GY}{be_width}{R}
""")

    # Probability-of-profit bar
    T_near = near_days / 365.0
    if be_lo > 0 and be_hi > 0:
        p_in = max(0.0, (
            prob_otm_put (spot, be_lo, T_near, RISK_FREE_RATE, iv) +
            prob_otm_call(spot, be_hi, T_near, RISK_FREE_RATE, iv) - 1
        ) * 100)
    else:
        p_in = 0.0

    bar_w  = 40
    filled = int(p_in / 100 * bar_w)
    bar    = f"{GR}{'█' * filled}{GY}{'░' * (bar_w - filled)}{R}"
    print(f"  {WH}Est. Prob of Profit: {bar}  {GR}{p_in:.0f}%{R}\n")


# ── Trade History Display ─────────────────────────────────────────────────────

def show_trade_history() -> None:
    """
    Display a list of all closed trades with per-trade P&L and cumulative P&L.

    Reads from SQLite: singles, strangles, and calendars tables.
    Sorts trades by date, displays table with running total.
    """
    from models import get_session, Single, Strangle, Calendar

    session = get_session()
    try:
        _closed = ("Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)")
        singles   = session.query(Single).filter(Single.result.in_(_closed)).all()
        strangles = session.query(Strangle).filter(Strangle.result.in_(_closed)).all()
        calendars = session.query(Calendar).filter(Calendar.result.in_(_closed)).all()
    finally:
        session.close()

    trades = []
    for t in singles:
        trades.append({
            "date":   t.date_close or t.date_open,
            "asset":  t.asset or "",
            "type":   f"Wheel {t.option_type or ''}".strip(),
            "pnl":    float(t.pnl or 0.0),
            "result": t.result or "",
        })
    for t in strangles:
        trades.append({
            "date":   t.date_close or t.date_open,
            "asset":  t.asset or "",
            "type":   "Strangle",
            "pnl":    float(t.pnl or 0.0),
            "result": t.result or "",
        })
    for t in calendars:
        trades.append({
            "date":   t.date_close or t.date_open,
            "asset":  t.asset or "",
            "type":   f"Calendar {t.option_type or ''}".strip(),
            "pnl":    float(t.pnl or 0.0),
            "result": t.result or "",
        })

    trades.sort(key=lambda t: t["date"] or "")

    if not trades:
        print(f"\n  {YL}No closed trades found in database.{R}\n")
        return

    hdr("Trade History")
    print(
        f"\n  {WH}Asset  Date       Type                          P&L         Cumulative{R}"
    )
    print(f"  {CY}{'─' * 80}{R}")

    cumulative = 0.0
    for trade in trades:
        pnl = trade["pnl"]
        cumulative += pnl
        pnl_text = f"${pnl:>9.2f}"
        cum_text = f"${cumulative:>10.2f}"
        pnl_col = GR if pnl >= 0 else RD
        cum_col = GR if cumulative >= 0 else RD

        print(
            f"  {str(trade['asset']):<5}  {str(trade['date']):<10}  {trade['type']:<30}  {pnl_col}{pnl_text}{R}  {cum_col}{cum_text}{R}"
        )

    print(f"  {CY}{'─' * 80}{R}")
    inf("Total trades", str(len(trades)))
    inf("Total P&L", f"{GR if cumulative >= 0 else RD}${cumulative:,.2f}{R}")
    print()
