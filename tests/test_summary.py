"""
tests/test_summary.py
=====================
Tests for strategies/summary.py — performance summary display and
broker-forwarding via show_summary.

Test strategy
-------------
Tier 1 — no-trades path:
    show_summary with empty database prints "No trades yet" for each section
    show_summary prints active broker name in header

Tier 2 — with-trades path:
    show_summary displays trade counts, win rate, and P&L from database

Tier 3 — open positions:
    show_summary shows open wheel / strangle / calendar positions from state
    show_summary prints "No open positions" when all states are empty

Tier 4 — broker forwarding:
    default broker is DeribitClient
    supplied broker name is displayed in header
"""

from unittest.mock import patch, MagicMock, PropertyMock
import pytest

from access import OrderResult
from strategies.summary import show_summary, _show_open_positions


# ── Broker helpers ────────────────────────────────────────────────────────────

def _fake_broker(name: str = "deribit-paper") -> MagicMock:
    b = MagicMock()
    type(b).broker_name = PropertyMock(return_value=name)
    return b


# ── Shared state helpers ──────────────────────────────────────────────────────

_EMPTY_WHEEL = {"stage": "no_position", "open": None, "wins": 0, "losses": 0}
_EMPTY_STRANGLE = {"open": None, "wins": 0, "losses": 0}
_EMPTY_CALENDAR = {"open": None, "wins": 0, "losses": 0}

_OPEN_WHEEL = {
    "stage": "short_put",
    "broker": "deribit-paper",
    "open": {
        "type": "Put", "strike": 1800.0, "expiry": "15-May-2026",
        "premium": 50.0, "spot_open": 2000.0, "qty": 0.5,
        "days": 14, "asset": "ETH",
    },
    "wins": 0, "losses": 0,
}
_OPEN_STRANGLE = {
    "broker": "deribit-paper",
    "open": {
        "put_strike": 1800.0, "call_strike": 2200.0, "total_premium": 80.0,
        "qty": 0.5, "expiry": "15-May-2026", "spot_open": 2000.0,
        "days": 14, "asset": "ETH",
    },
    "wins": 0, "losses": 0,
}
_OPEN_CALENDAR = {
    "broker": "deribit-paper",
    "open": {
        "option_type": "Call", "strike": 2000.0,
        "expiry_near": "15-May-2026", "expiry_far": "15-Jun-2026",
        "net_debit": 30.0, "qty": 0.5, "spot_open": 2000.0,
        "near_days": 14, "far_days": 45, "asset": "ETH",
    },
    "wins": 0, "losses": 0,
}


# ── Tier 1: no-trades path ────────────────────────────────────────────────────

