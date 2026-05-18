"""
tests/test_strangle.py
======================
Tests for strategies/strangle.py — P&L helpers, breakeven calculation,
stop-loss checker, database state helpers, and executor delegation.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _pnl            : spot inside strikes, below put, above call,
                      exactly at strikes, zero premium, zero qty
    _breakevens     : basic calculation, symmetric OTM, zero premium
    check_stop_loss : ok / warn / stop status thresholds, return types,
                      multiplier calculation, zero premium guard

Tier 2 — database state (uses in-memory SQLite via conftest autouse fixture):
    load_strangle_state : fresh default state, existing state returned
    save_strangle_state : persists all fields, roundtrip

Tier 3 — executor delegation:
    strangle_paper_menu [1] calls enter_trade with Strangle candidate
"""

from unittest.mock import MagicMock, patch

import pytest

from strategies.strangle import (
    _pnl,
    _breakevens,
    check_stop_loss,
)
from database.strangle_db import load_strangle_state, save_strangle_state, create_strangle_trade, close_strangle_trade
from config import STOP_LOSS_MULTIPLIER, STOP_WARN_MULTIPLIER
from datetime import date


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def open_position():
    """A standard open strangle position dict."""
    return {
        "put_strike":    1800.0,
        "call_strike":   2200.0,
        "total_premium": 50.0,
        "qty":           0.125,
        "expiry":        "01-May-2026",
        "spot_open":     2000.0,
        "days":          7,
        "asset":         "ETH",
        "trade_id":      1,
    }


@pytest.fixture
def default_state():
    """The default state returned when no row exists."""
    return {
        "open":          None,
        "total_premium": 0.0,
        "wins":          0,
        "losses":        0,
        "trades":        0,
    }


# ── _pnl ──────────────────────────────────────────────────────────────────────

class TestPnl:
    """
    Short strangle P&L at expiry:
      pnl = premium - put_loss - call_loss
      put_loss  = max(put_strike  - spot, 0) * qty
      call_loss = max(spot - call_strike, 0) * qty
    """

    def test_spot_inside_strikes_full_profit(self):
        """Spot between strikes → both legs expire worthless → keep full premium."""
        result = _pnl(2000.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0)

    def test_spot_at_put_strike_full_profit(self):
        """Spot exactly at put strike → put worthless (no intrinsic) → full premium."""
        result = _pnl(1800.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0)

    def test_spot_at_call_strike_full_profit(self):
        """Spot exactly at call strike → call worthless → full premium."""
        result = _pnl(2200.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0)

    def test_spot_below_put_strike_reduces_pnl(self):
        """Spot below put strike → put exercised → loss on put leg."""
        # put_loss = (1800 - 1700) * 0.125 = 12.5
        result = _pnl(1700.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0 - 12.5)

    def test_spot_above_call_strike_reduces_pnl(self):
        """Spot above call strike → call exercised → loss on call leg."""
        # call_loss = (2400 - 2200) * 0.125 = 25.0
        result = _pnl(2400.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0 - 25.0)

    def test_large_move_down_causes_loss(self):
        """Very large downside move → net loss after premium exhausted."""
        # put_loss = (1800 - 1000) * 0.125 = 100.0 > premium 50.0
        result = _pnl(1000.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0 - 100.0)
        assert result < 0

    def test_large_move_up_causes_loss(self):
        """Very large upside move → net loss after premium exhausted."""
        # call_loss = (3000 - 2200) * 0.125 = 100.0 > premium 50.0
        result = _pnl(3000.0, 1800.0, 2200.0, 50.0, 0.125)
        assert result == pytest.approx(50.0 - 100.0)
        assert result < 0

    def test_zero_premium_always_loss_or_zero(self):
        """With zero premium, any move ITM is a loss."""
        result_otm = _pnl(2000.0, 1800.0, 2200.0, 0.0, 0.125)
        result_itm = _pnl(1700.0, 1800.0, 2200.0, 0.0, 0.125)
        assert result_otm == pytest.approx(0.0)
        assert result_itm < 0

    def test_higher_qty_scales_loss(self):
        """Doubling qty doubles the loss."""
        pnl1 = _pnl(1700.0, 1800.0, 2200.0, 50.0, 0.125)
        pnl2 = _pnl(1700.0, 1800.0, 2200.0, 100.0, 0.250)
        assert pnl2 == pytest.approx(pnl1 * 2)

    def test_returns_float(self):
        assert isinstance(_pnl(2000.0, 1800.0, 2200.0, 50.0, 0.125), float)

    def test_only_one_leg_exercises_at_a_time(self):
        """Both put and call cannot be ITM simultaneously."""
        # If spot < put_strike, call is deep OTM (no call loss)
        result = _pnl(1500.0, 1800.0, 2200.0, 50.0, 0.125)
        call_loss = max(1500.0 - 2200.0, 0) * 0.125
        put_loss  = max(1800.0 - 1500.0, 0) * 0.125
        assert call_loss == 0.0
        assert result == pytest.approx(50.0 - put_loss)


