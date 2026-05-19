"""
tests/test_portfolio.py
=======================
Tests for trading/portfolio.py — open position aggregation and P&L helpers.

All tests use the in-memory SQLite database provided by the conftest
autouse fixture (use_in_memory_db), so no real database is touched.
Market data and pricing functions are patched to avoid network calls.

Coverage
--------
collect_open_positions : empty DB returns [], DB state returns summaries
_position_summary      : Wheel / Strangle / Calendar / Spread P&L shapes
collect_spread_history : returns closed spread rows
show_portfolio         : prints expected headers and position rows
"""

import pytest
from datetime import date
from unittest.mock import patch

from trading import portfolio
from trading.portfolio import collect_open_positions, collect_spread_history
from database.wheel_db import save_wheel_state
from database.strangle_db import save_strangle_state
from database.calendar_db import save_calendar_state
from database.spread_db import create_spread_trade, close_spread_trade


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_wb():
    from unittest.mock import MagicMock
    return MagicMock()


@pytest.fixture
def wheel_open_state():
    return {
        "stage": "short_put",
        "open": {
            "type":      "Put",
            "strike":    1800.0,
            "premium":   50.0,
            "qty":       1.0,
            "spot_open": 2000.0,
            "days":      7,
            "asset":     "ETH",
            "expiry":    "2099-01-01",
        },
        "asset_held": 0.0, "cost_basis": 0.0, "total_premium": 50.0,
        "wins": 0, "losses": 0, "cycles": 0,
    }


@pytest.fixture
def strangle_open_state():
    return {
        "open": {
            "put_strike":    1500.0,
            "call_strike":   2500.0,
            "total_premium": 15.0,
            "qty":           1.0,
            "spot_open":     2000.0,
            "days":          7,
            "asset":         "ETH",
            "expiry":        "2099-01-01",
        },
        "total_premium": 15.0,
        "wins": 0, "losses": 0, "trades": 1,
    }


@pytest.fixture
def calendar_open_state():
    return {
        "open": {
            "strike":      2000.0,
            "option_type": "Put",
            "net_debit":   20.0,
            "qty":         1.0,
            "spot_open":   2000.0,
            "expiry_near": "2099-01-01",
            "expiry_far":  "2099-02-01",
            "near_days":   7,
            "far_days":    30,
            "asset":       "ETH",
        },
        "total_pnl": 0.0, "wins": 0, "losses": 0, "trades": 1,
    }


# ── collect_open_positions ────────────────────────────────────────────────────

def test_empty_db_returns_empty_list(monkeypatch):
    """No state in DB → empty positions list."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: None)
    positions = collect_open_positions()
    assert positions == []


def test_returns_wheel_position(monkeypatch, wheel_open_state):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    save_wheel_state("ETH", wheel_open_state)
    positions = collect_open_positions()

    wheel = next((p for p in positions if p["strategy"] == "Wheel"), None)
    assert wheel is not None
    assert wheel["position"] == "Short Put"
    # P&L = (premium - open_fee) - current_value - close_fee
    # The fixture has default fees of 0, so: (50.0 - 0) - 2.0*1.0 - 0 = 48.0
    assert wheel["unrealised_pnl"] == pytest.approx(48.0)


def test_wheel_position_with_fees(monkeypatch):
    """Wheel position P&L should subtract both open and close fees."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)

    state_with_fees = {
        "stage": "short_put",
        "open": {
            "type":      "Put",
            "strike":    1800.0,
            "premium":   50.0,
            "open_fees": 5.0,
            "close_fees": 3.0,
            "qty":       1.0,
            "spot_open": 2000.0,
            "days":      7,
            "asset":     "ETH",
            "expiry":    "2099-01-01",
        },
        "asset_held": 0.0, "cost_basis": 0.0, "total_premium": 50.0,
        "wins": 0, "losses": 0, "cycles": 0,
    }

    save_wheel_state("ETH", state_with_fees)
    positions = collect_open_positions()

    wheel = next((p for p in positions if p["strategy"] == "Wheel"), None)
    assert wheel is not None
    # P&L = (premium - open_fee) - current_value - close_fee
    # P&L = (50.0 - 5.0) - 2.0*1.0 - 3.0 = 40.0
    assert wheel["unrealised_pnl"] == pytest.approx(40.0)


def test_returns_strangle_position(monkeypatch, strangle_open_state):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    save_strangle_state("ETH", strangle_open_state)
    positions = collect_open_positions()

    strangle = next((p for p in positions if p["strategy"] == "Strangle"), None)
    assert strangle is not None
    assert strangle["position"] == "Short Strangle"
    # P&L = (premium - open_fee) - current_value - close_fee
    # The fixture has default fees of 0, so: (15.0 - 0) - (2+3)*1.0 - 0 = 10.0
    assert strangle["unrealised_pnl"] == pytest.approx(10.0)


