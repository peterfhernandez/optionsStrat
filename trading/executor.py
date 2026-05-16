"""
trading/executor.py
===================
Trade execution logic for all strategies.

Positions are always recorded to the local database (paper trading).
When a broker adapter is supplied the same trade is also submitted as a
live/paper order via the access layer.

Public API
----------
enter_trade(candidate, days, broker)
    Open the position described by candidate, persist it, and optionally
    place a live order through the supplied broker.

Internal helpers
----------------
_enter_csp(c, T, broker)      Cash-Secured Put
_enter_cc(c, T, broker)       Covered Call
_enter_strangle(c, T, broker) Short Strangle
_enter_calendar(c, T, broker) Calendar spread
"""

from datetime import date, datetime, timedelta
from typing import Optional

from config import (
    BUDGET_USD, RISK_FREE_RATE, CALENDAR_FAR_DAYS, SPREAD_WIDTH_PCT, DERIBIT_PAPER,
)
from database import load_wheel_state, save_wheel_state, create_single_trade
from database.strangle_db import load_strangle_state, save_strangle_state, create_strangle_trade
from database.calendar_db import load_calendar_state, save_calendar_state, create_calendar_trade
from database.spread_db import load_spread_state, save_spread_state, create_spread_trade
from market.pricing import bs_put, bs_call, round_strike
from access import BrokerBase, OrderResult, make_instrument, DeribitClient

# Assets whose Deribit contracts are inverse (amount = USD notional).
# All other assets are linear (amount = number of contracts).
_INVERSE_ASSETS = {"BTC", "ETH"}


def _next_friday(from_date: date) -> date:
    """Return the nearest Friday on or after from_date (Deribit weekly expiry day)."""
    days_ahead = (4 - from_date.weekday()) % 7   # 4 = Friday
    return from_date + timedelta(days=days_ahead)


def _expiry_date(days: int) -> date:
    """
    Calculate a Deribit-valid expiry date.
    Snaps the target date (today + days) to the next Friday, since Deribit
    only lists options expiring on Fridays.
    """
    target = date.today() + timedelta(days=days)
    return _next_friday(target)


def _strike_from_instrument(instrument: str) -> float:
    """
    Parse the strike price from a broker instrument name.
    Deribit format: {TICKER}-{DDMMMYY}-{STRIKE}-{C|P}
    e.g. "ETH-30MAY25-2000-P" -> 2000.0, "SOL_USDC-30MAY25-150-P" -> 150.0
    """
    return float(instrument.split("-")[-2])


def _order_price(asset: str, price_usd: float, spot: float) -> float:
    """
    Convert a USD option price to the Deribit order price format, rounded to
    the exchange tick size (0.0001 for all supported option contracts).
    Inverse contracts (BTC, ETH): price as fraction of underlying (price / spot).
    Linear USDC contracts (SOL, XRP): price in USDC directly.
    """
    if asset.upper() in _INVERSE_ASSETS:
        return round(price_usd / spot, 4)
    return round(price_usd, 4)


def _broker_amount(asset: str, spot: float) -> int:
    """
    Return the Deribit order amount for one full budget allocation.
    Always returns an int so requests serialises it without a decimal point
    (Deribit rejects '250.0'; it requires '250').
    """
    if asset.upper() in _INVERSE_ASSETS:
        return int(BUDGET_USD)             # USD notional for inverse contracts
    return max(1, int(BUDGET_USD / spot))  # whole contracts for linear assets


def _place_option(
    broker: BrokerBase,
    asset: str,
    expiry_date: date,
    strike: float,
    option_type: str,    # "put" | "call"
    direction: str,      # "buy" | "sell"
    price_usd: float,    # BS price per-unit in USD
    spot: float,
    label: Optional[str] = None,
) -> OrderResult:
    """Build the instrument name and submit a single-leg order to the broker."""
    # Resolve to the closest listed instrument (expiry + strike) on the exchange.
    instrument = broker.find_instrument(asset, expiry_date, strike, option_type)
    amount     = _broker_amount(asset, spot)
    lmt_price  = _order_price(asset, price_usd, spot)
    return broker.place_order(instrument, direction, amount, "limit", lmt_price, label)


