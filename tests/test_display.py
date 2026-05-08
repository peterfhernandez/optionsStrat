"""
tests/test_display.py
====================
Tests for ui/display.py trade history rendering.
"""
from datetime import date

import pytest

from database.wheel_db import create_single_trade
from database.strangle_db import create_strangle_trade, close_strangle_trade
from ui.display import show_trade_history


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def test_show_trade_history_counts_rows_with_missing_pnl(capsys):
    create_single_trade(
        asset="ETH", date_open=date(2026, 5, 1), option_type="Put",
        strike=1800.0, expiry="01-May-2026", spot_open=2000.0,
        premium=150.0, qty=0.1, days=7, stage="short_put",
        notes="test",
    )
    # Close the ETH trade with a Win
    from database.wheel_db import close_single_trade
    from models import get_session, Single
    session = get_session()
    try:
        trade = session.query(Single).filter(Single.asset == "ETH").first()
        trade_id = trade.id
    finally:
        session.close()
    close_single_trade(trade_id, date_close=date(2026, 5, 8),
                       spot_close=1900.0, pnl=150.0, result="Win")

    # BTC trade left open (no date_close) — should NOT appear in history
    create_single_trade(
        asset="BTC", date_open=date(2026, 5, 2), option_type="Call",
        strike=60000.0, expiry="02-May-2026", spot_open=65000.0,
        premium=0.0, qty=0.01, days=7, stage="short_call",
        notes="open",
    )

    show_trade_history()
    out = capsys.readouterr().out

    assert "ETH" in out
    assert "Total trades" in out
    assert "1" in out


def test_show_trade_history_shows_closed_trade_without_date_close(capsys):
    """Trades migrated from Excel have result set but date_close=None; should still appear."""
    from models import get_session, Single
    create_single_trade(
        asset="ETH", date_open=date(2026, 4, 1), option_type="Put",
        strike=1800.0, expiry="01-Apr-2026", spot_open=2000.0,
        premium=100.0, qty=0.1, days=7, stage="short_put", notes="migrated",
    )
    session = get_session()
    try:
        trade = session.query(Single).filter(Single.asset == "ETH").first()
        trade.result = "Win (Auto TP)"
        trade.pnl = 80.0
        # date_close intentionally left as None (Excel migration scenario)
        session.commit()
    finally:
        session.close()

    show_trade_history()
    out = capsys.readouterr().out
    assert "ETH" in out
    assert "Total trades" in out


def test_show_trade_history_handles_strangle(capsys):
    trade = create_strangle_trade(
        asset="ETH", date_open=date(2026, 5, 3),
        put_strike=1800.0, call_strike=2200.0,
        spot_open=2000.0, total_premium=50.0,
        qty=0.125, days=7, expiry="10-May-2026",
        notes="test strangle",
    )
    close_strangle_trade(
        trade.id, date_close=date(2026, 5, 10),
        spot_close=2000.0, pnl=-25.0, result="Loss",
    )

    show_trade_history()
    out = capsys.readouterr().out

    assert "Strangle" in out
    assert "Total trades" in out
    assert "1" in out
