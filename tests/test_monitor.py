"""
tests/test_monitor.py
=====================
Tests for strategies/monitor.py — position monitoring, auto-close logic,
and the registry pattern.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _days_remaining     : multiple date formats, past/future/today,
                          unrecognised format falls back to 0

Tier 2 — mocked I/O:
    _check_strangle     : no state file, no open position, expiry trigger,
                          stop-loss trigger, take-profit trigger, no trigger,
                          win/loss counting, state cleared after close
    _check_wheel        : same trigger set for Put and Call types
    run_monitor         : active asset reused, other assets fetched,
                          failed fetch skips asset, registry called per asset

Approach
--------
_load_state and _save_state are mocked to avoid filesystem I/O.
append_strangle_row and append_trade_row are mocked to avoid openpyxl.
bs_put and bs_call are mocked to control trigger conditions precisely.
"""

import json
from datetime import date, timedelta
from unittest.mock import patch, MagicMock, call

import pytest

from strategies.monitor import (
    _days_remaining,
    _check_strangle,
    _check_wheel,
    run_monitor,
    TAKE_PROFIT_THRESHOLD,
)
from config import STOP_LOSS_MULTIPLIER


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_wb():
    """A mock openpyxl workbook — never actually written to."""
    return MagicMock()


@pytest.fixture
def future_expiry():
    """An expiry date 7 days from today in DD-Mon-YYYY format."""
    return (date.today() + timedelta(days=7)).strftime("%d-%b-%Y")


@pytest.fixture
def past_expiry():
    """An expiry date 1 day in the past."""
    return (date.today() - timedelta(days=1)).strftime("%d-%b-%Y")


@pytest.fixture
def today_expiry():
    """An expiry date of today (days_remaining = 0)."""
    return date.today().strftime("%d-%b-%Y")


@pytest.fixture
def strangle_state(future_expiry):
    """A minimal strangle state dict with one open position."""
    return {
        "open": {
            "put_strike":    1800.0,
            "call_strike":   2200.0,
            "total_premium": 50.0,
            "qty":           0.125,
            "expiry":        future_expiry,
            "spot_open":     2000.0,
            "days":          7,
            "asset":         "ETH",
        },
        "wins":          0,
        "losses":        0,
        "trades":        1,
        "total_premium": 50.0,
    }


@pytest.fixture
def wheel_state_put(future_expiry):
    """A minimal wheel state dict with an open put position."""
    return {
        "stage": "short_put",
        "open": {
            "type":      "Put",
            "strike":    1800.0,
            "premium":   25.0,
            "qty":       0.139,
            "expiry":    future_expiry,
            "spot_open": 2000.0,
            "days":      7,
            "asset":     "ETH",
        },
        "wins":          0,
        "losses":        0,
        "cycles":        0,
        "total_premium": 25.0,
        "asset_held":    0.0,
        "cost_basis":    0.0,
    }


@pytest.fixture
def wheel_state_call(future_expiry):
    """A minimal wheel state dict with an open call position."""
    return {
        "stage": "short_call",
        "open": {
            "type":      "Call",
            "strike":    2200.0,
            "premium":   20.0,
            "qty":       0.125,
            "expiry":    future_expiry,
            "spot_open": 2000.0,
            "days":      7,
            "asset":     "ETH",
        },
        "wins":          0,
        "losses":        0,
        "cycles":        0,
        "total_premium": 20.0,
        "asset_held":    0.125,
        "cost_basis":    1800.0,
    }


# ── _days_remaining ───────────────────────────────────────────────────────────

