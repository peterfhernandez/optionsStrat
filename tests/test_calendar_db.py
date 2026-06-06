"""
tests/test_calendar_db.py
=========================
Tests for database/calendar_db.py — CRUD helpers for calendar state and trades.

All tests use the in-memory SQLite database provided by the conftest
autouse fixture (use_in_memory_db), so no real database is touched.

Coverage
--------
load_calendar_state   : defaults on first call, persists existing row
save_calendar_state   : creates row, updates row, roundtrip
create_calendar_trade : inserts Calendar record with all fields, returns id
close_calendar_trade  : updates result/pnl/spot_close/date_close
get_calendar_stats    : empty, filtered by asset, win/loss counts
"""

from datetime import date

import pytest

from database.calendar_db import (
    load_calendar_state,
    save_calendar_state,
    create_calendar_trade,
    close_calendar_trade,
    get_calendar_stats,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _open_trade(asset="ETH", **kwargs) -> object:
    """Insert a minimal open calendar trade and return the record."""
    defaults = dict(
        asset=asset,
        date_open=date(2026, 5, 1),
        option_type="Call",
        strike=2000.0,
        expiry_near="08-May-2026",
        expiry_far="01-Jun-2026",
        near_days=7,
        far_days=30,
        qty=0.125,
        spot_open=2000.0,
        near_prem=10.0,
        far_prem=25.0,
        net_debit=15.0,
    )
    defaults.update(kwargs)
    return create_calendar_trade(**defaults)


# ── load_calendar_state ───────────────────────────────────────────────────────

class TestLoadCalendarState:

    def test_first_call_returns_defaults(self):
        s = load_calendar_state("ETH")
        assert s["open"]      is None
        assert s["total_pnl"] == 0.0
        assert s["wins"]      == 0
        assert s["losses"]    == 0
        assert s["trades"]    == 0

    def test_all_required_keys_present(self):
        s = load_calendar_state("BTC")
        for key in ("open", "total_pnl", "wins", "losses", "trades"):
            assert key in s

    def test_persisted_row_is_returned_on_second_call(self):
        """After creating trades, load aggregates their stats."""
        # Create 3 closed trades for SOL
        for i in range(2):
            trade = _open_trade(asset="SOL")
            close_calendar_trade(trade.id, trade.date_open, 2000.0, 20.0, "Win")

        trade = _open_trade(asset="SOL")
        close_calendar_trade(trade.id, trade.date_open, 2000.0, 2.0, "Loss")

        s = load_calendar_state("SOL")
        assert s["total_pnl"] == pytest.approx(42.0)
        assert s["wins"]      == 2
        assert s["losses"]    == 1
        assert s["trades"]    == 3

    def test_separate_assets_are_independent(self):
        # Create trade for ETH
        trade = _open_trade(asset="ETH")
        trade.result = "Win"
        trade.pnl = 100.0

        # BTC should have no trades
        btc = load_calendar_state("BTC")
        assert btc["total_pnl"] == 0.0  # BTC untouched
        assert btc["wins"]      == 0


# ── save_calendar_state ───────────────────────────────────────────────────────

class TestSaveCalendarState:

    def test_creates_new_row_on_first_save(self):
        """save_calendar_state updates broker on existing record."""
        trade = _open_trade(asset="XRP")
        close_calendar_trade(trade.id, trade.date_open, 2000.0, 25.0, "Win")
        save_calendar_state("XRP", {"broker": "deribit_paper"})

        s = load_calendar_state("XRP")
        assert s["total_pnl"] == pytest.approx(25.0)
        assert s["broker"] == "deribit_paper"

    def test_update_overwrites_previous_values(self):
        """Creating more trades updates the aggregated stats."""
        # First trade
        trade1 = _open_trade(asset="ETH")
        close_calendar_trade(trade1.id, trade1.date_open, 2000.0, 50.0, "Win")

        # Second set of trades
        trade2 = _open_trade(asset="ETH")
        close_calendar_trade(trade2.id, trade2.date_open, 2000.0, 70.0, "Win")

        trade3 = _open_trade(asset="ETH")
        close_calendar_trade(trade3.id, trade3.date_open, 2000.0, 0.0, "Loss")

        s = load_calendar_state("ETH")
        assert s["total_pnl"] == pytest.approx(120.0)
        assert s["wins"]      == 2
        assert s["losses"]    == 1
        assert s["trades"]    == 3

    def test_open_position_dict_stored_and_retrieved(self):
        """Open trade position is reconstructed from recent open record."""
        trade = _open_trade(
            asset="ETH",
            strike=2000.0,
            option_type="Call"
        )
        # Leave it open

        s = load_calendar_state("ETH")
        assert s["open"] is not None
        assert s["open"]["strike"]     == pytest.approx(2000.0)
        assert s["open"]["option_type"] == "Call"

    def test_clear_open_position_to_none(self):
        save_calendar_state("ETH", {
            "open": {"trade_id": 5}, "total_pnl": 0.0, "wins": 0, "losses": 0, "trades": 1,
        })
        save_calendar_state("ETH", {
            "open": None, "total_pnl": 10.0, "wins": 1, "losses": 0, "trades": 1,
        })
        s = load_calendar_state("ETH")
        assert s["open"] is None


# ── create_calendar_trade ─────────────────────────────────────────────────────

class TestCreateCalendarTrade:

    def test_returns_calendar_object(self):
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
            option_type="Put",
            strike=60000.0,
            near_prem=80.0,
            far_prem=200.0,
            net_debit=120.0,
            qty=0.01,
            near_days=7,
            far_days=30,
            notes="BTC test calendar",
        )
        assert trade.asset        == "BTC"
        assert trade.option_type  == "Put"
        assert trade.strike       == pytest.approx(60000.0)
        assert trade.near_prem    == pytest.approx(80.0)
        assert trade.far_prem     == pytest.approx(200.0)
        assert trade.net_debit    == pytest.approx(120.0)
        assert trade.notes        == "BTC test calendar"

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


