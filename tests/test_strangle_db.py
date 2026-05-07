"""
tests/test_strangle_db.py
=========================
Tests for database/strangle_db.py — CRUD helpers for strangle state and trades.

All tests use the in-memory SQLite database provided by the conftest
autouse fixture (use_in_memory_db), so no real database is touched.

Coverage
--------
load_strangle_state   : defaults on first call, persists existing row
save_strangle_state   : creates row, updates row, roundtrip
create_strangle_trade : inserts Strangle record with all fields, returns id
close_strangle_trade  : updates result/pnl/spot_close/date_close
get_strangle_stats    : empty, filtered by asset, win/loss counts
"""

from datetime import date

import pytest

from database.strangle_db import (
    load_strangle_state,
    save_strangle_state,
    create_strangle_trade,
    close_strangle_trade,
    get_strangle_stats,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _open_trade(asset="ETH", **kwargs) -> object:
    """Insert a minimal open strangle trade and return the record."""
    defaults = dict(
        asset=asset,
        date_open=date(2026, 5, 1),
        put_strike=1800.0,
        call_strike=2200.0,
        spot_open=2000.0,
        total_premium=50.0,
        qty=0.125,
        days=7,
        expiry="08-May-2026",
    )
    defaults.update(kwargs)
    return create_strangle_trade(**defaults)


# ── load_strangle_state ───────────────────────────────────────────────────────

class TestLoadStrangleState:

    def test_first_call_returns_defaults(self):
        s = load_strangle_state("ETH")
        assert s["open"]          is None
        assert s["total_premium"] == 0.0
        assert s["wins"]          == 0
        assert s["losses"]        == 0
        assert s["trades"]        == 0

    def test_all_required_keys_present(self):
        s = load_strangle_state("BTC")
        for key in ("open", "total_premium", "wins", "losses", "trades"):
            assert key in s

    def test_persisted_row_is_returned_on_second_call(self):
        """After saving, load returns the saved values."""
        save_strangle_state("SOL", {
            "open": None, "total_premium": 75.0, "wins": 2, "losses": 1, "trades": 3,
        })
        s = load_strangle_state("SOL")
        assert s["total_premium"] == pytest.approx(75.0)
        assert s["wins"]          == 2

    def test_separate_assets_are_independent(self):
        save_strangle_state("ETH", {
            "open": None, "total_premium": 100.0, "wins": 1, "losses": 0, "trades": 1,
        })
        btc = load_strangle_state("BTC")
        assert btc["total_premium"] == 0.0  # BTC untouched


# ── save_strangle_state ───────────────────────────────────────────────────────

class TestSaveStrangleState:

    def test_creates_new_row_on_first_save(self):
        save_strangle_state("XRP", {
            "open": None, "total_premium": 25.0, "wins": 1, "losses": 0, "trades": 1,
        })
        s = load_strangle_state("XRP")
        assert s["total_premium"] == pytest.approx(25.0)

    def test_update_overwrites_previous_values(self):
        save_strangle_state("ETH", {
            "open": None, "total_premium": 50.0, "wins": 1, "losses": 0, "trades": 1,
        })
        save_strangle_state("ETH", {
            "open": None, "total_premium": 120.0, "wins": 2, "losses": 1, "trades": 3,
        })
        s = load_strangle_state("ETH")
        assert s["total_premium"] == pytest.approx(120.0)
        assert s["wins"]          == 2
        assert s["trades"]        == 3

    def test_open_position_dict_stored_and_retrieved(self):
        op = {"put_strike": 1800.0, "call_strike": 2200.0, "trade_id": 99}
        save_strangle_state("ETH", {
            "open": op, "total_premium": 50.0, "wins": 0, "losses": 0, "trades": 1,
        })
        s = load_strangle_state("ETH")
        assert s["open"]["put_strike"]  == pytest.approx(1800.0)
        assert s["open"]["trade_id"]    == 99

    def test_clear_open_position_to_none(self):
        save_strangle_state("ETH", {
            "open": {"trade_id": 5}, "total_premium": 50.0, "wins": 0, "losses": 0, "trades": 1,
        })
        save_strangle_state("ETH", {
            "open": None, "total_premium": 50.0, "wins": 1, "losses": 0, "trades": 1,
        })
        s = load_strangle_state("ETH")
        assert s["open"] is None


# ── create_strangle_trade ─────────────────────────────────────────────────────

class TestCreateStrangleTrade:

    def test_returns_strangle_object(self):
        trade = _open_trade()
        assert trade is not None

    def test_has_valid_id(self):
        trade = _open_trade()
        assert isinstance(trade.id, int)
        assert trade.id > 0

    def test_result_is_open(self):
        trade = _open_trade()
        assert trade.result == "Open"

    def test_fields_stored_correctly(self):
        trade = _open_trade(
            asset="BTC",
            put_strike=60000.0,
            call_strike=70000.0,
            spot_open=65000.0,
            total_premium=500.0,
            qty=0.01,
            days=14,
            expiry="15-May-2026",
            notes="BTC test strangle",
        )
        assert trade.asset         == "BTC"
        assert trade.put_strike    == pytest.approx(60000.0)
        assert trade.call_strike   == pytest.approx(70000.0)
        assert trade.spot_open     == pytest.approx(65000.0)
        assert trade.total_premium == pytest.approx(500.0)
        assert trade.days          == 14
        assert trade.notes         == "BTC test strangle"

    def test_date_open_stored(self):
        trade = _open_trade(date_open=date(2026, 5, 7))
        assert trade.date_open == date(2026, 5, 7)

    def test_fees_default_to_zero(self):
        trade = _open_trade()
        assert trade.fees == pytest.approx(0.0)

    def test_multiple_trades_get_unique_ids(self):
        t1 = _open_trade()
        t2 = _open_trade()
        assert t1.id != t2.id

    def test_date_close_is_none_on_open(self):
        trade = _open_trade()
        assert trade.date_close is None

    def test_pnl_is_none_on_open(self):
        trade = _open_trade()
        assert trade.pnl is None


# ── close_strangle_trade ──────────────────────────────────────────────────────

class TestCloseStrangleTrade:

    def test_updates_result(self):
        trade = _open_trade()
        closed = close_strangle_trade(
            trade.id, date(2026, 5, 8), 1950.0, 45.0, "Win"
        )
        assert closed.result == "Win"

    def test_updates_pnl(self):
        trade = _open_trade()
        closed = close_strangle_trade(
            trade.id, date(2026, 5, 8), 1950.0, 45.0, "Win"
        )
        assert closed.pnl == pytest.approx(45.0)

    def test_updates_spot_close(self):
        trade = _open_trade()
        closed = close_strangle_trade(
            trade.id, date(2026, 5, 8), 1950.0, 45.0, "Win"
        )
        assert closed.spot_close == pytest.approx(1950.0)

    def test_updates_date_close(self):
        trade = _open_trade()
        closed = close_strangle_trade(
            trade.id, date(2026, 5, 8), 1950.0, 45.0, "Win"
        )
        assert closed.date_close == date(2026, 5, 8)

    def test_loss_result(self):
        trade = _open_trade()
        closed = close_strangle_trade(
            trade.id, date(2026, 5, 8), 2500.0, -30.0, "Loss"
        )
        assert closed.result == "Loss"
        assert closed.pnl    == pytest.approx(-30.0)

    def test_notes_updated_when_provided(self):
        trade = _open_trade()
        closed = close_strangle_trade(
            trade.id, date(2026, 5, 8), 2000.0, 50.0, "Win", notes="Expired OTM"
        )
        assert closed.notes == "Expired OTM"

    def test_raises_for_invalid_trade_id(self):
        with pytest.raises(ValueError, match="not found"):
            close_strangle_trade(99999, date(2026, 5, 8), 2000.0, 50.0, "Win")


# ── get_strangle_stats ────────────────────────────────────────────────────────

class TestGetStrangleStats:

    def test_empty_database_returns_zeros(self):
        stats = get_strangle_stats()
        assert stats["trades"]        == 0
        assert stats["wins"]          == 0
        assert stats["losses"]        == 0
        assert stats["win_rate"]      == 0.0
        assert stats["total_premium"] == 0.0
        assert stats["avg_premium"]   == 0.0

    def test_open_trades_excluded(self):
        """Trades with result='Open' should not appear in stats."""
        _open_trade()  # not closed → should be excluded
        stats = get_strangle_stats()
        assert stats["trades"] == 0

    def test_counts_wins_and_losses(self):
        t1 = _open_trade(); close_strangle_trade(t1.id, date(2026, 5, 8), 1900.0, 40.0, "Win")
        t2 = _open_trade(); close_strangle_trade(t2.id, date(2026, 5, 9), 2500.0, -20.0, "Loss")
        t3 = _open_trade(); close_strangle_trade(t3.id, date(2026, 5, 10), 2050.0, 48.0, "Win")

        stats = get_strangle_stats()
        assert stats["trades"]  == 3
        assert stats["wins"]    == 2
        assert stats["losses"]  == 1

    def test_win_rate_calculation(self):
        t1 = _open_trade(); close_strangle_trade(t1.id, date(2026, 5, 8), 2000.0, 50.0, "Win")
        t2 = _open_trade(); close_strangle_trade(t2.id, date(2026, 5, 9), 2000.0, 50.0, "Win")
        t3 = _open_trade(); close_strangle_trade(t3.id, date(2026, 5, 10), 2000.0, -10.0, "Loss")

        stats = get_strangle_stats()
        assert stats["win_rate"] == pytest.approx(2 / 3 * 100, rel=1e-3)

    def test_total_premium_sum(self):
        t1 = _open_trade(total_premium=50.0)
        t2 = _open_trade(total_premium=75.0)
        close_strangle_trade(t1.id, date(2026, 5, 8), 2000.0, 50.0, "Win")
        close_strangle_trade(t2.id, date(2026, 5, 9), 2000.0, 70.0, "Win")

        stats = get_strangle_stats()
        assert stats["total_premium"] == pytest.approx(125.0)

    def test_avg_premium_calculation(self):
        t1 = _open_trade(total_premium=40.0)
        t2 = _open_trade(total_premium=60.0)
        close_strangle_trade(t1.id, date(2026, 5, 8), 2000.0, 35.0, "Win")
        close_strangle_trade(t2.id, date(2026, 5, 9), 2000.0, 55.0, "Win")

        stats = get_strangle_stats()
        assert stats["avg_premium"] == pytest.approx(50.0)

    def test_filter_by_asset(self):
        eth = _open_trade(asset="ETH")
        btc = _open_trade(asset="BTC")
        close_strangle_trade(eth.id, date(2026, 5, 8), 2000.0, 50.0, "Win")
        close_strangle_trade(btc.id, date(2026, 5, 9), 65000.0, -100.0, "Loss")

        eth_stats = get_strangle_stats(asset="ETH")
        btc_stats = get_strangle_stats(asset="BTC")
        assert eth_stats["trades"] == 1
        assert eth_stats["wins"]   == 1
        assert btc_stats["trades"] == 1
        assert btc_stats["losses"] == 1

    def test_auto_tp_counted_as_win(self):
        t = _open_trade()
        close_strangle_trade(t.id, date(2026, 5, 8), 2000.0, 45.0, "Win (Auto TP)")
        stats = get_strangle_stats()
        assert stats["wins"] == 1

    def test_auto_stop_counted_as_loss(self):
        t = _open_trade()
        close_strangle_trade(t.id, date(2026, 5, 8), 2500.0, -40.0, "Loss (Auto Stop)")
        stats = get_strangle_stats()
        assert stats["losses"] == 1
