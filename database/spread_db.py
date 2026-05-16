"""Database helpers for Credit Spread strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Spread, TradeState, get_session
from models.trade_state import STRATEGY_SPREAD


def load_spread_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load credit spread trading state for an asset from the database.

    Returns a dict with keys: open, net_credit, wins, losses, trades.
    If no state exists, creates and returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(TradeState).filter_by(strategy=STRATEGY_SPREAD, asset=asset).first()

        if row:
            return {
                "open":       row.open_position,
                "net_credit": row.total_premium,
                "wins":       row.wins,
                "losses":     row.losses,
                "trades":     row.trades,
                "broker":     row.broker,
            }

        state = TradeState(
            strategy=STRATEGY_SPREAD,
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
            "open":       None,
            "net_credit": 0.0,
            "wins":       0,
            "losses":     0,
            "trades":     0,
            "broker":     None,
        }
    finally:
        if close_session:
            session.close()


def save_spread_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """Persist credit spread trading state to the database."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(TradeState).filter_by(strategy=STRATEGY_SPREAD, asset=asset).first()

        if not row:
            row = TradeState(strategy=STRATEGY_SPREAD, asset=asset)
            session.add(row)

        row.open_position = state.get("open")
        row.total_premium = state.get("net_credit", 0.0)
        row.wins          = state.get("wins", 0)
        row.losses        = state.get("losses", 0)
        row.trades        = state.get("trades", 0)
        row.broker        = state.get("broker")

        session.commit()
    finally:
        if close_session:
            session.close()


def create_spread_trade(
    asset: str,
    spread_type: str,
    date_open: date,
    short_strike: float,
    long_strike: float,
    spot_open: float,
    net_credit: float,
    max_loss: float,
    qty: float,
    days: int,
    expiry: str,
    notes: Optional[str] = None,
    broker: Optional[str] = None,
    session: Optional[Session] = None,
) -> Spread:
    """Create and insert a Spread trade record. Returns the persisted Spread."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = Spread(
            asset=asset,
            spread_type=spread_type,
            short_strike=short_strike,
            long_strike=long_strike,
            expiry=expiry,
            qty=qty,
            days=days,
            date_open=date_open,
            spot_open=spot_open,
            net_credit=net_credit,
            max_loss=max_loss,
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


def close_spread_trade(
    trade_id: int,
    date_close: date,
    spot_close: float,
    pnl: float,
    result: str,
    notes: Optional[str] = None,
    session: Optional[Session] = None,
) -> Spread:
    """Close a Spread trade by updating its close price, P&L, and result."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = session.get(Spread, trade_id)
        if not trade:
            raise ValueError(f"Spread trade ID {trade_id} not found")

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


_CLOSED_RESULTS = ("Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)", "Expired")


def get_open_spreads(asset: Optional[str] = None, session: Optional[Session] = None) -> list[Spread]:
    """Return all Spread rows with result='Open' from the spreads table."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(Spread).filter(Spread.result == "Open")
        if asset:
            query = query.filter(Spread.asset == asset)
        return query.order_by(Spread.date_open).all()
    finally:
        if close_session:
            session.close()


def get_spread_history(asset: Optional[str] = None, session: Optional[Session] = None) -> list[Spread]:
    """Return all closed Spread rows from the spreads table, newest first."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(Spread).filter(Spread.result.in_(_CLOSED_RESULTS))
        if asset:
            query = query.filter(Spread.asset == asset)
        return query.order_by(Spread.date_close.desc()).all()
    finally:
        if close_session:
            session.close()


def get_spread_stats(asset: Optional[str] = None, session: Optional[Session] = None) -> dict:
    """
    Get performance statistics for closed credit spread trades.

    Returns dict with: trades, wins, losses, win_rate, total_credit, avg_credit.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(Spread).filter(
            Spread.result.in_(["Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)"])
        )
        if asset:
            query = query.filter(Spread.asset == asset)

        trades = query.all()
        if not trades:
            return {
                "trades":       0,
                "wins":         0,
                "losses":       0,
                "win_rate":     0.0,
                "total_credit": 0.0,
                "avg_credit":   0.0,
            }

        wins        = sum(1 for t in trades if "Win" in (t.result or ""))
        losses      = len(trades) - wins
        credits     = [t.net_credit for t in trades if t.net_credit is not None]
        total_credit = sum(credits) if credits else 0.0

        return {
            "trades":       len(trades),
            "wins":         wins,
            "losses":       losses,
            "win_rate":     (wins / len(trades) * 100) if trades else 0.0,
            "total_credit": total_credit,
            "avg_credit":   (total_credit / len(credits)) if credits else 0.0,
        }
    finally:
        if close_session:
            session.close()
