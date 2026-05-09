"""
tests/test_executor.py
======================
Unit tests for trading/executor.py.

All database calls, market pricing, and broker interactions are mocked so
tests run without a real database or network.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from access import OrderResult
from trading.executor import (
    enter_trade,
    _index_price,
    _broker_amount,
    _place_option,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _order(order_id: str = "ORD-1") -> OrderResult:
    return OrderResult(
        order_id=order_id,
        instrument="ETH-30MAY25-2000-P",
        direction="sell",
        amount=10000.0,
        price=0.025,
        state="open",
        filled_amount=0.0,
        avg_price=None,
        label=None,
    )


def _mock_broker(*order_ids) -> MagicMock:
    """Return a broker mock whose place_order returns _order instances in sequence."""
    broker = MagicMock()
    if len(order_ids) == 1:
        broker.place_order.return_value = _order(order_ids[0])
    else:
        broker.place_order.side_effect = [_order(oid) for oid in order_ids]
    return broker


def _candidate(**kwargs):
    defaults = dict(
        asset="ETH",
        spot=2000.0,
        iv=0.80,
        days=30,
        prob_profit=70,
        yield_ann=40,
        liquidity_tag="ok",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── _index_price / _broker_amount ─────────────────────────────────────────────

class TestHelpers:
    def test_index_price_inverse(self):
        # $50 option on a $2000 spot → 0.025 index price
        assert _index_price(50.0, 2000.0) == pytest.approx(0.025, rel=1e-5)

    def test_broker_amount_inverse(self):
        from config import BUDGET_USD
        assert _broker_amount("ETH", 2000.0) == float(BUDGET_USD)
        assert _broker_amount("BTC", 50000.0) == float(BUDGET_USD)

    def test_broker_amount_linear(self):
        from config import BUDGET_USD
        result = _broker_amount("SOL", 200.0)
        assert result == pytest.approx(BUDGET_USD / 200.0, rel=1e-4)


# ── Default broker ────────────────────────────────────────────────────────────

class TestDefaultBroker:
    @patch("trading.executor.DeribitClient")
    @patch("trading.executor.save_wheel_state")
    @patch("trading.executor.create_single_trade")
    @patch("trading.executor.load_wheel_state",
           side_effect=lambda *a, **kw: {"stage": "idle", "open": None,
                                         "total_premium": 0.0, "asset_held": 0})
    @patch("trading.executor.bs_put", return_value=0.05)
    def test_no_broker_arg_uses_deribit_client(
        self, mock_bs, mock_load, mock_create, mock_save, mock_cls
    ):
        """enter_trade() with no broker creates DeribitClient(paper=DERIBIT_PAPER)."""
        from config import DERIBIT_PAPER
        mock_cls.return_value = _mock_broker("AUTO-1")

        c = _candidate(strategy="CSP", strike="$2000")
        enter_trade(c)

        mock_cls.assert_called_once_with(paper=DERIBIT_PAPER)

    @patch("trading.executor.DeribitClient")
    @patch("trading.executor.save_wheel_state")
    @patch("trading.executor.create_single_trade")
    @patch("trading.executor.load_wheel_state",
           side_effect=lambda *a, **kw: {"stage": "idle", "open": None,
                                         "total_premium": 0.0, "asset_held": 0})
    @patch("trading.executor.bs_put", return_value=0.05)
    def test_explicit_broker_skips_deribit_client(
        self, mock_bs, mock_load, mock_create, mock_save, mock_cls
    ):
        """When broker is passed, DeribitClient is never constructed."""
        broker = _mock_broker("EXP-1")
        c = _candidate(strategy="CSP", strike="$2000")
        enter_trade(c, broker=broker)
        mock_cls.assert_not_called()


# ── CSP ───────────────────────────────────────────────────────────────────────

WHEEL_STATE_IDLE = {"stage": "idle", "open": None, "total_premium": 0.0, "asset_held": 0}
WHEEL_STATE_HOLD = {"stage": "holding", "open": None, "total_premium": 0.0, "asset_held": 5.0}


@patch("trading.executor.save_wheel_state")
@patch("trading.executor.create_single_trade")
@patch("trading.executor.load_wheel_state",
       side_effect=lambda *a, **kw: dict(WHEEL_STATE_IDLE))
@patch("trading.executor.bs_put", return_value=0.05)
class TestEnterCSP:
    def test_places_order_and_records_db(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-1")
        c = _candidate(strategy="CSP", strike="$2000")
        result = enter_trade(c, broker=broker)

        assert result["type"] == "Put"
        assert result["strike"] == 2000.0
        mock_create.assert_called_once()
        mock_save.assert_called_once()
        broker.place_order.assert_called_once()
        assert result["broker_order_id"] == "PUT-ORD-1"

    def test_order_direction_is_sell(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-2")
        c = _candidate(strategy="CSP", strike="$2000")
        enter_trade(c, broker=broker)
        assert broker.place_order.call_args.args[1] == "sell"

    def test_instrument_contains_asset(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-3")
        c = _candidate(strategy="CSP", strike="$2000")
        enter_trade(c, broker=broker)
        assert "ETH" in broker.place_order.call_args.args[0]

    def test_strike_parsed_with_comma(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-4")
        c = _candidate(strategy="CSP", strike="$1,800")
        result = enter_trade(c, broker=broker)
        assert result["strike"] == 1800.0

    def test_days_override(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-5")
        c = _candidate(strategy="CSP", strike="$2000", days=7)
        enter_trade(c, days=14, broker=broker)
        t_arg = mock_bs.call_args.args[2]
        assert t_arg == pytest.approx(14 / 365.0)


# ── CC ────────────────────────────────────────────────────────────────────────

@patch("trading.executor.save_wheel_state")
@patch("trading.executor.create_single_trade")
@patch("trading.executor.load_wheel_state",
       side_effect=lambda *a, **kw: dict(WHEEL_STATE_HOLD))
@patch("trading.executor.bs_call", return_value=0.04)
class TestEnterCC:
    def test_places_order(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("CC-ORD-1")
        c = _candidate(strategy="CC", strike="$2200")
        result = enter_trade(c, broker=broker)
        assert result["type"] == "Call"
        broker.place_order.assert_called_once()
        assert result["broker_order_id"] == "CC-ORD-1"

    def test_wrong_stage_raises(self, mock_bs, mock_load, mock_create, mock_save):
        mock_load.side_effect = lambda *a, **kw: dict(WHEEL_STATE_IDLE)
        broker = _mock_broker("CC-ORD-X")
        c = _candidate(strategy="CC", strike="$2200")
        with pytest.raises(RuntimeError, match="wheel stage"):
            enter_trade(c, broker=broker)


# ── Strangle ──────────────────────────────────────────────────────────────────

STRANGLE_STATE = {"open": None, "total_premium": 0.0, "trades": 0}


@patch("trading.executor.save_strangle_state")
@patch("trading.executor.create_strangle_trade", return_value=SimpleNamespace(id=42))
@patch("trading.executor.load_strangle_state",
       side_effect=lambda *a, **kw: dict(STRANGLE_STATE))
@patch("trading.executor.bs_call", return_value=0.03)
@patch("trading.executor.bs_put",  return_value=0.04)
class TestEnterStrangle:
    def test_two_orders_placed(self, mock_bp, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("STR-P-1", "STR-C-1")
        c = _candidate(strategy="Strangle", put_strike=1800.0, call_strike=2200.0)
        result = enter_trade(c, broker=broker)
        assert broker.place_order.call_count == 2
        assert result["broker_put_order_id"]  == "STR-P-1"
        assert result["broker_call_order_id"] == "STR-C-1"

    def test_trade_counter_incremented(self, mock_bp, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("STR-P-2", "STR-C-2")
        c = _candidate(strategy="Strangle", put_strike=1800.0, call_strike=2200.0)
        enter_trade(c, broker=broker)
        saved_state = mock_save.call_args.args[1]
        assert saved_state["trades"] == 1


# ── Calendar ──────────────────────────────────────────────────────────────────

CALENDAR_STATE = {"open": None, "trades": 0}


@patch("trading.executor.save_calendar_state")
@patch("trading.executor.create_calendar_trade", return_value=SimpleNamespace(id=7))
@patch("trading.executor.load_calendar_state",
       side_effect=lambda *a, **kw: dict(CALENDAR_STATE))
@patch("trading.executor.bs_call", return_value=0.06)
class TestEnterCalendar:
    def test_cal_c_two_orders(self, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("CAL-N-1", "CAL-F-1")
        c = _candidate(strategy="Cal-C", strike="$2000 call", far_days=60)
        result = enter_trade(c, broker=broker)
        assert result["option_type"] == "Call"
        assert broker.place_order.call_count == 2
        assert result["broker_near_order_id"] == "CAL-N-1"
        assert result["broker_far_order_id"]  == "CAL-F-1"

    def test_near_sold_far_bought(self, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("CAL-N-2", "CAL-F-2")
        c = _candidate(strategy="Cal-C", strike="$2000", far_days=60)
        enter_trade(c, broker=broker)
        directions = [broker.place_order.call_args_list[i].args[1] for i in range(2)]
        assert directions == ["sell", "buy"]

    def test_cal_p_uses_bs_put(self, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("CAL-N-3", "CAL-F-3")
        with patch("trading.executor.bs_put", return_value=0.05) as mock_bp:
            c = _candidate(strategy="Cal-P", strike="$1800", far_days=60)
            result = enter_trade(c, broker=broker)
            assert result["option_type"] == "Put"
            assert mock_bp.call_count == 2   # near + far legs


# ── enter_trade dispatch ──────────────────────────────────────────────────────

class TestEnterTradeDispatch:
    def test_unsupported_strategy_raises(self):
        c = _candidate(strategy="UNKNOWN")
        with pytest.raises(ValueError, match="Unsupported strategy"):
            enter_trade(c, broker=_mock_broker("X"))