class TestShowSummaryNoTrades:
    def test_prints_no_trades_for_each_section(self, capsys):
        broker = _fake_broker()
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_premium": 0.0, "avg_premium": 0.0}),
            patch("strategies.summary.get_strangle_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
            patch("strategies.summary.get_calendar_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
        ):
            show_summary(broker=broker)
        out = capsys.readouterr().out
        assert "No trades yet" in out

    def test_prints_broker_name(self, capsys):
        broker = _fake_broker("my-test-broker")
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_premium": 0.0, "avg_premium": 0.0}),
            patch("strategies.summary.get_strangle_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
            patch("strategies.summary.get_calendar_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
        ):
            show_summary(broker=broker)
        out = capsys.readouterr().out
        assert "my-test-broker" in out


# ── Tier 2: with-trades path ──────────────────────────────────────────────────

class TestShowSummaryWithTrades:
    _wheel_stats = {
        "trades": 5, "wins": 4, "losses": 1,
        "win_rate": 80.0, "total_premium": 200.0, "avg_premium": 40.0,
    }
    _strangle_stats = {
        "trades": 3, "wins": 2, "losses": 1,
        "win_rate": 66.7, "total_pnl": 150.0, "avg_pnl": 50.0,
    }
    _calendar_stats = {
        "trades": 2, "wins": 2, "losses": 0,
        "win_rate": 100.0, "total_pnl": 80.0, "avg_pnl": 40.0,
    }

    def test_displays_trade_counts(self, capsys):
        broker = _fake_broker()
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value=self._wheel_stats),
            patch("strategies.summary.get_strangle_stats", return_value=self._strangle_stats),
            patch("strategies.summary.get_calendar_stats", return_value=self._calendar_stats),
        ):
            show_summary(broker=broker)
        out = capsys.readouterr().out
        assert "5" in out   # wheel trade count
        assert "3" in out   # strangle trade count
        assert "2" in out   # calendar trade count

    def test_displays_win_rate(self, capsys):
        broker = _fake_broker()
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value=self._wheel_stats),
            patch("strategies.summary.get_strangle_stats", return_value=self._strangle_stats),
            patch("strategies.summary.get_calendar_stats", return_value=self._calendar_stats),
        ):
            show_summary(broker=broker)
        out = capsys.readouterr().out
        assert "80.0%" in out

    def test_uses_total_premium_for_wheel(self, capsys):
        broker = _fake_broker()
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value=self._wheel_stats),
            patch("strategies.summary.get_strangle_stats", return_value=self._strangle_stats),
            patch("strategies.summary.get_calendar_stats", return_value=self._calendar_stats),
        ):
            show_summary(broker=broker)
        out = capsys.readouterr().out
        assert "Total Premium" in out
        assert "200.00" in out

    def test_uses_total_pnl_for_strangle(self, capsys):
        broker = _fake_broker()
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value=self._wheel_stats),
            patch("strategies.summary.get_strangle_stats", return_value=self._strangle_stats),
            patch("strategies.summary.get_calendar_stats", return_value=self._calendar_stats),
        ):
            show_summary(broker=broker)
        out = capsys.readouterr().out
        assert "Total P&L" in out
        assert "150.00" in out


# ── Tier 3: open positions ────────────────────────────────────────────────────

class TestOpenPositions:
    def test_no_open_positions_message(self, capsys):
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
        ):
            _show_open_positions()
        out = capsys.readouterr().out
        assert "No open positions" in out

    def test_shows_open_wheel_position(self, capsys):
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_OPEN_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
        ):
            _show_open_positions()
        out = capsys.readouterr().out
        assert "ETH Wheel" in out
        assert "1,800" in out   # strike formatted with comma
        assert "deribit-paper" in out

    def test_shows_open_strangle_position(self, capsys):
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_OPEN_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
        ):
            _show_open_positions()
        out = capsys.readouterr().out
        assert "ETH Strangle" in out
        assert "2,200" in out   # call strike
        assert "deribit-paper" in out

    def test_shows_open_calendar_position(self, capsys):
        with (
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_OPEN_CALENDAR),
        ):
            _show_open_positions()
        out = capsys.readouterr().out
        assert "ETH Calendar" in out
        assert "2,000" in out   # ATM strike
        assert "deribit-paper" in out


# ── Tier 4: broker forwarding ─────────────────────────────────────────────────

class TestBrokerForwarding:
    def test_default_broker_is_deribit_client(self, capsys):
        with (
            patch("strategies.summary.DeribitClient") as mock_deribit,
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_premium": 0.0, "avg_premium": 0.0}),
            patch("strategies.summary.get_strangle_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
            patch("strategies.summary.get_calendar_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
        ):
            instance = mock_deribit.return_value
            type(instance).broker_name = PropertyMock(return_value="deribit-paper")
            show_summary()
        mock_deribit.assert_called_once()

    def test_supplied_broker_not_replaced(self, capsys):
        broker = _fake_broker("custom-broker")
        with (
            patch("strategies.summary.DeribitClient") as mock_deribit,
            patch("strategies.summary.load_wheel_state",    return_value=_EMPTY_WHEEL),
            patch("strategies.summary.load_strangle_state", return_value=_EMPTY_STRANGLE),
            patch("strategies.summary.load_calendar_state", return_value=_EMPTY_CALENDAR),
            patch("strategies.summary.get_wheel_stats",    return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_premium": 0.0, "avg_premium": 0.0}),
            patch("strategies.summary.get_strangle_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
            patch("strategies.summary.get_calendar_stats", return_value={"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}),
        ):
            show_summary(broker=broker)
        mock_deribit.assert_not_called()
        out = capsys.readouterr().out
        assert "custom-broker" in out
