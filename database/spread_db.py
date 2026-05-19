"""Database helpers for Credit Spread strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Spread, get_session, STRATEGY_SPREAD


def load_spread_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load credit spread trading state for an asset from the database.

    Queries the Spread table to reconstruct state from trade history.
    Returns a dict with keys: open, net_credit, wins, losses, trades, broker.
    If no trades exist, returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trades = session.query(Spread).filter_by(asset=asset).order_by(Spread.date_open).all()

        if not trades:
            return {
                "open":       None,
                "net_credit": 0.0,
                "wins":       0,
                "losses":     0,
                "trades":     0,
                "broker":     None,
            }

        # Calculate aggregate stats from trade history
        closed_trades = [t for t in trades if t.result != "Open"]
        wins = sum(1 for t in closed_trades if "Win" in (t.result or ""))
        losses = len(closed_trades) - wins
        net_credit = sum(t.net_credit for t in trades if t.net_credit) or 0.0

        # Get open position from the most recent open trade if any
        open_position = None
        for trade in reversed(trades):
            if trade.result == "Open":
                open_position = {
                    "spread_type": trade.spread_type,
                    "short_strike": trade.short_strike,
                    "long_strike": trade.long_strike,
                    "qty": trade.qty,
                    "expiry": trade.expiry,
                }
                break

        # Get broker from the most recent trade
        latest = trades[-1]

        return {
            "open":       open_position,
            "net_credit": net_credit,
            "wins":       wins,
            "losses":     losses,
            "trades":     len(closed_trades),
            "broker":     latest.broker,
        }
    finally:
        if close_session:
            session.close()


def save_spread_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """
    Persist credit spread trading state to the database.

    Updates the most recent Spread record with the current broker.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(Spread).filter_by(asset=asset).order_by(Spread.date_open.desc()).first()

        if row:
            row.broker = state.get("broker")
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
    open_fees: float = 0.0,
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
            open_fees=open_fees,
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
    close_fees: float = 0.0,
    session: Optional[Session] = None,
) -> Spread:
    """Close a Spread trade by updating its close price, P&L, result, and close fees."""
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
        trade.close_fees = close_fees
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
