"""
strategies/monitor.py
=====================
Cross-strategy position monitor for the Crypto Options Strategy Tool.

Checks all open positions across all assets and strategies. Triggers
automatic closes when stop-loss, take-profit, or expiry conditions are
met, and logs every auto-close to the SQLite database.

Designed to be extensible — register a new strategy by adding one entry
to _REGISTRY at the bottom of this file.

Public API
----------
run_monitor(spot, iv, days, asset, silent=False)
    Check all open positions and auto-close any that breach thresholds.
    Called silently on every main menu display, or verbosely via menu.

Internal helpers
----------------
_days_remaining(expiry_str)         Days left until expiry date string
_check_strangle(asset, spot, iv,    Evaluate and optionally close a
                silent)              strangle position
_check_wheel(asset, spot, iv,       Evaluate and optionally close a
             silent)                 wheel position
_check_calendar(asset, spot, iv,    Evaluate and optionally close a
                silent)              calendar spread position
_REGISTRY                           List of checker functions to call
"""

from datetime import date, datetime
import requests

from config  import (
    SUPPORTED_ASSETS, RISK_FREE_RATE,
    STOP_LOSS_MULTIPLIER, BUDGET_USD,
    CALENDAR_STOP_PCT, DERIBIT_PAPER,
)
from market.pricing import bs_put, bs_call
from ui.display import ok, warn, err, hdr, inf, sub, GR, RD, YL, CY, WH, GY, R
from database.strangle_db import load_strangle_state, save_strangle_state, close_strangle_trade
from database.calendar_db import load_calendar_state, save_calendar_state, close_calendar_trade
from database.wheel_db import load_wheel_state, save_wheel_state, close_single_trade
from models import get_session, Single
from access import BrokerBase, DeribitClient
from trading.executor import close_wheel_position, close_strangle_position, close_calendar_position


# ── Thresholds ────────────────────────────────────────────────────────────────

# Take-profit: auto-close when position retains less than this fraction
# of original premium (i.e. nearly worthless — lock in the gain)
TAKE_PROFIT_THRESHOLD = 0.05   # 5% of premium remaining → close (was 10%)

# Minimum days before expiry to allow take-profit closes (prevent premature closure)
MIN_DAYS_FOR_TP = 2            # Don't auto-close puts/calls within 2 days of expiry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_remaining(expiry_str: str) -> int:
    """
    Calculate calendar days remaining until an expiry date string.

    Accepts formats: "25-Apr-2026", "25-APR-2026", "2026-04-25".
    Returns 0 if expiry is today or in the past.
    """
    expiry_str = expiry_str.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            expiry_date = datetime.strptime(expiry_str, fmt).date()
            return max((expiry_date - date.today()).days, 0)
        except ValueError:
            continue
    return 0  # unrecognised format → treat as expired


# ── Broker call helper ────────────────────────────────────────────────────────

_BROKER_OK     = "ok"       # order placed successfully
_BROKER_SKIP   = "skip"    # instrument gone (4xx) — record close locally
_BROKER_ABORT  = "abort"   # transient server/network error — do not close


def _try_broker_close(fn, *args, label: str = "") -> str:
    """
    Attempt a broker close.  Returns one of _BROKER_OK / _BROKER_SKIP / _BROKER_ABORT.

    4xx errors mean the instrument no longer exists on the exchange (e.g. expired
    option); the position should still be recorded as closed locally.
    5xx / network errors are transient — leave the position open for the next cycle.
    """
    try:
        fn(*args)
        return _BROKER_OK
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if 400 <= status < 500:
            warn(f"Broker rejected order ({label}) [{status}] — instrument gone, recording close locally")
            return _BROKER_SKIP
        warn(f"Broker server error ({label}) [{status}]: {exc} — position NOT closed")
        return _BROKER_ABORT
    except requests.exceptions.ConnectionError as exc:
        warn(f"Broker unreachable ({label}): {exc} — position NOT closed")
        return _BROKER_ABORT
    except Exception as exc:
        warn(f"Broker error ({label}): {exc} — position NOT closed")
        return _BROKER_ABORT


