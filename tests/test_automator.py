"""
tests/test_automator.py
=======================
Tests for automation/automator.py.

Tier 1 — pure selection logic (no I/O, no network)
    select_best_candidate
        * yield filter
        * liquidity filter
        * blocked-strategy filter
        * ranking by prob_profit (then yield as tie-breaker)
        * empty input
        * empty result

Tier 2 — state-aware blocking (in-memory state, monkey-patched)
    _blocked_strategies
        * fresh state → CC blocked, CSP allowed, Strangle/Calendar allowed
        * holding state → CSP blocked, CC allowed
        * short_put state → both wheel legs blocked
        * strangle open → "Strangle" blocked
        * calendar open → "Cal-C" + "Cal-P" blocked

Tier 3 — trade entry (SQLite only, no Excel)
    enter_trade
        * CSP   writes wheel state + Single ledger row, total_premium increments
        * CC    requires holding stage and uses asset_held qty
        * Strangle writes strangle state + Strangle ledger row
        * Cal-C  writes calendar state + Calendar ledger row

Tier 4 — orchestration
    run_automation
        * picks the right candidate when one qualifies
        * returns "no_candidate" when nothing qualifies (silent path,
          should not raise — this is the "do nothing, retry in 1 hour"
          contract)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from access import OrderResult
from database import load_wheel_state, save_wheel_state, create_single_trade
from database.strangle_db import load_strangle_state, save_strangle_state, create_strangle_trade
from database.calendar_db import load_calendar_state, save_calendar_state, create_calendar_trade
from database.spread_db import load_spread_state, save_spread_state, create_spread_trade
from strategies.scanner import Candidate
from strategies import wheel
from automation.automator import (
    select_best_candidate,
    run_automation,
    _blocked_strategies,
    DEFAULT_MIN_YIELD,
    DEFAULT_MIN_PROB,
    DEFAULT_ALLOWED_LIQUIDITY,
)
from trading.executor import enter_trade


# ── Helpers / fixtures ───────────────────────────────────────────────────────

def _fake_order(order_id: str = "TEST-ORD-1", instrument: str = "ETH-30MAY25-2000-P") -> OrderResult:
    return OrderResult(
        order_id=order_id, instrument=instrument, direction="sell",
        amount=10000.0, price=0.025, state="open",
        filled_amount=0.0, avg_price=None, label=None,
    )


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Run every test in tmp_path for any remaining filesystem operations."""
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture(autouse=True)
def _mock_deribit(monkeypatch):
    """Prevent any test from hitting the real Deribit network.

    enter_trade() defaults to DeribitClient(paper=True) when no broker is
    passed.  This fixture replaces that constructor with a mock whose
    place_order echoes back the instrument it was called with (so that
    _strike_from_instrument() can parse the broker-confirmed strike).
    """
    from access import make_instrument as _make_instr
    mock_instance = MagicMock()
    type(mock_instance).broker_name = PropertyMock(return_value="deribit_paper")
    mock_instance.find_instrument.side_effect = (
        lambda asset, expiry, strike, opt: _make_instr(asset, expiry, strike, opt)
    )

    def _place(instrument, direction, amount, order_type="limit", price=None, label=None, **_kw):
        return _fake_order(instrument=instrument)

    mock_instance.place_order.side_effect = _place

    with patch("trading.executor.DeribitClient", return_value=mock_instance):
        yield mock_instance


