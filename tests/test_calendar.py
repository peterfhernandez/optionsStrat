"""
tests/test_calendar.py
======================
Tests for strategies/calendar.py — P&L helpers, breakeven finder,
status checker (pure functions, no I/O), and executor delegation.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _spread_value        : call/put, correct sign, scales with qty
    _pnl_at_near_expiry  : spot at strike (max), spot far from strike (loss),
                           near/far day relationships
    _find_breakevens     : returns two prices, be_lo < strike < be_hi,
                           wider debit → narrower profit zone
    check_calendar_status: ok / warn / stop / tp status thresholds

Tier 2 — executor delegation:
    calendar_paper_menu [1] calls enter_trade with Cal-C / Cal-P candidate
"""

from unittest.mock import MagicMock, patch

import pytest

from strategies.calendar import (
    _spread_value,
    _pnl_at_near_expiry,
    _find_breakevens,
    check_calendar_status,
    handle_far_leg_only_menu,
)
from config import CALENDAR_STOP_PCT, RISK_FREE_RATE


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def spot():
    return 2000.0

@pytest.fixture
def iv():
    return 0.80

@pytest.fixture
def r():
    return RISK_FREE_RATE

@pytest.fixture
def strike_atm(spot):
    return 2000.0

@pytest.fixture
def qty(spot):
    from config import BUDGET_USD
    return BUDGET_USD / spot  # 0.125

@pytest.fixture
def near_days():
    return 7

@pytest.fixture
def far_days():
    return 30

@pytest.fixture
def T_near(near_days):
    return near_days / 365.0

@pytest.fixture
def T_far(far_days):
    return far_days / 365.0

@pytest.fixture
def open_position(strike_atm, qty, near_days, far_days):
    """A standard open ATM call calendar position dict."""
    from config import BUDGET_USD
    from market.pricing import bs_call
    spot = 2000.0
    iv   = 0.80
    T_n  = near_days / 365.0
    T_f  = far_days  / 365.0
    near_prem = bs_call(spot, strike_atm, T_n, RISK_FREE_RATE, iv) * qty
    far_prem  = bs_call(spot, strike_atm, T_f, RISK_FREE_RATE, iv) * qty
    net_debit = far_prem - near_prem
    return {
        "strike":      strike_atm,
        "option_type": "Call",
        "near_prem":   near_prem,
        "far_prem":    far_prem,
        "net_debit":   net_debit,
        "qty":         qty,
        "expiry_near": "01-May-2026",
        "expiry_far":  "25-May-2026",
        "spot_open":   spot,
        "near_days":   near_days,
        "far_days":    far_days,
        "asset":       "ETH",
    }


# ── _spread_value ─────────────────────────────────────────────────────────────

class TestSpreadValue:

    def test_call_far_greater_than_near(self, spot, strike_atm, T_near, T_far, r, iv, qty):
        """Far leg always worth more than near leg at same spot → positive spread."""
        sv = _spread_value(spot, strike_atm, T_near, T_far, r, iv, qty, "Call")
        assert sv > 0

    def test_put_far_greater_than_near(self, spot, strike_atm, T_near, T_far, r, iv, qty):
        sv = _spread_value(spot, strike_atm, T_near, T_far, r, iv, qty, "Put")
        assert sv > 0

    def test_spread_scales_with_qty(self, spot, strike_atm, T_near, T_far, r, iv):
        sv1 = _spread_value(spot, strike_atm, T_near, T_far, r, iv, 0.10, "Call")
        sv2 = _spread_value(spot, strike_atm, T_near, T_far, r, iv, 0.20, "Call")
        assert sv2 == pytest.approx(sv1 * 2, rel=1e-6)

    def test_equal_T_gives_zero_spread(self, spot, strike_atm, T_near, r, iv, qty):
        """Same T for both legs → spread = 0."""
        sv = _spread_value(spot, strike_atm, T_near, T_near, r, iv, qty, "Call")
        assert sv == pytest.approx(0.0, abs=1e-8)

    def test_returns_float(self, spot, strike_atm, T_near, T_far, r, iv, qty):
        result = _spread_value(spot, strike_atm, T_near, T_far, r, iv, qty, "Call")
        assert isinstance(result, float)


