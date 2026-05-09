"""Database helpers for Calendar strategy trades."""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from models import Calendar, TradeState, get_session
from models.trade_state import STRATEGY_CALENDAR


def load_calendar_state(asset: str, session: Optional[Session] = None) -> dict:
    """
    Load calendar trading state for an asset from the database.

    Returns a dict with keys: open, total_pnl, wins, losses, trades.
    If no state exists, creates and returns a fresh default state.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(TradeState).filter_by(strategy=STRATEGY_CALENDAR, asset=asset).first()

        if row:
            return {
                "open":      row.open_position,
                "total_pnl": row.total_pnl,
                "wins":      row.wins,
                "losses":    row.losses,
                "trades":    row.trades,
                "broker":    row.broker,
            }

        state = TradeState(
            strategy=STRATEGY_CALENDAR,
            asset=asset,
            total_pnl=0.0,
            wins=0,
            losses=0,
            trades=0,
            open_position=None,
        )
        session.add(state)
        session.commit()

        return {
            "open":      None,
            "total_pnl": 0.0,
            "wins":      0,
            "losses":    0,
            "trades":    0,
            "broker":    None,
        }
    finally:
        if close_session:
            session.close()


def save_calendar_state(asset: str, state: dict, session: Optional[Session] = None) -> None:
    """Persist calendar trading state to the database."""
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        row = session.query(TradeState).filter_by(strategy=STRATEGY_CALENDAR, asset=asset).first()

        if not row:
            row = TradeState(strategy=STRATEGY_CALENDAR, asset=asset)
            session.add(row)

        row.open_position = state.get("open")
        row.total_pnl     = state.get("total_pnl", 0.0)
        row.wins          = state.get("wins", 0)
        row.losses        = state.get("losses", 0)
        row.trades        = state.get("trades", 0)
        row.broker        = state.get("broker")

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
            Calendar.result.in_(["Win", "Loss", "Win (Auto TP)", "Loss (Auto Stop)"])
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
