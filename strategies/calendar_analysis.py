"""
strategies/calendar_analysis.py
================================
Analyze far-leg of an expired calendar spread near leg.

When a calendar spread's near leg expires worthless, this module:
  1. Fetches far-leg data from Deribit API
  2. Extracts IV, greeks, and current prices
  3. Analyzes the situation and generates recommendations
  4. Presents options to close, hold, or roll (buy new near leg)

Public API
----------
analyze_calendar_far_leg(asset, strike, option_type, expiry_far, qty, net_debit)
    Fetch far-leg data and return analysis with recommendations.
"""

import requests
from datetime import date, datetime, timedelta
from dataclasses import dataclass

from access.deribit import make_instrument
from config import DERIBIT_PAPER, RISK_FREE_RATE
from market.pricing import bs_call, bs_put, prob_otm_call, prob_otm_put
from ui.display import hdr, sub, inf, ok, warn, GR, RD, YL, CY, WH, GY, R


@dataclass
class FarLegData:
    """Market data for the far leg."""
    instrument_name: str
    mark_price: float
    mark_iv: float
    bid_iv: float
    ask_iv: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    bid_price: float
    ask_price: float
    open_interest: float
    underlying_price: float


@dataclass
class FarLegAnalysis:
    """Analysis and recommendations for far leg."""
    data: FarLegData
    current_pnl: float
    current_pct: float
    iv_level: str  # "very_low", "low", "normal", "high", "very_high"
    theta_decay: float
    delta_comment: str
    recommendation: str
    suggested_rolls: list[tuple[int, str]]  # [(days, instrument_name), ...]


@dataclass
class RollOptionDetail:
    """Detailed information for a roll option (new near leg)."""
    days: int
    expiry_date: date
    strike: float
    option_type: str
    estimated_premium: float  # Premium to be collected for new near leg
    probability_profit: float  # 0.0-1.0, probability of expiring worthless
    expected_pnl: float  # If far leg + rolled near leg held to expiry
    justification: str


def _fetch_far_leg_data(instrument_name: str, paper: bool = True) -> FarLegData | None:
    """
    Fetch far-leg order book data from Deribit API.

    Returns FarLegData on success, None on error.
    """
    url_base = "https://test.deribit.com" if paper else "https://www.deribit.com"
    url = f"{url_base}/api/v2/public/get_order_book"

    try:
        resp = requests.get(
            url,
            params={"instrument_name": instrument_name},
            timeout=10
        )
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("error"):
            warn(f"Deribit API error: {payload['error']}")
            return None

        result = payload.get("result", {})
        greeks = result.get("greeks", {})

        bids = result.get("bids", [])
        asks = result.get("asks", [])
        bid_price = bids[0][0] if bids else 0.0
        ask_price = asks[0][0] if asks else 0.0

        return FarLegData(
            instrument_name=result.get("instrument_name", instrument_name),
            mark_price=result.get("mark_price", 0.0),
            mark_iv=result.get("mark_iv", 0.0) / 100.0,  # API returns as percentage
            bid_iv=result.get("bid_iv", 0.0) / 100.0,    # API returns as percentage
            ask_iv=result.get("ask_iv", 0.0) / 100.0,    # API returns as percentage
            delta=greeks.get("delta", 0.0),
            gamma=greeks.get("gamma", 0.0),
            vega=greeks.get("vega", 0.0),
            theta=greeks.get("theta", 0.0),
            rho=greeks.get("rho", 0.0),
            bid_price=bid_price,
            ask_price=ask_price,
            open_interest=result.get("open_interest", 0.0),
            underlying_price=result.get("underlying_price", 0.0),
        )
    except Exception as exc:
        warn(f"Failed to fetch far-leg data: {exc}")
        return None


def _interpret_iv_level(mark_iv: float) -> str:
    """Classify IV as very_low, low, normal, high, or very_high."""
    # These thresholds are rough heuristics; adjust based on historical IV for the asset
    if mark_iv < 0.30:
        return "very_low"
    elif mark_iv < 0.50:
        return "low"
    elif mark_iv < 0.80:
        return "normal"
    elif mark_iv < 1.20:
        return "high"
    else:
        return "very_high"


def _days_remaining(expiry_str: str) -> int:
    """Calculate days remaining from expiry date string."""
    expiry_str = expiry_str.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            expiry_date = datetime.strptime(expiry_str, fmt).date()
            return max((expiry_date - date.today()).days, 0)
        except ValueError:
            continue
    return 0


def _next_friday(from_date: date) -> date:
    """Return the nearest Friday on or after from_date (Deribit weekly expiry day)."""
    days_ahead = (4 - from_date.weekday()) % 7   # 4 = Friday
    return from_date + timedelta(days=days_ahead)


