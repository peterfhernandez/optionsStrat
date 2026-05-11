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

Tier 3 — broker forwarding:
    broker place_order called on auto-close for wheel, strangle, calendar
    run_monitor forwards broker kwarg to checkers

Approach
--------
_load_state and _save_state are mocked to avoid filesystem I/O.
bs_put and bs_call are mocked to control trigger conditions precisely.
DeribitClient is patched globally so no network calls are made.
"""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from access import OrderResult
from automation.monitor import (
    _days_remaining,
    _check_strangle,
    _check_wheel,
    _check_calendar,
    run_monitor,
    TAKE_PROFIT_THRESHOLD)
from config import STOP_LOSS_MULTIPLIER


# ── Shared broker helpers ─────────────────────────────────────────────────────

def _fake_order(order_id: str = "TEST-ORD-1") -> OrderResult:
    return OrderResult(
        order_id=order_id, instrument="ETH-TEST", direction="buy",
        amount=10000.0, price=None, state="open",
        filled_amount=0.0, avg_price=None, label=None,
    )


def _make_broker(order_id: str = "TEST-ORD-1") -> MagicMock:
    """Return a mock BrokerBase whose place_order always succeeds."""
    m = MagicMock()
    type(m).broker_name = PropertyMock(return_value="deribit_paper")
    m.place_order.return_value = _fake_order(order_id)
    return m


@pytest.fixture(autouse=True)
def _mock_deribit(monkeypatch):
    """Prevent any test from hitting the real Deribit network.

    run_monitor() defaults to DeribitClient(paper=True) when no broker is
    passed. This fixture replaces that constructor with a controlled mock.
    """
    mock_instance = _make_broker()
    with patch("automation.monitor.DeribitClient", return_value=mock_instance), \
         patch("trading.executor.DeribitClient",   return_value=mock_instance):
        yield mock_instance


# ── Shared fixtures ───────────────────────────────────────────────────────────

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
               close_mock=None, save_mock=None):
        """Build a context manager stack for common patches."""
        return [
            patch("automation.monitor.load_strangle_state",  return_value=state),
            patch("automation.monitor.save_strangle_state",  save_mock or MagicMock()),
            patch("automation.monitor.bs_put",               return_value=bs_put_val),
            patch("automation.monitor.bs_call",              return_value=bs_call_val),
            patch("automation.monitor.close_strangle_trade", close_mock or MagicMock()),
        ]

    def test_no_open_position_returns_false(self):
        """Fresh/empty state (open=None) → nothing to monitor → False."""
        state = {"open": None, "wins": 0, "losses": 0}
        with patch("automation.monitor.load_strangle_state", return_value=state):
            result = _check_strangle("ETH", 2000.0, 0.80, True)
        assert result is False

    def test_no_trigger_returns_false(self, strangle_state):
        """Normal position — no trigger, no close."""
        p0  = strangle_state["open"]["total_premium"]   # 50.0
        qty = strangle_state["open"]["qty"]             # 0.125
        # cur_val = (bs_put + bs_call) * qty
        # Must be: > p0 * TAKE_PROFIT_THRESHOLD (5.0) and < p0 * STOP_LOSS (100.0)
        # Use 50% of premium as current value — safely in the middle
        # (bs_put + bs_call) * qty = p0 * 0.50 → per_unit = p0 * 0.50 / qty / 2
        per_unit = (p0 * 0.50) / qty / 2
        with patch("automation.monitor.load_strangle_state",  return_value=strangle_state), \
             patch("automation.monitor.save_strangle_state",  MagicMock()), \
             patch("automation.monitor.bs_put",               return_value=per_unit), \
             patch("automation.monitor.bs_call",              return_value=per_unit), \
             patch("automation.monitor.close_strangle_trade", MagicMock()):
            result = _check_strangle("ETH", 2000.0, 0.80, True)
        assert result is False

    def test_stop_loss_trigger_returns_true(self, strangle_state):
        """Position value exceeds STOP_LOSS_MULTIPLIER × premium."""
        p0      = strangle_state["open"]["total_premium"]   # 50.0
        qty     = strangle_state["open"]["qty"]             # 0.125
        # cur_val needs to be ≥ p0 * STOP_LOSS_MULTIPLIER = 100.0
        # bs_put + bs_call returns are per unit, multiplied by qty
        # so we need per_unit × qty × 2 ≥ 100 → per_unit ≥ 400
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = _check_strangle("ETH", 2000.0, 0.80, True)
        assert result is True

    def test_stop_loss_logs_to_database(self, strangle_state):
        """Stop-loss trigger must call close_strangle_trade when trade_id is set."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        strangle_state["open"]["trade_id"] = 42
        close   = MagicMock()
        patches = self._patch(strangle_state, per_unit, per_unit, close)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, True)
        close.assert_called_once()

    def test_stop_loss_clears_open_position(self, strangle_state):
        """After stop-loss, state["open"] must be set to None."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        saved_states = []
        def capture_save(asset, state):
            saved_states.append({"open": state.get("open"), "wins": state.get("wins"),
                                  "losses": state.get("losses")})
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], \
             patch("automation.monitor.save_strangle_state", side_effect=capture_save), \
             patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, True)
        assert len(saved_states) > 0
        assert saved_states[-1]["open"] is None

    def test_stop_loss_increments_losses(self, strangle_state):
        """Stop-loss (loss) should increment state['losses']."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        saved_states = []
        def capture_save(asset, state):
            saved_states.append({"wins": state.get("wins"), "losses": state.get("losses")})
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], \
             patch("automation.monitor.save_strangle_state", side_effect=capture_save), \
             patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, True)
        assert saved_states[-1]["losses"] == 1
        assert saved_states[-1]["wins"]   == 0

    def test_take_profit_trigger_returns_true(self, strangle_state):
        """Position value ≤ TAKE_PROFIT_THRESHOLD × premium → take profit."""
        p0  = strangle_state["open"]["total_premium"]   # 50.0
        qty = strangle_state["open"]["qty"]             # 0.125
        # cur_val needs to be ≤ p0 * TAKE_PROFIT_THRESHOLD = 5.0
        # bs_put + bs_call per_unit × qty × 2 ≤ 5.0 → per_unit ≤ 20
        per_unit = (p0 * TAKE_PROFIT_THRESHOLD) / qty / 2 - 0.01
        patches  = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = _check_strangle("ETH", 2000.0, 0.80, True)
        assert result is True

    def test_take_profit_increments_wins(self, strangle_state):
        """Take-profit (win) should increment state['wins']."""
        p0  = strangle_state["open"]["total_premium"]
        qty = strangle_state["open"]["qty"]
        per_unit = (p0 * TAKE_PROFIT_THRESHOLD) / qty / 2 - 0.01
        saved_states = []
        def capture_save(asset, state):
            saved_states.append({"wins": state.get("wins"), "losses": state.get("losses")})
        patches = self._patch(strangle_state, per_unit, per_unit)
        with patches[0], \
             patch("automation.monitor.save_strangle_state", side_effect=capture_save), \
             patches[2], patches[3], patches[4]:
            _check_strangle("ETH", 2000.0, 0.80, True)
        assert saved_states[-1]["wins"]   == 1
        assert saved_states[-1]["losses"] == 0

    def test_expiry_trigger_spot_inside_strikes_is_win(
            self, strangle_state, today_expiry):
        """At expiry, spot between strikes → both legs worthless → win."""
        strangle_state["open"]["expiry"] = today_expiry
        spot = 2000.0  # between put=1800 and call=2200
        saved_states = []
        def capture_save(asset, state):
            saved_states.append({"wins": state.get("wins"), "losses": state.get("losses"),
                                  "open": state.get("open")})
        with patch("automation.monitor.load_strangle_state",  return_value=strangle_state), \
             patch("automation.monitor.save_strangle_state",  side_effect=capture_save), \
             patch("automation.monitor.bs_put",               return_value=0.1), \
             patch("automation.monitor.bs_call",              return_value=0.1), \
             patch("automation.monitor.close_strangle_trade", MagicMock()):
            result = _check_strangle("ETH", spot, 0.80, True)
        assert result is True
        assert saved_states[-1]["wins"] == 1

    def test_expiry_trigger_spot_below_put_strike_is_loss(
            self, strangle_state, today_expiry):
        """At expiry, spot far below put strike → intrinsic loss exceeds premium → loss."""
        strangle_state["open"]["expiry"] = today_expiry
        spot = 1300.0  # intrinsic = (1800 - 1300) * 0.125 = 62.5 > premium 50.0
        saved_states = []
        def capture_save(asset, state):
            saved_states.append({"wins": state.get("wins"), "losses": state.get("losses"),
                                  "open": state.get("open")})
        with patch("automation.monitor.load_strangle_state",  return_value=strangle_state), \
             patch("automation.monitor.save_strangle_state",  side_effect=capture_save), \
             patch("automation.monitor.bs_put",               return_value=0.1), \
             patch("automation.monitor.bs_call",              return_value=0.1), \
             patch("automation.monitor.close_strangle_trade", MagicMock()):
            result = _check_strangle("ETH", spot, 0.80, True)
        assert result is True
        assert saved_states[-1]["losses"] == 1


