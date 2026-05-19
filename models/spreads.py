from sqlalchemy import Column, Date, Float, Index, Integer, String, Text
from .base import Base


class Spread(Base):
    """Credit spread trades — Bull Put Spread (BPS) or Bear Call Spread (BCS)."""

    __tablename__ = "spreads"

    id = Column(Integer, primary_key=True)
    asset = Column(String(10), nullable=False)
    spread_type = Column(String(5), nullable=False)  # "BPS" | "BCS"
    short_strike = Column(Float)                      # sold leg (closer to ATM)
    long_strike = Column(Float)                       # bought leg (further OTM)
    expiry = Column(String(15))                       # DD-MMM-YYYY
    qty = Column(Float)
    days = Column(Integer)

    date_open = Column(Date, nullable=False)
    date_close = Column(Date)
    spot_open = Column(Float)
    spot_close = Column(Float)

    net_credit = Column(Float)   # premium received (max profit)
    max_loss = Column(Float)     # strike width * qty - net_credit
    fees = Column(Float, default=0.0)
    open_fees = Column(Float, default=0.0)   # fee charged at entry
    close_fees = Column(Float, default=0.0)  # fee charged at exit
    pnl = Column(Float)

    result = Column(String(15))  # Win | Loss | Open | Win (Auto TP) | Loss (Auto Stop)
    notes = Column(Text)
    broker = Column(String(30), nullable=True)

    __table_args__ = (
        Index("ix_spreads_asset", "asset"),
        Index("ix_spreads_result", "result"),
        Index("ix_spreads_date_open", "date_open"),
    )

    def __repr__(self) -> str:
        return (
            f"<Spread id={self.id} asset={self.asset} type={self.spread_type} "
            f"short={self.short_strike} long={self.long_strike} result={self.result}>"
        )
