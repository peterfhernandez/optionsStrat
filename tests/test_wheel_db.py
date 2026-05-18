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
from models import STRATEGY_WHEEL


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
        """First load with no trades should return default state."""
        state = load_wheel_state("ETH", session=session)

        assert state["stage"] == "no_position"
        assert state["open"] is None
        assert state["asset_held"] == 0.0
        assert state["total_premium"] == 0.0
        assert state["wins"] == 0
        assert state["losses"] == 0
        assert state["cycles"] == 0

    def test_load_existing_trades(self, session):
        """Loading state should aggregate stats from existing trades."""
        # Create some trades
        trade1 = create_single_trade(
            asset="BTC",
            date_open=datetime.date(2026, 5, 1),
            option_type="Put",
            strike=50000.0,
            expiry="13-May-2026",
            spot_open=55000.0,
            premium=100.0,
            qty=0.005,
            days=7,
            stage="short_put",
            session=session,
        )
        trade1.result = "Win"
        session.commit()

        # Load state
        state = load_wheel_state("BTC", session=session)
        assert state["stage"] == "short_put"
        assert state["total_premium"] == 100.0
        assert state["wins"] == 1
        assert state["losses"] == 0
        assert state["cycles"] == 1

    def test_save_updates_state(self, session):
        """Saving should update the most recent trade record."""
        # Create a trade
        trade = create_single_trade(
            asset="SOL",
            date_open=datetime.date(2026, 5, 1),
            option_type="Put",
            strike=150.0,
            expiry="13-May-2026",
            spot_open=160.0,
            premium=50.0,
            qty=0.1,
            days=7,
            stage="short_put",
            session=session,
        )
        session.commit()

        # Modify and save
        state = {"stage": "holding", "asset_held": 10.0, "cost_basis": 150.0, "broker": "test"}
        save_wheel_state("SOL", state, session=session)
        session.commit()

        # Reload and verify
        updated_trade = session.get(Single, trade.id)
        assert updated_trade.stage == "holding"
        assert updated_trade.asset_held == 10.0
        assert updated_trade.cost_basis == 150.0
        assert updated_trade.broker == "test"


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