# ── _check_wheel ──────────────────────────────────────────────────────────────

class TestCheckWheel:

    def test_no_state_in_db_returns_false(self):
        """No state seeded → fresh defaults have open=None → False."""
        result = _check_wheel("ETH", 2000.0, 0.80, True)
        assert result is False

    def test_no_open_position_returns_false(self):
        from database.wheel_db import save_wheel_state
        save_wheel_state("ETH", {
            "stage": "no_position", "open": None, "wins": 0, "losses": 0,
            "cycles": 0, "total_premium": 0.0, "asset_held": 0.0, "cost_basis": 0.0,
        })
        result = _check_wheel("ETH", 2000.0, 0.80, True)
        assert result is False

    def test_no_trigger_put_returns_false(self, wheel_state_put):
        """Normal put position — no trigger."""
        from database.wheel_db import save_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * 0.50) / qty  # safely between TP and SL thresholds
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            result = _check_wheel("ETH", 2000.0, 0.80, True)
        assert result is False

    def test_stop_loss_put_returns_true(self, wheel_state_put):
        """Put position value exceeds stop-loss threshold."""
        from database.wheel_db import save_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            result = _check_wheel("ETH", 2000.0, 0.80, True)
        assert result is True

    def test_stop_loss_call_returns_true(self, wheel_state_call):
        """Call position value exceeds stop-loss threshold."""
        from database.wheel_db import save_wheel_state
        save_wheel_state("ETH", wheel_state_call)
        p0       = wheel_state_call["open"]["premium"]
        qty      = wheel_state_call["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            result = _check_wheel("ETH", 2000.0, 0.80, True)
        assert result is True

    def test_stop_loss_clears_open_and_resets_stage(self, wheel_state_put):
        """After stop-loss, open=None and stage='no_position'."""
        from database.wheel_db import save_wheel_state, load_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            _check_wheel("ETH", 2000.0, 0.80, True)
        updated = load_wheel_state("ETH")
        assert updated["open"]  is None
        assert updated["stage"] == "no_position"

    def test_take_profit_put_returns_true(self, wheel_state_put):
        """Put nearly worthless → take profit."""
        from database.wheel_db import save_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * TAKE_PROFIT_THRESHOLD) / qty - 0.001
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            result = _check_wheel("ETH", 2000.0, 0.80, True)
        assert result is True

    def test_expiry_put_otm_is_win(self, wheel_state_put, today_expiry):
        """At expiry, spot > put strike → put expires OTM → win."""
        from database.wheel_db import save_wheel_state, load_wheel_state
        wheel_state_put["open"]["expiry"] = today_expiry
        save_wheel_state("ETH", wheel_state_put)
        spot = 2000.0  # above put strike of 1800
        with patch("automation.monitor.bs_put",  return_value=0.1), \
             patch("automation.monitor.bs_call", return_value=0.1):
            result = _check_wheel("ETH", spot, 0.80, True)
        assert result is True
        updated = load_wheel_state("ETH")
        assert updated["wins"] == 1

    def test_expiry_put_itm_is_loss(self, wheel_state_put, today_expiry):
        """At expiry, spot far below put strike → intrinsic loss exceeds premium → loss."""
        from database.wheel_db import save_wheel_state, load_wheel_state
        wheel_state_put["open"]["expiry"] = today_expiry
        save_wheel_state("ETH", wheel_state_put)
        # (K - spot) * qty > p0 → spot < 1800 - 25/0.139 ≈ 1620
        spot = 1500.0
        with patch("automation.monitor.bs_put",  return_value=0.1), \
             patch("automation.monitor.bs_call", return_value=0.1):
            result = _check_wheel("ETH", spot, 0.80, True)
        assert result is True
        updated = load_wheel_state("ETH")
        assert updated["losses"] == 1

    def test_stop_loss_increments_losses(self, wheel_state_put):
        from database.wheel_db import save_wheel_state, load_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            _check_wheel("ETH", 2000.0, 0.80, True)
        updated = load_wheel_state("ETH")
        assert updated["losses"] == 1
        assert updated["wins"]   == 0

    def test_stop_loss_updates_database(self, wheel_state_put):
        """Stop-loss trigger must persist state to database (open cleared)."""
        from database.wheel_db import save_wheel_state, load_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            _check_wheel("ETH", 2000.0, 0.80, True)
        updated = load_wheel_state("ETH")
        assert updated["open"] is None