def calculate_roll_options(
    strike: float,
    option_type: str,
    spot: float,
    iv: float,
    qty: float,
    current_far_pnl: float,
    expiry_far_days: int,
) -> list[RollOptionDetail]:
    """
    Calculate detailed roll options (1d, 3d, 7d near legs) with PoP and expected profit.

    Parameters
    ----------
    strike          : float Strike price
    option_type     : str   "Call" or "Put"
    spot            : float Current spot price
    iv              : float Implied volatility (decimal)
    qty             : float Position quantity
    current_far_pnl : float Current P&L from far leg alone
    expiry_far_days : int   Days remaining on far leg

    Returns
    -------
    list[RollOptionDetail]
        Sorted list of roll options with PoP and justification.
    """
    roll_options = []
    is_call = option_type.lower().startswith("c")

    for near_days in [1, 3, 7]:
        if near_days >= expiry_far_days:
            continue  # Don't suggest rolling past the far leg expiry

        # Calculate expiry date (snap to Friday for Deribit)
        expiry_dt = _next_friday(date.today() + timedelta(days=near_days))
        T_near = near_days / 365.0

        # Estimate premium for new near leg (short position)
        if is_call:
            near_premium = bs_call(spot, strike, T_near, RISK_FREE_RATE, iv) * qty
            prob_profit = prob_otm_call(spot, strike, T_near, RISK_FREE_RATE, iv)
        else:
            near_premium = bs_put(spot, strike, T_near, RISK_FREE_RATE, iv) * qty
            prob_profit = prob_otm_put(spot, strike, T_near, RISK_FREE_RATE, iv)

        # Expected P&L: current far leg P&L + premium from new near leg
        # (This assumes near expires worthless and we keep the premium)
        expected_pnl = current_far_pnl + near_premium

        # Justification based on premium and PoP
        if prob_profit > 0.80:
            justification = f"High PoP ({prob_profit*100:.0f}%), collect ${near_premium:.2f} premium"
        elif prob_profit > 0.65:
            justification = f"Good PoP ({prob_profit*100:.0f}%), collect ${near_premium:.2f} premium"
        else:
            justification = f"Lower PoP ({prob_profit*100:.0f}%), modest ${near_premium:.2f} premium"

        roll_options.append(
            RollOptionDetail(
                days=near_days,
                expiry_date=expiry_dt,
                strike=strike,
                option_type=option_type,
                estimated_premium=round(near_premium, 4),
                probability_profit=round(prob_profit, 4),
                expected_pnl=round(expected_pnl, 4),
                justification=justification,
            )
        )

    return roll_options


def analyze_calendar_far_leg(
    asset: str,
    strike: float,
    option_type: str,
    expiry_far: str,
    qty: float,
    net_debit: float,
    paper: bool = True,
) -> FarLegAnalysis | None:
    """
    Analyze the far leg of an expired calendar spread.

    Fetches current market data from Deribit, interprets IV and greeks,
    and generates recommendations for the position.

    Parameters
    ----------
    asset          : str   Asset symbol (ETH, BTC, etc.)
    strike         : float Strike price
    option_type    : str   "Call" or "Put"
    expiry_far     : str   Far leg expiry date (DD-MMM-YYYY format)
    qty            : float Position quantity
    net_debit      : float Original net debit paid
    paper          : bool  True for testnet, False for mainnet

    Returns
    -------
    FarLegAnalysis | None
        Complete analysis with recommendations, or None if fetch fails.
    """
    # Build the far-leg instrument name
    try:
        expiry_date = datetime.strptime(expiry_far.strip(), "%d-%b-%Y").date()
    except ValueError:
        warn(f"Invalid expiry date format: {expiry_far}")
        return None

    instrument_name = make_instrument(asset, expiry_date, strike, option_type)

    # Fetch data from Deribit
    data = _fetch_far_leg_data(instrument_name, paper=paper)
    if data is None:
        return None

    # Calculate current P&L
    current_value = data.mark_price * qty
    current_pnl = current_value - net_debit
    current_pct = current_value / net_debit if net_debit > 0 else 0.0

    # Interpret IV level
    iv_level = _interpret_iv_level(data.mark_iv)

    # Interpret delta
    if option_type.lower() == "call":
        if data.delta > 0.7:
            delta_comment = f"Deep ITM (delta={data.delta:.2f}) — likely to finish ITM"
        elif data.delta > 0.3:
            delta_comment = f"Moderately ITM (delta={data.delta:.2f}) — some profit potential"
        elif data.delta > 0.1:
            delta_comment = f"Slightly ITM (delta={data.delta:.2f}) — low profit potential"
        else:
            delta_comment = f"OTM/near-the-money (delta={data.delta:.2f}) — low probability of profit"
    else:
        # For puts, lower delta means more OTM
        abs_delta = abs(data.delta)
        if abs_delta > 0.7:
            delta_comment = f"Deep ITM (delta={data.delta:.2f}) — likely to finish ITM"
        elif abs_delta > 0.3:
            delta_comment = f"Moderately ITM (delta={data.delta:.2f}) — some profit potential"
        elif abs_delta > 0.1:
            delta_comment = f"Slightly ITM (delta={data.delta:.2f}) — low profit potential"
        else:
            delta_comment = f"OTM/near-the-money (delta={data.delta:.2f}) — low probability of profit"

    # Interpret theta (daily decay in option value, in dollars per day)
    theta_daily = data.theta / 365.0 if data.theta else 0.0

    # Generate recommendation based on IV and greeks
    days_left = _days_remaining(expiry_far)

    if iv_level == "very_low":
        rec = "CLOSE far leg — IV is very low, limited upside from theta decay. Lock in gains from near-leg expiry."
    elif iv_level == "low" and current_pct > 1.0:
        rec = "CLOSE far leg — IV is low and position is already profitable. Consider locking in the gain."
    elif iv_level == "low" and days_left < 7:
        rec = "CONSIDER ROLLING — IV is low but expiry is near. Either close or roll to a new near leg with high IV."
    elif iv_level == "high" and days_left > 14:
        rec = "HOLD far leg — IV is elevated, theta decay is strong, time is on your side. Consider rolling a new near leg."
    elif iv_level == "very_high":
        rec = "STRONG HOLD — IV is very elevated. Sell a new near leg (sell premium) or close and re-initiate the spread."
    else:
        if current_pnl >= 0 and days_left < 7:
            rec = "CLOSE or ROLL — Position is profitable but expiry is approaching. Lock in gains or roll for more theta."
        elif current_pnl >= 0:
            rec = "HOLD or CLOSE — Position is profitable. Hold for more theta decay or lock in gains."
        else:
            rec = "CLOSE — Position is losing money. Exit and avoid further losses."

    # Suggest new near legs (1d, 3d, 7d rolls) if far leg expiry is after suggested near expiry
    suggested_rolls = []
    for near_days in [1, 3, 7]:
        if near_days < days_left:  # Only suggest if near leg expires before far leg
            suggested_rolls.append((near_days, f"{near_days}d near leg (expires in {near_days}d)"))

    return FarLegAnalysis(
        data=data,
        current_pnl=current_pnl,
        current_pct=current_pct,
        iv_level=iv_level,
        theta_decay=theta_daily * qty,
        delta_comment=delta_comment,
        recommendation=rec,
        suggested_rolls=suggested_rolls,
    )


