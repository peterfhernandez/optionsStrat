from sqlalchemy import Column, Date, Float, Index, Integer, String, Text
from .base import Base


class Calendar(Base):
    """Calendar spread trades — replaces the Calendars Excel tab."""

    __tablename__ = "calendars"

    id = Column(Integer, primary_key=True)
    asset = Column(String(10), nullable=False)
    option_type = Column(String(4))           # Call | Put
    strike = Column(Float)
    expiry_near = Column(String(15))          # DD-MMM-YYYY short leg
    expiry_far = Column(String(15))           # DD-MMM-YYYY long leg
    near_days = Column(Integer)
    far_days = Column(Integer)
    qty = Column(Float)

    date_open = Column(Date, nullable=False)
    date_close = Column(Date)
    spot_open = Column(Float)
    spot_close = Column(Float)

    near_prem = Column(Float)                 # short leg premium received
    far_prem = Column(Float)                  # long leg premium paid
    net_debit = Column(Float)                 # far_prem - near_prem (max loss)
    fees = Column(Float, default=0.0)
    open_fees = Column(Float, default=0.0)   # fee charged at entry
    close_fees = Column(Float, default=0.0)  # fee charged at exit
    pnl = Column(Float)

    result = Column(String(10))               # Win | Loss | Open
    notes = Column(Text)
    broker = Column(String(30), nullable=True)  # e.g. deribit_paper | deribit_live
    near_instrument = Column(String(30), nullable=True)  # broker instrument name for near leg
    far_instrument = Column(String(30), nullable=True)   # broker instrument name for far leg

    __table_args__ = (
        Index("ix_calendars_asset", "asset"),
        Index("ix_calendars_result", "result"),
        Index("ix_calendars_date_open", "date_open"),
    )

    def __repr__(self) -> str:
        return (
            f"<Calendar id={self.id} asset={self.asset} type={self.option_type} "
            f"strike={self.strike} result={self.result}>"
        )
