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
_position_summary      : Wheel / Strangle / Calendar P&L shapes
show_portfolio         : prints expected headers and position rows
"""

import pytest
from unittest.mock import patch

from trading import portfolio
from trading.portfolio import collect_open_positions
from database.wheel_db import save_wheel_state
from database.strangle_db import save_strangle_state
from database.calendar_db import save_calendar_state


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
    assert wheel["unrealised_pnl"] == pytest.approx(48.0)   # 50.0 - 2.0*1.0


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
    assert strangle["unrealised_pnl"] == pytest.approx(10.0)   # 15.0 - (2+3)*1.0


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
    # spread_value = far - near = 2.0 - 2.0 = 0.0 → pnl = net_debit - 0 = 20.0
    assert cal["unrealised_pnl"] == pytest.approx(20.0)


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

def test_show_portfolio_prints_headers(monkeypatch, mock_wb, wheel_open_state, capsys):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put",  lambda spot, strike, T, r, iv: 1.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 5.0)

    save_wheel_state("ETH", wheel_open_state)

    from ui.menus import show_portfolio
    show_portfolio(mock_wb)
    captured = capsys.readouterr().out
    assert "Open Portfolio Positions" in captured
    assert "Open positions" in captured
    assert "Unrealised P&L" in captured


def test_show_portfolio_empty_db(monkeypatch, mock_wb, capsys):
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: None)

    from ui.menus import show_portfolio
    show_portfolio(mock_wb)
    captured = capsys.readouterr().out
    assert "None found" in captured