def _enter_csp(c, T: float, broker: BrokerBase) -> dict:
    """Open a Cash-Secured Put position in the wheel state."""
    K          = float(c.strike.replace("$", "").replace(",", ""))
    qty        = BUDGET_USD / K
    unit_price = bs_put(c.spot, K, T, RISK_FREE_RATE, c.iv)
    premium    = unit_price * qty
    expiry_dt  = _expiry_date(c.days)
    expiry     = expiry_dt.strftime("%d-%b-%Y")

    order = _place_option(
        broker, c.asset, expiry_dt, K, "put", "sell",
        unit_price, c.spot, label=f"CSP-{c.asset}",
    )
    K = _strike_from_instrument(order.instrument)

    s = load_wheel_state(c.asset)
    s["stage"]  = "short_put"
    s["broker"] = broker.broker_name
    s["open"]   = {
        "type":             "Put",
        "strike":           K,
        "expiry":           expiry,
        "premium":          round(premium, 4),
        "spot_open":        c.spot,
        "qty":              qty,
        "days":             c.days,
        "asset":            c.asset,
        "broker_order_id":  order.order_id,
        "instrument":       order.instrument,
    }
    s["total_premium"] = s.get("total_premium", 0.0) + premium
    save_wheel_state(c.asset, s)

    create_single_trade(
        asset=c.asset,
        date_open=date.today(),
        option_type="Put",
        strike=K,
        expiry=expiry,
        spot_open=c.spot,
        premium=round(premium, 4),
        qty=qty,
        days=c.days,
        stage="short_put",
        broker=broker.broker_name,
        notes=(
            f"AUTO {c.asset} CSP, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag}"
        ),
    )

    return s["open"]


def _enter_cc(c, T: float, broker: BrokerBase) -> dict:
    """Open a Covered Call position. Requires wheel state in 'holding'."""
    s = load_wheel_state(c.asset)
    if s["stage"] != "holding":
        raise RuntimeError(f"Cannot enter CC for {c.asset}: wheel stage={s['stage']}")

    K          = float(c.strike.replace("$", "").replace(",", ""))
    qty        = s["asset_held"] or (BUDGET_USD / c.spot)
    unit_price = bs_call(c.spot, K, T, RISK_FREE_RATE, c.iv)
    premium    = unit_price * qty
    expiry_dt  = _expiry_date(c.days)
    expiry     = expiry_dt.strftime("%d-%b-%Y")

    s["stage"]  = "short_call"
    s["broker"] = broker.broker_name
    s["open"]   = {
        "type":      "Call",
        "strike":    K,
        "expiry":    expiry,
        "premium":   round(premium, 4),
        "spot_open": c.spot,
        "qty":       qty,
        "days":      c.days,
        "asset":     c.asset,
    }
    order = _place_option(
        broker, c.asset, expiry_dt, K, "call", "sell",
        unit_price, c.spot, label=f"CC-{c.asset}",
    )
    K = _strike_from_instrument(order.instrument)
    s["open"]["strike"] = K

    s["open"]["broker_order_id"] = order.order_id
    s["open"]["instrument"]      = order.instrument
    s["total_premium"] = s.get("total_premium", 0.0) + premium
    save_wheel_state(c.asset, s)

    create_single_trade(
        asset=c.asset,
        date_open=date.today(),
        option_type="Call",
        strike=K,
        expiry=expiry,
        spot_open=c.spot,
        premium=round(premium, 4),
        qty=qty,
        days=c.days,
        stage="short_call",
        broker=broker.broker_name,
        notes=(
            f"AUTO {c.asset} CC, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag}"
        ),
    )

    return s["open"]


