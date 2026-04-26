"""
tests/test_strangle.py
======================
Tests for strategies/strangle.py — P&L helpers, breakeven calculation,
stop-loss checker, and state file helpers.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _state_file     : filename format, asset uppercasing
    _pnl            : spot inside strikes, below put, above call,
                      exactly at strikes, zero premium, zero qty
    _breakevens     : basic calculation, symmetric OTM, zero premium
    check_stop_loss : ok / warn / stop status thresholds, return types,
                      multiplier calculation, zero premium guard

Tier 2 — mocked I/O:
    _load           : existing file, missing file returns defaults
    _save           : writes correct JSON content

Note: strangle_paper_menu and show_strangle_analysis are not tested here
because they depend on builtins.input() for interactive prompts.
"""

import json
import os
from unittest.mock import patch, mock_open, MagicMock

import pytest

from strategies.strangle import (
    _state_file,
    _pnl,
    _breakevens,
    _load,
    _save,
    check_stop_loss,
)
from config import STOP_LOSS_MULTIPLIER, STOP_WARN_MULTIPLIER


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
    }


@pytest.fixture
def default_state():
    """The default state returned when no file exists."""
    return {
        "open":          None,
        "total_premium": 0.0,
        "wins":          0,
        "losses":        0,
        "trades":        0,
    }


# ── _state_file ───────────────────────────────────────────────────────────────

class TestStateFile:

    def test_eth_filename(self):
        assert _state_file("ETH") == "strangle_state_ETH.json"

    def test_btc_filename(self):
        assert _state_file("BTC") == "strangle_state_BTC.json"

    def test_sol_filename(self):
        assert _state_file("SOL") == "strangle_state_SOL.json"

    def test_lowercase_uppercased(self):
        assert _state_file("eth") == "strangle_state_ETH.json"

    def test_format_consistent(self):
        """All state files follow the same pattern."""
        for asset in ("ETH", "BTC", "SOL"):
            name = _state_file(asset)
            assert name.startswith("strangle_state_")
            assert name.endswith(".json")
            assert asset in name


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


# ── _load ─────────────────────────────────────────────────────────────────────

class TestLoad:

    def test_missing_file_returns_defaults(self):
        """When state file doesn't exist, returns a fresh default state."""
        with patch("strategies.strangle.os.path.exists", return_value=False):
            result = _load("ETH")
        assert result["open"]          is None
        assert result["total_premium"] == 0.0
        assert result["wins"]          == 0
        assert result["losses"]        == 0
        assert result["trades"]        == 0

    def test_existing_file_returns_contents(self):
        """When state file exists, returns its parsed contents."""
        state = {"open": None, "total_premium": 100.0, "wins": 3, "losses": 1, "trades": 4}
        with patch("strategies.strangle.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=json.dumps(state))):
            result = _load("ETH")
        assert result["total_premium"] == 100.0
        assert result["wins"]          == 3

    def test_uses_correct_filename(self):
        """_load uses _state_file to determine the path."""
        with patch("strategies.strangle.os.path.exists", return_value=False) as mock_exists:
            _load("BTC")
        mock_exists.assert_called_once_with("strangle_state_BTC.json")


# ── _save ─────────────────────────────────────────────────────────────────────

class TestSave:

    def test_saves_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = {"open": None, "wins": 1, "losses": 0, "trades": 1, "total_premium": 50.0}
        _save("ETH", state)
        with open("strangle_state_ETH.json") as f:
            assert json.load(f) == state

    def test_saves_to_correct_filename(self):
        """_save writes to the asset-specific filename."""
        state = {"open": None, "wins": 0, "losses": 0, "trades": 0, "total_premium": 0.0}
        m     = mock_open()
        with patch("builtins.open", m):
            _save("SOL", state)
        m.assert_called_once_with("strangle_state_SOL.json", "w")

    def test_roundtrip(self):
        """State saved by _save can be loaded back by _load."""
        state = {"open": None, "wins": 2, "losses": 1, "trades": 3, "total_premium": 75.0}
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_dir = _os.getcwd()
            _os.chdir(tmpdir)
            try:
                _save("ETH", state)
                loaded = _load("ETH")
            finally:
                _os.chdir(orig_dir)
        assert loaded["wins"]          == 2
        assert loaded["total_premium"] == 75.0
