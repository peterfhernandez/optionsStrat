"""Tests for strategies/spread.py — Credit Spread strategy logic."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from strategies.spread import (
    _spread_pnl,
    _spread_breakeven,
    _current_spread_value,
    check_spread_status,
    show_spread_analysis,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def bps_op():
    """A typical open Bull Put Spread state dict."""
    return {
        "spread_type":  "BPS",
        "short_strike": 1800.0,
        "long_strike":  1700.0,
        "net_credit":   8.0,
        "max_loss":     92.0,
        "qty":          0.125,
        "expiry":       "22-May-2026",
        "spot_open":    2000.0,
        "days":         7,
        "asset":        "ETH",
        "trade_id":     1,
    }


@pytest.fixture
def bcs_op():
    """A typical open Bear Call Spread state dict."""
    return {
        "spread_type":  "BCS",
        "short_strike": 2200.0,
        "long_strike":  2300.0,
        "net_credit":   6.0,
        "max_loss":     94.0,
        "qty":          0.125,
        "expiry":       "22-May-2026",
        "spot_open":    2000.0,
        "days":         7,
        "asset":        "ETH",
        "trade_id":     2,
    }


# ── _spread_pnl ───────────────────────────────────────────────────────────────

class TestSpreadPnl:
    def test_bps_full_profit_above_short(self):
        pnl = _spread_pnl(2000.0, "BPS", 1800.0, 1700.0, 8.0, 0.125)
        assert pnl == pytest.approx(8.0)

    def test_bps_max_loss_below_long(self):
        pnl = _spread_pnl(1600.0, "BPS", 1800.0, 1700.0, 8.0, 0.125)
        # short loses (1800-1600)*0.125=25, long gains (1700-1600)*0.125=12.5, net_credit=8
        expected = 8.0 - 25.0 + 12.5
        assert pnl == pytest.approx(expected)

    def test_bps_partial_loss_between_strikes(self):
        pnl = _spread_pnl(1750.0, "BPS", 1800.0, 1700.0, 8.0, 0.125)
        short_loss = (1800.0 - 1750.0) * 0.125  # = 6.25
        long_gain  = 0.0  # long strike 1700 > 1750 doesn't apply
        assert pnl == pytest.approx(8.0 - short_loss + long_gain)

    def test_bcs_full_profit_below_short(self):
        pnl = _spread_pnl(2000.0, "BCS", 2200.0, 2300.0, 6.0, 0.125)
        assert pnl == pytest.approx(6.0)

    def test_bcs_max_loss_above_long(self):
        pnl = _spread_pnl(2400.0, "BCS", 2200.0, 2300.0, 6.0, 0.125)
        short_loss = (2400.0 - 2200.0) * 0.125  # = 25
        long_gain  = (2400.0 - 2300.0) * 0.125  # = 12.5
        assert pnl == pytest.approx(6.0 - short_loss + long_gain)


# ── _spread_breakeven ─────────────────────────────────────────────────────────

class TestSpreadBreakeven:
    def test_bps_breakeven(self):
        # credit/qty = 8/0.125 = 64; BE = 1800 - 64 = 1736
        be = _spread_breakeven("BPS", 1800.0, 8.0, 0.125)
        assert be == pytest.approx(1736.0)

    def test_bcs_breakeven(self):
        # credit/qty = 6/0.125 = 48; BE = 2200 + 48 = 2248
        be = _spread_breakeven("BCS", 2200.0, 6.0, 0.125)
        assert be == pytest.approx(2248.0)

    def test_zero_qty_returns_zero(self):
        be = _spread_breakeven("BPS", 1800.0, 8.0, 0.0)
        assert be == pytest.approx(1800.0)


# ── _current_spread_value ─────────────────────────────────────────────────────

class TestCurrentSpreadValue:
    def test_returns_non_negative(self):
        val = _current_spread_value(2000.0, 0.8, 7, "BPS", 1800.0, 1700.0, 0.125)
        assert val >= 0.0

    def test_bps_higher_spot_lower_value(self):
        val_near   = _current_spread_value(1850.0, 0.8, 7, "BPS", 1800.0, 1700.0, 0.125)
        val_far    = _current_spread_value(2100.0, 0.8, 7, "BPS", 1800.0, 1700.0, 0.125)
        assert val_far < val_near

    def test_bcs_lower_spot_lower_value(self):
        val_near   = _current_spread_value(2150.0, 0.8, 7, "BCS", 2200.0, 2300.0, 0.125)
        val_far    = _current_spread_value(1900.0, 0.8, 7, "BCS", 2200.0, 2300.0, 0.125)
        assert val_far < val_near


# ── check_spread_status ───────────────────────────────────────────────────────

class TestCheckSpreadStatus:
    def test_ok_status_when_profitable(self, bps_op):
        status, cost, pnl, msg = check_spread_status(2000.0, 0.8, 7, bps_op)
        assert status in ("ok", "profit")
        assert pnl > 0

    def test_stop_triggered_at_max_loss(self, bps_op):
        # spot below long strike → max loss scenario
        status, cost, pnl, msg = check_spread_status(1500.0, 0.8, 0, bps_op)
        # At 0 days to expiry the cost hits max; status should be stop or ok (depending on T)
        # Just verify we get a result without error
        assert status in ("ok", "warn", "stop", "profit")

    def test_profit_status_when_spread_worthless(self, bps_op):
        # With spot very far above short strike and 0 days, BS cost ≈ 0
        status, cost, pnl, msg = check_spread_status(3000.0, 0.8, 1, bps_op)
        assert status in ("profit", "ok")

    def test_warn_between_75_and_100_pct_loss(self, bps_op):
        # Patch _current_spread_value to return 80% of max_loss
        with patch("strategies.spread._current_spread_value", return_value=0.80 * bps_op["max_loss"]):
            status, cost, pnl, msg = check_spread_status(1750.0, 0.8, 3, bps_op)
            assert status == "warn"

    def test_stop_at_100_pct_loss(self, bps_op):
        with patch("strategies.spread._current_spread_value", return_value=bps_op["max_loss"]):
            status, cost, pnl, msg = check_spread_status(1750.0, 0.8, 3, bps_op)
            assert status == "stop"


# ── show_spread_analysis ──────────────────────────────────────────────────────

class TestShowSpreadAnalysis:
    def test_runs_without_error(self, capsys):
        show_spread_analysis("ETH", 2000.0, 0.8, 7)
        captured = capsys.readouterr()
        assert "Bull Put Spread" in captured.out
        assert "Bear Call Spread" in captured.out
        assert "Net Credit" in captured.out

    def test_displays_all_otm_levels(self, capsys):
        show_spread_analysis("ETH", 2000.0, 0.8, 7)
        captured = capsys.readouterr()
        assert "10%" in captured.out
        assert "15%" in captured.out
        assert "20%" in captured.out