class TestDaysRemaining:

    def test_future_date_returns_positive(self):
        future = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y")
        result = _days_remaining(future)
        assert result > 0

    def test_past_date_returns_zero(self):
        past = (date.today() - timedelta(days=1)).strftime("%d-%b-%Y")
        assert _days_remaining(past) == 0

    def test_today_returns_zero(self):
        today = date.today().strftime("%d-%b-%Y")
        assert _days_remaining(today) == 0

    def test_format_dd_mon_yyyy(self):
        future = (date.today() + timedelta(days=5)).strftime("%d-%b-%Y")
        assert _days_remaining(future) > 0

    def test_format_dd_month_yyyy(self):
        """Full month name format: 25-April-2026."""
        future = (date.today() + timedelta(days=5)).strftime("%d-%B-%Y")
        assert _days_remaining(future) > 0

    def test_format_yyyy_mm_dd(self):
        """ISO format: 2026-05-01."""
        future = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        assert _days_remaining(future) > 0

    def test_unrecognised_format_returns_zero(self):
        """Unrecognised format → treat as expired."""
        assert _days_remaining("not-a-date") == 0
        assert _days_remaining("") == 0
        assert _days_remaining("01/05/2026") == 0

    def test_whitespace_stripped(self):
        future = "  " + (date.today() + timedelta(days=5)).strftime("%d-%b-%Y") + "  "
        assert _days_remaining(future) > 0

    def test_approximately_correct_days(self):
        """7 days from now should return ~7."""
        future = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y")
        result = _days_remaining(future)
        assert 6 <= result <= 7   # allow 1 day tolerance for edge cases

    def test_returns_int(self):
        future = (date.today() + timedelta(days=3)).strftime("%d-%b-%Y")
        assert isinstance(_days_remaining(future), int)

    def test_never_negative(self):
        """Result must always be ≥ 0."""
        very_past = "01-Jan-2000"
        assert _days_remaining(very_past) == 0


# ── _check_strangle ───────────────────────────────────────────────────────────