# ── _pnl_at_near_expiry ───────────────────────────────────────────────────────

class TestPnlAtNearExpiry:

    def test_spot_at_strike_is_best_case(self, spot, strike_atm, near_days, far_days, r, iv, qty):
        """Spot pinning the strike at near expiry → near expires worthless → max P&L."""
        from market.pricing import bs_call
        T_rem = max(far_days - near_days, 1) / 365.0
        T_near = near_days / 365.0
        T_far  = far_days  / 365.0
        near_prem = bs_call(spot, strike_atm, T_near, r, iv) * qty
        far_prem  = bs_call(spot, strike_atm, T_far,  r, iv) * qty
        net_debit = far_prem - near_prem

        pnl_at_K = _pnl_at_near_expiry(
            strike_atm, strike_atm, near_days, far_days, r, iv, qty, net_debit, "Call"
        )
        # P&L = far_remaining - near_intrinsic(=0) - net_debit
        expected = bs_call(strike_atm, strike_atm, T_rem, r, iv) * qty - net_debit
        assert pnl_at_K == pytest.approx(expected, rel=1e-5)

    def test_large_move_up_causes_loss_call(self, spot, near_days, far_days, r, iv, qty):
        """Call calendar: spot far above strike → near exercised, loss."""
        K = spot   # ATM
        T_near = near_days / 365.0
        T_far  = far_days  / 365.0
        from market.pricing import bs_call
        net_debit = (bs_call(spot, K, T_far, r, iv) - bs_call(spot, K, T_near, r, iv)) * qty
        spot_high = spot * 1.40
        pnl = _pnl_at_near_expiry(spot_high, K, near_days, far_days, r, iv, qty, net_debit, "Call")
        assert pnl < 0

    def test_large_move_down_causes_loss_put(self, spot, near_days, far_days, r, iv, qty):
        """Put calendar: spot far below strike → near exercised, loss."""
        K = spot
        T_near = near_days / 365.0
        T_far  = far_days  / 365.0
        from market.pricing import bs_put
        net_debit = (bs_put(spot, K, T_far, r, iv) - bs_put(spot, K, T_near, r, iv)) * qty
        spot_low = spot * 0.60
        pnl = _pnl_at_near_expiry(spot_low, K, near_days, far_days, r, iv, qty, net_debit, "Put")
        assert pnl < 0

    def test_max_loss_bounded_by_net_debit(self, spot, near_days, far_days, r, iv, qty):
        """At extreme spot, loss approaches but does not exceed net debit + far leg residual."""
        K = spot
        T_near = near_days / 365.0
        T_far  = far_days  / 365.0
        from market.pricing import bs_call
        net_debit = (bs_call(spot, K, T_far, r, iv) - bs_call(spot, K, T_near, r, iv)) * qty
        # At spot = 0 (extreme down for call), far call worthless, near worthless → loss = debit
        pnl_zero = _pnl_at_near_expiry(1.0, K, near_days, far_days, r, iv, qty, net_debit, "Call")
        # Loss should not exceed net_debit by more than a small amount (far has residual value)
        assert pnl_zero >= -net_debit - 0.01  # near cost = 0; far has tiny value

    def test_returns_float(self, spot, strike_atm, near_days, far_days, r, iv, qty, open_position):
        net_debit = open_position["net_debit"]
        result = _pnl_at_near_expiry(spot, strike_atm, near_days, far_days, r, iv, qty, net_debit, "Call")
        assert isinstance(result, float)


# ── _find_breakevens ──────────────────────────────────────────────────────────

