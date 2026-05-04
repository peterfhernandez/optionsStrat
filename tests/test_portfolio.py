import json
from pathlib import Path

import pytest

from trading import portfolio


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


def test_collect_open_positions_returns_none_when_no_state_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: None)
    positions = portfolio.collect_open_positions(base_dir=str(tmp_path))
    assert positions == []


def test_collect_open_positions_returns_open_positions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})

    write_json(
        tmp_path / "paper_state_ETH.json",
        {
            "open": {
                "type": "Put",
                "strike": 1800,
                "premium": 50.0,
                "qty": 1.0,
                "spot_open": 2000.0,
                "days": 7,
                "asset": "ETH",
            }
        },
    )
    write_json(
        tmp_path / "strangle_state_ETH.json",
        {
            "open": {
                "put_strike": 1500,
                "call_strike": 2500,
                "total_premium": 15.0,
                "qty": 1.0,
                "spot_open": 2000.0,
                "days": 7,
                "asset": "ETH",
            }
        },
    )
    write_json(
        tmp_path / "calendar_state_ETH.json",
        {
            "open": {
                "strike": 2000,
                "option_type": "Put",
                "net_debit": 20.0,
                "qty": 1.0,
                "spot_open": 2000.0,
                "expiry_near": "2099-01-01",
                "expiry_far": "2099-02-01",
                "near_days": 7,
                "far_days": 30,
                "asset": "ETH",
            }
        },
    )

    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put", lambda spot, strike, T, r, iv: 2.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 3.0)

    positions = portfolio.collect_open_positions(base_dir=str(tmp_path))
    assert len(positions) == 3

    wheel = next(p for p in positions if p["strategy"] == "Wheel")
    strangle = next(p for p in positions if p["strategy"] == "Strangle")
    calendar = next(p for p in positions if p["strategy"] == "Calendar")

    assert wheel["position"] == "Short Put"
    assert wheel["unrealised_pnl"] == pytest.approx(48.0)
    assert strangle["position"] == "Short Strangle"
    assert strangle["unrealised_pnl"] == pytest.approx(10.0)
    assert calendar["position"] == "Put Calendar"
    assert calendar["unrealised_pnl"] == pytest.approx(20.0)


def test_show_portfolio_prints_open_position_table(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portfolio, "SUPPORTED_ASSETS", {"ETH": {}})
    write_json(
        tmp_path / "paper_state_ETH.json",
        {
            "open": {
                "type": "Call",
                "strike": 1800,
                "premium": 40.0,
                "qty": 1.0,
                "spot_open": 2000.0,
                "days": 7,
                "asset": "ETH",
            }
        },
    )
    monkeypatch.setattr(portfolio, "get_spot_price", lambda asset: 2000.0)
    monkeypatch.setattr(portfolio, "get_deribit_iv", lambda asset, spot, days: 0.80)
    monkeypatch.setattr(portfolio, "bs_put", lambda spot, strike, T, r, iv: 1.0)
    monkeypatch.setattr(portfolio, "bs_call", lambda spot, strike, T, r, iv: 5.0)

    from ui.menus import show_portfolio

    show_portfolio(None)
    captured = capsys.readouterr().out
    assert "Open Portfolio Positions" in captured
    assert "Open positions" in captured
    assert "Unrealised P&L" in captured
