from sqlalchemy import Column, Date, Float, Index, Integer, String, Text
from .base import Base


class Strangle(Base):
    """Short strangle trades — replaces the Strangles Excel tab."""

    __tablename__ = "strangles"

    id = Column(Integer, primary_key=True)
    asset = Column(String(10), nullable=False)
    put_strike = Column(Float)
    call_strike = Column(Float)
    expiry = Column(String(15))               # DD-MMM-YYYY
    qty = Column(Float)
    days = Column(Integer)

    date_open = Column(Date, nullable=False)
    date_close = Column(Date)
    spot_open = Column(Float)
    spot_close = Column(Float)

    total_premium = Column(Float)             # combined premium from both legs
    fees = Column(Float, default=0.0)
    open_fees = Column(Float, default=0.0)   # fee charged at entry
    close_fees = Column(Float, default=0.0)  # fee charged at exit
    pnl = Column(Float)

    result = Column(String(10))               # Win | Loss | Open
    notes = Column(Text)
    broker = Column(String(30), nullable=True)  # e.g. deribit_paper | deribit_live

    __table_args__ = (
        Index("ix_strangles_asset", "asset"),
        Index("ix_strangles_result", "result"),
        Index("ix_strangles_date_open", "date_open"),
    )

    def __repr__(self) -> str:
        return (
            f"<Strangle id={self.id} asset={self.asset} "
            f"put={self.put_strike} call={self.call_strike} result={self.result}>"
        )
