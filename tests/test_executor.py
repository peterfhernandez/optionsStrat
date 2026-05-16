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
    _order_price,
    _broker_amount,
    _place_option,
    _next_friday,
    _expiry_date,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _order(order_id: str = "ORD-1", instrument: str = "ETH-30MAY25-2000-P") -> OrderResult:
    return OrderResult(
        order_id=order_id,
        instrument=instrument,
        direction="sell",
        amount=10000.0,
        price=0.025,
        state="open",
        filled_amount=0.0,
        avg_price=None,
        label=None,
    )


def _mock_broker(*order_ids, name: str = "deribit_paper") -> MagicMock:
    """
    Return a broker mock whose place_order returns OrderResult instances in sequence.
    The instrument in each OrderResult echoes the instrument arg passed to place_order,
    so that _strike_from_instrument() reflects the broker-confirmed (find_instrument-resolved)
    strike rather than a fixed value.
    """
    from access import make_instrument
    broker = MagicMock()
    type(broker).broker_name = property(lambda self: name)
    # find_instrument returns a real instrument name so _place_option can use it.
    broker.find_instrument.side_effect = (
        lambda asset, expiry, strike, opt: make_instrument(asset, expiry, strike, opt)
    )
    order_id_iter = iter(order_ids)

    def _place(instrument, direction, amount, order_type="limit", price=None, label=None, **_kw):
        oid = next(order_id_iter)
        return _order(oid, instrument=instrument)

    broker.place_order.side_effect = _place
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


# ── _next_friday / _expiry_date ──────────────────────────────────────────────

class TestExpiryHelpers:
    def test_next_friday_from_friday(self):
        fri = date(2026, 5, 15)   # a Friday
        assert _next_friday(fri) == fri

    def test_next_friday_from_tuesday(self):
        tue = date(2026, 5, 12)   # a Tuesday
        assert _next_friday(tue) == date(2026, 5, 15)

    def test_next_friday_from_saturday(self):
        sat = date(2026, 5, 16)   # a Saturday
        assert _next_friday(sat) == date(2026, 5, 22)

    def test_expiry_date_always_friday(self):
        for days in [1, 7, 14, 30, 60]:
            result = _expiry_date(days)
            assert result.weekday() == 4, f"days={days} gave {result} ({result.strftime('%A')})"

    def test_expiry_date_not_in_past(self):
        from datetime import date as _date
        assert _expiry_date(7) >= _date.today()


# ── _order_price / _broker_amount ─────────────────────────────────────────────

