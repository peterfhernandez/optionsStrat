"""Tests for database/spread_db.py — Credit Spread persistence layer."""
import pytest
from datetime import date

from database.spread_db import (
    load_spread_state,
    save_spread_state,
    create_spread_trade,
    close_spread_trade,
    get_spread_stats,
)


@pytest.fixture(autouse=True)
def patch_spread_session(monkeypatch, use_in_memory_db):
    """Redirect spread_db get_session to the in-memory database."""
    from models import get_session
    monkeypatch.setattr("database.spread_db.get_session", get_session)


# ── load / save state ─────────────────────────────────────────────────────────

class TestLoadSaveState:
    def test_initial_state_is_default(self):
        s = load_spread_state("ETH")
        assert s["open"]       is None
        assert s["net_credit"] == 0.0
        assert s["wins"]       == 0
        assert s["losses"]     == 0
        assert s["trades"]     == 0

    def test_save_and_reload(self):
        state = {
            "open":       {"spread_type": "BPS", "short_strike": 1800.0},
            "net_credit": 12.5,
            "wins":       2,
            "losses":     1,
            "trades":     3,
            "broker":     "deribit_paper",
        }
        save_spread_state("ETH", state)
        loaded = load_spread_state("ETH")
        assert loaded["net_credit"] == 12.5
        assert loaded["wins"]       == 2
        assert loaded["losses"]     == 1
        assert loaded["trades"]     == 3
        assert loaded["broker"]     == "deribit_paper"
        assert loaded["open"]["short_strike"] == 1800.0

    def test_save_clears_open_position(self):
        state = {"open": {"spread_type": "BCS"}, "net_credit": 5.0,
                 "wins": 0, "losses": 0, "trades": 0, "broker": None}
        save_spread_state("BTC", state)
        state["open"] = None
        save_spread_state("BTC", state)
        loaded = load_spread_state("BTC")
        assert loaded["open"] is None


# ── create / close trade ──────────────────────────────────────────────────────

class TestCreateCloseTrade:
    def test_create_bps_trade(self):
        t = create_spread_trade(
            asset="ETH", spread_type="BPS",
            date_open=date(2026, 5, 15),
            short_strike=1800.0, long_strike=1700.0,
            spot_open=2000.0, net_credit=8.0, max_loss=92.0,
            qty=0.125, days=7, expiry="22-May-2026",
        )
        assert t.id is not None
        assert t.spread_type  == "BPS"
        assert t.short_strike == 1800.0
        assert t.long_strike  == 1700.0
        assert t.result       == "Open"

    def test_create_bcs_trade(self):
        t = create_spread_trade(
            asset="ETH", spread_type="BCS",
            date_open=date(2026, 5, 15),
            short_strike=2200.0, long_strike=2300.0,
            spot_open=2000.0, net_credit=6.0, max_loss=94.0,
            qty=0.125, days=7, expiry="22-May-2026",
        )
        assert t.spread_type == "BCS"
        assert t.result      == "Open"

    def test_close_trade_win(self):
        t = create_spread_trade(
            asset="ETH", spread_type="BPS",
            date_open=date(2026, 5, 15),
            short_strike=1800.0, long_strike=1700.0,
            spot_open=2000.0, net_credit=8.0, max_loss=92.0,
            qty=0.125, days=7, expiry="22-May-2026",
        )
        closed = close_spread_trade(
            t.id, date_close=date(2026, 5, 22),
            spot_close=2100.0, pnl=8.0, result="Win",
        )
        assert closed.result    == "Win"
        assert closed.pnl       == 8.0
        assert closed.date_close == date(2026, 5, 22)

    def test_close_trade_loss(self):
        t = create_spread_trade(
            asset="ETH", spread_type="BCS",
            date_open=date(2026, 5, 15),
            short_strike=2200.0, long_strike=2300.0,
            spot_open=2000.0, net_credit=6.0, max_loss=94.0,
            qty=0.125, days=7, expiry="22-May-2026",
        )
        closed = close_spread_trade(
            t.id, date_close=date(2026, 5, 22),
            spot_close=2350.0, pnl=-88.0, result="Loss",
        )
        assert closed.result == "Loss"
        assert closed.pnl    == -88.0

    def test_close_invalid_id_raises(self):
        with pytest.raises(ValueError, match="not found"):
            close_spread_trade(9999, date.today(), 2000.0, 0.0, "Win")


# ── stats ─────────────────────────────────────────────────────────────────────

class TestGetSpreadStats:
    def test_empty_returns_zeros(self):
        stats = get_spread_stats()
        assert stats["trades"]   == 0
        assert stats["win_rate"] == 0.0

    def test_counts_wins_and_losses(self):
        for result, pnl in [("Win", 8.0), ("Win", 6.0), ("Loss", -50.0)]:
            t = create_spread_trade(
                asset="ETH", spread_type="BPS",
                date_open=date(2026, 5, 15),
                short_strike=1800.0, long_strike=1700.0,
                spot_open=2000.0, net_credit=8.0, max_loss=92.0,
                qty=0.125, days=7, expiry="22-May-2026",
            )
            close_spread_trade(t.id, date.today(), 2000.0, pnl, result)

        stats = get_spread_stats()
        assert stats["trades"]   == 3
        assert stats["wins"]     == 2
        assert stats["losses"]   == 1
        assert abs(stats["win_rate"] - 66.67) < 0.1

    def test_filter_by_asset(self):
        for asset in ("ETH", "BTC"):
            t = create_spread_trade(
                asset=asset, spread_type="BPS",
                date_open=date(2026, 5, 15),
                short_strike=1800.0, long_strike=1700.0,
                spot_open=2000.0, net_credit=8.0, max_loss=92.0,
                qty=0.125, days=7, expiry="22-May-2026",
            )
            close_spread_trade(t.id, date.today(), 2000.0, 8.0, "Win")

        eth_stats = get_spread_stats(asset="ETH")
        assert eth_stats["trades"] == 1

    def test_open_trades_excluded(self):
        create_spread_trade(
            asset="ETH", spread_type="BPS",
            date_open=date(2026, 5, 15),
            short_strike=1800.0, long_strike=1700.0,
            spot_open=2000.0, net_credit=8.0, max_loss=92.0,
            qty=0.125, days=7, expiry="22-May-2026",
        )
        # Not closed — result="Open" should be excluded
        stats = get_spread_stats()
        assert stats["trades"] == 0
