from sqlalchemy import Column, Date, Float, Index, Integer, String, Text
from .base import Base


class Single(Base):
    """Wheel strategy trades — replaces the Paper Trades Excel tab."""

    __tablename__ = "singles"

    id = Column(Integer, primary_key=True)
    asset = Column(String(10), nullable=False)
    option_type = Column(String(4))           # Put | Call
    stage = Column(String(20))                # short_put | holding | short_call | closed
    strike = Column(Float)
    expiry = Column(String(15))               # DD-MMM-YYYY
    qty = Column(Float)
    days = Column(Integer)

    date_open = Column(Date, nullable=False)
    date_close = Column(Date)
    spot_open = Column(Float)
    spot_close = Column(Float)

    premium = Column(Float)
    asset_held = Column(Float, default=0.0)   # qty held after assignment
    cost_basis = Column(Float)                # strike price at assignment
    fees = Column(Float, default=0.0)
    pnl = Column(Float)

    result = Column(String(10))               # Win | Loss | Open
    notes = Column(Text)

    __table_args__ = (
        Index("ix_singles_asset", "asset"),
        Index("ix_singles_result", "result"),
        Index("ix_singles_date_open", "date_open"),
    )

    def __repr__(self) -> str:
        return (
            f"<Single id={self.id} asset={self.asset} type={self.option_type} "
            f"strike={self.strike} result={self.result}>"
        )
