"""
tests/test_menus.py
===================
Tests for ui/menus.py — user menu actions and monitor integration.
"""

from unittest.mock import patch

from ui.menus import main_menu, strategies_menu


def test_main_menu_monitor_triggers_run_monitor():
    with patch("ui.menus.run_monitor") as mock_monitor, \
         patch("builtins.input", return_value="M"):
        should_continue, asset, spot, iv, days, near, far = main_menu(
            "ETH", 2000.0, 0.80, 7, 7, 30
        )

    mock_monitor.assert_called_once_with(2000.0, 0.80, 7, "ETH", silent=False)
    assert should_continue is True
    assert asset == "ETH"
    assert spot == 2000.0
    assert iv == 0.80
    assert days == 7
    assert near == 7
    assert far == 30


def test_strategy_menu_monitor_option_calls_run_monitor():
    input_sequence = ["M", "0"]
    with patch("ui.menus.run_monitor") as mock_monitor, \
         patch("builtins.input", side_effect=input_sequence):
        strategies_menu("ETH", 2000.0, 0.80, 7, 7, 30)

    mock_monitor.assert_called_once_with(2000.0, 0.80, 7, "ETH", silent=False)


def test_main_menu_trade_history_calls_show_trade_history():
    with patch("ui.menus.show_trade_history") as mock_history, \
         patch("builtins.input", return_value="H"):
        should_continue, asset, spot, iv, days, near, far = main_menu(
            "ETH", 2000.0, 0.80, 7, 7, 30
        )

    mock_history.assert_called_once_with()
    assert should_continue is True
    assert asset == "ETH"
    assert spot == 2000.0
    assert iv == 0.80
    assert days == 7
    assert near == 7
    assert far == 30


def test_main_menu_calendar_horizon_toggle():
    with patch("builtins.input", return_value="4"):
        should_continue, asset, spot, iv, days, near, far = main_menu(
            "ETH", 2000.0, 0.80, 7, 7, 30
        )

    assert should_continue is True
    assert near > 7 and near < far
    assert far == 30