# ── Strangle checker ──────────────────────────────────────────────────────────

def _check_strangle(
    asset: str,
    spot: float,
    iv: float,
    silent: bool,
    broker: BrokerBase | None = None,
) -> bool:
    """
    Evaluate an open strangle position and auto-close if thresholds are met.

    Checks in priority order:
      1. Expiry reached  → close at intrinsic value
      2. Stop-loss hit   → close at current mark price (loss)
      3. Take-profit hit → close at current mark price (gain locked in)

    Returns True if a close was triggered, False otherwise.
    """
    state = load_strangle_state(asset)
    if not state.get("open"):
        return False

    op  = state["open"]
    Kp  = op["put_strike"]
    Kc  = op["call_strike"]
    p0  = op["total_premium"]
    qty = op["qty"]
    T   = max(_days_remaining(op.get("expiry", "")) / 365.0, 1 / 365.0)
    days_left = _days_remaining(op.get("expiry", ""))

    cur_pp  = bs_put (spot, Kp, T, RISK_FREE_RATE, iv) * qty
    cur_cp  = bs_call(spot, Kc, T, RISK_FREE_RATE, iv) * qty
    cur_val = cur_pp + cur_cp
    mult    = cur_val / p0 if p0 > 0 else 0.0
    pnl     = p0 - cur_val

    # ── Determine trigger ─────────────────────────────────────────────────────
    trigger = None
    result  = None
    note    = None

    if days_left == 0:
        trigger = "EXPIRY"
        pnl     = p0 - max(Kp - spot, 0) * qty - max(spot - Kc, 0) * qty
        result  = "Win" if pnl >= 0 else "Loss"
        note    = f"Auto-closed at expiry. P&L: ${pnl:.2f}"

    elif mult >= STOP_LOSS_MULTIPLIER:
        trigger = "STOP-LOSS"
        result  = "Loss (Auto Stop)"
        note    = f"Auto stop-loss at {mult:.2f}x premium. P&L: ${pnl:.2f}"

    elif cur_val <= p0 * TAKE_PROFIT_THRESHOLD and days_left >= MIN_DAYS_FOR_TP:
        trigger = "TAKE-PROFIT"
        result  = "Win (Auto TP)"
        note    = f"Auto take-profit — {(1-mult)*100:.0f}% of premium captured. P&L: ${pnl:.2f}"

    if trigger is None:
        if not silent:
            colour = GR if pnl >= 0 else RD
            inf(f"  {asset} Strangle",
                f"val=${cur_val:.2f}  {mult:.2f}x  P&L={colour}${pnl:.2f}{R}  "
                f"{days_left}d left  → No action")
        return False

    # ── Auto-close ────────────────────────────────────────────────────────────
    colour = GR if pnl >= 0 else RD
    print(f"\n  {RD if trigger == 'STOP-LOSS' else YL if trigger == 'EXPIRY' else GR}"
          f"⚡ AUTO-CLOSE [{trigger}] {asset} Strangle{R}")
    print(f"  Put ${Kp:,.0f} / Call ${Kc:,.0f}  |  "
          f"Premium: ${p0:.2f}  |  P&L: {colour}${pnl:.2f}{R}")

    if broker is not None:
        if _try_broker_close(close_strangle_position, op, broker, spot, label="strangle") == _BROKER_ABORT:
            return False

    trade_id = op.get("trade_id")
    if trade_id:
        close_strangle_trade(
            trade_id,
            date_close=date.today(),
            spot_close=spot,
            pnl=round(pnl, 4),
            result=result,
            notes=note,
        )

    if pnl >= 0:
        state["wins"]   += 1
    else:
        state["losses"] += 1
    state["open"] = None
    save_strangle_state(asset, state)
    ok(f"{asset} strangle auto-closed and logged to database.")
    return True


