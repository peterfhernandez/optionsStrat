"""
trading/expiry_handler.py
=========================
Shared option expiry handling logic for all strategies.

This module provides unified logic for handling options that expire worthless
or in-the-money. Used by: calendar, scanner, spread, strangle, executor, monitor.

Public API
----------
is_expired_worthless(position_type, spot, strike)
    Determine if an option expired worthless (OTM).

handle_expires_worthless(position_type, spot, strike, is_short, premium, qty)
    Calculate P&L when an option expires worthless.

handle_expires_itm(position_type, spot, strike, is_short, premium, qty)
    Calculate P&L when an option expires in-the-money.
"""


def is_expired_worthless(position_type: str, spot: float, strike: float) -> bool:
    """
    Determine if an option expired worthless (out-of-the-money).

    Parameters
    ----------
    position_type : str
        "Call" or "Put"
    spot : float
        Underlying spot price at expiration
    strike : float
        Strike price

    Returns
    -------
    bool
        True if the option expired worthless (OTM), False if in-the-money.
    """
    if position_type.lower().startswith("c"):  # Call
        return spot < strike
    else:  # Put
        return spot > strike


def handle_expires_worthless(
    position_type: str,
    spot: float,
    strike: float,
    is_short: bool,
    premium: float,
    qty: float = 1.0,
) -> dict:
    """
    Calculate P&L when an option expires worthless.

    For short positions: premium is kept (profit).
    For long positions: premium is lost (loss).

    Parameters
    ----------
    position_type : str
        "Call" or "Put"
    spot : float
        Underlying spot price at expiration
    strike : float
        Strike price
    is_short : bool
        True if position is short (sold), False if long (bought)
    premium : float
        Premium per unit (in USD)
    qty : float
        Quantity (default 1.0)

    Returns
    -------
    dict
        {
            "expired_worthless": bool,
            "pnl": float,
            "total_intrinsic": float (always 0 for worthless),
            "notes": str
        }
    """
    # Determine if expired worthless
    worthless = is_expired_worthless(position_type, spot, strike)

    if not worthless:
        # Option expired ITM — use handle_expires_itm instead
        raise ValueError(
            f"{position_type} is in-the-money (spot=${spot:.0f}, strike=${strike:.0f}). "
            "Use handle_expires_itm() instead."
        )

    # Calculate P&L for worthless expiration
    position_side = "short" if is_short else "long"
    total_premium = premium * qty

    if is_short:
        # Short position: we keep the premium (profit)
        pnl = total_premium
        notes = f"{position_type} expired worthless. Seller keeps full premium: +${pnl:.2f}"
    else:
        # Long position: we lose the premium (loss)
        pnl = -total_premium
        notes = f"{position_type} expired worthless. Buyer loses premium: ${pnl:.2f}"

    return {
        "expired_worthless": True,
        "pnl": round(pnl, 4),
        "total_intrinsic": 0.0,
        "notes": notes,
    }


def handle_expires_itm(
    position_type: str,
    spot: float,
    strike: float,
    is_short: bool,
    premium: float,
    qty: float = 1.0,
) -> dict:
    """
    Calculate P&L when an option expires in-the-money.

    For calls: intrinsic = max(spot - strike, 0)
    For puts: intrinsic = max(strike - spot, 0)

    For short positions: lose intrinsic value.
    For long positions: gain intrinsic value.

    Parameters
    ----------
    position_type : str
        "Call" or "Put"
    spot : float
        Underlying spot price at expiration
    strike : float
        Strike price
    is_short : bool
        True if position is short (sold), False if long (bought)
    premium : float
        Premium per unit (in USD)
    qty : float
        Quantity (default 1.0)

    Returns
    -------
    dict
        {
            "expired_worthless": False,
            "pnl": float,
            "total_intrinsic": float,
            "notes": str
        }
    """
    # Determine if expired ITM
    worthless = is_expired_worthless(position_type, spot, strike)

    if worthless:
        # Option expired OTM — use handle_expires_worthless instead
        raise ValueError(
            f"{position_type} is out-of-the-money (spot=${spot:.0f}, strike=${strike:.0f}). "
            "Use handle_expires_worthless() instead."
        )

    # Calculate intrinsic value
    if position_type.lower().startswith("c"):  # Call
        intrinsic_per_unit = max(spot - strike, 0)
    else:  # Put
        intrinsic_per_unit = max(strike - spot, 0)

    total_intrinsic = intrinsic_per_unit * qty
    total_premium = premium * qty

    if is_short:
        # Short position: we owe the intrinsic value
        pnl = total_premium - total_intrinsic
        position_side = "short seller"
    else:
        # Long position: we receive the intrinsic value
        pnl = total_intrinsic - total_premium
        position_side = "long buyer"

    notes = (
        f"{position_type} expired ITM. {position_side.capitalize()} settlement: "
        f"premium=${total_premium:.2f}, intrinsic=${total_intrinsic:.2f}, P&L=${pnl:.2f}"
    )

    return {
        "expired_worthless": False,
        "pnl": round(pnl, 4),
        "total_intrinsic": round(total_intrinsic, 4),
        "notes": notes,
    }