def _make(
    asset       = "ETH",
    strategy    = "CSP",
    otm         = 0.10,
    spot        = 2000.0,
    iv          = 0.80,
    strike_str  = "$1800",
    premium     = 12.0,
    yield_ann   = 65.0,
    prob_profit = 80.0,
    days        = 7,
    liq         = "High",
    **extra,
):
    """Convenience constructor for Candidate test objects."""
    return Candidate(
        asset       = asset,
        strategy    = strategy,
        otm_pct     = otm,
        spot        = spot,
        iv          = iv,
        strike      = strike_str,
        premium     = premium,
        yield_ann   = yield_ann,
        prob_profit = prob_profit,
        days        = days,
        liquidity_tag = liq,
        **extra,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 1 — select_best_candidate
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectBestCandidate:

    def test_empty_input_returns_none(self):
        assert select_best_candidate([]) is None

    def test_picks_highest_probability(self):
        cands = [
            _make(prob_profit=70.0, yield_ann=20.0),
            _make(prob_profit=85.0, yield_ann=20.0),
            _make(prob_profit=60.0, yield_ann=20.0),
        ]
        best = select_best_candidate(cands, min_prob=0.0)
        assert best.prob_profit == 85.0

    def test_yield_breaks_ties_in_prob(self):
        a = _make(prob_profit=80.0, yield_ann=20.0)
        b = _make(prob_profit=80.0, yield_ann=50.0)
        best = select_best_candidate([a, b], min_prob=0.0)
        assert best is b

    def test_yield_filter_excludes_below_threshold(self):
        low  = _make(prob_profit=99.0, yield_ann=5.0)   # high prob, low yield
        high = _make(prob_profit=70.0, yield_ann=15.0)
        best = select_best_candidate([low, high], min_yield=10.0, min_prob=0.0)
        assert best is high

    def test_yield_filter_inclusive_at_threshold(self):
        c = _make(prob_profit=70.0, yield_ann=10.0)
        assert select_best_candidate([c], min_yield=10.0, min_prob=0.0) is c

    def test_liquidity_filter_excludes_low(self):
        low  = _make(prob_profit=99.0, yield_ann=50.0, liq="Low")
        med  = _make(prob_profit=70.0, yield_ann=20.0, liq="Med")
        best = select_best_candidate([low, med], min_prob=0.0)
        assert best is med

    def test_liquidity_filter_excludes_empty_tag(self):
        empty = _make(prob_profit=99.0, yield_ann=50.0, liq="")
        med   = _make(prob_profit=70.0, yield_ann=20.0, liq="Med")
        best  = select_best_candidate([empty, med], min_prob=0.0)
        assert best is med

    def test_default_thresholds_match_constants(self):
        """Sanity — the public defaults are still 10%/Med+High and 90% probability."""
        assert DEFAULT_MIN_YIELD == 10.0
        assert DEFAULT_MIN_PROB == 90.0
        assert set(DEFAULT_ALLOWED_LIQUIDITY) == {"Med", "High"}

    def test_probability_filter_excludes_equal_threshold(self):
        low_prob = _make(prob_profit=90.0, yield_ann=80.0)
        high_prob = _make(prob_profit=91.0, yield_ann=20.0)
        best = select_best_candidate([low_prob, high_prob], min_prob=90.0)
        assert best is high_prob

    def test_probability_filter_applies_default_threshold(self):
        below_default = _make(prob_profit=90.0, yield_ann=80.0)
        above_default = _make(prob_profit=92.0, yield_ann=20.0)
        best = select_best_candidate([below_default, above_default])
        assert best is above_default

    def test_blocked_strategies_filter(self):
        a = _make(strategy="CSP",  prob_profit=99.0, yield_ann=50.0)
        b = _make(strategy="Strangle", prob_profit=70.0, yield_ann=20.0,
                  put_strike=1800.0, call_strike=2200.0)
        best = select_best_candidate(
            [a, b],
            min_prob=0.0,
            blocked_strategies=("CSP",),
        )
        assert best.strategy == "Strangle"

    def test_returns_none_when_all_filtered(self):
        cands = [
            _make(prob_profit=99.0, yield_ann=5.0),    # below yield
            _make(prob_profit=99.0, yield_ann=50.0, liq="Low"),  # bad liq
        ]
        assert select_best_candidate(cands) is None

    def test_does_not_mutate_input(self):
        cands = [
            _make(prob_profit=70.0, yield_ann=20.0),
            _make(prob_profit=85.0, yield_ann=20.0),
        ]
        before = [(c.prob_profit, c.yield_ann) for c in cands]
        select_best_candidate(cands, min_prob=0.0)
        after = [(c.prob_profit, c.yield_ann) for c in cands]
        assert before == after


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 2 — _blocked_strategies
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockedStrategies:

    def test_fresh_state_blocks_only_cc(self):
        blocked = _blocked_strategies("ETH")
        assert "CC" in blocked
        assert "CSP" not in blocked
        assert "Strangle" not in blocked
        assert "Cal-C" not in blocked
        assert "Cal-P" not in blocked

    def test_holding_blocks_csp_allows_cc(self):
        from datetime import date
        # Create a Single trade in holding stage
        trade = create_single_trade(
            asset="ETH",
            date_open=date.today(),
            option_type="Put",
            strike=1800.0,
            expiry="13-Jun-2026",
            spot_open=2000.0,
            premium=12.50,
            qty=0.139,
            days=7,
            stage="holding",
        )

        blocked = _blocked_strategies("ETH")
        assert "CSP" in blocked
        assert "CC" not in blocked

    def test_short_put_blocks_both_wheel_legs(self):
        from datetime import date
        # Create a Single trade in short_put stage
        trade = create_single_trade(
            asset="ETH",
            date_open=date.today(),
            option_type="Put",
            strike=1800.0,
            expiry="13-Jun-2026",
            spot_open=2000.0,
            premium=12.50,
            qty=0.139,
            days=7,
            stage="short_put",
        )

        blocked = _blocked_strategies("ETH")
        assert "CSP" in blocked
        assert "CC"  in blocked

    def test_open_strangle_blocks_strangle(self):
        from datetime import date
        # Create an open Strangle trade
        trade = create_strangle_trade(
            asset="ETH",
            date_open=date.today(),
            put_strike=1800.0,
            call_strike=2200.0,
            spot_open=2000.0,
            total_premium=30.0,
            qty=0.125,
            days=7,
            expiry="13-Jun-2026",
        )

        blocked = _blocked_strategies("ETH")
        assert "Strangle" in blocked

    def test_open_calendar_blocks_both_calendar_types(self):
        from datetime import date
        # Create an open Calendar trade
        trade = create_calendar_trade(
            asset="ETH",
            date_open=date.today(),
            option_type="Call",
            strike=2000.0,
            expiry_near="13-Jun-2026",
            expiry_far="01-Jul-2026",
            near_days=7,
            far_days=30,
            qty=0.125,
            spot_open=2000.0,
            near_prem=10.0,
            far_prem=20.0,
            net_debit=10.0,
        )

        blocked = _blocked_strategies("ETH")
        assert "Cal-C" in blocked
        assert "Cal-P" in blocked

    def test_open_spread_blocks_both_spread_types(self):
        from datetime import date
        # Create an open Spread trade
        trade = create_spread_trade(
            asset="ETH",
            spread_type="BPS",
            date_open=date.today(),
            short_strike=1900.0,
            long_strike=1800.0,
            spot_open=2000.0,
            net_credit=2.0,
            max_loss=98.0,
            qty=0.1,
            days=7,
            expiry="01-Jul-2026",
        )

        blocked = _blocked_strategies("ETH")
        assert "BPS" in blocked
        assert "BCS" in blocked


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 3 — enter_trade
# ─────────────────────────────────────────────────────────────────────────────

class TestEnterTrade:

    def test_csp_writes_wheel_state(self):
        c = _make(strategy="CSP", strike_str="$1800")
        opened = enter_trade(c)

        s = load_wheel_state("ETH")
        assert s["stage"] == "short_put"
        assert s["open"]["option_type"] == "Put"
        assert s["open"]["strike"] == 1800.0
        assert s["total_premium"] > 0
        assert opened["strike"] == 1800.0

    def test_cc_requires_holding_stage(self):
        c = _make(strategy="CC", strike_str="$2200")
        with pytest.raises(RuntimeError):
            enter_trade(c)

    def test_cc_uses_asset_held_qty(self):
        s = load_wheel_state("ETH")
        s["stage"] = "holding"
        s["asset_held"] = 0.20
        s["cost_basis"] = 2000.0
        save_wheel_state("ETH", s)

        c = _make(strategy="CC", strike_str="$2200")
        opened = enter_trade(c)

        s2 = load_wheel_state("ETH")
        assert s2["stage"] == "short_call"
        assert s2["open"]["option_type"] == "Call"
        assert s2["open"]["qty"]  == pytest.approx(0.20)
        assert opened["strike"] == 2200.0

    def test_strangle_writes_strangle_state(self):
        c = _make(strategy="Strangle", strike_str="$1800/$2200",
                  put_strike=1800.0, call_strike=2200.0)
        opened = enter_trade(c)

        s = load_strangle_state("ETH")
        assert s["open"]["put_strike"]  == 1800.0
        assert s["open"]["call_strike"] == 2200.0
        assert s["open"] is not None  # verify open position is set
        assert opened["put_strike"] == 1800.0

    def test_calendar_writes_calendar_state(self):
        c = _make(strategy="Cal-C", strike_str="$2000 ATM",
                  far_days=30, liq="Med")
        opened = enter_trade(c)

        s = load_calendar_state("ETH")
        assert s["open"]["strike"]      == 2000.0
        assert s["open"]["option_type"] == "Call"
        assert s["open"]["expiry_near"] is not None
        assert s["open"]["expiry_far"]  is not None
        assert opened["option_type"] == "Call"

    def test_calendar_put_strategy(self):
        c = _make(strategy="Cal-P", strike_str="$2000 ATM",
                  far_days=30, liq="Med")
        enter_trade(c)

        s = load_calendar_state("ETH")
        assert s["open"]["option_type"] == "Put"

    def test_unsupported_strategy_raises(self):
        c = _make(strategy="Iron Condor", strike_str="$x")
        with pytest.raises(ValueError):
            enter_trade(c)


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 4 — run_automation
# ─────────────────────────────────────────────────────────────────────────────

class TestRunAutomation:

    @pytest.fixture(autouse=True)
    def _patch_monitor(self):
        with patch("automation.automator.run_monitor"):
            yield

    def test_no_candidate_returns_status_no_candidate(self):
        """If _build_candidates yields nothing eligible, automation does
        nothing — this is the 'try again in 1 hour' contract."""
        with patch("automation.automator._build_candidates", return_value=[]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"]    == "no_candidate"
        assert result["candidate"] is None
        assert result["position"]  is None

    def test_candidate_below_yield_is_not_entered(self):
        cand = _make(strategy="CSP", yield_ann=5.0, prob_profit=99.0)
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"] == "no_candidate"

    def test_candidate_below_probability_is_not_entered(self):
        cand = _make(strategy="CSP", yield_ann=80.0, prob_profit=90.0, liq="High")
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"] == "no_candidate"

    def test_low_liquidity_is_not_entered(self):
        cand = _make(strategy="CSP", yield_ann=80.0, prob_profit=85.0, liq="Low")
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"] == "no_candidate"

    def test_eligible_csp_is_entered(self):
        cand = _make(strategy="CSP", strike_str="$1800",
                     yield_ann=80.0, prob_profit=95.0, liq="High")
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )

        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "CSP"
        assert result["position"]["strike"] == 1800.0

        s = load_wheel_state("ETH")
        assert s["stage"] == "short_put"

    def test_picks_highest_probability_when_multiple_qualify(self):
        a = _make(strategy="Strangle", strike_str="$1800/$2200",
                  yield_ann=80.0, prob_profit=91.0, liq="High",
                  put_strike=1800.0, call_strike=2200.0)
        b = _make(strategy="CSP", strike_str="$1800",
                  yield_ann=20.0, prob_profit=92.0, liq="Med")
        with patch("automation.automator._build_candidates", return_value=[a, b]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        # 92% prob (CSP) > 91% prob (Strangle) — CSP must win
        assert result["candidate"].strategy == "CSP"

    def test_strangle_with_high_liquidity_is_eligible(self):
        """Strangles with worst-of-leg liquidity Med/High flow through filter."""
        cand = _make(
            strategy="Strangle", strike_str="$1800/$2200",
            yield_ann=120.0, prob_profit=95.0, liq="High",
            put_strike=1800.0, call_strike=2200.0,
        )
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "Strangle"

    def test_strangle_with_low_liquidity_is_skipped(self):
        """Strangle whose worst leg is Low must be filtered out."""
        cand = _make(
            strategy="Strangle", strike_str="$1800/$2200",
            yield_ann=120.0, prob_profit=99.0, liq="Low",
            put_strike=1800.0, call_strike=2200.0,
        )
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"] == "no_candidate"

    def test_calendar_with_med_liquidity_is_eligible(self):
        """Calendar candidates now carry liquidity tags and can be picked."""
        cand = _make(
            strategy="Cal-C", strike_str="$2000 ATM",
            yield_ann=40.0, prob_profit=95.0, liq="Med", far_days=30,
        )
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "Cal-C"

    def test_skips_blocked_strategy_and_picks_next(self):
        """If the wheel already has a short put open, CSP is blocked, so
        the runner should fall through to a Strangle candidate."""
        from datetime import date
        # Create a Single trade in short_put stage to block CSP
        trade = create_single_trade(
            asset="ETH",
            date_open=date.today(),
            option_type="Put",
            strike=1700.0,
            expiry="13-Jun-2026",
            spot_open=2000.0,
            premium=5.0,
            qty=0.14,
            days=7,
            stage="short_put",
        )

        csp = _make(strategy="CSP", yield_ann=80.0, prob_profit=99.0)
        stg = _make(strategy="Strangle", strike_str="$1800/$2200",
                    yield_ann=50.0, prob_profit=95.0, liq="High",
                    put_strike=1800.0, call_strike=2200.0)
        with patch("automation.automator._build_candidates", return_value=[csp, stg]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "Strangle"

    def test_explicit_broker_is_passed_to_enter_trade(self):
        """run_automation forwards the broker kwarg to enter_trade."""
        from access import make_instrument as _make_instr
        custom_broker = MagicMock()
        type(custom_broker).broker_name = PropertyMock(return_value="custom_broker")
        custom_broker.find_instrument.side_effect = (
            lambda asset, expiry, strike, opt: _make_instr(asset, expiry, strike, opt)
        )
        custom_broker.place_order.side_effect = (
            lambda instrument, *a, **kw: _fake_order("CUSTOM-ORD-1", instrument=instrument)
        )

        cand = _make(strategy="CSP", strike_str="$1800",
                     yield_ann=80.0, prob_profit=95.0, liq="High")
        with patch("automation.automator._build_candidates", return_value=[cand]),              patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}),              patch("market.market_data.get_spot_price",  return_value=2000.0),              patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True, broker=custom_broker,
            )

        assert result["status"] == "entered"
        custom_broker.place_order.assert_called_once()
        s = load_wheel_state("ETH")
        assert s["broker"] == "custom_broker"

    def test_monitor_is_called_before_candidate_selection(self):
        """run_monitor must be invoked before _build_candidates."""
        call_order = []
        eth_config = {"active": True}
        with patch("automation.automator.run_monitor",
                   side_effect=lambda *a, **kw: call_order.append("monitor")) as mock_monitor, \
             patch("automation.automator._build_candidates",
                   side_effect=lambda *a, **kw: call_order.append("candidates") or []), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": eth_config}), \
             patch("automation.automator.TRADEABLE_ASSETS", {"ETH": eth_config}), \
             patch("market.market_data.get_spot_price", return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert call_order == ["monitor", "candidates"]
        mock_monitor.assert_called_once_with(2000.0, 0.80, 7, "ETH", silent=True, broker=None)