# ── run_monitor ───────────────────────────────────────────────────────────────

class TestRunMonitor:

    def test_active_asset_not_refetched(self):
        """Active asset spot/IV should be reused, not fetched again."""
        import automation.monitor as monitor_module

        with patch.object(monitor_module, "_REGISTRY", [
                MagicMock(return_value=False),
                MagicMock(return_value=False),
                MagicMock(return_value=False),
            ]), \
             patch("market.market_data.get_spot_price", return_value=None) as mock_price, \
             patch("market.market_data.get_deribit_iv", return_value=0.60) as mock_iv:
            run_monitor(2000.0, 0.80, 7, "ETH", silent=True)
        eth_calls = [c for c in mock_price.call_args_list
                     if c.args and c.args[0] == "ETH"]
        assert len(eth_calls) == 0
        eth_iv_calls = [c for c in mock_iv.call_args_list
                        if c.args and c.args[0] == "ETH"]
        assert len(eth_iv_calls) == 0

    def test_other_assets_are_fetched(self):
        """Non-active assets should have their price fetched."""
        import automation.monitor as monitor_module
        with patch.object(monitor_module, "_REGISTRY", [MagicMock(return_value=False)]), \
             patch("market.market_data.get_spot_price",  return_value=80000.0) as mock_price, \
             patch("market.market_data.get_deribit_iv",  return_value=0.60):
            run_monitor(2000.0, 0.80, 7, "ETH", silent=True)
        fetched = {c.args[0] for c in mock_price.call_args_list}
        assert "BTC" in fetched or "SOL" in fetched

    def test_failed_price_fetch_skips_asset(self):
        """If price fetch fails for non-active assets, run_monitor completes without error."""
        with patch("market.market_data.get_spot_price", return_value=None), \
             patch("market.market_data.get_deribit_iv", return_value=0.60):
            result = run_monitor(2000.0, 0.80, 7, "ETH", silent=True)
        assert result is None

    def test_registry_called_for_each_asset(self):
        """run_monitor processes all assets without error when prices are available."""
        with patch("market.market_data.get_spot_price", return_value=80000.0), \
             patch("market.market_data.get_deribit_iv", return_value=0.60):
            result = run_monitor(2000.0, 0.80, 7, "ETH", silent=True)
        assert result is None

    def test_iv_fallback_when_deribit_fails(self):
        """If IV fetch fails for a non-active asset, active IV is used as fallback."""
        import automation.monitor as monitor_module
        used_ivs = []
        def tracking_checker(asset, spot, iv, silent, broker=None):
            used_ivs.append((asset, iv))
            return False

        active_iv = 0.80
        with patch.object(monitor_module, "_REGISTRY", [tracking_checker]), \
             patch("market.market_data.get_spot_price", return_value=80000.0), \
             patch("market.market_data.get_deribit_iv", return_value=None):
            run_monitor(active_iv, active_iv, 7, "ETH", silent=True)

        non_eth = [(a, iv) for a, iv in used_ivs if a != "ETH"]
        assert all(iv == active_iv for _, iv in non_eth)