# ── _breakevens ───────────────────────────────────────────────────────────────

class TestBreakevens:

    def test_basic_calculation(self):
        """be_lo = put_strike - prem_per_unit, be_hi = call_strike + prem_per_unit."""
        be_lo, be_hi = _breakevens(1800.0, 2200.0, 100.0)
        assert be_lo == pytest.approx(1700.0)
        assert be_hi == pytest.approx(2300.0)

    def test_returns_tuple_of_two(self):
        result = _breakevens(1800.0, 2200.0, 50.0)
        assert len(result) == 2

    def test_be_lo_less_than_put_strike(self):
        be_lo, _ = _breakevens(1800.0, 2200.0, 50.0)
        assert be_lo < 1800.0

    def test_be_hi_greater_than_call_strike(self):
        _, be_hi = _breakevens(1800.0, 2200.0, 50.0)
        assert be_hi > 2200.0

    def test_zero_premium_breakevens_equal_strikes(self):
        """With zero premium, breakevens = strikes."""
        be_lo, be_hi = _breakevens(1800.0, 2200.0, 0.0)
        assert be_lo == pytest.approx(1800.0)
        assert be_hi == pytest.approx(2200.0)

    def test_symmetric_otm_gives_symmetric_breakevens(self):
        """Symmetric strikes around spot → symmetric breakevens around spot."""
        spot = 2000.0
        Kp, Kc = spot * 0.90, spot * 1.10   # 1800, 2200
        ppu = 100.0
        be_lo, be_hi = _breakevens(Kp, Kc, ppu)
        midpoint = (be_lo + be_hi) / 2
        # Midpoint of breakevens should be midpoint of strikes
        assert midpoint == pytest.approx((Kp + Kc) / 2)

    def test_larger_premium_widens_profit_zone(self):
        """Higher premium per unit → wider profit zone (lower be_lo, higher be_hi)."""
        be_lo_small, be_hi_small = _breakevens(1800.0, 2200.0, 50.0)
        be_lo_large, be_hi_large = _breakevens(1800.0, 2200.0, 150.0)
        assert be_lo_large < be_lo_small
        assert be_hi_large > be_hi_small

    def test_profit_zone_width(self):
        """Width of profit zone = (call_strike - put_strike) + 2 * prem_per_unit."""
        Kp, Kc, ppu = 1800.0, 2200.0, 100.0
        be_lo, be_hi = _breakevens(Kp, Kc, ppu)
        expected_width = (Kc - Kp) + 2 * ppu
        assert (be_hi - be_lo) == pytest.approx(expected_width)


# ── check_stop_loss ───────────────────────────────────────────────────────────