def _enter_strangle(c, T: float, broker: BrokerBase) -> dict:
    """Open a short strangle position."""
    Kp         = c.put_strike
    Kc         = c.call_strike
    qty        = BUDGET_USD / c.spot
    put_price  = bs_put (c.spot, Kp, T, RISK_FREE_RATE, c.iv)
    call_price = bs_call(c.spot, Kc, T, RISK_FREE_RATE, c.iv)
    pp         = put_price  * qty
    cp         = call_price * qty
    tot        = pp + cp
    expiry_dt  = _expiry_date(c.days)
    expiry     = expiry_dt.strftime("%d-%b-%Y")

    put_order  = _place_option(
        broker, c.asset, expiry_dt, Kp, "put",  "sell",
        put_price, c.spot, label=f"STR-P-{c.asset}",
    )
    call_order = _place_option(
        broker, c.asset, expiry_dt, Kc, "call", "sell",
        call_price, c.spot, label=f"STR-C-{c.asset}",
    )
    Kp = _strike_from_instrument(put_order.instrument)
    Kc = _strike_from_instrument(call_order.instrument)

    trade = create_strangle_trade(
        asset=c.asset,
        date_open=date.today(),
        put_strike=Kp,
        call_strike=Kc,
        spot_open=c.spot,
        total_premium=round(tot, 4),
        qty=qty,
        days=c.days,
        expiry=expiry,
        broker=broker.broker_name,
        notes=(
            f"AUTO {c.asset} strangle, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag or 'N/A'}"
        ),
    )

    s = load_strangle_state(c.asset)
    s["broker"] = broker.broker_name
    s["open"] = {
        "put_strike":           Kp,
        "call_strike":          Kc,
        "total_premium":        round(tot, 4),
        "qty":                  qty,
        "expiry":               expiry,
        "spot_open":            c.spot,
        "days":                 c.days,
        "asset":                c.asset,
        "trade_id":             trade.id,
        "broker_put_order_id":  put_order.order_id,
        "broker_call_order_id": call_order.order_id,
        "put_instrument":       put_order.instrument,
        "call_instrument":      call_order.instrument,
    }
    s["total_premium"] = s.get("total_premium", 0.0) + tot
    s["trades"]        = s.get("trades",        0)   + 1
    save_strangle_state(c.asset, s)

    return s["open"]


def _enter_calendar(c, T: float, broker: BrokerBase) -> dict:
    """Open a calendar (Cal-C or Cal-P) spread position."""
    K           = float(c.strike.split()[0].replace("$", "").replace(",", ""))
    far_days    = c.far_days or CALENDAR_FAR_DAYS
    T_far       = far_days / 365.0
    qty         = BUDGET_USD / c.spot
    option_type = "Call" if c.strategy == "Cal-C" else "Put"
    bs_fn       = bs_call if option_type == "Call" else bs_put

    near_price = bs_fn(c.spot, K, T,     RISK_FREE_RATE, c.iv)
    far_price  = bs_fn(c.spot, K, T_far, RISK_FREE_RATE, c.iv)
    near_prem  = near_price * qty
    far_prem   = far_price  * qty
    net_debit  = far_prem - near_prem

    expiry_near_dt = _expiry_date(c.days)
    expiry_far_dt  = _expiry_date(far_days)
    expiry_near    = expiry_near_dt.strftime("%d-%b-%Y")
    expiry_far     = expiry_far_dt.strftime("%d-%b-%Y")

    ot = option_type.lower()
    # Calendar: sell near leg, buy far leg
    near_order = _place_option(
        broker, c.asset, expiry_near_dt, K, ot, "sell",
        near_price, c.spot, label=f"CAL-NEAR-{c.asset}",
    )
    far_order = _place_option(
        broker, c.asset, expiry_far_dt,  K, ot, "buy",
        far_price, c.spot, label=f"CAL-FAR-{c.asset}",
    )
    K = _strike_from_instrument(near_order.instrument)

    trade = create_calendar_trade(
        asset=c.asset,
        date_open=date.today(),
        option_type=option_type,
        strike=K,
        expiry_near=expiry_near,
        expiry_far=expiry_far,
        near_days=c.days,
        far_days=far_days,
        qty=qty,
        spot_open=c.spot,
        near_prem=round(near_prem, 4),
        far_prem=round(far_prem, 4),
        net_debit=round(net_debit, 4),
        broker=broker.broker_name,
        notes=(
            f"AUTO {c.asset} {option_type} calendar, "
            f"{c.days}d/{far_days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr"
        ),
    )

    s = load_calendar_state(c.asset)
    s["broker"] = broker.broker_name
    s["open"] = {
        "strike":               K,
        "option_type":          option_type,
        "near_prem":            round(near_prem, 4),
        "far_prem":             round(far_prem,  4),
        "net_debit":            round(net_debit, 4),
        "qty":                  qty,
        "expiry_near":          expiry_near,
        "expiry_far":           expiry_far,
        "spot_open":            c.spot,
        "near_days":            c.days,
        "far_days":             far_days,
        "asset":                c.asset,
        "trade_id":             trade.id,
        "broker_near_order_id": near_order.order_id,
        "broker_far_order_id":  far_order.order_id,
        "near_instrument":      near_order.instrument,
        "far_instrument":       far_order.instrument,
    }
    s["trades"] = s.get("trades", 0) + 1
    save_calendar_state(c.asset, s)

    return s["open"]


