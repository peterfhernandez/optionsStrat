from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.sql import func
from .base import Base


class ScanResult(Base):
    """One candidate row from a scanner run."""

    __tablename__ = "scan_results"

    id            = Column(Integer, primary_key=True)
    scanned_at    = Column(DateTime, nullable=False, server_default=func.now())

    # Candidate identity
    asset         = Column(String(10), nullable=False)
    strategy      = Column(String(12), nullable=False)   # CSP, CC, Strangle, Cal-C, Cal-P
    otm_pct       = Column(Float, nullable=False)
    days          = Column(Integer, nullable=False)

    # Market snapshot
    spot          = Column(Float)
    iv            = Column(Float)
    strike        = Column(String(30))

    # Performance metrics
    premium       = Column(Float)
    yield_ann     = Column(Float)
    prob_profit   = Column(Float)

    # Liquidity
    open_interest = Column(Float)
    volume_usd    = Column(Float)
    iv_spread     = Column(Float)
    liquidity_tag = Column(String(5))

    # Strangle extras
    put_strike    = Column(Float)
    call_strike   = Column(Float)
    be_lo         = Column(Float)
    be_hi         = Column(Float)

    # Calendar extras
    far_days      = Column(Integer)
    max_profit    = Column(Float)

    notes         = Column(Text)

    __table_args__ = (
        Index("ix_scan_results_scanned_at", "scanned_at"),
        Index("ix_scan_results_asset",      "asset"),
        Index("ix_scan_results_strategy",   "strategy"),
    )

    def __repr__(self) -> str:
        return (
            f"<ScanResult id={self.id} asset={self.asset} "
            f"strategy={self.strategy} yield={self.yield_ann}%>"
        )
