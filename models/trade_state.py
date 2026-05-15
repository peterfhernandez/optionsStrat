from sqlalchemy import Column, Float, Index, Integer, JSON, String, UniqueConstraint
from .base import Base

STRATEGY_WHEEL = "wheel"
STRATEGY_STRANGLE = "strangle"
STRATEGY_CALENDAR = "calendar"
STRATEGY_SPREAD = "spread"

STAGE_NO_POSITION = "no_position"
STAGE_SHORT_PUT = "short_put"
STAGE_HOLDING = "holding"
STAGE_SHORT_CALL = "short_call"


class TradeState(Base):
    """
    Per-strategy, per-asset runtime state — replaces the *_state_*.json files.

    One row exists for every (strategy, asset) pair that has been initialised.
    The open_position column stores the current open leg(s) as a JSON blob
    (nullable when no position is held), mirroring the dict that was previously
    written to disk.
    """

    __tablename__ = "trade_state"

    id = Column(Integer, primary_key=True)
    strategy = Column(String(20), nullable=False)   # wheel | strangle | calendar
    asset = Column(String(10), nullable=False)

    # ── Wheel-specific ────────────────────────────────────────────────────────
    stage = Column(String(20), default=STAGE_NO_POSITION)
    asset_held = Column(Float, default=0.0)          # units held after assignment
    cost_basis = Column(Float, default=0.0)           # strike at assignment

    # ── Aggregate stats ───────────────────────────────────────────────────────
    total_premium = Column(Float, default=0.0)        # wheel / strangle
    total_pnl = Column(Float, default=0.0)            # calendar
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    cycles = Column(Integer, default=0)               # wheel only
    trades = Column(Integer, default=0)               # strangle / calendar

    # ── Broker that placed the current open position ──────────────────────────
    broker = Column(String(30), nullable=True)   # e.g. deribit_paper | deribit_live

    # ── Current open position ─────────────────────────────────────────────────
    # Nullable; set to None when flat.  Structure mirrors the dicts previously
    # stored in the JSON state files (see strategy module docstrings).
    open_position = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("strategy", "asset", name="uq_trade_state_strategy_asset"),
        Index("ix_trade_state_strategy", "strategy"),
        Index("ix_trade_state_asset", "asset"),
    )

    def __repr__(self) -> str:
        return (
            f"<TradeState strategy={self.strategy} asset={self.asset} "
            f"stage={self.stage} open={self.open_position is not None}>"
        )