class TestFindBreakevens:

    def _make_debit(self, spot, strike, near_days, far_days, r, iv, qty, option_type):
        from market.pricing import bs_call, bs_put
        T_near = near_days / 365.0
        T_far  = far_days  / 365.0
        if option_type == "Call":
            return (bs_call(spot, strike, T_far, r, iv) - bs_call(spot, strike, T_near, r, iv)) * qty
        return (bs_put(spot, strike, T_far, r, iv) - bs_put(spot, strike, T_near, r, iv)) * qty

    def test_call_returns_two_breakevens(self, spot, near_days, far_days, r, iv, qty):
        K = spot
        nd = self._make_debit(spot, K, near_days, far_days, r, iv, qty, "Call")
        be_lo, be_hi = _find_breakevens(spot, K, near_days, far_days, r, iv, qty, nd, "Call")
        assert be_lo > 0
        assert be_hi > 0

    def test_put_returns_two_breakevens(self, spot, near_days, far_days, r, iv, qty):
        K = spot
        nd = self._make_debit(spot, K, near_days, far_days, r, iv, qty, "Put")
        be_lo, be_hi = _find_breakevens(spot, K, near_days, far_days, r, iv, qty, nd, "Put")
        assert be_lo > 0
        assert be_hi > 0

    def test_strike_inside_profit_zone(self, spot, near_days, far_days, r, iv, qty):
        """The strike is always inside the breakeven range (profit at K)."""
        K = spot
        nd = self._make_debit(spot, K, near_days, far_days, r, iv, qty, "Call")
        be_lo, be_hi = _find_breakevens(spot, K, near_days, far_days, r, iv, qty, nd, "Call")
        assert be_lo < K < be_hi

    def test_be_lo_less_than_be_hi(self, spot, near_days, far_days, r, iv, qty):
        K = spot
        nd = self._make_debit(spot, K, near_days, far_days, r, iv, qty, "Call")
        be_lo, be_hi = _find_breakevens(spot, K, near_days, far_days, r, iv, qty, nd, "Call")
        assert be_lo < be_hi

    def test_call_put_symmetric_atm(self, spot, near_days, far_days, r, iv, qty):
        """ATM call and put calendars should have symmetric breakeven widths."""
        K = spot
        nd_c = self._make_debit(spot, K, near_days, far_days, r, iv, qty, "Call")
        nd_p = self._make_debit(spot, K, near_days, far_days, r, iv, qty, "Put")
        bec_lo, bec_hi = _find_breakevens(spot, K, near_days, far_days, r, iv, qty, nd_c, "Call")
        bep_lo, bep_hi = _find_breakevens(spot, K, near_days, far_days, r, iv, qty, nd_p, "Put")
        # Widths should be approximately equal at ATM due to put-call parity
        width_c = bec_hi - bec_lo
        width_p = bep_hi - bep_lo
        assert width_c == pytest.approx(width_p, rel=0.10)   # within 10%


# ── check_calendar_status ─────────────────────────────────────────────────────

