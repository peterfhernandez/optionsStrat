"""Tests for models/ package — SQLAlchemy models backed by an in-memory SQLite DB."""
import datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from models.base import Base
from models.calendars import Calendar
from models.singles import Single
from models.strangles import Strangle
from models.trade_ledger import TradeLedger
from models.trade_state import (
    TradeState,
    STAGE_NO_POSITION,
    STAGE_SHORT_PUT,
    STRATEGY_WHEEL,
    STRATEGY_STRANGLE,
    STRATEGY_CALENDAR,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="function")
def session():
    """Fresh in-memory SQLite session per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()
    Base.metadata.drop_all(engine)


TODAY = datetime.date(2026, 5, 6)
YESTERDAY = TODAY - datetime.timedelta(days=1)


# ── Single (Wheel) ────────────────────────────────────────────────────────────


class TestSingle:
    def test_create_open_trade(self, session):
        trade = Single(
            asset="ETH",
            option_type="Put",
            stage="short_put",
            strike=1800.0,
            expiry="13-May-2026",
            qty=0.139,
            days=7,
            date_open=TODAY,
            spot_open=1950.0,
            premium=12.50,
            fees=0.0,
            result="Open",
        )
        session.add(trade)
        session.commit()

        fetched = session.get(Single, trade.id)
        assert fetched.asset == "ETH"
        assert fetched.result == "Open"
        assert fetched.date_close is None

    def test_close_trade_updates_fields(self, session):
        trade = Single(
            asset="ETH",
            option_type="Put",
            strike=1800.0,
            expiry="13-May-2026",
            qty=0.139,
            days=7,
            date_open=YESTERDAY,
            spot_open=1950.0,
            premium=12.50,
            result="Open",
        )
        session.add(trade)
        session.commit()

        trade.date_close = TODAY
        trade.spot_close = 2000.0
        trade.pnl = 12.50
        trade.result = "Win"
        session.commit()

        fetched = session.get(Single, trade.id)
        assert fetched.result == "Win"
        assert fetched.pnl == 12.50

    def test_assignment_fields(self, session):
        trade = Single(
            asset="ETH",
            option_type="Put",
            strike=1800.0,
            expiry="13-May-2026",
            qty=0.139,
            days=7,
            date_open=TODAY,
            spot_open=1750.0,
            premium=12.50,
            asset_held=0.139,
            cost_basis=1800.0,
            stage="holding",
            result="Open",
        )
        session.add(trade)
        session.commit()

        fetched = session.get(Single, trade.id)
        assert fetched.asset_held == 0.139
        assert fetched.cost_basis == 1800.0
        assert fetched.stage == "holding"

    def test_multiple_assets(self, session):
        for asset in ("ETH", "BTC", "SOL", "XRP"):
            session.add(Single(asset=asset, date_open=TODAY, result="Open"))
        session.commit()

        count = session.scalar(select(func.count()).select_from(Single))
        assert count == 4


# ── Strangle ──────────────────────────────────────────────────────────────────


class TestStrangle:
    def test_create_strangle(self, session):
        trade = Strangle(
            asset="ETH",
            put_strike=1700.0,
            call_strike=2100.0,
            expiry="13-May-2026",
            qty=0.128,
            days=7,
            date_open=TODAY,
            spot_open=1900.0,
            total_premium=25.00,
            fees=0.0,
            result="Open",
        )
        session.add(trade)
        session.commit()

        fetched = session.get(Strangle, trade.id)
        assert fetched.put_strike == 1700.0
        assert fetched.call_strike == 2100.0
        assert fetched.total_premium == 25.00

    def test_close_strangle(self, session):
        trade = Strangle(
            asset="ETH",
            put_strike=1700.0,
            call_strike=2100.0,
            expiry="13-May-2026",
            qty=0.128,
            days=7,
            date_open=YESTERDAY,
            spot_open=1900.0,
            total_premium=25.00,
            result="Open",
        )
        session.add(trade)
        session.commit()

        trade.date_close = TODAY
        trade.spot_close = 1850.0
        trade.pnl = 25.00
        trade.result = "Win"
        session.commit()

        assert session.get(Strangle, trade.id).result == "Win"

    def test_strangle_repr(self, session):
        trade = Strangle(asset="BTC", put_strike=50000.0, call_strike=70000.0, date_open=TODAY, result="Open")
        session.add(trade)
        session.commit()
        assert "BTC" in repr(trade)


# ── Calendar ──────────────────────────────────────────────────────────────────


class TestCalendar:
    def test_create_calendar(self, session):
        trade = Calendar(
            asset="ETH",
            option_type="Call",
            strike=2000.0,
            expiry_near="13-May-2026",
            expiry_far="06-Jun-2026",
            near_days=7,
            far_days=30,
            qty=0.139,
            date_open=TODAY,
            spot_open=1950.0,
            near_prem=8.00,
            far_prem=20.00,
            net_debit=12.00,
            fees=0.0,
            result="Open",
        )
        session.add(trade)
        session.commit()

        fetched = session.get(Calendar, trade.id)
        assert fetched.net_debit == 12.00
        assert fetched.option_type == "Call"
        assert fetched.expiry_near == "13-May-2026"

    def test_close_calendar_with_pnl(self, session):
        trade = Calendar(
            asset="ETH",
            option_type="Put",
            strike=1900.0,
            near_days=7,
            far_days=30,
            date_open=YESTERDAY,
            spot_open=1950.0,
            near_prem=6.0,
            far_prem=15.0,
            net_debit=9.0,
            result="Open",
        )
        session.add(trade)
        session.commit()

        trade.date_close = TODAY
        trade.spot_close = 1900.0
        trade.pnl = 4.50   # 50% of debit — take profit
        trade.result = "Win"
        session.commit()

        fetched = session.get(Calendar, trade.id)
        assert fetched.result == "Win"
        assert fetched.pnl == pytest.approx(4.50)


# ── TradeState ────────────────────────────────────────────────────────────────


class TestTradeState:
    def test_create_wheel_state(self, session):
        state = TradeState(
            strategy=STRATEGY_WHEEL,
            asset="ETH",
            stage=STAGE_NO_POSITION,
            asset_held=0.0,
            cost_basis=0.0,
            total_premium=0.0,
            wins=0,
            losses=0,
            cycles=0,
        )
        session.add(state)
        session.commit()

        fetched = session.get(TradeState, state.id)
        assert fetched.strategy == STRATEGY_WHEEL
        assert fetched.stage == STAGE_NO_POSITION

    def test_open_position_json_roundtrip(self, session):
        position = {
            "type": "Put",
            "strike": 1800.0,
            "expiry": "13-May-2026",
            "premium": 12.50,
            "spot_open": 1950.0,
            "qty": 0.139,
            "days": 7,
            "asset": "ETH",
        }
        state = TradeState(
            strategy=STRATEGY_WHEEL,
            asset="ETH",
            stage=STAGE_SHORT_PUT,
            open_position=position,
        )
        session.add(state)
        session.commit()
        session.expire(state)

        fetched = session.get(TradeState, state.id)
        assert fetched.open_position["strike"] == 1800.0
        assert fetched.open_position["type"] == "Put"

    def test_strangle_state(self, session):
        state = TradeState(
            strategy=STRATEGY_STRANGLE,
            asset="ETH",
            total_premium=0.0,
            wins=0,
            losses=0,
            trades=0,
        )
        session.add(state)
        session.commit()
        assert session.get(TradeState, state.id).strategy == STRATEGY_STRANGLE

    def test_calendar_state(self, session):
        state = TradeState(
            strategy=STRATEGY_CALENDAR,
            asset="BTC",
            total_pnl=0.0,
            wins=0,
            losses=0,
            trades=0,
        )
        session.add(state)
        session.commit()
        assert session.get(TradeState, state.id).strategy == STRATEGY_CALENDAR

    def test_unique_constraint(self, session):
        session.add(TradeState(strategy=STRATEGY_WHEEL, asset="ETH"))
        session.commit()
        session.add(TradeState(strategy=STRATEGY_WHEEL, asset="ETH"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_clear_open_position(self, session):
        state = TradeState(
            strategy=STRATEGY_WHEEL,
            asset="SOL",
            open_position={"type": "Put", "strike": 140.0},
        )
        session.add(state)
        session.commit()

        state.open_position = None
        session.commit()
        session.expire(state)

        fetched = session.get(TradeState, state.id)
        assert fetched.open_position is None


# ── TradeLedger ───────────────────────────────────────────────────────────────


class TestTradeLedger:
    def _make_ledger(self, session, underlying="ETH", strategy="singles", date_open=None, pnl=None, date_close=None):
        row = TradeLedger(
            underlying=underlying,
            strategy=strategy,
            trade_ref_id=1,
            date_open=date_open or TODAY,
            date_close=date_close,
            spot_open=1900.0,
            spot_close=1950.0 if date_close else None,
            fees=0.0,
            pnl=pnl,
        )
        session.add(row)
        session.commit()
        return row

    def test_open_trade(self, session):
        row = self._make_ledger(session)
        assert row.is_open
        assert row.date_close is None

    def test_close_trade(self, session):
        row = self._make_ledger(session, pnl=12.50, date_close=TODAY)
        assert not row.is_open
        assert row.pnl == 12.50

    def test_open_trades_classmethod(self, session):
        self._make_ledger(session, date_open=YESTERDAY)            # open
        self._make_ledger(session, pnl=5.0, date_close=TODAY)     # closed
        open_trades = TradeLedger.open_trades(session)
        assert len(open_trades) == 1
        assert open_trades[0].is_open

    def test_closed_trades_classmethod(self, session):
        self._make_ledger(session)                                 # open
        self._make_ledger(session, pnl=10.0, date_close=YESTERDAY)
        self._make_ledger(session, pnl=5.0, date_close=TODAY)
        closed = TradeLedger.closed_trades(session)
        assert len(closed) == 2
        assert closed[0].date_close >= closed[1].date_close  # desc order

    def test_cumulative_pnl(self, session):
        d1 = datetime.date(2026, 4, 1)
        d2 = datetime.date(2026, 4, 2)
        d3 = datetime.date(2026, 4, 3)

        self._make_ledger(session, pnl=10.0, date_close=d1)
        self._make_ledger(session, pnl=-5.0, date_close=d2)
        self._make_ledger(session, pnl=8.0, date_close=d3)

        rows = TradeLedger.with_cumulative_pnl(session)
        assert len(rows) == 3
        assert rows[0]["cumulative_pnl"] == pytest.approx(10.0)
        assert rows[1]["cumulative_pnl"] == pytest.approx(5.0)
        assert rows[2]["cumulative_pnl"] == pytest.approx(13.0)

    def test_cumulative_pnl_excludes_open(self, session):
        self._make_ledger(session, pnl=10.0, date_close=TODAY)
        self._make_ledger(session)   # open — no pnl
        rows = TradeLedger.with_cumulative_pnl(session)
        assert len(rows) == 1

    def test_multiple_strategies(self, session):
        for strategy in ("singles", "strangle", "calendar"):
            self._make_ledger(session, strategy=strategy, pnl=5.0, date_close=TODAY)
        rows = TradeLedger.with_cumulative_pnl(session)
        assert len(rows) == 3
        assert rows[-1]["cumulative_pnl"] == pytest.approx(15.0)

    def test_repr_open(self, session):
        row = self._make_ledger(session)
        assert "open" in repr(row)

    def test_repr_closed(self, session):
        row = self._make_ledger(session, pnl=12.50, date_close=TODAY)
        assert "pnl" in repr(row)