class TestCheckStopLoss:

    def _make_op(self, p0=50.0, qty=0.125, Kp=1800.0, Kc=2200.0):
        """Build a minimal open position dict."""
        return {
            "total_premium": p0,
            "qty":           qty,
            "put_strike":    Kp,
            "call_strike":   Kc,
        }

    def test_returns_tuple_of_four(self):
        op = self._make_op()
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            result = check_stop_loss(2000.0, 0.80, 7, op)
        assert len(result) == 4

    def test_status_is_string(self):
        op = self._make_op()
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            status, _, _, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert isinstance(status, str)
        assert status in ("ok", "warn", "stop")

    def test_current_value_is_float(self):
        op = self._make_op()
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            _, cur_val, _, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert isinstance(cur_val, float)

    def test_multiplier_is_float(self):
        op = self._make_op()
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            _, _, mult, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert isinstance(mult, float)

    def test_message_is_string(self):
        op = self._make_op()
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            _, _, _, msg = check_stop_loss(2000.0, 0.80, 7, op)
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_ok_status_when_below_warn(self):
        """cur_val well below warn threshold → status 'ok'."""
        op = self._make_op(p0=50.0, qty=0.125)
        # cur_val = (put + call) * qty = 2 * 1.0 * 0.125 = 0.25
        # mult = 0.25 / 50.0 = 0.005 — well below STOP_WARN_MULTIPLIER
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            status, _, _, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert status == "ok"

    def test_warn_status_at_warn_threshold(self):
        """cur_val at warn threshold → status 'warn'."""
        op  = self._make_op(p0=50.0, qty=0.125)
        # mult = STOP_WARN_MULTIPLIER → cur_val = p0 * STOP_WARN_MULTIPLIER = 75.0
        # (put + call) * qty = 75.0 → per_unit = 75.0 / 0.125 / 2 = 300.0
        per_unit = (50.0 * STOP_WARN_MULTIPLIER) / 0.125 / 2
        with patch("strategies.strangle.bs_put",  return_value=per_unit), \
             patch("strategies.strangle.bs_call", return_value=per_unit):
            status, _, mult, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert status == "warn"
        assert mult == pytest.approx(STOP_WARN_MULTIPLIER, rel=1e-3)

    def test_stop_status_at_stop_threshold(self):
        """cur_val at stop threshold → status 'stop'."""
        op  = self._make_op(p0=50.0, qty=0.125)
        per_unit = (50.0 * STOP_LOSS_MULTIPLIER) / 0.125 / 2
        with patch("strategies.strangle.bs_put",  return_value=per_unit), \
             patch("strategies.strangle.bs_call", return_value=per_unit):
            status, _, mult, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert status == "stop"
        assert mult == pytest.approx(STOP_LOSS_MULTIPLIER, rel=1e-3)

    def test_stop_takes_priority_over_warn(self):
        """If above both thresholds, 'stop' is returned not 'warn'."""
        op  = self._make_op(p0=50.0, qty=0.125)
        per_unit = (50.0 * STOP_LOSS_MULTIPLIER) / 0.125 / 2 + 100
        with patch("strategies.strangle.bs_put",  return_value=per_unit), \
             patch("strategies.strangle.bs_call", return_value=per_unit):
            status, _, _, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert status == "stop"

    def test_multiplier_correct_calculation(self):
        """mult = cur_val / p0."""
        op = self._make_op(p0=50.0, qty=0.125)
        # cur_val = 2 * 1.0 * 0.125 = 0.25
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            _, cur_val, mult, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert cur_val == pytest.approx(0.25)
        assert mult    == pytest.approx(0.25 / 50.0)

    def test_zero_premium_guard(self):
        """With p0=0, mult should be 0.0 not ZeroDivisionError."""
        op = self._make_op(p0=0.0, qty=0.125)
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            _, _, mult, _ = check_stop_loss(2000.0, 0.80, 7, op)
        assert mult == 0.0

    def test_zero_days_uses_minimum_T(self):
        """days=0 should not cause division errors — uses floor of 1/365."""
        op = self._make_op()
        with patch("strategies.strangle.bs_put",  return_value=1.0), \
             patch("strategies.strangle.bs_call", return_value=1.0):
            result = check_stop_loss(2000.0, 0.80, 0, op)
        assert len(result) == 4   # completes without exception


# ── Database state helpers ────────────────────────────────────────────────────

class TestLoadStrangleState:
    """Tests for load_strangle_state — uses in-memory SQLite via conftest."""

    def test_missing_asset_returns_defaults(self):
        """First call for an asset returns a fresh default state."""
        result = load_strangle_state("ETH")
        assert result["open"]          is None
        assert result["total_premium"] == 0.0
        assert result["wins"]          == 0
        assert result["losses"]        == 0
        assert result["trades"]        == 0

    def test_returns_all_required_keys(self):
        result = load_strangle_state("BTC")
        for key in ("open", "total_premium", "wins", "losses", "trades"):
            assert key in result

    def test_different_assets_are_independent(self):
        """State for ETH and BTC are stored separately."""
        eth = load_strangle_state("ETH")
        btc = load_strangle_state("BTC")
        assert eth["total_premium"] == btc["total_premium"]  # both fresh

    def test_second_call_returns_persisted_data(self):
        """Calling load twice without saving still returns the same defaults."""
        first  = load_strangle_state("SOL")
        second = load_strangle_state("SOL")
        assert first["wins"] == second["wins"]


