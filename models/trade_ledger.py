from __future__ import annotations

from sqlalchemy import Column, Date, Float, Index, Integer, String, func, select
from sqlalchemy.orm import Session

from .base import Base

STRATEGY_SINGLES = "singles"
STRATEGY_STRANGLE = "strangle"
STRATEGY_CALENDAR = "calendar"


class TradeLedger(Base):
    """
    Unified trade log across all strategies.

    One row is written when a trade opens and updated when it closes.
    This table drives the open-trades and trade-history views.

    cumulative_pnl is NOT stored; use TradeLedger.with_cumulative_pnl()
    to compute it via a window function at query time.
    """

    __tablename__ = "trade_ledger"

    id = Column(Integer, primary_key=True)
    underlying = Column(String(10), nullable=False)   # ETH | BTC | SOL | XRP …
    strategy = Column(String(20), nullable=False)     # singles | strangle | calendar
    trade_ref_id = Column(Integer, nullable=False)    # PK of the row in the strategy table

    date_open = Column(Date, nullable=False)
    date_close = Column(Date)                         # NULL while open

    spot_open = Column(Float)
    spot_close = Column(Float)                        # NULL while open

    fees = Column(Float, default=0.0)
    pnl = Column(Float)                               # NULL while open
    broker = Column(String(30), nullable=True)        # e.g. deribit_paper | deribit_live

    __table_args__ = (
        Index("ix_trade_ledger_underlying", "underlying"),
        Index("ix_trade_ledger_strategy", "strategy"),
        Index("ix_trade_ledger_date_open", "date_open"),
        Index("ix_trade_ledger_date_close", "date_close"),
    )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self.date_close is None

    @classmethod
    def open_trades(cls, session: Session) -> list[TradeLedger]:
        """All trades with no close date, ordered by open date."""
        return (
            session.execute(
                select(cls).where(cls.date_close.is_(None)).order_by(cls.date_open)
            )
            .scalars()
            .all()
        )

    @classmethod
    def closed_trades(cls, session: Session) -> list[TradeLedger]:
        """All closed trades ordered by close date descending."""
        return (
            session.execute(
                select(cls)
                .where(cls.date_close.is_not(None))
                .order_by(cls.date_close.desc())
            )
            .scalars()
            .all()
        )

    @classmethod
    def with_cumulative_pnl(cls, session: Session) -> list[dict]:
        """
        Return all closed trades with a running cumulative P&L.

        SQLite window-function support (3.25+) is used for the sum.
        Each element is a dict with all TradeLedger columns plus
        'cumulative_pnl'.
        """
        cumulative = func.sum(cls.pnl).over(order_by=cls.date_close).label(
            "cumulative_pnl"
        )
        rows = session.execute(
            select(cls, cumulative)
            .where(cls.date_close.is_not(None))
            .order_by(cls.date_close)
        ).all()

        result = []
        for row, cum_pnl in rows:
            result.append(
                {
                    "id": row.id,
                    "underlying": row.underlying,
                    "strategy": row.strategy,
                    "trade_ref_id": row.trade_ref_id,
                    "date_open": row.date_open,
                    "date_close": row.date_close,
                    "spot_open": row.spot_open,
                    "spot_close": row.spot_close,
                    "fees": row.fees,
                    "pnl": row.pnl,
                    "cumulative_pnl": cum_pnl,
                }
            )
        return result

    def __repr__(self) -> str:
        status = "open" if self.is_open else f"pnl={self.pnl}"
        return (
            f"<TradeLedger id={self.id} {self.underlying} {self.strategy} "
            f"{self.date_open} {status}>"
        )