def display_calendar_analysis(analysis: FarLegAnalysis, asset: str, strike: float, option_type: str) -> None:
    """
    Display the far-leg analysis in a formatted way.
    """
    if analysis is None:
        warn("Could not analyze far leg.")
        return

    data = analysis.data

    hdr(f"Far Leg Analysis — {asset} {option_type} ${strike:,.0f}")

    sub("Market Data")
    inf("Instrument", data.instrument_name)
    inf("Underlying Price", f"${data.underlying_price:,.2f}")
    inf("Mark Price", f"${data.mark_price:.4f}")
    inf("Bid / Ask", f"${data.bid_price:.4f} / ${data.ask_price:.4f}")

    sub("Implied Volatility")
    iv_color = {
        "very_low": GY, "low": YL, "normal": WH,
        "high": YL, "very_high": RD
    }.get(analysis.iv_level, WH)
    inf("Mark IV", f"{iv_color}{analysis.iv_level.upper()} ({data.mark_iv*100:.1f}%){R}")
    inf("Bid / Ask IV", f"{data.bid_iv*100:.1f}% / {data.ask_iv*100:.1f}%")

    sub("Greeks & Greeks-Based Analysis")
    inf("Delta", f"{data.delta:.4f}  {analysis.delta_comment}")
    inf("Gamma", f"{data.gamma:.6f}  {('Neutral' if data.gamma < 0.001 else 'Price-sensitive')}")
    inf("Vega", f"{data.vega:.4f}  {('Long vol' if data.vega > 0 else 'Short vol')}")
    inf("Theta (daily)", f"{analysis.theta_decay:+.4f}  {('Time-decay positive' if analysis.theta_decay > 0 else 'Time-decay negative')}")
    inf("Rho", f"{data.rho:.4f}")

    sub("Position Analysis")
    col = GR if analysis.current_pnl >= 0 else RD
    inf("Current Mark Value", f"${data.mark_price * 1:.2f}")  # Assuming qty≈1 for display
    inf("Current P&L", f"{col}${analysis.current_pnl:.2f}{R}  ({analysis.current_pct*100:.0f}% of original debit)")
    inf("Open Interest", f"{data.open_interest:.0f} contracts")

    sub("Recommendation")
    print(f"  {GR}{analysis.recommendation}{R}\n")

    if analysis.suggested_rolls:
        sub("Roll Options (New Near Legs)")
        for days, desc in analysis.suggested_rolls:
            print(f"  • {desc}")
        print()