def test_strangle_position_with_fees(monkeypatch):
    """Strangle position P&L should subtract both open and close fees."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    state_with_fees = {
        "open": {
            "put_strike":    1500.0,
            "call_strike":   2500.0,
            "total_premium": 15.0,
            "open_fees":     2.0,
            "close_fees":    1.5,
            "qty":           1.0,
            "spot_open":     2000.0,
            "days":          7,
            "asset":         "ETH",
            "expiry":        "2099-01-01",
        },
        "total_premium": 15.0,
        "wins": 0, "losses": 0, "trades": 1,
    }

    save_strangle_state("ETH", state_with_fees)
    positions = collect_open_positions()

    strangle = next((p for p in positions if p["strategy"] == "Strangle"), None)
    assert strangle is not None
    # P&L = (premium - open_fee) - current_value - close_fee
    # P&L = (15.0 - 2.0) - (2+3)*1.0 - 1.5 = 13.0 - 5.0 - 1.5 = 6.5
    assert strangle["unrealised_pnl"] == pytest.approx(6.5)


def test_returns_calendar_position(monkeypatch, calendar_open_state):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    save_calendar_state("ETH", calendar_open_state)
    positions = collect_open_positions()

    cal = next((p for p in positions if p["strategy"] == "Calendar"), None)
    assert cal is not None
    assert cal["position"] == "Put Calendar"
    # P&L = (net_debit - open_fee) - spread_value - close_fee
    # The fixture has default fees of 0, so: (20.0 - 0) - 0.0 - 0 = 20.0
    assert cal["unrealised_pnl"] == pytest.approx(20.0)


def test_calendar_position_with_fees(monkeypatch):
    """Calendar position P&L should subtract both open and close fees."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    state_with_fees = {
        "open": {
            "strike":      2000.0,
            "option_type": "Put",
            "net_debit":   20.0,
            "open_fees":   2.0,
            "close_fees":  1.0,
            "qty":         1.0,
            "spot_open":   2000.0,
            "expiry_near": "2099-01-01",
            "expiry_far":  "2099-02-01",
            "near_days":   7,
            "far_days":    30,
            "asset":       "ETH",
        },
        "total_pnl": 0.0, "wins": 0, "losses": 0, "trades": 1,
    }

    save_calendar_state("ETH", state_with_fees)
    positions = collect_open_positions()

    cal = next((p for p in positions if p["strategy"] == "Calendar"), None)
    assert cal is not None
    # P&L = (net_debit - open_fee) - spread_value - close_fee
    # spread_value = far - near = 2.0 - 2.0 = 0.0
    # P&L = (20.0 - 2.0) - 0.0 - 1.0 = 17.0
    assert cal["unrealised_pnl"] == pytest.approx(17.0)


def test_returns_all_three_strategies(
    monkeypatch, wheel_open_state, strangle_open_state, calendar_open_state
):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    save_wheel_state("ETH", wheel_open_state)
    save_strangle_state("ETH", strangle_open_state)
    save_calendar_state("ETH", calendar_open_state)

    positions = collect_open_positions()
    assert len(positions) == 3
    strategies = {p["strategy"] for p in positions}
    assert strategies == {"Wheel", "Strangle", "Calendar"}


def test_failed_price_fetch_returns_position_with_no_pnl(monkeypatch, wheel_open_state):
    """If spot price fetch fails, position is included but P&L is None."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: None)

    save_wheel_state("ETH", wheel_open_state)
    positions = collect_open_positions()
    wheel = next((p for p in positions if p["strategy"] == "Wheel"), None)
    assert wheel is not None
    assert wheel["unrealised_pnl"] is None
    assert wheel["current_value"] is None


# ── show_portfolio ────────────────────────────────────────────────────────────

def test_returns_spread_open_position(monkeypatch):
    """Open spread in spreads table appears in collect_open_positions."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 5.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    create_spread_trade(
        asset="ETH",
        spread_type="BPS",
        date_open=date(2099, 1, 1),
        short_strike=1800.0,
        long_strike=1700.0,
        spot_open=2000.0,
        net_credit=30.0,
        max_loss=70.0,
        qty=1.0,
        days=7,
        expiry="01-Jan-2099",
    )

    positions = collect_open_positions()
    spread = next((p for p in positions if p["strategy"] == "Spread"), None)
    assert spread is not None
    assert spread["position"] == "Bull Put Spread"
    assert spread["strike"] == "$1,800/$1,700"
    # P&L = (net_credit - open_fee) - current_value - close_fee
    # No fees provided, so: (30.0 - 0) - (5-5)*1 - 0 = 30.0
    assert spread["unrealised_pnl"] == pytest.approx(30.0)