# ── Broker forwarding ─────────────────────────────────────────────────────────

class TestBrokerForwarding:
    """Verify that auto-close events place orders through the broker."""

    def test_strangle_stop_loss_calls_broker(self, strangle_state):
        """Stop-loss on a strangle must invoke broker.place_order twice (put + call)."""
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        broker = _make_broker()

        with patch("automation.monitor.load_strangle_state",  return_value=strangle_state), \
             patch("automation.monitor.save_strangle_state",  MagicMock()), \
             patch("automation.monitor.bs_put",               return_value=per_unit), \
             patch("automation.monitor.bs_call",              return_value=per_unit), \
             patch("automation.monitor.close_strangle_trade", MagicMock()):
            _check_strangle("ETH", 2000.0, 0.80, True, broker=broker)

        assert broker.place_order.call_count == 2

    def test_wheel_stop_loss_calls_broker(self, wheel_state_put):
        """Stop-loss on a wheel put must invoke broker.place_order once."""
        from database.wheel_db import save_wheel_state
        save_wheel_state("ETH", wheel_state_put)
        p0       = wheel_state_put["open"]["premium"]
        qty      = wheel_state_put["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty + 1
        broker   = _make_broker()

        with patch("automation.monitor.bs_put",  return_value=per_unit), \
             patch("automation.monitor.bs_call", return_value=per_unit):
            _check_wheel("ETH", 2000.0, 0.80, True, broker=broker)

        broker.place_order.assert_called_once()

    def test_calendar_stop_loss_calls_broker(self, today_expiry):
        """Stop-loss on a calendar must invoke broker.place_order twice (near + far)."""
        from database.calendar_db import save_calendar_state
        far_expiry = (date.today() + timedelta(days=30)).strftime("%d-%b-%Y")
        state = {
            "open": {
                "strike":      2000.0,
                "option_type": "Call",
                "net_debit":   10.0,
                "qty":         0.125,
                "near_days":   7,
                "far_days":    30,
                "expiry_near": today_expiry,  # triggers EXPIRY (days_left == 0)
                "expiry_far":  far_expiry,
                "spot_open":   2000.0,
                "asset":       "ETH",
            },
            "wins": 0, "losses": 0, "trades": 1, "total_pnl": 0.0,
        }
        save_calendar_state("ETH", state)
        broker = _make_broker()

        with patch("automation.monitor.bs_call", return_value=5.0), \
             patch("automation.monitor.bs_put",  return_value=5.0):
            _check_calendar("ETH", 2000.0, 0.80, True, broker=broker)

        assert broker.place_order.call_count == 2

    def test_run_monitor_forwards_broker(self):
        """run_monitor passes the broker kwarg through to each checker."""
        import automation.monitor as monitor_module
        broker = _make_broker()
        received_brokers = []

        def tracking_checker(asset, spot, iv, silent, broker=None):
            received_brokers.append(broker)
            return False

        with patch.object(monitor_module, "_REGISTRY", [tracking_checker]), \
             patch("market.market_data.get_spot_price", return_value=80000.0), \
             patch("market.market_data.get_deribit_iv", return_value=0.60):
            run_monitor(2000.0, 0.80, 7, "ETH", silent=True, broker=broker)

        assert all(b is broker for b in received_brokers)

    def test_run_monitor_default_broker_is_deribit(self):
        """When no broker is passed, run_monitor creates a DeribitClient."""
        import automation.monitor as monitor_module
        with patch.object(monitor_module, "_REGISTRY", [MagicMock(return_value=False)]), \
             patch("market.market_data.get_spot_price", return_value=80000.0), \
             patch("market.market_data.get_deribit_iv", return_value=0.60), \
             patch("automation.monitor.DeribitClient") as mock_cls:
            mock_cls.return_value = _make_broker()
            run_monitor(2000.0, 0.80, 7, "ETH", silent=True)
        mock_cls.assert_called_once()

    def test_broker_http_error_does_not_record_close(self, strangle_state):
        """A 502/503 from the broker must not save the close to the DB or state."""
        import requests as req
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        broker = _make_broker()
        broker.place_order.side_effect = req.exceptions.HTTPError("502 Bad Gateway")

        mock_save  = MagicMock()
        mock_close = MagicMock()
        with patch("automation.monitor.load_strangle_state",  return_value=strangle_state), \
             patch("automation.monitor.save_strangle_state",  mock_save), \
             patch("automation.monitor.bs_put",               return_value=per_unit), \
             patch("automation.monitor.bs_call",              return_value=per_unit), \
             patch("automation.monitor.close_strangle_trade", mock_close):
            result = _check_strangle("ETH", 2000.0, 0.80, True, broker=broker)

        assert result is False
        mock_close.assert_not_called()
        mock_save.assert_not_called()

    def test_broker_connection_error_does_not_record_close(self, strangle_state):
        """A network connection error must not save the close to the DB or state."""
        import requests as req
        p0      = strangle_state["open"]["total_premium"]
        qty     = strangle_state["open"]["qty"]
        per_unit = (p0 * STOP_LOSS_MULTIPLIER) / qty / 2 + 1
        broker = _make_broker()
        broker.place_order.side_effect = req.exceptions.ConnectionError("unreachable")

        mock_save  = MagicMock()
        mock_close = MagicMock()
        with patch("automation.monitor.load_strangle_state",  return_value=strangle_state), \
             patch("automation.monitor.save_strangle_state",  mock_save), \
             patch("automation.monitor.bs_put",               return_value=per_unit), \
             patch("automation.monitor.bs_call",              return_value=per_unit), \
             patch("automation.monitor.close_strangle_trade", mock_close):
            result = _check_strangle("ETH", 2000.0, 0.80, True, broker=broker)

        assert result is False
        mock_close.assert_not_called()
        mock_save.assert_not_called()