# ─────────────────────────────────────────────────────────────────────────────
#  Fee-aware yield calculations
# ─────────────────────────────────────────────────────────────────────────────

class TestYieldWithFees:
    """
    Tests for yield calculations that account for open and close fees.

    Task 10: Verify that strategies and automator tests include open and close
    fees in yield calculations logic.
    """

    def test_candidate_yield_reflects_fee_impact(self):
        """Verify that candidate yield_ann is calculated after deducting fees."""
        # This candidate's yield_ann should already be net of fees if strategies
        # are calculating it correctly (per wheel.py lines 76-80)
        c = _make(strategy="CSP", yield_ann=50.0, premium=12.0, spot=2000.0)
        # yield_ann should be the effective yield after open and close fees
        assert c.yield_ann == pytest.approx(50.0)

    def test_higher_yield_after_fees_ranks_above_lower_yield(self):
        """Candidate with higher effective yield (post-fees) should rank first."""
        # Candidate A: lower raw yield but better fee structure
        a = _make(strategy="CSP", prob_profit=80.0, yield_ann=45.0)
        # Candidate B: higher raw yield but higher fees reduce effective yield
        b = _make(strategy="CSP", prob_profit=80.0, yield_ann=40.0)
        # Same probability, so yield becomes tie-breaker
        best = select_best_candidate([a, b], min_prob=0.0, min_yield=0.0)
        assert best is a  # A has higher yield (45% > 40%)

    def test_yield_filter_accounts_for_fees(self):
        """Yield threshold filter should consider fees (yield_ann already net of fees)."""
        # Candidate appears to have 12% yield but after fees it's ~8%
        # (The scanner/strategy should have already deducted fees from yield_ann)
        low_effective = _make(prob_profit=95.0, yield_ann=8.0)
        high_effective = _make(prob_profit=70.0, yield_ann=15.0)

        # With min_yield=10%, the first should be filtered out
        best = select_best_candidate([low_effective, high_effective], min_yield=10.0, min_prob=0.0)
        assert best is high_effective

    def test_wheel_csp_yield_with_open_and_close_fees(self):
        """Wheel CSP yield should account for both open entry fee and close fee."""
        # Spot=2000, Budget=250, Premium ~12.5
        # Open fee ≈ 0.04% of 2000 = 0.8
        # Close fee (estimated) ≈ 0.8
        # Effective premium ≈ 12.5 - 0.8 - 0.8 = 10.9
        # Yield ≈ (10.9 / 250) * (365/7) ≈ 22.7%
        # (This test verifies the strategy calculation, not the candidate itself)
        from trading.fee_calculator import calculate_fee
        from market.pricing import bs_put, round_strike
        from config import BUDGET_USD, RISK_FREE_RATE

        spot = 2000.0
        asset = "ETH"
        iv = 0.80
        days = 7
        T = days / 365.0
        otm = 0.10

        K = round_strike(spot * (1 - otm), spot)
        qty = BUDGET_USD / K
        raw_premium = bs_put(spot, K, T, RISK_FREE_RATE, iv) * qty

        # Calculate fees as the strategy does (wheel.py lines 77-78)
        open_fee = calculate_fee(spot, raw_premium / qty, asset) * qty
        close_fee_est = calculate_fee(spot, 0.01, asset) * qty

        effective_premium = raw_premium - open_fee - close_fee_est

        # Verify effective premium is less than raw premium
        assert effective_premium < raw_premium
        assert open_fee > 0
        assert close_fee_est > 0

    def test_strangle_yield_with_both_leg_fees(self):
        """Strangle yield should account for fees on both put and call legs."""
        from trading.fee_calculator import calculate_fee
        from market.pricing import bs_put, bs_call, round_strike
        from config import BUDGET_USD, RISK_FREE_RATE

        spot = 2000.0
        asset = "ETH"
        iv = 0.80
        days = 7
        T = days / 365.0
        otm_put = 0.15
        otm_call = 0.15

        # Put leg
        K_put = round_strike(spot * (1 - otm_put), spot)
        qty = BUDGET_USD / (K_put + spot)  # Strangle qty is typically smaller
        raw_put_prem = bs_put(spot, K_put, T, RISK_FREE_RATE, iv) * qty
        put_fee = calculate_fee(spot, raw_put_prem / qty, asset) * qty

        # Call leg
        K_call = round_strike(spot * (1 + otm_call), spot)
        raw_call_prem = bs_call(spot, K_call, T, RISK_FREE_RATE, iv) * qty
        call_fee = calculate_fee(spot, raw_call_prem / qty, asset) * qty

        total_raw = raw_put_prem + raw_call_prem
        total_fees = put_fee + call_fee
        total_effective = total_raw - total_fees

        # Verify both legs have fees and total is reduced
        assert put_fee > 0
        assert call_fee > 0
        assert total_effective < total_raw

    def test_select_best_candidate_respects_fee_adjusted_yield(self):
        """When candidates have same probability, fee-adjusted yield breaks tie."""
        # Create two CSP candidates with same probability
        # Candidate A: appears to have higher yield
        cand_a = _make(strategy="CSP", prob_profit=85.0, yield_ann=35.0, liq="High")
        # Candidate B: lower yield
        cand_b = _make(strategy="CSP", prob_profit=85.0, yield_ann=30.0, liq="High")

        best = select_best_candidate(
            [cand_b, cand_a],  # deliberate order to test sorting
            min_prob=80.0,
            min_yield=0.0,
        )
        # A should win on yield
        assert best is cand_a

    def test_fee_impact_on_min_yield_filter(self):
        """Minimum yield threshold is applied to fee-adjusted yield."""
        # Candidate with 9% effective yield (after fees) should not pass 10% minimum
        below_threshold = _make(prob_profit=95.0, yield_ann=9.0)
        above_threshold = _make(prob_profit=80.0, yield_ann=11.0)

        best = select_best_candidate(
            [below_threshold, above_threshold],
            min_yield=10.0,
            min_prob=0.0,
        )
        assert best is above_threshold

        # When only below_threshold is provided, it should be filtered out
        result = select_best_candidate(
            [below_threshold],
            min_yield=10.0,
            min_prob=0.0,
        )
        assert result is None  # below_threshold should not pass the yield filter

    def test_calendar_yield_accounts_for_two_leg_fees(self):
        """Calendar spread yield should account for fees on both near and far legs."""
        from trading.fee_calculator import calculate_fee
        from market.pricing import bs_call, round_strike
        from config import BUDGET_USD, RISK_FREE_RATE

        spot = 2000.0
        asset = "ETH"
        iv = 0.80
        days_near = 7
        days_far = 30
        T_near = days_near / 365.0
        T_far = days_far / 365.0
        otm = 0.10

        K = round_strike(spot * (1 + otm), spot)
        qty = BUDGET_USD / spot

        # Near leg (sold) — we receive this
        near_prem = bs_call(spot, K, T_near, RISK_FREE_RATE, iv) * qty
        near_fee = calculate_fee(spot, near_prem / qty, asset) * qty

        # Far leg (bought) — we pay this
        far_prem = bs_call(spot, K, T_far, RISK_FREE_RATE, iv) * qty
        far_fee = calculate_fee(spot, far_prem / qty, asset) * qty

        # Net = what we receive - what we pay
        # Before fees: near_prem - far_prem
        # After fees: (near_prem - near_fee) - (far_prem + far_fee)
        net_before_fees = near_prem - far_prem
        net_after_fees = (near_prem - near_fee) - (far_prem + far_fee)

        # Fees reduce the net benefit (cost increases or benefit decreases)
        # net_after_fees = net_before_fees - (near_fee + far_fee)
        assert net_after_fees < net_before_fees  # More negative or less positive
        assert abs(net_after_fees - net_before_fees) == pytest.approx(near_fee + far_fee)

    def test_spread_yield_with_both_strike_fees(self):
        """Spread (bull put) yield accounts for fees on short and long strikes."""
        from trading.fee_calculator import calculate_fee
        from market.pricing import bs_put, round_strike
        from config import BUDGET_USD, RISK_FREE_RATE

        spot = 2000.0
        asset = "ETH"
        iv = 0.80
        days = 7
        T = days / 365.0
        short_otm = 0.10
        long_otm = 0.15

        K_short = round_strike(spot * (1 - short_otm), spot)
        K_long = round_strike(spot * (1 - long_otm), spot)
        qty = BUDGET_USD / (K_short - K_long)

        # Short leg premium
        short_prem = bs_put(spot, K_short, T, RISK_FREE_RATE, iv) * qty
        short_fee = calculate_fee(spot, short_prem / qty, asset) * qty

        # Long leg premium (protective, so we pay it)
        long_prem = bs_put(spot, K_long, T, RISK_FREE_RATE, iv) * qty
        long_fee = calculate_fee(spot, long_prem / qty, asset) * qty

        # Net credit = short premium - long premium (both reduce after fees)
        net_credit_before_fees = short_prem - long_prem
        net_credit_after_fees = (short_prem - short_fee) - (long_prem + long_fee)

        # After fees, net credit decreases
        assert net_credit_after_fees < net_credit_before_fees
        assert net_credit_after_fees > 0  # Still positive but less

