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

from pricing import prob_otm_put, prob_otm_call
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