def _enter_spread(c, T: float, broker: BrokerBase) -> dict:
    """Open a credit spread (BPS or BCS) position."""
    from config import SUPPORTED_ASSETS
    spread_type = c.strategy   # "BPS" | "BCS"
    otm         = c.otm_pct
    width       = SPREAD_WIDTH_PCT
    cfg         = SUPPORTED_ASSETS[c.asset]
    strike_rnd  = cfg["strike_round"]

    if spread_type == "BPS":
        short_k = round_strike(c.spot * (1 - otm),          strike_rnd)
        long_k  = round_strike(c.spot * (1 - otm - width),  strike_rnd)
        short_p = bs_put(c.spot, short_k, T, RISK_FREE_RATE, c.iv)
        long_p  = bs_put(c.spot, long_k,  T, RISK_FREE_RATE, c.iv)
        otype   = "put"
    else:  # BCS
        short_k = round_strike(c.spot * (1 + otm),          strike_rnd)
        long_k  = round_strike(c.spot * (1 + otm + width),  strike_rnd)
        short_p = bs_call(c.spot, short_k, T, RISK_FREE_RATE, c.iv)
        long_p  = bs_call(c.spot, long_k,  T, RISK_FREE_RATE, c.iv)
        otype   = "call"

    qty        = BUDGET_USD / c.spot
    net_credit = (short_p - long_p) * qty
    max_loss   = abs(short_k - long_k) * qty - net_credit
    expiry_dt  = _expiry_date(c.days)
    expiry     = expiry_dt.strftime("%d-%b-%Y")

    short_order = _place_option(
        broker, c.asset, expiry_dt, short_k, otype, "sell",
        short_p, c.spot, label=f"SPR-S-{c.asset}",
    )
    long_order = _place_option(
        broker, c.asset, expiry_dt, long_k, otype, "buy",
        long_p, c.spot, label=f"SPR-L-{c.asset}",
    )
    short_k = _strike_from_instrument(short_order.instrument)
    long_k  = _strike_from_instrument(long_order.instrument)

    trade = create_spread_trade(
        asset=c.asset,
        spread_type=spread_type,
        date_open=date.today(),
        short_strike=short_k,
        long_strike=long_k,
        spot_open=c.spot,
        net_credit=round(net_credit, 4),
        max_loss=round(max_loss, 4),
        qty=qty,
        days=c.days,
        expiry=expiry,
        broker=broker.broker_name,
        notes=(
            f"AUTO {c.asset} {spread_type}, {c.days}d, "
            f"P(prof)={c.prob_profit:.0f}% yld={c.yield_ann:.0f}%/yr "
            f"liq={c.liquidity_tag or 'N/A'}"
        ),
    )

    s = load_spread_state(c.asset)
    s["broker"] = broker.broker_name
    s["open"] = {
        "spread_type":            spread_type,
        "short_strike":           short_k,
        "long_strike":            long_k,
        "net_credit":             round(net_credit, 4),
        "max_loss":               round(max_loss, 4),
        "qty":                    qty,
        "expiry":                 expiry,
        "spot_open":              c.spot,
        "days":                   c.days,
        "asset":                  c.asset,
        "trade_id":               trade.id,
        "broker_short_order_id":  short_order.order_id,
        "broker_long_order_id":   long_order.order_id,
        "short_instrument":       short_order.instrument,
        "long_instrument":        long_order.instrument,
    }
    s["net_credit"] = s.get("net_credit", 0.0) + net_credit
    s["trades"]     = s.get("trades", 0) + 1
    save_spread_state(c.asset, s)

    return s["open"]


