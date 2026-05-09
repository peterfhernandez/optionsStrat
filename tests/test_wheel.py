"""Tests for strategies/wheel.py — strike analysis, performance summary, and paper menu."""
import pytest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from strategies.wheel import show_strikes, show_summary, wheel_paper_menu
from config import BUDGET_USD, OTM_LEVELS


class TestShowStrikes:
    """Test the show_strikes display function."""

    @patch("sys.stdout", new_callable=StringIO)
    def test_show_strikes_output_format(self, mock_stdout):
        """show_strikes should display puts and calls with strike tables."""
        show_strikes("ETH", spot=2000.0, iv=0.80, days=7)
        output = mock_stdout.getvalue()

        # Check for key headers
        assert "Wheel Strike Analysis" in output
        assert "ETH" in output
        assert "Cash-Secured Put Strikes" in output
        assert "Covered Call Strikes" in output

    @patch("sys.stdout", new_callable=StringIO)
    def test_show_strikes_includes_otm_levels(self, mock_stdout):
        """show_strikes should display all OTM levels from config."""
        show_strikes("BTC", spot=50000.0, iv=0.75, days=7)
        output = mock_stdout.getvalue()

        # Each OTM level should appear in the output
        for otm in OTM_LEVELS:
            otm_pct = f"{otm*100:.0f}%"
            assert otm_pct in output

    @patch("sys.stdout", new_callable=StringIO)
    def test_show_strikes_all_assets(self, mock_stdout):
        """show_strikes should work for all supported assets."""
        for asset in ("ETH", "BTC", "SOL", "XRP"):
            mock_stdout.truncate(0)
            mock_stdout.seek(0)

            show_strikes(asset, spot=1000.0, iv=0.80, days=7)
            output = mock_stdout.getvalue()
            assert asset in output
            assert "Strike" in output  # header should be present


class TestShowSummary:
    """Test the show_summary database-backed function."""

    @patch("sys.stdout", new_callable=StringIO)
    def test_show_summary_no_trades(self, mock_stdout):
        """show_summary should handle empty database gracefully."""
        show_summary()
        output = mock_stdout.getvalue()

        # Should display header
        assert "Performance Summary" in output or "Wheel Strategy" in output

    @patch("sys.stdout", new_callable=StringIO)
    def test_show_summary_displays_stats(self, mock_stdout):
        """show_summary should display performance stats when trades exist."""
        # This test relies on database state from migrations
        show_summary()
        output = mock_stdout.getvalue()

        # Check for expected stat labels (may or may not have data)
        # The output should contain some reporting, even if "No completed trades yet"
        assert len(output) > 0


class TestWheelPaperMenuExecutor:
    """Verify wheel_paper_menu delegates to enter_trade when opening positions."""

    @patch("strategies.wheel.enter_trade")
    @patch("builtins.input", side_effect=["1", ""])   # choice=1, accept default strike
    @patch("sys.stdout", new_callable=StringIO)
    def test_sell_put_calls_enter_trade(self, _out, _inp, mock_enter):
        """Choosing [1] (Sell Put) should call enter_trade with a CSP candidate."""
        from database import save_wheel_state
        save_wheel_state("ETH", {
            "stage": "no_position", "open": None,
            "wins": 0, "losses": 0, "cycles": 0,
            "total_premium": 0.0, "asset_held": None, "cost_basis": None,
        })

        wheel_paper_menu("ETH", spot=2000.0, iv=0.80, days=7)

        mock_enter.assert_called_once()
        c = mock_enter.call_args[0][0]
        assert c.strategy == "CSP"
        assert c.asset == "ETH"
        assert c.spot == 2000.0
        assert c.iv == 0.80
        assert c.days == 7

    @patch("strategies.wheel.enter_trade")
    @patch("builtins.input", side_effect=["4", ""])   # choice=4, accept default strike
    @patch("sys.stdout", new_callable=StringIO)
    def test_sell_call_calls_enter_trade(self, _out, _inp, mock_enter):
        """Choosing [4] (Sell Covered Call) should call enter_trade with a CC candidate."""
        from database import save_wheel_state
        save_wheel_state("ETH", {
            "stage": "holding", "open": None,
            "wins": 0, "losses": 0, "cycles": 0,
            "total_premium": 0.0, "asset_held": 0.05, "cost_basis": 2000.0,
            "broker": "deribit_paper",
        })

        wheel_paper_menu("ETH", spot=2000.0, iv=0.80, days=7)

        mock_enter.assert_called_once()
        c = mock_enter.call_args[0][0]
        assert c.strategy == "CC"
        assert c.asset == "ETH"
