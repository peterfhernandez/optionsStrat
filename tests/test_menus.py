"""
tests/test_menus.py
===================
Tests for ui/menus.py — user menu actions and monitor integration.
"""

from unittest.mock import patch, MagicMock

from ui.menus import main_menu, strategies_menu


def test_main_menu_monitor_triggers_run_monitor(mock_wb):
    with patch("ui.menus.run_monitor") as mock_monitor, \
         patch("builtins.input", return_value="M"):
        should_continue, asset, spot, iv, days = main_menu("ETH", 2000.0, 0.80, mock_wb, 7)

    mock_monitor.assert_called_once_with(2000.0, 0.80, mock_wb, 7, "ETH", silent=False)
    assert should_continue is True
    assert asset == "ETH"
    assert spot == 2000.0
    assert iv == 0.80
    assert days == 7


def test_strategy_menu_monitor_option_calls_run_monitor(mock_wb):
    input_sequence = ["M", "0"]
    with patch("ui.menus.run_monitor") as mock_monitor, \
         patch("builtins.input", side_effect=input_sequence):
        strategies_menu("ETH", 2000.0, 0.80, mock_wb, 7)

    mock_monitor.assert_called_once_with(2000.0, 0.80, mock_wb, 7, "ETH", silent=False)