def enter_trade(
    c,
    days: Optional[int] = None,
    broker: Optional[BrokerBase] = None,
) -> dict:
    """
    Open the position described by c, persist it, and place an order through
    the broker.

    Parameters
    ----------
    c:      Strategy candidate with .strategy, .asset, .spot, .iv, .days, etc.
    days:   Override c.days when supplied.
    broker: BrokerBase adapter.  Defaults to DeribitClient(paper=DERIBIT_PAPER)
            so trades are always submitted to Deribit testnet unless DERIBIT_PAPER
            is set to False or a different adapter is supplied explicitly.

    Returns
    -------
    The open-position dict, with broker_order_id field(s) appended.
    """
    if broker is None:
        broker = DeribitClient(paper=DERIBIT_PAPER)

    days_eff = days or c.days
    T        = days_eff / 365.0

    if c.strategy == "CSP":
        return _enter_csp(c, T, broker)
    if c.strategy == "CC":
        return _enter_cc(c, T, broker)
    if c.strategy == "Strangle":
        return _enter_strangle(c, T, broker)
    if c.strategy in ("Cal-C", "Cal-P"):
        return _enter_calendar(c, T, broker)
    if c.strategy in ("BPS", "BCS"):
        return _enter_spread(c, T, broker)

    raise ValueError(f"Unsupported strategy '{c.strategy}'")


# ── Close helpers ────────────────────────────────────────────────────────────

def _parse_expiry(expiry_str: str) -> date:
    """Parse an expiry string in any of the formats used by the state files."""
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised expiry format: {expiry_str!r}")


def close_wheel_position(op: dict, broker: BrokerBase, spot: float) -> "OrderResult":
    """
    Place a market buy order to close a short put or call leg.

    Parameters
    ----------
    op:     The open-position dict stored in wheel state.
    broker: Broker adapter (same one used to open the trade).
    spot:   Current spot price (used to compute contract amount).
    """
    asset      = op["asset"]
    amount     = _broker_amount(asset, spot)
    instrument = op["instrument"]
    return broker.place_order(
        instrument, "buy", amount, "market",
        label=f"CLOSE-{op['type'].upper()}-{asset}",
    )


def close_strangle_position(
    op: dict, broker: BrokerBase, spot: float
) -> "tuple[OrderResult, OrderResult]":
    """
    Place market buy orders to close both legs of a short strangle.

    Returns (put_order, call_order).
    """
    asset      = op["asset"]
    amount     = _broker_amount(asset, spot)
    put_instr  = op["put_instrument"]
    call_instr = op["call_instrument"]
    put_order  = broker.place_order(put_instr,  "buy", amount, "market", label=f"CLOSE-STR-P-{asset}")
    call_order = broker.place_order(call_instr, "buy", amount, "market", label=f"CLOSE-STR-C-{asset}")
    return put_order, call_order


def close_calendar_position(
    op: dict, broker: BrokerBase, spot: float
) -> "tuple[OrderResult, OrderResult]":
    """
    Close a calendar spread: buy back the near leg (we were short) and
    sell back the far leg (we were long).

    Returns (near_order, far_order).
    """
    asset      = op["asset"]
    amount     = _broker_amount(asset, spot)
    near_instr = op["near_instrument"]
    far_instr  = op["far_instrument"]
    near_order = broker.place_order(near_instr, "buy",  amount, "market", label=f"CLOSE-CAL-NEAR-{asset}")
    far_order  = broker.place_order(far_instr,  "sell", amount, "market", label=f"CLOSE-CAL-FAR-{asset}")
    return near_order, far_order


def close_spread_position(
    op: dict, broker: BrokerBase, spot: float
) -> "tuple[OrderResult, OrderResult]":
    """
    Close a credit spread: buy back the short leg and sell back the long leg.

    Returns (short_order, long_order).
    """
    asset      = op["asset"]
    amount     = _broker_amount(asset, spot)

    # Use the exact instrument names recorded at open time — never recompute.
    short_instr = op["short_instrument"]
    long_instr  = op["long_instrument"]

    # Buy back the short leg (close our short position)
    short_order = broker.place_order(
        short_instr, "buy", amount, "market", label=f"CLOSE-SPR-S-{asset}"
    )
    # Sell back the long leg (close our long protection)
    long_order = broker.place_order(
        long_instr, "sell", amount, "market", label=f"CLOSE-SPR-L-{asset}"
    )
    return short_order, long_order