def test_spread_position_with_open_fees(monkeypatch):
    """Spread position P&L should subtract open fees."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 5.0)

    create_spread_trade(
        asset="ETH",
        spread_type="BPS",
        date_open=date(2099, 1, 1),
        short_strike=1800.0,
        long_strike=1700.0,
        spot_open=2000.0,
        net_credit=30.0,
        max_loss=70.0,
        qty=1.0,
        days=7,
        expiry="01-Jan-2099",
        open_fees=3.0,
    )

    positions = collect_open_positions()
    spread = next((p for p in positions if p["strategy"] == "Spread"), None)
    assert spread is not None
    # P&L = (net_credit - open_fee) - current_value - close_fee
    # For open position: (30.0 - 3.0) - (5-5)*1 - 0 = 27.0
    assert spread["unrealised_pnl"] == pytest.approx(27.0)


def test_spread_position_no_pnl_when_price_unavailable(monkeypatch):
    """Spread with no spot price → unrealised_pnl is None."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: None)

    create_spread_trade(
        asset="ETH",
        spread_type="BCS",
        date_open=date(2099, 1, 1),
        short_strike=2200.0,
        long_strike=2300.0,
        spot_open=2000.0,
        net_credit=20.0,
        max_loss=80.0,
        qty=1.0,
        days=7,
        expiry="01-Jan-2099",
    )

    positions = collect_open_positions()
    spread = next((p for p in positions if p["strategy"] == "Spread"), None)
    assert spread is not None
    assert spread["unrealised_pnl"] is None
    assert spread["position"] == "Bear Call Spread"


def test_collect_spread_history_returns_closed_trades(monkeypatch):
    """collect_spread_history returns only closed spreads from spreads table."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})

    trade = create_spread_trade(
        asset="ETH",
        spread_type="BPS",
        date_open=date(2025, 1, 1),
        short_strike=1800.0,
        long_strike=1700.0,
        spot_open=2000.0,
        net_credit=30.0,
        max_loss=70.0,
        qty=1.0,
        days=7,
        expiry="08-Jan-2025",
    )
    close_spread_trade(
        trade_id=trade.id,
        date_close=date(2025, 1, 8),
        spot_close=1900.0,
        pnl=30.0,
        result="Win",
    )

    history = collect_spread_history("ETH")
    assert len(history) == 1
    assert history[0]["result"] == "Win"
    assert history[0]["pnl"] == pytest.approx(30.0)
    assert history[0]["asset"] == "ETH"


def test_collect_spread_history_excludes_open_trades(monkeypatch):
    """Open spreads are excluded from history."""
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})

    create_spread_trade(
        asset="ETH",
        spread_type="BPS",
        date_open=date(2099, 1, 1),
        short_strike=1800.0,
        long_strike=1700.0,
        spot_open=2000.0,
        net_credit=30.0,
        max_loss=70.0,
        qty=1.0,
        days=7,
        expiry="01-Jan-2099",
    )

    history = collect_spread_history("ETH")
    assert history == []


def test_returns_all_four_strategies(
    monkeypatch, wheel_open_state, strangle_open_state, calendar_open_state
):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    save_wheel_state("ETH", wheel_open_state)
    save_strangle_state("ETH", strangle_open_state)
    save_calendar_state("ETH", calendar_open_state)
    create_spread_trade(
        asset="ETH",
        spread_type="BPS",
        date_open=date(2099, 1, 1),
        short_strike=1800.0,
        long_strike=1700.0,
        spot_open=2000.0,
        net_credit=30.0,
        max_loss=70.0,
        qty=1.0,
        days=7,
        expiry="01-Jan-2099",
    )

    positions = collect_open_positions()
    strategies = {p["strategy"] for p in positions}
    assert strategies == {"Wheel", "Strangle", "Calendar", "Spread"}


def test_show_portfolio_prints_headers(monkeypatch, mock_wb, wheel_open_state, capsys):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 1.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 5.0)

    save_wheel_state("ETH", wheel_open_state)

    from ui.menus import show_portfolio
    show_portfolio()
    captured = capsys.readouterr().out
    assert "Open Portfolio Positions" in captured
    assert "Open positions" in captured
    assert "Unrealised P&L" in captured


def test_show_portfolio_empty_db(monkeypatch, mock_wb, capsys):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: None)

    from ui.menus import show_portfolio
    show_portfolio()
    captured = capsys.readouterr().out
    assert "None found" in captured
