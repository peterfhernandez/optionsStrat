"""Database helpers for scanner scan results."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import get_session
from models.scan_results import ScanResult


def save_scan_results(candidates: list, scanned_at: Optional[datetime] = None, session: Optional[Session] = None) -> list[ScanResult]:
    """
    Persist a list of Candidate objects as ScanResult rows.

    Returns the list of inserted ScanResult ORM objects.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    ts = scanned_at or datetime.now(timezone.utc)

    try:
        rows = []
        for c in candidates:
            row = ScanResult(
                scanned_at    = ts,
                asset         = c.asset,
                strategy      = c.strategy,
                otm_pct       = c.otm_pct,
                days          = c.days,
                spot          = c.spot,
                iv            = c.iv,
                strike        = c.strike,
                premium       = c.premium,
                yield_ann     = c.yield_ann,
                prob_profit   = c.prob_profit,
                open_interest = c.open_interest,
                volume_usd    = c.volume_usd,
                iv_spread     = c.iv_spread,
                liquidity_tag = c.liquidity_tag,
                put_strike    = c.put_strike,
                call_strike   = c.call_strike,
                be_lo         = c.be_lo,
                be_hi         = c.be_hi,
                far_days      = c.far_days,
                max_profit    = c.max_profit,
            )
            session.add(row)
            rows.append(row)

        session.commit()
        for row in rows:
            session.refresh(row)
        return rows
    finally:
        if close_session:
            session.close()


def get_latest_scan(asset: Optional[str] = None, session: Optional[Session] = None) -> list[ScanResult]:
    """
    Return all ScanResult rows from the most recent scan run.

    Optionally filter by asset. Returns an empty list if no scans exist.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(ScanResult)
        if asset:
            query = query.filter(ScanResult.asset == asset)

        latest_ts = (
            session.query(ScanResult.scanned_at)
            .order_by(ScanResult.scanned_at.desc())
            .limit(1)
            .scalar()
        )
        if not latest_ts:
            return []

        return query.filter(ScanResult.scanned_at == latest_ts).all()
    finally:
        if close_session:
            session.close()


def get_scan_history(
    asset: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 100,
    session: Optional[Session] = None,
) -> list[ScanResult]:
    """
    Return recent ScanResult rows, newest first.

    Optionally filter by asset and/or strategy. Capped at ``limit`` rows.
    """
    close_session = session is None
    if session is None:
        session = get_session()

    try:
        query = session.query(ScanResult).order_by(ScanResult.scanned_at.desc())
        if asset:
            query = query.filter(ScanResult.asset == asset)
        if strategy:
            query = query.filter(ScanResult.strategy == strategy)
        return query.limit(limit).all()
    finally:
        if close_session:
            session.close()
