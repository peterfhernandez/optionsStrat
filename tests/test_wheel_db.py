"""Tests for database/wheel_db.py helpers."""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.wheel_db import (
    load_wheel_state,
    save_wheel_state,
    create_single_trade,
    close_single_trade,
    get_wheel_stats,
)
from models.base import Base
from models.singles import Single
from models.trade_state import TradeState, STRATEGY_WHEEL


@pytest.fixture(scope="function")
def session():
    """Fresh in-memory SQLite session per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()


class TestLoadSaveWheelState:
    def test_load_fresh_state(self, session):
        """First load should return default state and create a DB row."""
        state = load_wheel_state("ETH", session=session)

        assert state["stage"] == "no_position"
        assert state["open"] is None
        assert state["asset_held"] == 0.0
        assert state["total_premium"] == 0.0
        assert state["wins"] == 0
        assert state["losses"] == 0
        assert state["cycles"] == 0

        # Verify row was created
        row = session.query(TradeState).filter_by(strategy=STRATEGY_WHEEL, asset="ETH").first()
        assert row is not None

    def test_load_existing_state(self, session):
        """Loading an existing state should return saved values."""
        # Create a state manually
        row = TradeState(
            strategy=STRATEGY_WHEEL,
            asset="BTC",
            stage="short_put",
            total_premium=100.0,
            wins=5,
            losses=2,
            cycles=2,
        )
        session.add(row)
        session.commit()

        # Load it
        state = load_wheel_state("BTC", session=session)
        assert state["stage"] == "short_put"
        assert state["total_premium"] == 100.0
        assert state["wins"] == 5
        assert state["losses"] == 2
        assert state["cycles"] == 2

    def test_save_updates_state(self, session):
        """Saving should update the database row."""
        # Load initial state (creates row)
        state = load_wheel_state("SOL", session=session)

        # Modify and save
        state["stage"] = "short_put"
        state["total_premium"] = 50.0
        state["wins"] = 3
        save_wheel_state("SOL", state, session=session)

        # Reload and verify
        reloaded = load_wheel_state("SOL", session=session)
        assert reloaded["stage"] == "short_put"
        assert reloaded["total_premium"] == 50.0
        assert reloaded["wins"] == 3


class TestCreateCloseSingleTrade:
    def test_create_single_trade(self, session):
        """Creating a single trade should insert a row."""
        trade = create_single_trade(
            asset="ETH",
            date_open=datetime.date(2026, 5, 6),
            option_type="Put",
            strike=1800.0,
            expiry="13-May-2026",
            spot_open=1950.0,
            premium=12.50,
            qty=0.139,
            days=7,
            stage="short_put",
            notes="test trade",
            session=session,
        )

        assert trade.asset == "ETH"
        assert trade.option_type == "Put"
        assert trade.result == "Open"
        assert trade.date_close is None
        assert trade.pnl is None

    def test_close_single_trade(self, session):
        """Closing a trade should update close date and P&L."""
        # Create a trade
        trade = create_single_trade(
            asset="ETH",
            date_open=datetime.date(2026, 5, 1),
            option_type="Put",
            strike=1800.0,
            expiry="13-May-2026",
            spot_open=1950.0,
            premium=12.50,
            qty=0.139,
            days=7,
            session=session,
        )

        # Close it
        closed = close_single_trade(
            trade_id=trade.id,
            date_close=datetime.date(2026, 5, 6),
            spot_close=1900.0,
            pnl=12.50,
            result="Win",
            session=session,
        )

        assert closed.date_close == datetime.date(2026, 5, 6)
        assert closed.spot_close == 1900.0
        assert closed.pnl == 12.50
        assert closed.result == "Win"

    def test_close_nonexistent_trade_raises(self, session):
        """Closing a nonexistent trade should raise ValueError."""
        with pytest.raises(ValueError, match="Trade ID 999"):
            close_single_trade(
                trade_id=999,
                date_close=datetime.date.today(),
                spot_close=1900.0,
                pnl=10.0,
                result="Win",
                session=session,
            )


class TestGetWheelStats:
    def test_no_trades(self, session):
        """Empty database should return zero stats."""
        stats = get_wheel_stats(session=session)

        assert stats["trades"] == 0
        assert stats["wins"] == 0
        assert stats["losses"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["total_premium"] == 0.0

    def test_single_asset_stats(self, session):
        """Stats should filter by asset if provided."""
        # Create trades for two assets
        create_single_trade(
            asset="ETH",
            date_open=datetime.date(2026, 5, 1),
            option_type="Put",
            strike=1800.0,
            expiry="13-May-2026",
            spot_open=1950.0,
            premium=12.50,
            qty=0.139,
            days=7,
            session=session,
        )
        session.commit()

        create_single_trade(
            asset="BTC",
            date_open=datetime.date(2026, 5, 1),
            option_type="Put",
            strike=50000.0,
            expiry="13-May-2026",
            spot_open=55000.0,
            premium=100.0,
            qty=0.005,
            days=7,
            session=session,
        )
        session.commit()

        # Mark them as completed
        eth_trade = session.query(Single).filter_by(asset="ETH").first()
        eth_trade.result = "Win"
        btc_trade = session.query(Single).filter_by(asset="BTC").first()
        btc_trade.result = "Win"
        session.commit()

        # Get stats
        eth_stats = get_wheel_stats(asset="ETH", session=session)
        assert eth_stats["trades"] == 1
        assert eth_stats["wins"] == 1
        assert eth_stats["total_premium"] == 12.50

        btc_stats = get_wheel_stats(asset="BTC", session=session)
        assert btc_stats["trades"] == 1
        assert btc_stats["wins"] == 1
        assert btc_stats["total_premium"] == 100.0

    def test_win_loss_stats(self, session):
        """Stats should correctly count wins and losses."""
        # Create 3 trades: 2 wins, 1 loss
        for i in range(2):
            trade = create_single_trade(
                asset="ETH",
                date_open=datetime.date(2026, 5, 1),
                option_type="Put",
                strike=1800.0,
                expiry="13-May-2026",
                spot_open=1950.0,
                premium=12.50,
                qty=0.139,
                days=7,
                session=session,
            )
            trade.result = "Win"

        trade = create_single_trade(
            asset="ETH",
            date_open=datetime.date(2026, 5, 8),
            option_type="Put",
            strike=2000.0,
            expiry="20-May-2026",
            spot_open=1900.0,
            premium=15.0,
            qty=0.125,
            days=7,
            session=session,
        )
        trade.result = "Loss"

        session.commit()

        stats = get_wheel_stats(session=session)
        assert stats["trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate"] == pytest.approx(66.67, rel=1)
        assert stats["total_premium"] == pytest.approx(12.50 + 12.50 + 15.0)