class TestCheckCalendarStatus:

    def _patch_spread(self, sv, op):
        """Patch _spread_value to return sv for a given op."""
        return patch("strategies.calendar._spread_value", return_value=sv)

    def test_returns_tuple_of_four(self, open_position, spot, iv):
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd):
            result = check_calendar_status(spot, iv, 7, 28, open_position)
        assert len(result) == 4

    def test_ok_status_at_full_debit(self, open_position, spot, iv):
        """spread_value = net_debit → 100% of debit → status 'ok'."""
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd):
            status, sv, pct, msg = check_calendar_status(spot, iv, 7, 28, open_position)
        assert status == "ok"
        assert pct == pytest.approx(1.0, rel=1e-3)

    def test_warn_status_below_70pct(self, open_position, spot, iv):
        """spread_value = 65% of debit → status 'warn'."""
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd * 0.65):
            status, _, pct, _ = check_calendar_status(spot, iv, 7, 28, open_position)
        assert status == "warn"
        assert pct == pytest.approx(0.65, rel=1e-3)

    def test_stop_status_at_stop_threshold(self, open_position, spot, iv):
        """spread_value = exactly CALENDAR_STOP_PCT of debit → status 'stop'."""
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd * CALENDAR_STOP_PCT):
            status, _, pct, _ = check_calendar_status(spot, iv, 7, 28, open_position)
        assert status == "stop"

    def test_tp_status_at_150pct(self, open_position, spot, iv):
        """spread_value = 150% of debit → status 'tp'."""
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd * 1.50):
            status, _, pct, _ = check_calendar_status(spot, iv, 7, 28, open_position)
        assert status == "tp"

    def test_stop_takes_priority_over_warn(self, open_position, spot, iv):
        """Below stop threshold → 'stop', not 'warn'."""
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd * 0.30):
            status, _, _, _ = check_calendar_status(spot, iv, 7, 28, open_position)
        assert status == "stop"

    def test_pct_calculation(self, open_position, spot, iv):
        """pct = sv / net_debit."""
        nd = open_position["net_debit"]
        target_sv = nd * 1.20
        with patch("strategies.calendar._spread_value", return_value=target_sv):
            _, sv, pct, _ = check_calendar_status(spot, iv, 7, 28, open_position)
        assert sv  == pytest.approx(target_sv, rel=1e-6)
        assert pct == pytest.approx(1.20, rel=1e-3)

    def test_zero_debit_guard(self, open_position, spot, iv):
        """With net_debit=0, pct=0 — no ZeroDivisionError."""
        op = dict(open_position, net_debit=0.0)
        with patch("strategies.calendar._spread_value", return_value=1.0):
            _, _, pct, _ = check_calendar_status(spot, iv, 7, 28, op)
        assert pct == 0.0

    def test_message_is_string(self, open_position, spot, iv):
        nd = open_position["net_debit"]
        with patch("strategies.calendar._spread_value", return_value=nd):
            _, _, _, msg = check_calendar_status(spot, iv, 7, 28, open_position)
        assert isinstance(msg, str)
        assert len(msg) > 0


# ── Executor delegation ───────────────────────────────────────────────────────

class TestCalendarPaperMenuExecutor:
    """Verify calendar_paper_menu delegates to enter_trade when opening a position."""

    def _run_open_menu(self, option_type_input, mock_enter):
        """Helper: open a calendar position with mocked input."""
        from io import StringIO
        from strategies.calendar import calendar_paper_menu
        from database.calendar_db import save_calendar_state

        open_trade = {
            "strike": 2000.0, "option_type": "Call",
            "near_prem": 10.0, "far_prem": 20.0, "net_debit": 10.0,
            "qty": 0.05, "expiry_near": "10-May-2026",
            "expiry_far": "09-Jun-2026", "spot_open": 2000.0,
            "near_days": 7, "far_days": 30, "asset": "ETH", "trade_id": 1,
        }
        save_calendar_state("ETH", {
            "open": None, "total_pnl": 0.0,
            "wins": 0, "losses": 0, "trades": 0,
        })

        def _fake_enter(c, broker=None):
            save_calendar_state("ETH", {
                "open": open_trade, "total_pnl": 0.0,
                "wins": 0, "losses": 0, "trades": 1,
            })
            return open_trade

        mock_enter.side_effect = _fake_enter

        inputs = iter(["1", option_type_input, ""])  # choice=1, call/put, default strike
        with patch("builtins.input", side_effect=inputs), \
             patch("sys.stdout", new_callable=StringIO), \
             patch("strategies.calendar.draw_calendar_zone"):
            calendar_paper_menu("ETH", spot=2000.0, iv=0.80, near_days=7, far_days=30)

    @patch("strategies.calendar.enter_trade")
    def test_open_call_calls_enter_trade(self, mock_enter):
        """Opening a Call calendar should call enter_trade with Cal-C candidate."""
        self._run_open_menu("1", mock_enter)

        mock_enter.assert_called_once()
        c = mock_enter.call_args[0][0]
        assert c.strategy == "Cal-C"
        assert c.asset == "ETH"
        assert c.spot == 2000.0
        assert c.days == 7
        assert c.far_days == 30

    @patch("strategies.calendar.enter_trade")
    def test_open_put_calls_enter_trade(self, mock_enter):
        """Opening a Put calendar should call enter_trade with Cal-P candidate."""
        self._run_open_menu("2", mock_enter)

        mock_enter.assert_called_once()
        c = mock_enter.call_args[0][0]
        assert c.strategy == "Cal-P"


