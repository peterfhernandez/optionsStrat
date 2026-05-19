"""Database helpers for Strangle strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Strangle, get_session, STRATEGY_STRANGLE


def load_strangle_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load strangle trading state for an asset from the database.

    Queries the Strangle table to reconstruct state from trade history.
    Returns a dict with keys: open, total_premium, wins, losses, trades, broker.
    If no trades exist, returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trades = session.query(Strangle).filter_by(asset=asset).order_by(Strangle.date_open).all()

        if not trades:
            return {
                "open":          None,
                "total_premium": 0.0,
                "wins":          0,
                "losses":        0,
                "trades":        0,
                "broker":        None,
            }

        # Calculate aggregate stats from trade history
        closed_trades = [t for t in trades if t.result != "Open"]
        wins = sum(1 for t in closed_trades if "Win" in (t.result or ""))
        losses = len(closed_trades) - wins
        total_premium = sum(t.total_premium for t in trades if t.total_premium) or 0.0

        # Get open position from the most recent open trade if any
        open_position = None
        for trade in reversed(trades):
            if trade.result == "Open":
                open_position = {
                    "asset": trade.asset,
                    "put_strike": trade.put_strike,
                    "call_strike": trade.call_strike,
                    "qty": trade.qty,
                    "expiry": trade.expiry,
                    "total_premium": trade.total_premium or 0.0,
                    "spot_open": trade.spot_open,
                    "days": trade.days,
                }
                break

        # Get broker from the most recent trade
        latest = trades[-1]

        return {
            "open":          open_position,
            "total_premium": total_premium,
            "wins":          wins,
            "losses":        losses,
            "trades":        len(closed_trades),
            "broker":        latest.broker,
        }
    finally:
        if close_session:
            session.close()


def save_strangle_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """
    Persist strangle trading state to the database.

    Updates the most recent Strangle record with the current broker and result.
    If an open position is provided, creates a new record (used for opening positions).
    If no record exists, creates one with the provided state (used in tests).
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(Strangle).filter_by(asset=asset).order_by(Strangle.date_open.desc()).first()
        open_pos = state.get("open")

        if row and open_pos:
            # If we have a new open position and an existing record, create a new trade record
            trade = Strangle(
                asset=asset,
                put_strike=open_pos.get("put_strike", 0.0),
                call_strike=open_pos.get("call_strike", 0.0),
                qty=open_pos.get("qty", 1.0),
                expiry=open_pos.get("expiry", ""),
                total_premium=open_pos.get("total_premium", 0.0),
                spot_open=open_pos.get("spot_open", 0.0),
                days=open_pos.get("days", 7),
                date_open=date.today(),
                result="Open",
                broker=state.get("broker"),
                fees=0.0,
            )
            session.add(trade)
            session.commit()
        elif row and not open_pos and row.result == "Open":
            # Mark existing open position as closed (no result specified, default to Closed)
            row.result = "Closed"
            row.broker = state.get("broker")
            session.commit()
        elif row:
            # Update only broker if no new open position and row is not open
            row.broker = state.get("broker")
            session.commit()
        else:
            # Create a new record if none exists (for testing)
            trade = Strangle(
                asset=asset,
                put_strike=open_pos.get("put_strike") if open_pos else 0.0,
                call_strike=open_pos.get("call_strike") if open_pos else 0.0,
                qty=open_pos.get("qty") if open_pos else 1.0,
                expiry=open_pos.get("expiry") if open_pos else "",
                total_premium=state.get("total_premium", 0.0),
                spot_open=open_pos.get("spot_open") if open_pos else 0.0,
                days=open_pos.get("days") if open_pos else 7,
                date_open=date.today(),
                result="Open" if open_pos else "Closed",
                broker=state.get("broker"),
                fees=0.0,
            )
            session.add(trade)
            session.commit()
    finally:
        if close_session:
            session.close()


def create_strangle_trade(
    asset: str,
    date_open: date,
    put_strike: float,
    call_strike: float,
    spot_open: float,
    total_premium: float,
    qty: float,
    days: int,
    expiry: str,
    notes: Optional[str] = None,
    broker: Optional[str] = None,
    session: Optional[Session] = None,
) -> Strangle:
    """Create and insert a Strangle trade record. Returns the persisted Strangle."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = Strangle(
            asset=asset,
            put_strike=put_strike,
            call_strike=call_strike,
            expiry=expiry,
            qty=qty,
            days=days,
            date_open=date_open,
            spot_open=spot_open,
            total_premium=total_premium,
            fees=0.0,
            result="Open",
            notes=notes,
            broker=broker,
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    finally:
        if close_session:
            session.close()


def close_strangle_trade(
    trade_id: int,
    date_close: date,
    spot_close: float,
    pnl: float,
    result: str,
    notes: Optional[str] = None,
    session: Optional[Session] = None,
) -> Strangle:
    """Close a Strangle trade by updating its close price, P&L, and result."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = session.get(Strangle, trade_id)
        if not trade:
            raise ValueError(f"Strangle trade ID {trade_id} not found")

        trade.date_close = date_close
        trade.spot_close = spot_close
        trade.pnl        = pnl
        trade.result     = result
        if notes:
            trade.notes = notes

        session.commit()
        session.refresh(trade)
        return trade
    finally:
        if close_session:
            session.close()


def get_strangle_stats(asset: Optional[str] = None, session: Optional[Session] = None) -> dict:
    """
    Get performance statistics for closed strangle trades.

    Returns dict with: trades, wins, losses, win_rate, total_premium, avg_premium.
    Filters to 'Win' and 'Loss' results only (excludes open positions).
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(Strangle).filter(
            Strangle.result.in_(["Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)", "Loss (Stop)", "Loss (Early)"])
        )
        if asset:
            query = query.filter(Strangle.asset == asset)

        trades = query.all()
        if not trades:
            return {
                "trades":        0,
                "wins":          0,
                "losses":        0,
                "win_rate":      0.0,
                "total_premium": 0.0,
                "avg_premium":   0.0,
            }

        wins      = sum(1 for t in trades if "Win" in (t.result or ""))
        losses    = len(trades) - wins
        prems     = [t.total_premium for t in trades if t.total_premium is not None]
        total_prem = sum(prems) if prems else 0.0

        return {
            "trades":        len(trades),
            "wins":          wins,
            "losses":        losses,
            "win_rate":      (wins / len(trades) * 100) if trades else 0.0,
            "total_premium": total_prem,
            "avg_premium":   (total_prem / len(prems)) if prems else 0.0,
        }
    finally:
        if close_session:
            session.close()
