"""Database helpers for Strangle strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Strangle, TradeState, get_session
from models.trade_state import STRATEGY_STRANGLE


def load_strangle_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load strangle trading state for an asset from the database.

    Returns a dict with keys: open, total_premium, wins, losses, trades.
    If no state exists, creates and returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(TradeState).filter_by(strategy=STRATEGY_STRANGLE, asset=asset).first()

        if row:
            return {
                "open":          row.open_position,
                "total_premium": row.total_premium,
                "wins":          row.wins,
                "losses":        row.losses,
                "trades":        row.trades,
                "broker":        row.broker,
            }

        # First time — create initial state row
        state = TradeState(
            strategy=STRATEGY_STRANGLE,
            asset=asset,
            total_premium=0.0,
            wins=0,
            losses=0,
            trades=0,
            open_position=None,
        )
        session.add(state)
        session.commit()

        return {
            "open":          None,
            "total_premium": 0.0,
            "wins":          0,
            "losses":        0,
            "trades":        0,
            "broker":        None,
        }
    finally:
        if close_session:
            session.close()


def save_strangle_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """Persist strangle trading state to the database."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(TradeState).filter_by(strategy=STRATEGY_STRANGLE, asset=asset).first()

        if not row:
            row = TradeState(strategy=STRATEGY_STRANGLE, asset=asset)
            session.add(row)

        row.open_position = state.get("open")
        row.total_premium = state.get("total_premium", 0.0)
        row.wins          = state.get("wins", 0)
        row.losses        = state.get("losses", 0)
        row.trades        = state.get("trades", 0)
        row.broker        = state.get("broker")

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
            Strangle.result.in_(["Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)"])
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