# ── Wheel checker ─────────────────────────────────────────────────────────────

def _check_wheel(
    asset: str,
    spot: float,
    iv: float,
    silent: bool,
    broker: BrokerBase | None = None,
) -> bool:
    """
    Evaluate an open wheel position and auto-close if thresholds are met.

    Checks in priority order:
      1. Expiry reached  → expire worthless or at intrinsic value
      2. Stop-loss hit   → close at current mark price (loss)
      3. Take-profit hit → close at current mark price (gain locked in)

    Returns True if a close was triggered, False otherwise.
    """
    state = load_wheel_state(asset)
    if not state.get("open"):
        return False

    op       = state["open"]
    K        = op["strike"]
    p0       = op["premium"]
    qty      = op.get("qty", BUDGET_USD / K)
    opt_type = op["type"]   # "Put" or "Call"
    days_left = _days_remaining(op.get("expiry", ""))
    T        = max(days_left / 365.0, 1 / 365.0)

    cur = (
        bs_put (spot, K, T, RISK_FREE_RATE, iv)
        if opt_type == "Put"
        else bs_call(spot, K, T, RISK_FREE_RATE, iv)
    ) * qty
    mult = cur / p0 if p0 > 0 else 0.0
    pnl  = p0 - cur

    # ── Determine trigger ─────────────────────────────────────────────────────
    trigger = None
    result  = None
    note    = None

    if days_left == 0:
        trigger   = "EXPIRY"
        expired_otm = (spot > K if opt_type == "Put" else spot < K)
        pnl       = p0 if expired_otm else p0 - abs(spot - K) * qty
        result    = "Win" if expired_otm else "Loss"
        note      = f"Auto-expired {'OTM' if expired_otm else 'ITM'}. P&L: ${pnl:.2f}"

    elif mult >= STOP_LOSS_MULTIPLIER:
        trigger = "STOP-LOSS"
        result  = "Loss (Auto Stop)"
        note    = f"Auto stop-loss at {mult:.2f}x premium. P&L: ${pnl:.2f}"

    elif cur <= p0 * TAKE_PROFIT_THRESHOLD and days_left >= MIN_DAYS_FOR_TP:
        trigger = "TAKE-PROFIT"
        result  = "Win (Auto TP)"
        note    = f"Auto take-profit — {(1-mult)*100:.0f}% captured. P&L: ${pnl:.2f}"

    if trigger is None:
        if not silent:
            colour = GR if pnl >= 0 else RD
            inf(f"  {asset} {opt_type}",
                f"val=${cur:.2f}  {mult:.2f}x  P&L={colour}${pnl:.2f}{R}  "
                f"{days_left}d left  → No action")
        return False

    # ── Auto-close ────────────────────────────────────────────────────────────
    colour = GR if pnl >= 0 else RD
    print(f"\n  {RD if trigger == 'STOP-LOSS' else YL if trigger == 'EXPIRY' else GR}"
          f"⚡ AUTO-CLOSE [{trigger}] {asset} {opt_type}{R}")
    print(f"  Strike ${K:,.0f}  |  Premium: ${p0:.2f}  |  P&L: {colour}${pnl:.2f}{R}")

    if broker is not None:
        if _try_broker_close(close_wheel_position, op, broker, spot, label="wheel") == _BROKER_ABORT:
            return False

    # Close the open Single trade record in the database
    stage_tag = "short_put" if opt_type == "Put" else "short_call"
    session = get_session()
    try:
        open_trade = (
            session.query(Single)
            .filter(Single.asset == asset, Single.stage == stage_tag, Single.date_close.is_(None))
            .order_by(Single.date_open.desc())
            .first()
        )
    finally:
        session.close()

    if open_trade:
        close_single_trade(
            trade_id=open_trade.id,
            date_close=date.today(),
            spot_close=spot,
            pnl=round(pnl, 4),
            result=result,
            notes=note,
        )

    if pnl >= 0:
        state["wins"]   += 1
    else:
        state["losses"] += 1
    state["open"]  = None
    state["stage"] = "no_position"
    save_wheel_state(asset, state)
    ok(f"{asset} {opt_type} auto-closed and logged to database.")
    return True