# ── close_calendar_trade ──────────────────────────────────────────────────────

class TestCloseCalendarTrade:

    def test_updates_result(self):
        trade = _open_trade()
        closed = close_calendar_trade(trade.id, date(2026, 5, 8), 2050.0, 8.0, "Win")
        assert closed.result == "Win"

    def test_updates_pnl(self):
        trade = _open_trade()
        closed = close_calendar_trade(trade.id, date(2026, 5, 8), 2050.0, 8.0, "Win")
        assert closed.pnl == pytest.approx(8.0)

    def test_updates_spot_close(self):
        trade = _open_trade()
        closed = close_calendar_trade(trade.id, date(2026, 5, 8), 2050.0, 8.0, "Win")
        assert closed.spot_close == pytest.approx(2050.0)

    def test_updates_date_close(self):
        trade = _open_trade()
        closed = close_calendar_trade(trade.id, date(2026, 5, 8), 2050.0, 8.0, "Win")
        assert closed.date_close == date(2026, 5, 8)

    def test_loss_result(self):
        trade = _open_trade()
        closed = close_calendar_trade(trade.id, date(2026, 5, 8), 1500.0, -10.0, "Loss")
        assert closed.result == "Loss"
        assert closed.pnl    == pytest.approx(-10.0)

    def test_notes_updated_when_provided(self):
        trade = _open_trade()
        closed = close_calendar_trade(
            trade.id, date(2026, 5, 8), 2000.0, 5.0, "Win", notes="Expired near leg OTM"
        )
        assert closed.notes == "Expired near leg OTM"

    def test_raises_for_invalid_trade_id(self):
        with pytest.raises(ValueError, match="not found"):
            close_calendar_trade(99999, date(2026, 5, 8), 2000.0, 5.0, "Win")


# ── get_calendar_stats ────────────────────────────────────────────────────────