class TestHelpers:
    def test_order_price_inverse(self):
        # $50 option on a $2000 spot → 0.025 index price for BTC/ETH
        assert _order_price("ETH", 50.0, 2000.0) == pytest.approx(0.025, rel=1e-4)
        assert _order_price("BTC", 500.0, 100000.0) == pytest.approx(0.005, rel=1e-4)

    def test_order_price_inverse_tick_size(self):
        # Price must be rounded to 4 decimal places (Deribit tick = 0.0001)
        result = _order_price("ETH", 44.342, 2000.0)
        assert result == round(44.342 / 2000.0, 4)
        assert len(str(result).split(".")[-1]) <= 4

    def test_order_price_linear(self):
        # Linear USDC contracts: price is the USD value directly, not divided by spot
        assert _order_price("SOL", 5.0, 150.0) == pytest.approx(5.0, rel=1e-4)
        assert _order_price("XRP", 0.10, 2.5) == pytest.approx(0.10, rel=1e-4)

    def test_order_price_linear_tick_size(self):
        result = _order_price("SOL", 5.12345, 150.0)
        assert result == round(5.12345, 4)

    def test_broker_amount_inverse_is_coin_units(self):
        """BTC/ETH options: amount must be in coin units (BTC/ETH), not USD notional."""
        from config import BUDGET_USD
        result_eth = _broker_amount("ETH", 2000.0)
        result_btc = _broker_amount("BTC", 50000.0)
        assert result_eth == pytest.approx(BUDGET_USD / 2000.0, rel=1e-4)
        assert result_btc == pytest.approx(BUDGET_USD / 50000.0, rel=1e-4)
        # Must NOT equal the raw USD budget (old wrong behaviour)
        assert result_btc != int(BUDGET_USD)

    def test_broker_amount_linear_is_integer(self):
        from config import BUDGET_USD
        result = _broker_amount("SOL", 200.0)
        assert result == int(BUDGET_USD / 200.0)
        assert isinstance(result, int)

    def test_broker_amount_linear_minimum_one(self):
        result = _broker_amount("SOL", 10_000.0)
        assert result == 1
        assert isinstance(result, int)


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

    def test_broker_name_in_db_record(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-BN", name="deribit_paper")
        c = _candidate(strategy="CSP", strike="$2000")
        enter_trade(c, broker=broker)
        assert mock_create.call_args.kwargs["broker"] == "deribit_paper"

    def test_broker_name_in_state(self, mock_bs, mock_load, mock_create, mock_save):
        broker = _mock_broker("PUT-ORD-ST", name="deribit_paper")
        c = _candidate(strategy="CSP", strike="$2000")
        enter_trade(c, broker=broker)
        saved_state = mock_save.call_args.args[1]
        assert saved_state["broker"] == "deribit_paper"

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

    def test_broker_confirmed_strike_recorded(self, mock_bs, mock_load, mock_create, mock_save):
        """Strike saved to DB and state must be the broker-confirmed one from order.instrument."""
        from access import make_instrument
        from datetime import date as _date
        broker = _mock_broker("PUT-ORD-6")
        # Broker snaps $1950 request to the nearest listed strike (1900 on Deribit).
        snapped_instrument = "ETH-30MAY25-1900-P"
        broker.find_instrument.side_effect = lambda *a, **kw: snapped_instrument

        c = _candidate(strategy="CSP", strike="$1950")
        result = enter_trade(c, broker=broker)

        assert result["strike"] == 1900.0
        assert mock_create.call_args.kwargs["strike"] == 1900.0
        saved_state = mock_save.call_args.args[1]
        assert saved_state["open"]["strike"] == 1900.0


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

    def test_broker_name_in_db_and_state(self, mock_bp, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("STR-P-3", "STR-C-3", name="deribit_paper")
        c = _candidate(strategy="Strangle", put_strike=1800.0, call_strike=2200.0)
        enter_trade(c, broker=broker)
        assert mock_create.call_args.kwargs["broker"] == "deribit_paper"
        saved_state = mock_save.call_args.args[1]
        assert saved_state["broker"] == "deribit_paper"


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

    def test_broker_name_in_db_and_state(self, mock_bc, mock_load, mock_create, mock_save):
        broker = _mock_broker("CAL-N-BN", "CAL-F-BN", name="deribit_paper")
        c = _candidate(strategy="Cal-C", strike="$2000", far_days=60)
        enter_trade(c, broker=broker)
        assert mock_create.call_args.kwargs["broker"] == "deribit_paper"
        saved_state = mock_save.call_args.args[1]
        assert saved_state["broker"] == "deribit_paper"

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

    def test_btc_budget_too_small_raises_before_api_call(self):
        """With a $250 budget and BTC at $79,000, the minimum contract (~0.1 BTC)
        costs far more than the budget — enter_trade must raise early, not hit the API."""
        c = _candidate(asset="BTC", spot=79_000.0, strategy="Cal-C",
                       days=7, far_days=30, strike="$79000 ATM", otm_pct=0.0,
                       premium=10.0, yield_ann=50.0)
        broker = _mock_broker()
        with pytest.raises(ValueError, match="too small"):
            enter_trade(c, broker=broker)
        broker.place_order.assert_not_called()

    def test_eth_budget_too_small_raises_before_api_call(self):
        """ETH at $4,000 → minimum 0.1 ETH = $400 > $250 budget."""
        c = _candidate(asset="ETH", spot=4_000.0, strategy="CSP",
                       days=7, strike="$3600", otm_pct=0.1,
                       premium=10.0, yield_ann=50.0)
        broker = _mock_broker()
        with pytest.raises(ValueError, match="too small"):
            enter_trade(c, broker=broker)
        broker.place_order.assert_not_called()

    def test_eth_affordable_does_not_raise(self):
        """ETH at $1,000 → 0.25 ETH ≥ 0.1 ETH minimum — budget check passes."""
        from unittest.mock import patch
        c = _candidate(asset="ETH", spot=1_000.0, strategy="CSP",
                       days=7, strike="$900", otm_pct=0.1,
                       premium=5.0, yield_ann=50.0)
        broker = _mock_broker("ORD-X")
        # Patch all DB/state calls so only the budget check matters
        with patch("trading.executor.load_wheel_state", return_value={"stage": None}), \
             patch("trading.executor.save_wheel_state"), \
             patch("trading.executor.create_single_trade"):
            # Should not raise ValueError about budget
            try:
                enter_trade(c, broker=broker)
            except (KeyError, TypeError):
                pass  # state dict errors are fine — we only care the budget check passed