# ── Calendar Spread checker ───────────────────────────────────────────────────

def _check_calendar(
    asset: str,
    spot: float,
    iv: float,
    silent: bool,
    broker: BrokerBase | None = None,
) -> bool:
    """
    Evaluate an open calendar spread position and auto-close if thresholds are met.

    Checks in priority order:
      1. Near-leg expiry reached → close at near-expiry P&L
      2. Stop-loss hit           → spread value ≤ CALENDAR_STOP_PCT of debit
      3. Take-profit hit         → spread value ≥ 150% of debit

    Returns True if a close was triggered, False otherwise.
    """
    state = load_calendar_state(asset)
    if not state.get("open"):
        return False

    op          = state["open"]
    K           = op["strike"]
    opt_type    = op["option_type"]
    net_debit   = op["net_debit"]
    qty         = op["qty"]
    near_days   = op["near_days"]
    far_days    = op["far_days"]

    near_left = _days_remaining(op.get("expiry_near", ""))
    far_left  = _days_remaining(op.get("expiry_far",  ""))
    T_near    = max(near_left / 365.0, 1 / 365.0)
    T_far     = max(far_left  / 365.0, 1 / 365.0)

    if opt_type == "Call":
        far_val  = bs_call(spot, K, T_far,  RISK_FREE_RATE, iv) * qty
        near_val = bs_call(spot, K, T_near, RISK_FREE_RATE, iv) * qty
    else:
        far_val  = bs_put(spot, K, T_far,  RISK_FREE_RATE, iv) * qty
        near_val = bs_put(spot, K, T_near, RISK_FREE_RATE, iv) * qty

    sv   = far_val - near_val
    pct  = sv / net_debit if net_debit > 0 else 0.0

    # Near expiry: use intrinsic + remaining far value
    if near_left == 0:
        if opt_type == "Call":
            near_cost = max(spot - K, 0) * qty
            T_rem = max(far_days - near_days, 1) / 365.0
            far_rem = bs_call(spot, K, T_rem, RISK_FREE_RATE, iv) * qty
        else:
            near_cost = max(K - spot, 0) * qty
            T_rem = max(far_days - near_days, 1) / 365.0
            far_rem = bs_put(spot, K, T_rem, RISK_FREE_RATE, iv) * qty
        pnl     = far_rem - near_cost - net_debit
        trigger = "EXPIRY"
        result  = "Win" if pnl >= 0 else "Loss"
        note    = f"Auto-closed at near-leg expiry. P&L: ${pnl:.2f}"

    elif pct <= CALENDAR_STOP_PCT:
        pnl     = sv - net_debit
        trigger = "STOP-LOSS"
        result  = "Loss (Auto Stop)"
        note    = f"Auto stop-loss — spread at {pct*100:.0f}% of debit. P&L: ${pnl:.2f}"

    elif pct >= 1.50:
        pnl     = sv - net_debit
        trigger = "TAKE-PROFIT"
        result  = "Win (Auto TP)"
        note    = f"Auto take-profit — spread at {pct*100:.0f}% of debit. P&L: ${pnl:.2f}"

    else:
        if not silent:
            pnl = sv - net_debit
            col = GR if pnl >= 0 else RD
            inf(f"  {asset} {opt_type} Calendar",
                f"spread=${sv:.2f}  {pct*100:.0f}% of debit  "
                f"P&L={col}${pnl:.2f}{R}  {near_left}d near / {far_left}d far  → No action")
        return False

    # ── Auto-close ────────────────────────────────────────────────────────────
    col = GR if pnl >= 0 else RD
    trig_col = RD if trigger == "STOP-LOSS" else YL if trigger == "EXPIRY" else GR
    print(f"\n  {trig_col}⚡ AUTO-CLOSE [{trigger}] {asset} {opt_type} Calendar{R}")
    print(f"  Strike ${K:,.0f}  |  Net debit: ${net_debit:.2f}  |  P&L: {col}${pnl:.2f}{R}")

    if broker is not None:
        if _try_broker_close(close_calendar_position, op, broker, spot, label="calendar") == _BROKER_ABORT:
            return False

    trade_id = op.get("trade_id")
    if trade_id:
        close_calendar_trade(
            trade_id=trade_id,
            date_close=date.today(),
            spot_close=spot,
            pnl=round(pnl, 4),
            result=result,
            notes=note,
        )

    if pnl >= 0:
        state["wins"]  += 1
    else:
        state["losses"] += 1
    state["total_pnl"] = state.get("total_pnl", 0.0) + pnl
    state["open"] = None
    save_calendar_state(asset, state)
    ok(f"{asset} {opt_type} calendar auto-closed and logged to database.")
    return True