class TestGetCalendarStats:

    def test_empty_database_returns_zeros(self):
        stats = get_calendar_stats()
        assert stats["trades"]    == 0
        assert stats["wins"]      == 0
        assert stats["losses"]    == 0
        assert stats["win_rate"]  == 0.0
        assert stats["total_pnl"] == 0.0
        assert stats["avg_pnl"]   == 0.0

    def test_open_trades_excluded(self):
        _open_trade()  # not closed → should be excluded
        stats = get_calendar_stats()
        assert stats["trades"] == 0

    def test_counts_wins_and_losses(self):
        t1 = _open_trade(); close_calendar_trade(t1.id, date(2026, 5, 8), 2050.0, 10.0, "Win")
        t2 = _open_trade(); close_calendar_trade(t2.id, date(2026, 5, 9), 1500.0, -8.0, "Loss")
        t3 = _open_trade(); close_calendar_trade(t3.id, date(2026, 5, 10), 2020.0, 5.0, "Win")

        stats = get_calendar_stats()
        assert stats["trades"]  == 3
        assert stats["wins"]    == 2
        assert stats["losses"]  == 1

    def test_win_rate_calculation(self):
        t1 = _open_trade(); close_calendar_trade(t1.id, date(2026, 5, 8), 2000.0, 10.0, "Win")
        t2 = _open_trade(); close_calendar_trade(t2.id, date(2026, 5, 9), 2000.0, 10.0, "Win")
        t3 = _open_trade(); close_calendar_trade(t3.id, date(2026, 5, 10), 2000.0, -5.0, "Loss")

        stats = get_calendar_stats()
        assert stats["win_rate"] == pytest.approx(2 / 3 * 100, rel=1e-3)

    def test_total_pnl_sum(self):
        t1 = _open_trade(); close_calendar_trade(t1.id, date(2026, 5, 8), 2000.0, 10.0, "Win")
        t2 = _open_trade(); close_calendar_trade(t2.id, date(2026, 5, 9), 2000.0, 7.0, "Win")

        stats = get_calendar_stats()
        assert stats["total_pnl"] == pytest.approx(17.0)

    def test_avg_pnl_calculation(self):
        t1 = _open_trade(); close_calendar_trade(t1.id, date(2026, 5, 8), 2000.0, 10.0, "Win")
        t2 = _open_trade(); close_calendar_trade(t2.id, date(2026, 5, 9), 2000.0, 6.0, "Win")

        stats = get_calendar_stats()
        assert stats["avg_pnl"] == pytest.approx(8.0)

    def test_filter_by_asset(self):
        eth = _open_trade(asset="ETH")
        btc = _open_trade(asset="BTC")
        close_calendar_trade(eth.id, date(2026, 5, 8), 2000.0, 10.0, "Win")
        close_calendar_trade(btc.id, date(2026, 5, 9), 65000.0, -5.0, "Loss")

        eth_stats = get_calendar_stats(asset="ETH")
        btc_stats = get_calendar_stats(asset="BTC")
        assert eth_stats["trades"] == 1
        assert eth_stats["wins"]   == 1
        assert btc_stats["trades"] == 1
        assert btc_stats["losses"] == 1

    def test_auto_tp_counted_as_win(self):
        t = _open_trade()
        close_calendar_trade(t.id, date(2026, 5, 8), 2000.0, 8.0, "Win (Auto TP)")
        stats = get_calendar_stats()
        assert stats["wins"] == 1

    def test_auto_stop_counted_as_loss(self):
        t = _open_trade()
        close_calendar_trade(t.id, date(2026, 5, 8), 1500.0, -10.0, "Loss (Auto Stop)")
        stats = get_calendar_stats()
        assert stats["losses"] == 1

    def test_loss_stop_counted_as_loss(self):
        t = _open_trade()
        close_calendar_trade(t.id, date(2026, 5, 8), 1500.0, -10.0, "Loss (Stop)")
        stats = get_calendar_stats()
        assert stats["losses"] == 1


# ── Far Leg Only status ───────────────────────────────────────────────────────

class TestFarLegOnlyStatus:
    """Test that 'Far Leg Only' status is properly loaded and managed."""

    def test_far_leg_only_is_loaded_as_open(self):
        """A trade with 'Far Leg Only' status should be loaded as an open position."""
        t = _open_trade()
        # Simulate near leg expiring by changing status to "Far Leg Only"
        close_calendar_trade(t.id, date(2026, 5, 8), 2050.0, 0.0, "Far Leg Only")

        # Load state — should have an open position (the far leg)
        state = load_calendar_state("ETH")
        assert state["open"] is not None
        assert state["open"]["status"] == "Far Leg Only"
        assert state["open"]["strike"] == 2000.0
        assert state["open"]["expiry_far"] == "01-Jun-2026"

    def test_far_leg_only_includes_trade_id(self):
        """Open position dict should include trade_id for updating."""
        t = _open_trade()
        close_calendar_trade(t.id, date(2026, 5, 8), 2050.0, 0.0, "Far Leg Only")

        state = load_calendar_state("ETH")
        assert state["open"]["trade_id"] == t.id

    def test_multiple_far_leg_only_loads_latest(self):
        """Only the most recent Far Leg Only trade should be loaded."""
        t1 = _open_trade()
        close_calendar_trade(t1.id, date(2026, 5, 8), 2050.0, 0.0, "Far Leg Only")

        # Create another position that got to Far Leg Only
        t2 = _open_trade()
        close_calendar_trade(t2.id, date(2026, 5, 9), 2040.0, 0.0, "Far Leg Only")

        state = load_calendar_state("ETH")
        assert state["open"]["trade_id"] == t2.id
