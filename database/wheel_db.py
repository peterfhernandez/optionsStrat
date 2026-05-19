"""Database helpers for Wheel strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Single, get_session, STAGE_NO_POSITION


def load_wheel_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load wheel trading state for an asset from the database.

    Queries the Single table to reconstruct state from trade history.
    Returns a dict with keys: stage, open, asset_held, cost_basis, total_premium, wins, losses, cycles.
    If no trades exist, returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trades = session.query(Single).filter_by(asset=asset).order_by(Single.date_open).all()

        if not trades:
            return {
                "stage": STAGE_NO_POSITION,
                "open": None,
                "asset_held": 0.0,
                "cost_basis": 0.0,
                "total_premium": 0.0,
                "wins": 0,
                "losses": 0,
                "cycles": 0,
                "broker": None,
            }

        # Calculate aggregate stats from trade history
        wins = sum(1 for t in trades if "Win" in (t.result or ""))
        losses = sum(1 for t in trades if "Loss" in (t.result or ""))
        cycles = wins + losses  # completed cycles
        total_premium = sum(t.premium for t in trades if t.premium) or 0.0

        # Get state from the most recent trade
        latest = trades[-1]
        open_position = None
        if latest.result == "Open":
            open_position = {
                "asset": latest.asset,
                "type": latest.option_type,
                "strike": latest.strike,
                "qty": latest.qty,
                "expiry": latest.expiry,
                "option_type": latest.option_type,
                "premium": latest.premium,
                "spot_open": latest.spot_open,
                "days": latest.days,
                "instrument": latest.instrument,
                "open_fees": latest.open_fees or 0.0,
                "close_fees": latest.close_fees or 0.0,
            }

        return {
            "stage": latest.stage or STAGE_NO_POSITION,
            "open": open_position,
            "asset_held": latest.asset_held or 0.0,
            "cost_basis": latest.cost_basis or 0.0,
            "total_premium": total_premium,
            "wins": wins,
            "losses": losses,
            "cycles": cycles,
            "broker": latest.broker,
        }
    finally:
        if close_session:
            session.close()


def save_wheel_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """
    Persist wheel trading state to the database.

    Updates the most recent Single record with current stage, asset_held, cost_basis, and broker.
    If an open position is provided, creates a new record (used for opening positions).
    If no record exists, creates one with the provided state (used in tests).
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(Single).filter_by(asset=asset).order_by(Single.date_open.desc()).first()
        open_pos = state.get("open")
        stage = state.get("stage", STAGE_NO_POSITION)

        if row and open_pos:
            # If we have a new open position and an existing record, create a new trade record
            trade = Single(
                asset=asset,
                option_type=open_pos.get("type", "Put"),
                strike=open_pos.get("strike", 0.0),
                expiry=open_pos.get("expiry", ""),
                qty=open_pos.get("qty", 1.0),
                days=open_pos.get("days", 7),
                date_open=date.today(),
                spot_open=open_pos.get("spot_open", 0.0),
                premium=open_pos.get("premium", 0.0),
                stage=stage,
                asset_held=state.get("asset_held", 0.0),
                cost_basis=state.get("cost_basis", 0.0),
                result="Open",
                broker=state.get("broker"),
                instrument=open_pos.get("instrument"),
                fees=0.0,
                open_fees=open_pos.get("open_fees", 0.0),
                close_fees=open_pos.get("close_fees", 0.0),
            )
            session.add(trade)
            session.commit()
        elif row and not open_pos and row.result == "Open":
            # Mark existing open position as closed
            row.result = "Closed"
            row.stage = stage
            row.asset_held = state.get("asset_held", 0.0)
            row.cost_basis = state.get("cost_basis", 0.0)
            row.broker = state.get("broker")
            session.commit()
        elif row:
            # Update existing record if no new open position
            row.stage = stage
            row.asset_held = state.get("asset_held", 0.0)
            row.cost_basis = state.get("cost_basis", 0.0)
            row.broker = state.get("broker")
            session.commit()
        else:
            # Create a new record if none exists (for testing)
            result = "Open" if open_pos else "Closed"
            trade = Single(
                asset=asset,
                option_type=open_pos.get("type") if open_pos else "Put",
                strike=open_pos.get("strike") if open_pos else 0.0,
                expiry=open_pos.get("expiry") if open_pos else "",
                qty=open_pos.get("qty") if open_pos else 1.0,
                days=open_pos.get("days") if open_pos else 7,
                date_open=date.today(),
                spot_open=open_pos.get("spot_open") if open_pos else 0.0,
                premium=open_pos.get("premium") if open_pos else 0.0,
                stage=stage,
                asset_held=state.get("asset_held", 0.0),
                cost_basis=state.get("cost_basis", 0.0),
                result=result,
                broker=state.get("broker"),
                fees=0.0,
                open_fees=open_pos.get("open_fees", 0.0) if open_pos else 0.0,
                close_fees=open_pos.get("close_fees", 0.0) if open_pos else 0.0,
            )
            session.add(trade)
            session.flush()
            session.commit()
    finally:
        if close_session:
            session.close()


def create_single_trade(
    asset: str,
    date_open: date,
    option_type: str,
    strike: float,
    expiry: str,
    spot_open: float,
    premium: float,
    qty: float,
    days: int,
    stage: str = "short_put",
    notes: Optional[str] = None,
    broker: Optional[str] = None,
    open_fees: float = 0.0,
    session: Optional[Session] = None,
) -> Single:
    """Create and insert a Single (Wheel) trade record."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = Single(
            asset=asset,
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            spot_open=spot_open,
            premium=premium,
            qty=qty,
            days=days,
            stage=stage,
            date_open=date_open,
            fees=0.0,
            open_fees=open_fees,
            result="Open",
            notes=notes,
            broker=broker,
        )
        session.add(trade)
        session.commit()
        return trade
    finally:
        if close_session:
            session.close()


def close_single_trade(
    trade_id: int,
    date_close: date,
    spot_close: float,
    pnl: float,
    result: str,
    notes: Optional[str] = None,
    close_fees: float = 0.0,
    session: Optional[Session] = None,
) -> Single:
    """Close a Single trade (update with close price, P&L, and close fees)."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = session.get(Single, trade_id)
        if not trade:
            raise ValueError(f"Trade ID {trade_id} not found")

        trade.date_close = date_close
        trade.spot_close = spot_close
        trade.pnl = pnl
        trade.result = result
        trade.close_fees = close_fees
        if notes:
            trade.notes = notes

        session.commit()
        return trade
    finally:
        if close_session:
            session.close()


def get_wheel_stats(asset: Optional[str] = None, session: Optional[Session] = None) -> dict:
    """
    Get performance statistics for wheel trades.

    Returns dict with keys: trades, wins, losses, win_rate, total_premium, avg_premium.
    If asset is provided, filters to that asset only.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(Single).filter(Single.result.isnot(None), Single.result != "Open")
        if asset:
            query = query.filter(Single.asset == asset)

        trades = query.all()
        if not trades:
            return {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_premium": 0.0,
                "avg_premium": 0.0,
            }

        wins = sum(1 for t in trades if "Win" in (t.result or ""))
        losses = sum(1 for t in trades if "Loss" in (t.result or ""))
        prems = [t.premium for t in trades if t.premium]
        total_prem = sum(prems) if prems else 0.0

        return {
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / len(trades) * 100) if trades else 0.0,
            "total_premium": total_prem,
            "avg_premium": (total_prem / len(prems)) if prems else 0.0,
        }
    finally:
        if close_session:
            session.close()