class TestHandleFarLegOnlyMenu:
    """Tests for handle_far_leg_only_menu — Far Leg Only position handling."""

    @patch("strategies.calendar.load_calendar_state")
    def test_no_open_position_warns(self, mock_load):
        """Should warn if no open position exists."""
        mock_load.return_value = {"open": None}

        with patch("strategies.calendar.warn") as mock_warn:
            handle_far_leg_only_menu("ETH", 2000.0, 0.80)
            mock_warn.assert_called_with("No open position.")

    @patch("strategies.calendar.load_calendar_state")
    def test_not_far_leg_only_status_warns(self, mock_load):
        """Should warn if position status is not Far Leg Only."""
        mock_load.return_value = {
            "open": {
                "status": "Open",
                "strike": 2000.0,
                "asset": "ETH",
            }
        }

        with patch("strategies.calendar.warn") as mock_warn:
            handle_far_leg_only_menu("ETH", 2000.0, 0.80)
            mock_warn.assert_called_with("Position is Open, not Far Leg Only. Use regular close option.")

    @patch("strategies.calendar.load_calendar_state")
    def test_close_far_leg_option(self, mock_load):
        """Selecting [1] should attempt to close the far leg."""
        far_leg_open = {
            "status": "Far Leg Only",
            "strike": 2000.0,
            "option_type": "Call",
            "asset": "ETH",
            "qty": 0.125,
            "net_debit": 10.0,
            "expiry_far": "15-Jun-2026",
            "trade_id": 1,
            "far_instrument": "ETH-15JUN26-2000-C",
        }
        mock_load.return_value = {"open": far_leg_open}

        # Simulate user choice [1]
        inputs = iter(["1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("strategies.calendar_analysis.analyze_calendar_far_leg", return_value=None), \
             patch("strategies.calendar.close_calendar_far_leg") as mock_close_far, \
             patch("strategies.calendar.close_calendar_trade"), \
             patch("strategies.calendar.save_calendar_state"), \
             patch("strategies.calendar.warn"):  # Suppress warnings during test
            handle_far_leg_only_menu("ETH", 2000.0, 0.80)
            # Should attempt to close far leg (even if analysis is None)
            mock_close_far.assert_called_once()

    @patch("strategies.calendar.load_calendar_state")
    def test_keep_position_open_option(self, mock_load):
        """Selecting [2] should keep position open."""
        far_leg_open = {
            "status": "Far Leg Only",
            "strike": 2000.0,
            "option_type": "Put",
            "asset": "ETH",
            "qty": 0.125,
            "net_debit": 10.0,
            "expiry_far": "15-Jun-2026",
            "far_instrument": "ETH-15JUN26-2000-P",
        }
        mock_load.return_value = {"open": far_leg_open}

        # Simulate user choice [2]
        inputs = iter(["2"])
        with patch("builtins.input", side_effect=inputs), \
             patch("strategies.calendar_analysis.analyze_calendar_far_leg", return_value=None), \
             patch("strategies.calendar.ok") as mock_ok:
            handle_far_leg_only_menu("ETH", 2000.0, 0.80)
            mock_ok.assert_called_with("Position kept open for continued monitoring.")

    @patch("strategies.calendar.load_calendar_state")
    @patch("strategies.calendar.roll_near_leg")
    def test_roll_near_leg_option(self, mock_roll, mock_load):
        """Selecting [3] then [1] should roll near leg with 1d expiry."""
        far_leg_open = {
            "status": "Far Leg Only",
            "strike": 2000.0,
            "option_type": "Call",
            "asset": "ETH",
            "qty": 0.125,
            "net_debit": 10.0,
            "expiry_far": "15-Jun-2026",
            "far_prem": 125.0,
            "far_days": 30,
            "trade_id": 1,
            "far_instrument": "ETH-15JUN26-2000-C",
        }
        mock_load.return_value = {"open": far_leg_open}

        # Mock the order result
        mock_order = MagicMock()
        mock_order.order_id = "ROLL-ORDER-1"
        mock_roll.return_value = mock_order

        # Simulate user choice [3] (roll), then [1] (1d near leg), then submit
        inputs = iter(["3", "1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("strategies.calendar_analysis.analyze_calendar_far_leg", return_value=None), \
             patch("strategies.calendar.ok") as mock_ok:
            handle_far_leg_only_menu("ETH", 2000.0, 0.80)
            # Should call roll_near_leg with 1 day
            mock_roll.assert_called_once()
            call_args = mock_roll.call_args[0]
            assert call_args[1] == 1  # days parameter should be 1

    @patch("strategies.calendar.load_calendar_state")
    @patch("strategies.calendar.roll_near_leg")
    def test_roll_near_leg_3d_option(self, mock_roll, mock_load):
        """Rolling with [2] should use 3d expiry."""
        far_leg_open = {
            "status": "Far Leg Only",
            "strike": 2000.0,
            "option_type": "Put",
            "asset": "BTC",
            "qty": 0.125,
            "net_debit": 10.0,
            "expiry_far": "15-Jun-2026",
            "far_prem": 125.0,
            "far_days": 30,
            "trade_id": 2,
            "far_instrument": "BTC-15JUN26-2000-P",
        }
        mock_load.return_value = {"open": far_leg_open}

        mock_order = MagicMock()
        mock_order.order_id = "ROLL-ORDER-2"
        mock_roll.return_value = mock_order

        inputs = iter(["3", "2"])
        with patch("builtins.input", side_effect=inputs), \
             patch("strategies.calendar_analysis.analyze_calendar_far_leg", return_value=None), \
             patch("strategies.calendar.ok"):
            handle_far_leg_only_menu("BTC", 40000.0, 0.75)
            call_args = mock_roll.call_args[0]
            assert call_args[1] == 3  # days parameter should be 3

    @patch("strategies.calendar.load_calendar_state")
    @patch("strategies.calendar.roll_near_leg")
    def test_roll_near_leg_7d_option(self, mock_roll, mock_load):
        """Rolling with [3] should use 7d expiry."""
        far_leg_open = {
            "status": "Far Leg Only",
            "strike": 150.0,
            "option_type": "Call",
            "asset": "SOL",
            "qty": 1.0,
            "net_debit": 5.0,
            "expiry_far": "15-Jun-2026",
            "far_prem": 45.0,
            "far_days": 30,
            "trade_id": 3,
            "far_instrument": "SOL_USDC-15JUN26-150-C",
        }
        mock_load.return_value = {"open": far_leg_open}

        mock_order = MagicMock()
        mock_order.order_id = "ROLL-ORDER-3"
        mock_roll.return_value = mock_order

        inputs = iter(["3", "3"])
        with patch("builtins.input", side_effect=inputs), \
             patch("strategies.calendar_analysis.analyze_calendar_far_leg", return_value=None), \
             patch("strategies.calendar.ok"):
            handle_far_leg_only_menu("SOL", 150.0, 0.85)
            call_args = mock_roll.call_args[0]
            assert call_args[1] == 7  # days parameter should be 7