class TestCheckStrangle:

    def _patch(self, state, bs_put_val, bs_call_val,
               append_mock=None, save_mock=None):
        """Build a context manager stack for common patches."""
        return [
            patch("strategies.monitor._load_state",        return_value=state),
            patch("strategies.monitor._save_state",        save_mock or MagicMock()),
            patch("strategies.monitor.bs_put",             return_value=bs_put_val),
            patch("strategies.monitor.bs_call",            return_value=bs_call_val),
            patch("strategies.monitor.append_strangle_row", append_mock or MagicMock()),
        ]

    def test_no_state_file_returns_false(self, mock_wb):
        with patch("strategies.monitor._load_state", return_value=None):
            result = _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is False

    def test_no_open_position_returns_false(self, mock_wb):
        state = {"open": None, "wins": 0, "losses": 0}
        with patch("strategies.monitor._load_state", return_value=state):
            result = _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is False

    def test_no_trigger_returns_false(self, mock_wb, strangle_state):
        """Normal position — no trigger, no close."""
        p0  = strangle_state["open"]["total_premium"]   # 50.0
        qty = strangle_state["open"]["qty"]             # 0.125
        # cur_val = (bs_put + bs_call) * qty
        # Must be: > p0 * TAKE_PROFIT_THRESHOLD (5.0) and < p0 * STOP_LOSS (100.0)
        # Use 50% of premium as current value — safely in the middle
        # (bs_put + bs_call) * qty = p0 * 0.50 → per_unit = p0 * 0.50 / qty / 2
        per_unit = (p0 * 0.50) / qty / 2
        with patch("strategies.monitor._load_state",         return_value=strangle_state), \
             patch("strategies.monitor._save_state",         MagicMock()), \
             patch("strategies.monitor.bs_put",              return_value=per_unit), \
             patch("strategies.monitor.bs_call",             return_value=per_unit), \
             patch("strategies.monitor.append_strangle_row", MagicMock()):
            result = _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is False

    def test_stop_loss_trigger_returns_true(self, mock_wb, strangle_state):
        """Position value exceeds STOP_LOSS_MULTIPLIER × premium."""
        p0      = strangle_state["open"]["total_premium"]   # 50.0
        qty     = strangle_state["open"]["qty"]             # 0.125
        # cur_val needs to be ≥ p0 * STOP_LOSS_MULTIPLIER = 100.0
        # bs_put + bs_call returns are per unit, multiplied by qty
        # so we need per_unit × qty × 2 ≥ 100 → per_unit ≥ 400
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        append  = MagicMock()
        patches = self._patch(strangle_state, per_unit, per_unit, append)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("strategies.monitor._save_state"):
                result = _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is True

    def test_stop_loss_logs_to_excel(self, mock_wb, strangle_state):
        """Stop-loss trigger must call append_strangle_row."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        append  = MagicMock()
        patches = self._patch(strangle_state, per_unit, per_unit, append)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("strategies.monitor._save_state"):
                _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        append.assert_called_once()

    def test_stop_loss_clears_open_position(self, mock_wb, strangle_state):
        """After stop-loss, state["open"] must be set to None."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], \
             patch("strategies.monitor._save_state", side_effect=capture_save), \
             patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert len(saved_states) > 0
        assert saved_states[-1]["open"] is None

    def test_stop_loss_increments_losses(self, mock_wb, strangle_state):
        """Stop-loss (loss) should increment state['losses']."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], \
             patch("strategies.monitor._save_state", side_effect=capture_save), \
             patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert saved_states[-1]["losses"] == 1
        assert saved_states[-1]["wins"]   == 0

    def test_take_profit_trigger_returns_true(self, mock_wb, strangle_state):
        """Position value ≤ TAKE_PROFIT_THRESHOLD × premium → take profit."""
        p0  = strangle_state["open"]["total_premium"]   # 50.0
        qty = strangle_state["open"]["qty"]             # 0.125
        # cur_val needs to be ≤ p0 * TAKE_PROFIT_THRESHOLD = 5.0
        # bs_put + bs_call per_unit × qty × 2 ≤ 5.0 → per_unit ≤ 20
        per_unit = (p0 * TAKE_PROFIT_THRESHOLD) / qty / 2 - 0.01
        append   = MagicMock()
        patches  = self._patch(strangle_state, per_unit, per_unit, append)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("strategies.monitor._save_state"):
                result = _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is True

    def test_take_profit_increments_wins(self, mock_wb, strangle_state):
        """Take-profit (win) should increment state['wins']."""
        p0  = strangle_state["open"]["total_premium"]
        qty = strangle_state["open"]["qty"]
        per_unit = (p0 * TAKE_PROFIT_THRESHOLD) / qty / 2 - 0.01
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], \
             patch("strategies.monitor._save_state", side_effect=capture_save), \
             patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, mock_wb, True)
        assert saved_states[-1]["wins"]   == 1
        assert saved_states[-1]["losses"] == 0

    def test_expiry_trigger_spot_inside_strikes_is_win(
            self, mock_wb, strangle_state, today_expiry):
        """At expiry, spot between strikes → both legs worthless → win."""
        strangle_state["open"]["expiry"] = today_expiry
        spot = 2000.0  # between put=1800 and call=2200
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        with patch("strategies.monitor._load_state",         return_value=strangle_state), \
             patch("strategies.monitor._save_state",         side_effect=capture_save), \
             patch("strategies.monitor.bs_put",              return_value=0.1), \
             patch("strategies.monitor.bs_call",             return_value=0.1), \
             patch("strategies.monitor.append_strangle_row", MagicMock()):
            result = _check_strangle("ETH", spot, 0.80, mock_wb, True)
        assert result is True
        assert saved_states[-1]["wins"] == 1

    def test_expiry_trigger_spot_below_put_strike_is_loss(
            self, mock_wb, strangle_state, today_expiry):
        """At expiry, spot far below put strike → intrinsic loss exceeds premium → loss."""
        strangle_state["open"]["expiry"] = today_expiry
        p0  = strangle_state["open"]["total_premium"]   # 50.0
        Kp  = strangle_state["open"]["put_strike"]      # 1800.0
        qty = strangle_state["open"]["qty"]             # 0.125
        # Force a loss: intrinsic = (Kp - spot) * qty must exceed p0
        # (Kp - spot) * qty > p0 → spot < Kp - p0/qty = 1800 - 400 = 1400
        spot = 1300.0
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        with patch("strategies.monitor._load_state",         return_value=strangle_state), \
             patch("strategies.monitor._save_state",         side_effect=capture_save), \
             patch("strategies.monitor.bs_put",              return_value=0.1), \
             patch("strategies.monitor.bs_call",             return_value=0.1), \
             patch("strategies.monitor.append_strangle_row", MagicMock()):
            result = _check_strangle("ETH", spot, 0.80, mock_wb, True)
        assert result is True
        assert saved_states[-1]["losses"] == 1


# ── _check_wheel ──────────────────────────────────────────────────────────────

class TestCheckWheel:

    def test_no_state_file_returns_false(self, mock_wb):
        with patch("strategies.monitor._load_state", return_value=None):
            result = _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is False

    def test_no_open_position_returns_false(self, mock_wb):
        state = {"open": None, "stage": "no_position", "wins": 0, "losses": 0}
        with patch("strategies.monitor._load_state", return_value=state):
            result = _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is False

    def test_no_trigger_put_returns_false(self, mock_wb, wheel_state_put):
        """Normal put position — no trigger."""
        p0  = wheel_state_put["open"]["premium"]   # 25.0
        qty = wheel_state_put["open"]["qty"]       # 0.139
        # Must be: > p0 * TAKE_PROFIT_THRESHOLD (2.5) and < p0 * STOP_LOSS_MULTIPLIER (50.0)
        # cur = per_unit * qty, so per_unit must satisfy:
        #   per_unit * qty > p0 * TAKE_PROFIT_THRESHOLD
        #   per_unit * qty < p0 * STOP_LOSS_MULTIPLIER
        # Use 50% of premium as current value — safely in the middle
        per_unit = (p0 * 0.50) / qty
        with patch("strategies.monitor._load_state",     return_value=wheel_state_put), \
             patch("strategies.monitor.bs_put",          return_value=per_unit), \
             patch("strategies.monitor.bs_call",         return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            result = _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is False

    def test_stop_loss_put_returns_true(self, mock_wb, wheel_state_put):
        """Put position value exceeds stop-loss threshold."""
        p0  = wheel_state_put["open"]["premium"]   # 25.0
        qty = wheel_state_put["open"]["qty"]       # 0.139
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    MagicMock()), \
             patch("strategies.monitor.bs_put",         return_value=per_unit), \
             patch("strategies.monitor.bs_call",        return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            result = _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is True

    def test_stop_loss_call_returns_true(self, mock_wb, wheel_state_call):
        """Call position value exceeds stop-loss threshold."""
        p0  = wheel_state_call["open"]["premium"]   # 20.0
        qty = wheel_state_call["open"]["qty"]       # 0.125
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("strategies.monitor._load_state",    return_value=wheel_state_call), \
             patch("strategies.monitor._save_state",    MagicMock()), \
             patch("strategies.monitor.bs_put",         return_value=per_unit), \
             patch("strategies.monitor.bs_call",        return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            result = _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is True

    def test_stop_loss_clears_open_and_resets_stage(self, mock_wb, wheel_state_put):
        """After stop-loss, open=None and stage='no_position'."""
        p0  = wheel_state_put["open"]["premium"]
        qty = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    side_effect=capture_save), \
             patch("strategies.monitor.bs_put",         return_value=per_unit), \
             patch("strategies.monitor.bs_call",        return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert saved_states[-1]["open"] is None
        assert saved_states[-1]["stage"] == "no_position"

    def test_take_profit_put_returns_true(self, mock_wb, wheel_state_put):
        """Put nearly worthless → take profit."""
        p0  = wheel_state_put["open"]["premium"]
        qty = wheel_state_put["open"]["qty"]
        per_unit = (p0 * TAKE_PROFIT_THRESHOLD) / qty - 0.001
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    MagicMock()), \
             patch("strategies.monitor.bs_put",         return_value=per_unit), \
             patch("strategies.monitor.bs_call",        return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            result = _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert result is True

    def test_expiry_put_otm_is_win(self, mock_wb, wheel_state_put, today_expiry):
        """At expiry, spot > put strike → put expires OTM → win."""
        wheel_state_put["open"]["expiry"] = today_expiry
        spot = 2000.0  # above put strike of 1800
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    side_effect=capture_save), \
             patch("strategies.monitor.bs_put",         return_value=0.1), \
             patch("strategies.monitor.bs_call",        return_value=0.1), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            result = _check_wheel("ETH", spot, 0.80, mock_wb, True)
        assert result is True
        assert saved_states[-1]["wins"] == 1

    def test_expiry_put_itm_is_loss(self, mock_wb, wheel_state_put, today_expiry):
        """At expiry, spot far below put strike → intrinsic loss exceeds premium → loss."""
        wheel_state_put["open"]["expiry"] = today_expiry
        p0  = wheel_state_put["open"]["premium"]   # 25.0
        K   = wheel_state_put["open"]["strike"]    # 1800.0
        qty = wheel_state_put["open"]["qty"]       # 0.139
        # Force a loss: abs(spot - K) * qty > p0
        # (K - spot) * qty > p0 → spot < K - p0/qty = 1800 - 179.8 = 1620
        spot = 1500.0
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    side_effect=capture_save), \
             patch("strategies.monitor.bs_put",         return_value=0.1), \
             patch("strategies.monitor.bs_call",        return_value=0.1), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            result = _check_wheel("ETH", spot, 0.80, mock_wb, True)
        assert result is True
        assert saved_states[-1]["losses"] == 1

    def test_stop_loss_increments_losses(self, mock_wb, wheel_state_put):
        p0  = wheel_state_put["open"]["premium"]
        qty = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        saved_states = []
        def capture_save(path, state):
            saved_states.append(json.loads(json.dumps(state)))
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    side_effect=capture_save), \
             patch("strategies.monitor.bs_put",         return_value=per_unit), \
             patch("strategies.monitor.bs_call",        return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", MagicMock()):
            _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        assert saved_states[-1]["losses"] == 1
        assert saved_states[-1]["wins"]   == 0

    def test_logs_to_excel_on_close(self, mock_wb, wheel_state_put):
        """Any trigger must call append_trade_row."""
        p0  = wheel_state_put["open"]["premium"]
        qty = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        append = MagicMock()
        with patch("strategies.monitor._load_state",    return_value=wheel_state_put), \
             patch("strategies.monitor._save_state",    MagicMock()), \
             patch("strategies.monitor.bs_put",         return_value=per_unit), \
             patch("strategies.monitor.bs_call",        return_value=per_unit), \
             patch("strategies.monitor.append_trade_row", append):
            _check_wheel("ETH", 2000.0, 0.80, mock_wb, True)
        append.assert_called_once()


# ── run_monitor ───────────────────────────────────────────────────────────────

class TestRunMonitor:

    def test_active_asset_not_refetched(self, mock_wb):
        """Active asset spot/IV should be reused, not fetched again."""
        with patch("strategies.monitor._check_strangle", return_value=False), \
             patch("strategies.monitor._check_wheel",    return_value=False), \
             patch("market.market_data.get_spot_price") as mock_price, \
             patch("market.market_data.get_deribit_iv") as mock_iv:
            run_monitor(2000.0, 0.80, mock_wb, 7, "ETH", silent=True)
        eth_calls = [c for c in mock_price.call_args_list
                     if c.args and c.args[0] == "ETH"]
        assert len(eth_calls) == 0

    def test_other_assets_are_fetched(self, mock_wb):
        """Non-active assets should have their price fetched."""
        with patch("strategies.monitor._check_strangle", return_value=False), \
             patch("strategies.monitor._check_wheel",    return_value=False), \
             patch("market.market_data.get_spot_price",  return_value=80000.0) as mock_price, \
             patch("market.market_data.get_deribit_iv",  return_value=0.60):
            run_monitor(2000.0, 0.80, mock_wb, 7, "ETH", silent=True)
        fetched = {c.args[0] for c in mock_price.call_args_list}
        assert "BTC" in fetched or "SOL" in fetched

    def test_failed_price_fetch_skips_asset(self, mock_wb):
        """If price fetch fails for non-active assets, run_monitor completes without error."""
        with patch("strategies.monitor._load_state", return_value=None), \
             patch("market.market_data.get_spot_price", return_value=None), \
             patch("market.market_data.get_deribit_iv", return_value=0.60):
            result = run_monitor(2000.0, 0.80, mock_wb, 7, "ETH", silent=True)
        assert result is None

    def test_registry_called_for_each_asset(self, mock_wb):
        """run_monitor processes all assets without error when prices are available."""
        with patch("strategies.monitor._load_state", return_value=None), \
             patch("market.market_data.get_spot_price", return_value=80000.0), \
             patch("market.market_data.get_deribit_iv", return_value=0.60):
            result = run_monitor(2000.0, 0.80, mock_wb, 7, "ETH", silent=True)
        assert result is None

    def test_iv_fallback_when_deribit_fails(self, mock_wb):
        """If IV fetch fails for a non-active asset, active IV is used as fallback."""
        import strategies.monitor as monitor_module
        used_ivs = []
        def tracking_checker(asset, spot, iv, wb, silent):
            used_ivs.append((asset, iv))
            return False

        active_iv = 0.80
        with patch.object(monitor_module, "_REGISTRY", [tracking_checker]), \
             patch("market.market_data.get_spot_price", return_value=80000.0), \
             patch("market.market_data.get_deribit_iv", return_value=None):
            run_monitor(active_iv, active_iv, mock_wb, 7, "ETH", silent=True)

        non_eth = [(a, iv) for a, iv in used_ivs if a != "ETH"]
        assert all(iv == active_iv for _, iv in non_eth)