class TestSaveStrangleState:
    """Tests for save_strangle_state — uses in-memory SQLite via conftest."""

    def test_saves_and_loads_roundtrip(self):
        """State saved via save_strangle_state can be retrieved via load_strangle_state."""
        state = {
            "open":          None,
            "total_premium": 150.0,
            "wins":          3,
            "losses":        1,
            "trades":        4,
        }
        save_strangle_state("ETH", state)
        loaded = load_strangle_state("ETH")
        assert loaded["total_premium"] == pytest.approx(150.0)
        assert loaded["wins"]          == 3
        assert loaded["losses"]        == 1
        assert loaded["trades"]        == 4

    def test_saves_open_position(self):
        """Open position dict is persisted and retrieved correctly."""
        op = {
            "put_strike":    1800.0,
            "call_strike":   2200.0,
            "total_premium": 50.0,
            "qty":           0.125,
            "trade_id":      42,
        }
        state = {"open": op, "total_premium": 50.0, "wins": 0, "losses": 0, "trades": 1}
        save_strangle_state("ETH", state)
        loaded = load_strangle_state("ETH")
        assert loaded["open"] is not None
        assert loaded["open"]["put_strike"]  == pytest.approx(1800.0)
        assert loaded["open"]["trade_id"]    == 42

    def test_overwrites_existing_state(self):
        """Saving twice with different data replaces the previous values."""
        save_strangle_state("ETH", {
            "open": None, "total_premium": 50.0, "wins": 1, "losses": 0, "trades": 1,
        })
        save_strangle_state("ETH", {
            "open": None, "total_premium": 120.0, "wins": 2, "losses": 1, "trades": 3,
        })
        loaded = load_strangle_state("ETH")
        assert loaded["total_premium"] == pytest.approx(120.0)
        assert loaded["wins"]          == 2
        assert loaded["trades"]        == 3

    def test_clear_open_position(self):
        """Setting open=None after a position clears it in the database."""
        save_strangle_state("ETH", {
            "open": {"put_strike": 1800.0, "trade_id": 1},
            "total_premium": 50.0, "wins": 0, "losses": 0, "trades": 1,
        })
        save_strangle_state("ETH", {
            "open": None, "total_premium": 50.0, "wins": 1, "losses": 0, "trades": 1,
        })
        loaded = load_strangle_state("ETH")
        assert loaded["open"] is None


# ── Executor delegation ───────────────────────────────────────────────────────

class TestStranglePaperMenuExecutor:
    """Verify strangle_paper_menu delegates to enter_trade when opening a position."""

    @patch("strategies.strangle.enter_trade")
    @patch("builtins.input", side_effect=["1", "", ""])  # choice=1, default Kp, default Kc
    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    def test_open_calls_enter_trade(self, _out, _inp, mock_enter):
        """Choosing [1] (Open new strangle) should call enter_trade with a Strangle candidate."""
        from strategies.strangle import strangle_paper_menu
        save_strangle_state("ETH", {
            "open": None, "total_premium": 0.0,
            "wins": 0, "losses": 0, "trades": 0,
        })
        mock_enter.return_value = {
            "put_strike": 1700.0, "call_strike": 2300.0,
            "total_premium": 50.0, "qty": 0.05,
            "expiry": "01-Jun-2026", "spot_open": 2000.0,
            "days": 7, "asset": "ETH", "trade_id": 1,
        }

        strangle_paper_menu("ETH", spot=2000.0, iv=0.80, days=7)

        mock_enter.assert_called_once()
        c = mock_enter.call_args[0][0]
        assert c.strategy == "Strangle"
        assert c.asset == "ETH"
        assert c.spot == 2000.0
        assert c.iv == 0.80
        assert c.days == 7
        assert hasattr(c, "put_strike")
        assert hasattr(c, "call_strike")
