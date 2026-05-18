"""Database helpers for Calendar strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Calendar, get_session, STRATEGY_CALENDAR


def load_calendar_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load calendar trading state for an asset from the database.

    Queries the Calendar table to reconstruct state from trade history.
    Returns a dict with keys: open, total_pnl, wins, losses, trades, broker.
    If no trades exist, returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trades = session.query(Calendar).filter_by(asset=asset).order_by(Calendar.date_open).all()

        if not trades:
            return {
                "open":      None,
                "total_pnl": 0.0,
                "wins":      0,
                "losses":    0,
                "trades":    0,
                "broker":    None,
            }

        # Calculate aggregate stats from trade history
        closed_trades = [t for t in trades if t.result != "Open"]
        wins = sum(1 for t in closed_trades if "Win" in (t.result or ""))
        losses = len(closed_trades) - wins
        total_pnl = sum(t.pnl for t in closed_trades if t.pnl) or 0.0

        # Get open position from the most recent open trade if any
        open_position = None
        for trade in reversed(trades):
            if trade.result == "Open":
                open_position = {
                    "option_type": trade.option_type,
                    "strike": trade.strike,
                    "expiry_near": trade.expiry_near,
                    "expiry_far": trade.expiry_far,
                    "qty": trade.qty,
                }
                break

        # Get broker from the most recent trade
        latest = trades[-1]

        return {
            "open":      open_position,
            "total_pnl": total_pnl,
            "wins":      wins,
            "losses":    losses,
            "trades":    len(closed_trades),
            "broker":    latest.broker,
        }
    finally:
        if close_session:
            session.close()


def save_calendar_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """
    Persist calendar trading state to the database.

    Updates the most recent Calendar record with the current broker.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(Calendar).filter_by(asset=asset).order_by(Calendar.date_open.desc()).first()

        if row:
            row.broker = state.get("broker")
            session.commit()
    finally:
        if close_session:
            session.close()


def create_calendar_trade(
    asset: str,
    date_open: date,
    option_type: str,
    strike: float,
    expiry_near: str,
    expiry_far: str,
    near_days: int,
    far_days: int,
    qty: float,
    spot_open: float,
    near_prem: float,
    far_prem: float,
    net_debit: float,
    notes: Optional[str] = None,
    broker: Optional[str] = None,
    session: Optional[Session] = None,
) -> Calendar:
    """Create and insert a Calendar trade record. Returns the persisted Calendar."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = Calendar(
            asset=asset,
            option_type=option_type,
            strike=strike,
            expiry_near=expiry_near,
            expiry_far=expiry_far,
            near_days=near_days,
            far_days=far_days,
            qty=qty,
            date_open=date_open,
            spot_open=spot_open,
            near_prem=near_prem,
            far_prem=far_prem,
            net_debit=net_debit,
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


def close_calendar_trade(
    trade_id: int,
    date_close: date,
    spot_close: float,
    pnl: float,
    result: str,
    notes: Optional[str] = None,
    session: Optional[Session] = None,
) -> Calendar:
    """Close a Calendar trade by updating its close price, P&L, and result."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        trade = session.get(Calendar, trade_id)
        if not trade:
            raise ValueError(f"Calendar trade ID {trade_id} not found")

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


def get_calendar_stats(asset: Optional[str] = None, session: Optional[Session] = None) -> dict:
    """
    Get performance statistics for closed calendar trades.

    Returns dict with: trades, wins, losses, win_rate, total_pnl, avg_pnl.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(Calendar).filter(
            Calendar.result.in_(["Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)", "Loss (Stop)", "Loss (Early)"])
        )
        if asset:
            query = query.filter(Calendar.asset == asset)

        trades = query.all()
        if not trades:
            return {
                "trades":    0,
                "wins":      0,
                "losses":    0,
                "win_rate":  0.0,
                "total_pnl": 0.0,
                "avg_pnl":   0.0,
            }

        wins      = sum(1 for t in trades if "Win" in (t.result or ""))
        losses    = len(trades) - wins
        pnls      = [t.pnl for t in trades if t.pnl is not None]
        total_pnl = sum(pnls) if pnls else 0.0

        return {
            "trades":    len(trades),
            "wins":      wins,
            "losses":    losses,
            "win_rate":  (wins / len(trades) * 100) if trades else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl":   (total_pnl / len(pnls)) if pnls else 0.0,
        }
    finally:
        if close_session:
            session.close()