# ── Registry ──────────────────────────────────────────────────────────────────
#
# To add a new strategy, append a checker function here.
# Each checker must have the signature:
#   fn(asset, spot, iv, silent) -> bool
#
_REGISTRY = [
    _check_strangle,
    _check_wheel,
    _check_calendar,
]


# ── Public API ────────────────────────────────────────────────────────────────

def run_monitor(
    spot: float,
    iv: float,
    days: int,
    asset: str,
    silent: bool = True,
    *,
    broker: BrokerBase | None = None,
) -> None:
    """
    Check all open positions across all assets and strategies.

    Runs every checker in _REGISTRY for every asset in SUPPORTED_ASSETS.
    Auto-closes and logs any position that breaches a threshold.

    Parameters
    ----------
    spot   : float       Current spot price for the active asset
    iv     : float       Current IV for the active asset
    days   : int         Days to expiry (used for IV context only)
    asset  : str         Currently selected asset (used for spot/IV context)
    silent : bool        True = only print on trigger; False = print all statuses
    broker : BrokerBase  Adapter used to place close orders on auto-close.
                         Defaults to DeribitClient(paper=DERIBIT_PAPER).
    """
    from market.market_data import get_spot_price, get_deribit_iv

    if broker is None:
        broker = DeribitClient(paper=DERIBIT_PAPER)

    if not silent:
        hdr("Position Monitor")
        print(f"  {GY}Checking all open positions across all assets...{R}\n")

    any_triggered = False

    for a in SUPPORTED_ASSETS:
        # Reuse already-fetched values for the active asset to avoid
        # an unnecessary API call; fetch fresh for all other assets
        if a == asset:
            a_spot = spot
            a_iv   = iv
        else:
            a_spot = get_spot_price(a)
            if not a_spot:
                if not silent:
                    warn(f"Could not fetch {a} price — skipping {a} positions")
                continue
            a_iv = get_deribit_iv(a, a_spot, days) or iv

        for checker in _REGISTRY:
            triggered = checker(a, a_spot, a_iv, silent, broker)
            if triggered:
                any_triggered = True

    if not silent:
        if not any_triggered:
            print(f"\n  {GY}No positions required action.{R}")
        print(f"\n  {GY}Thresholds:  "
              f"Stop-loss {STOP_LOSS_MULTIPLIER:.1f}x strangle  |  "
              f"Take-profit <{TAKE_PROFIT_THRESHOLD*100:.0f}% remaining (>{MIN_DAYS_FOR_TP}d from expiry)  |  "
              f"Calendar stop {CALENDAR_STOP_PCT*100:.0f}% of debit{R}\n")

    elif any_triggered:
        print(f"\n  {YL}⚡ One or more positions were auto-closed. "
              f"Select [M] Monitor for details.{R}")
