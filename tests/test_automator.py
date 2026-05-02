"""
tests/test_automator.py
=======================
Tests for strategies/automator.py.

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

Tier 3 — trade entry (mocked Excel append + state I/O)
    enter_trade
        * CSP   writes wheel state + trade row, total_premium increments
        * CC    requires holding stage and uses asset_held qty
        * Strangle writes strangle state + row
        * Cal-C  writes calendar state + row

Tier 4 — orchestration
    run_automation
        * picks the right candidate when one qualifies
        * returns "no_candidate" when nothing qualifies (silent path,
          should not raise — this is the "do nothing, retry in 1 hour"
          contract)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from strategies.scanner   import Candidate
from strategies           import wheel, strangle, calendar
from automation.automator import (
    select_best_candidate,
    enter_trade,
    run_automation,
    _blocked_strategies,
    DEFAULT_MIN_YIELD,
    DEFAULT_ALLOWED_LIQUIDITY,
)


# ── Helpers / fixtures ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """
    Run every test in tmp_path so state files (paper_state_*.json,
    strangle_state_*.json, calendar_state_*.json) don't pollute the repo.
    """
    monkeypatch.chdir(tmp_path)
    yield


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
        best = select_best_candidate(cands)
        assert best.prob_profit == 85.0

    def test_yield_breaks_ties_in_prob(self):
        a = _make(prob_profit=80.0, yield_ann=20.0)
        b = _make(prob_profit=80.0, yield_ann=50.0)
        best = select_best_candidate([a, b])
        assert best is b

    def test_yield_filter_excludes_below_threshold(self):
        low  = _make(prob_profit=99.0, yield_ann=5.0)   # high prob, low yield
        high = _make(prob_profit=70.0, yield_ann=15.0)
        best = select_best_candidate([low, high], min_yield=10.0)
        assert best is high

    def test_yield_filter_inclusive_at_threshold(self):
        c = _make(prob_profit=70.0, yield_ann=10.0)
        assert select_best_candidate([c], min_yield=10.0) is c

    def test_liquidity_filter_excludes_low(self):
        low  = _make(prob_profit=99.0, yield_ann=50.0, liq="Low")
        med  = _make(prob_profit=70.0, yield_ann=20.0, liq="Med")
        best = select_best_candidate([low, med])
        assert best is med

    def test_liquidity_filter_excludes_empty_tag(self):
        empty = _make(prob_profit=99.0, yield_ann=50.0, liq="")
        med   = _make(prob_profit=70.0, yield_ann=20.0, liq="Med")
        best  = select_best_candidate([empty, med])
        assert best is med

    def test_default_thresholds_match_constants(self):
        """Sanity — the public defaults are still 10%/Med+High."""
        assert DEFAULT_MIN_YIELD == 10.0
        assert set(DEFAULT_ALLOWED_LIQUIDITY) == {"Med", "High"}

    def test_blocked_strategies_filter(self):
        a = _make(strategy="CSP",  prob_profit=99.0, yield_ann=50.0)
        b = _make(strategy="Strangle", prob_profit=70.0, yield_ann=20.0,
                  put_strike=1800.0, call_strike=2200.0)
        best = select_best_candidate(
            [a, b],
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
        select_best_candidate(cands)
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
        s = wheel._load("ETH")
        s["stage"] = "holding"
        s["asset_held"] = 0.13
        s["cost_basis"] = 1900
        wheel._save("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "CSP" in blocked
        assert "CC" not in blocked

    def test_short_put_blocks_both_wheel_legs(self):
        s = wheel._load("ETH")
        s["stage"] = "short_put"
        s["open"]  = {"type": "Put", "strike": 1800,
                      "premium": 5.0, "qty": 0.13, "days": 7}
        wheel._save("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "CSP" in blocked
        assert "CC"  in blocked

    def test_open_strangle_blocks_strangle(self):
        s = strangle._load("ETH")
        s["open"] = {
            "put_strike": 1800, "call_strike": 2200,
            "total_premium": 30.0, "qty": 0.125, "days": 7,
        }
        strangle._save("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "Strangle" in blocked

    def test_open_calendar_blocks_both_calendar_types(self):
        s = calendar._load("ETH")
        s["open"] = {
            "strike": 2000, "option_type": "Call",
            "near_prem": 10.0, "far_prem": 20.0, "net_debit": 10.0,
            "qty": 0.125, "near_days": 7, "far_days": 30,
        }
        calendar._save("ETH", s)

        blocked = _blocked_strategies("ETH")
        assert "Cal-C" in blocked
        assert "Cal-P" in blocked


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 3 — enter_trade
# ─────────────────────────────────────────────────────────────────────────────

class TestEnterTrade:

    def test_csp_writes_wheel_state(self):
        c  = _make(strategy="CSP", strike_str="$1800")
        wb = MagicMock()
        with patch("automation.automator.append_trade_row") as row:
            opened = enter_trade(c, wb)

        s = wheel._load("ETH")
        assert s["stage"] == "short_put"
        assert s["open"]["type"]   == "Put"
        assert s["open"]["strike"] == 1800.0
        assert s["total_premium"]  > 0
        row.assert_called_once()
        # Sheet name is the second positional arg
        assert row.call_args.args[1] == "📝 Paper Trades"
        assert opened["strike"] == 1800.0

    def test_cc_requires_holding_stage(self):
        c  = _make(strategy="CC", strike_str="$2200")
        wb = MagicMock()
        with patch("automation.automator.append_trade_row"):
            with pytest.raises(RuntimeError):
                enter_trade(c, wb)

    def test_cc_uses_asset_held_qty(self):
        s = wheel._load("ETH")
        s["stage"] = "holding"
        s["asset_held"] = 0.20
        s["cost_basis"] = 2000.0
        wheel._save("ETH", s)

        c  = _make(strategy="CC", strike_str="$2200")
        wb = MagicMock()
        with patch("automation.automator.append_trade_row"):
            opened = enter_trade(c, wb)

        s2 = wheel._load("ETH")
        assert s2["stage"] == "short_call"
        assert s2["open"]["type"] == "Call"
        assert s2["open"]["qty"]  == pytest.approx(0.20)
        assert opened["strike"] == 2200.0

    def test_strangle_writes_strangle_state(self):
        c  = _make(strategy="Strangle", strike_str="$1800/$2200",
                   put_strike=1800.0, call_strike=2200.0)
        wb = MagicMock()
        with patch("automation.automator.append_strangle_row") as row:
            opened = enter_trade(c, wb)

        s = strangle._load("ETH")
        assert s["open"]["put_strike"]  == 1800.0
        assert s["open"]["call_strike"] == 2200.0
        assert s["trades"] == 1
        row.assert_called_once()
        assert opened["put_strike"] == 1800.0

    def test_calendar_writes_calendar_state(self):
        c  = _make(strategy="Cal-C", strike_str="$2000 ATM",
                   far_days=30, liq="Med")
        wb = MagicMock()
        with patch("automation.automator.append_calendar_row") as row:
            opened = enter_trade(c, wb)

        s = calendar._load("ETH")
        assert s["open"]["strike"]      == 2000.0
        assert s["open"]["option_type"] == "Call"
        assert s["open"]["far_days"]    == 30
        assert s["open"]["net_debit"]   > 0
        row.assert_called_once()
        assert opened["option_type"] == "Call"

    def test_calendar_put_strategy(self):
        c  = _make(strategy="Cal-P", strike_str="$2000 ATM",
                   far_days=30, liq="Med")
        wb = MagicMock()
        with patch("automation.automator.append_calendar_row"):
            enter_trade(c, wb)

        s = calendar._load("ETH")
        assert s["open"]["option_type"] == "Put"

    def test_unsupported_strategy_raises(self):
        c = _make(strategy="Iron Condor", strike_str="$x")
        with pytest.raises(ValueError):
            enter_trade(c, MagicMock())


# ─────────────────────────────────────────────────────────────────────────────
#  Tier 4 — run_automation
# ─────────────────────────────────────────────────────────────────────────────

class TestRunAutomation:

    def test_no_candidate_returns_status_no_candidate(self):
        """If _build_candidates yields nothing eligible, automation does
        nothing — this is the 'try again in 1 hour' contract."""
        wb = MagicMock()
        with patch("automation.automator._build_candidates", return_value=[]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"]    == "no_candidate"
        assert result["candidate"] is None
        assert result["position"]  is None

    def test_candidate_below_yield_is_not_entered(self):
        wb = MagicMock()
        cand = _make(strategy="CSP", yield_ann=5.0, prob_profit=99.0)
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_trade_row") as row, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"] == "no_candidate"
        row.assert_not_called()

    def test_low_liquidity_is_not_entered(self):
        wb = MagicMock()
        cand = _make(strategy="CSP", yield_ann=80.0, prob_profit=85.0,
                     liq="Low")
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_trade_row") as row, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"] == "no_candidate"
        row.assert_not_called()

    def test_eligible_csp_is_entered(self):
        wb = MagicMock()
        cand = _make(strategy="CSP", strike_str="$1800",
                     yield_ann=80.0, prob_profit=85.0, liq="High")
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_trade_row") as row, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )

        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "CSP"
        assert result["position"]["strike"] == 1800.0
        row.assert_called_once()

        s = wheel._load("ETH")
        assert s["stage"] == "short_put"

    def test_picks_highest_probability_when_multiple_qualify(self):
        wb = MagicMock()
        a = _make(strategy="Strangle", strike_str="$1800/$2200",
                  yield_ann=80.0, prob_profit=70.0, liq="High",
                  put_strike=1800.0, call_strike=2200.0)
        b = _make(strategy="CSP", strike_str="$1800",
                  yield_ann=20.0, prob_profit=92.0, liq="Med")
        with patch("automation.automator._build_candidates", return_value=[a, b]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_trade_row"), \
             patch("automation.automator.append_strangle_row"), \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        # 92% prob (CSP) > 70% prob (Strangle) — CSP must win
        assert result["candidate"].strategy == "CSP"

    def test_strangle_with_high_liquidity_is_eligible(self):
        """Strangles with worst-of-leg liquidity Med/High flow through filter."""
        wb = MagicMock()
        cand = _make(
            strategy="Strangle", strike_str="$1800/$2200",
            yield_ann=120.0, prob_profit=80.0, liq="High",
            put_strike=1800.0, call_strike=2200.0,
        )
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_strangle_row") as row, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "Strangle"
        row.assert_called_once()

    def test_strangle_with_low_liquidity_is_skipped(self):
        """Strangle whose worst leg is Low must be filtered out."""
        wb = MagicMock()
        cand = _make(
            strategy="Strangle", strike_str="$1800/$2200",
            yield_ann=120.0, prob_profit=99.0, liq="Low",
            put_strike=1800.0, call_strike=2200.0,
        )
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_strangle_row") as row, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"] == "no_candidate"
        row.assert_not_called()

    def test_calendar_with_med_liquidity_is_eligible(self):
        """Calendar candidates now carry liquidity tags and can be picked."""
        wb = MagicMock()
        cand = _make(
            strategy="Cal-C", strike_str="$2000 ATM",
            yield_ann=40.0, prob_profit=65.0, liq="Med", far_days=30,
        )
        with patch("automation.automator._build_candidates", return_value=[cand]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_calendar_row") as row, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "Cal-C"
        row.assert_called_once()

    def test_skips_blocked_strategy_and_picks_next(self):
        """If the wheel already has a short put open, CSP is blocked, so
        the runner should fall through to a Strangle candidate."""
        # Pre-load wheel into 'short_put' state
        s = wheel._load("ETH")
        s["stage"] = "short_put"
        s["open"]  = {"type": "Put", "strike": 1700.0,
                      "premium": 5.0, "qty": 0.14, "days": 7}
        wheel._save("ETH", s)

        wb = MagicMock()
        csp = _make(strategy="CSP", yield_ann=80.0, prob_profit=99.0)
        stg = _make(strategy="Strangle", strike_str="$1800/$2200",
                    yield_ann=50.0, prob_profit=75.0, liq="High",
                    put_strike=1800.0, call_strike=2200.0)
        with patch("automation.automator._build_candidates", return_value=[csp, stg]), \
             patch("automation.automator.SUPPORTED_ASSETS", {"ETH": {}}), \
             patch("automation.automator.append_strangle_row") as srow, \
             patch("market.market_data.get_spot_price",  return_value=2000.0), \
             patch("market.market_data.get_deribit_iv",  return_value=0.80):
            result = run_automation(
                active_spot=2000.0, active_iv=0.80, active_asset="ETH",
                days=7, wb=wb, silent=True,
            )
        assert result["status"]            == "entered"
        assert result["candidate"].strategy == "Strangle"
        srow.assert_called_once()
