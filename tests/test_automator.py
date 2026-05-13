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
from database import load_wheel_state, save_wheel_state
from database.strangle_db import load_strangle_state, save_strangle_state
from database.calendar_db import load_calendar_state, save_calendar_state
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

def _fake_order(order_id: str = "TEST-ORD-1") -> OrderResult:
    return OrderResult(
        order_id=order_id, instrument="ETH-TEST", direction="sell",
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
    place_order always succeeds, so Tier 3 / Tier 4 tests exercise only
    the DB and strategy logic.
    """
    mock_instance = MagicMock()
    type(mock_instance).broker_name = PropertyMock(return_value="deribit_paper")
    mock_instance.place_order.return_value = _fake_order()

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
        s = load_wheel_state("ETH")
        s["stage"] = "holding"
        s["asset_held"] = 0.13
        s["cost_basis"] = 1900
        save_wheel_state("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "CSP" in blocked
        assert "CC" not in blocked

    def test_short_put_blocks_both_wheel_legs(self):
        s = load_wheel_state("ETH")
        s["stage"] = "short_put"
        s["open"] = {"type": "Put", "strike": 1800,
                     "premium": 5.0, "qty": 0.13, "days": 7}
        save_wheel_state("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "CSP" in blocked
        assert "CC"  in blocked

    def test_open_strangle_blocks_strangle(self):
        s = load_strangle_state("ETH")
        s["open"] = {
            "put_strike": 1800, "call_strike": 2200,
            "total_premium": 30.0, "qty": 0.125, "days": 7,
        }
        save_strangle_state("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "Strangle" in blocked

    def test_open_calendar_blocks_both_calendar_types(self):
        s = load_calendar_state("ETH")
        s["open"] = {
            "strike": 2000, "option_type": "Call",
            "near_prem": 10.0, "far_prem": 20.0, "net_debit": 10.0,
            "qty": 0.125, "near_days": 7, "far_days": 30,
        }
        save_calendar_state("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "Cal-C" in blocked
        assert "Cal-P" in blocked


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 3 — enter_trade
# ─────────────────────────────────────────────────────────────────────────────

class TestEnterTrade:

    def test_csp_writes_wheel_state(self):
        c = _make(strategy="CSP", strike_str="$1800")
        opened = enter_trade(c)

        s = load_wheel_state("ETH")
        assert s["stage"] == "short_put"
        assert s["open"]["type"] == "Put"
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
        assert s2["open"]["type"] == "Call"
        assert s2["open"]["qty"]  == pytest.approx(0.20)
        assert opened["strike"] == 2200.0

    def test_strangle_writes_strangle_state(self):
        c = _make(strategy="Strangle", strike_str="$1800/$2200",
                  put_strike=1800.0, call_strike=2200.0)
        opened = enter_trade(c)

        s = load_strangle_state("ETH")
        assert s["open"]["put_strike"]  == 1800.0
        assert s["open"]["call_strike"] == 2200.0
        assert s["trades"] == 1
        assert opened["put_strike"] == 1800.0

    def test_calendar_writes_calendar_state(self):
        c = _make(strategy="Cal-C", strike_str="$2000 ATM",
                  far_days=30, liq="Med")
        opened = enter_trade(c)

        s = load_calendar_state("ETH")
        assert s["open"]["strike"]      == 2000.0
        assert s["open"]["option_type"] == "Call"
        assert s["open"]["far_days"]    == 30
        assert s["open"]["net_debit"]   > 0
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
        # Pre-load wheel into 'short_put' state
        s = load_wheel_state("ETH")
        s["stage"] = "short_put"
        s["open"] = {"type": "Put", "strike": 1700.0,
                     "premium": 5.0, "qty": 0.14, "days": 7}
        save_wheel_state("ETH", s)

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
        custom_broker = MagicMock()
        type(custom_broker).broker_name = PropertyMock(return_value="custom_broker")
        custom_broker.place_order.return_value = _fake_order("CUSTOM-ORD-1")

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
        with patch("automation.automator.run_monitor",
                   side_effect=lambda *a, **kw: call_order.append("monitor")) as mock_monitor, \
             patch("automation.automator._build_candidates",
                   side_effect=lambda *a, **kw: call_order.append("candidates") or []), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price", return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, silent=True,
            )
        assert call_order == ["monitor", "candidates"]
        mock_monitor.assert_called_once_with(2000.0, 0.80, 7, "ETH", silent=True, broker=None)

