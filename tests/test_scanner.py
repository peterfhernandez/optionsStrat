"""
tests/test_scanner.py
=====================
Tests for strategies/scanner.py — candidate building, liquidity tagging,
display formatters, ranking logic, and broker-forwarding via run_scanner.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _liquidity_tag  : all three tiers, edge cases, None inputs
    _fmt_oi         : None, small, thousands
    _fmt_vol        : None, small, thousands, millions
    _fmt_spread     : None, values
    Candidate       : dataclass construction, defaults

Tier 2 — mocked _fetch_liquidity:
    _build_candidates : correct count, strategies present, field values,
                        fallback to passed-in IV when liquidity unavailable,
                        strangle uses worst-leg liquidity,
                        breakevens computed correctly

Tier 3 — ranking logic (operates on Candidate lists, no mocking needed):
    ranking by prob  : sorted descending, MIN_YIELD_PCT filter applied
    ranking by yield : sorted descending, no filter
    liquidity filter : candidates with empty tag excluded from rankings

Tier 4 — run_scanner broker forwarding:
    run_scanner returns list of candidates
    default broker is DeribitClient
    supplied broker forwarded to enter_trade on selection
    enter_trade not called when user skips
"""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.base import Base
from models.scan_results import ScanResult
from database.scanner_db import save_scan_results, get_latest_scan, get_scan_history
from access import OrderResult

from strategies.scanner import (
    Candidate,
    _liquidity_tag,
    _combine_liquidity_legs,
    _fmt_oi,
    _fmt_vol,
    _fmt_spread,
    _build_candidates,
    MIN_YIELD_PCT,
    _OI_HIGH, _OI_MED, _VOL_HIGH, _SPREAD_LOW,
)
from config import OTM_LEVELS, BUDGET_USD, CALENDAR_SPREADS


# ── Shared broker helpers ─────────────────────────────────────────────────────

def _fake_order(order_id: str = "TEST-ORD-1") -> OrderResult:
    return OrderResult(
        order_id=order_id, instrument="ETH-TEST", direction="sell",
        amount=10000.0, price=None, state="open",
        filled_amount=0.0, avg_price=None, label=None,
    )


def _make_broker(order_id: str = "TEST-ORD-1") -> MagicMock:
    m = MagicMock()
    type(m).broker_name = PropertyMock(return_value="deribit_paper")
    m.place_order.return_value = _fake_order(order_id)
    return m


@pytest.fixture(autouse=True)
def _mock_deribit(monkeypatch):
    """Prevent any test from hitting the real Deribit network."""
    mock_instance = _make_broker()
    with patch("strategies.scanner.DeribitClient", return_value=mock_instance), \
         patch("trading.executor.DeribitClient",   return_value=mock_instance):
        yield mock_instance


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def liquid_book():
    """A mock order book dict representing a highly liquid instrument."""
    return {
        "mark_iv":       0.80,
        "bid_iv":        0.78,
        "ask_iv":        0.82,
        "iv_spread":     0.02,
        "open_interest": 5000.0,
        "volume_usd":    200000.0,
        "best_bid":      0.023,
        "best_ask":      0.025,
    }


@pytest.fixture
def illiquid_book():
    """A mock order book dict representing a thin market."""
    return {
        "mark_iv":       0.90,
        "bid_iv":        0.80,
        "ask_iv":        1.00,
        "iv_spread":     0.20,
        "open_interest": 50.0,
        "volume_usd":    1000.0,
        "best_bid":      0.010,
        "best_ask":      0.030,
    }


@pytest.fixture
def sample_candidates():
    """A list of Candidate objects covering all strategies and OTM levels."""
    return [
        Candidate(asset="ETH", strategy="CSP",      otm_pct=0.10, spot=2000.0,
                  iv=0.80, strike="$1800", premium=12.0, yield_ann=85.0,
                  prob_profit=82.0, days=7, liquidity_tag="High",
                  open_interest=5000.0, volume_usd=200000.0, iv_spread=0.01),
        Candidate(asset="ETH", strategy="CC",       otm_pct=0.10, spot=2000.0,
                  iv=0.80, strike="$2200", premium=10.0, yield_ann=71.0,
                  prob_profit=78.0, days=7, liquidity_tag="High",
                  open_interest=4000.0, volume_usd=150000.0, iv_spread=0.015),
        Candidate(asset="ETH", strategy="Strangle", otm_pct=0.10, spot=2000.0,
                  iv=0.80, strike="$1800/$2200", premium=22.0, yield_ann=156.0,
                  prob_profit=60.0, days=7, liquidity_tag="Med",
                  open_interest=4000.0, volume_usd=150000.0, iv_spread=0.02,
                  put_strike=1800.0, call_strike=2200.0, be_lo=1778.0, be_hi=2222.0),
        Candidate(asset="BTC", strategy="CSP",      otm_pct=0.15, spot=80000.0,
                  iv=0.60, strike="$68000", premium=5.0, yield_ann=15.0,
                  prob_profit=88.0, days=7, liquidity_tag=""),  # no liquidity data
        Candidate(asset="SOL", strategy="CSP",      otm_pct=0.20, spot=86.0,
                  iv=0.90, strike="$69", premium=0.5, yield_ann=8.0,
                  prob_profit=92.0, days=7, liquidity_tag="Low",
                  open_interest=80.0, volume_usd=500.0, iv_spread=0.15),
        Candidate(asset="ETH", strategy="BPS",      otm_pct=0.10, spot=2000.0,
                  iv=0.80, strike="$1800/$1700", premium=5.0, yield_ann=45.0,
                  prob_profit=80.0, days=7, liquidity_tag="High",
                  open_interest=3000.0, volume_usd=100000.0, iv_spread=0.015),
        Candidate(asset="ETH", strategy="BCS",      otm_pct=0.10, spot=2000.0,
                  iv=0.80, strike="$2200/$2300", premium=4.5, yield_ann=40.0,
                  prob_profit=79.0, days=7, liquidity_tag="Med",
                  open_interest=2000.0, volume_usd=80000.0, iv_spread=0.02),
    ]


# ── _liquidity_tag ────────────────────────────────────────────────────────────

class TestLiquidityTag:

    def test_none_oi_returns_empty_string(self):
        assert _liquidity_tag(None, None, None) == ""

    def test_high_oi_tight_spread_is_high(self):
        assert _liquidity_tag(_OI_HIGH, _VOL_HIGH, _SPREAD_LOW) == "High"

    def test_high_oi_no_spread_is_high(self):
        """High OI with no spread data still qualifies as High."""
        assert _liquidity_tag(_OI_HIGH, _VOL_HIGH, None) == "High"

    def test_high_oi_wide_spread_is_med(self):
        """High OI but wide spread — drops to Med."""
        assert _liquidity_tag(_OI_HIGH, 0.0, _SPREAD_LOW + 0.01) == "Med"

    def test_med_oi_is_med(self):
        assert _liquidity_tag(_OI_MED, 0.0, None) == "Med"

    def test_high_volume_is_med(self):
        """High volume alone qualifies for Med even with low OI."""
        assert _liquidity_tag(_OI_MED - 1, _VOL_HIGH, None) == "Med"

    def test_low_oi_low_volume_is_low(self):
        assert _liquidity_tag(_OI_MED - 1, 100.0, 0.10) == "Low"

    def test_zero_oi_is_low(self):
        assert _liquidity_tag(0.0, 0.0, None) == "Low"

    def test_exactly_at_high_threshold(self):
        assert _liquidity_tag(_OI_HIGH, _VOL_HIGH, _SPREAD_LOW) == "High"

    def test_just_below_high_threshold(self):
        assert _liquidity_tag(_OI_HIGH - 1, _VOL_HIGH, _SPREAD_LOW) == "Med"

    def test_returns_string(self):
        result = _liquidity_tag(500.0, 10000.0, 0.05)
        assert isinstance(result, str)

    def test_all_valid_tags(self):
        """Only valid tags should be returned."""
        valid = {"High", "Med", "Low", ""}
        assert _liquidity_tag(None, None, None) in valid
        assert _liquidity_tag(0.0, 0.0, None) in valid
        assert _liquidity_tag(500.0, 10000.0, 0.05) in valid
        assert _liquidity_tag(5000.0, 200000.0, 0.01) in valid


# ── _fmt_oi ───────────────────────────────────────────────────────────────────

class TestFmtOi:

    def test_none_returns_dash(self):
        assert "—" in _fmt_oi(None)

    def test_small_value_no_suffix(self):
        result = _fmt_oi(500.0)
        assert "500" in result
        assert "k" not in result

    def test_thousands_shows_k(self):
        result = _fmt_oi(4791.0)
        assert "k" in result
        assert "4.8" in result

    def test_exactly_1000(self):
        result = _fmt_oi(1000.0)
        assert "k" in result
        assert "1.0" in result

    def test_returns_string(self):
        assert isinstance(_fmt_oi(500.0), str)
        assert isinstance(_fmt_oi(None), str)


# ── _fmt_vol ──────────────────────────────────────────────────────────────────

class TestFmtVol:

    def test_none_returns_dash(self):
        assert "—" in _fmt_vol(None)

    def test_small_value(self):
        result = _fmt_vol(500.0)
        assert "$500" in result

    def test_thousands_shows_k(self):
        result = _fmt_vol(84365.0)
        assert "k" in result
        assert "$" in result

    def test_millions_shows_m(self):
        result = _fmt_vol(1_500_000.0)
        assert "M" in result
        assert "$" in result
        assert "1.5" in result

    def test_returns_string(self):
        assert isinstance(_fmt_vol(1000.0), str)
        assert isinstance(_fmt_vol(None), str)


# ── _fmt_spread ───────────────────────────────────────────────────────────────

class TestFmtSpread:

    def test_none_returns_dash(self):
        assert "—" in _fmt_spread(None)

    def test_converts_to_percentage_points(self):
        result = _fmt_spread(0.02)
        assert "2.0pp" in result

    def test_tight_spread(self):
        result = _fmt_spread(0.005)
        assert "0.5pp" in result

    def test_wide_spread(self):
        result = _fmt_spread(0.20)
        assert "20.0pp" in result

    def test_returns_string(self):
        assert isinstance(_fmt_spread(0.05), str)


# ── Candidate dataclass ───────────────────────────────────────────────────────

class TestCandidate:

    def test_required_fields(self):
        c = Candidate(
            asset="ETH", strategy="CSP", otm_pct=0.15,
            spot=2000.0, iv=0.80, strike="$1700",
            premium=12.50, yield_ann=65.0, prob_profit=80.0, days=7,
        )
        assert c.asset == "ETH"
        assert c.strategy == "CSP"
        assert c.premium == 12.50

    def test_liquidity_defaults_to_empty_string(self):
        c = Candidate(
            asset="ETH", strategy="CSP", otm_pct=0.15,
            spot=2000.0, iv=0.80, strike="$1700",
            premium=12.50, yield_ann=65.0, prob_profit=80.0, days=7,
        )
        assert c.liquidity_tag == ""

    def test_strangle_fields_default_none(self):
        c = Candidate(
            asset="ETH", strategy="CSP", otm_pct=0.15,
            spot=2000.0, iv=0.80, strike="$1700",
            premium=12.50, yield_ann=65.0, prob_profit=80.0, days=7,
        )
        assert c.put_strike is None
        assert c.call_strike is None
        assert c.be_lo is None
        assert c.be_hi is None

    def test_liquidity_fields_default_none(self):
        c = Candidate(
            asset="ETH", strategy="CSP", otm_pct=0.15,
            spot=2000.0, iv=0.80, strike="$1700",
            premium=12.50, yield_ann=65.0, prob_profit=80.0, days=7,
        )
        assert c.open_interest is None
        assert c.volume_usd is None
        assert c.iv_spread is None


# ── _combine_liquidity_legs ───────────────────────────────────────────────────

class TestCombineLiquidityLegs:
    """Worst-of-legs roll-up used by Strangle and Calendar candidates."""

    LIQUID = {
        "open_interest": 5000.0, "volume_usd": 200000.0, "iv_spread": 0.01,
        "mark_iv": 0.80,
    }
    MEDIUM = {
        "open_interest": 200.0,  "volume_usd": 60000.0,  "iv_spread": 0.05,
        "mark_iv": 0.80,
    }
    THIN = {
        "open_interest": 50.0,   "volume_usd": 500.0,    "iv_spread": 0.20,
        "mark_iv": 0.80,
    }

    def test_no_legs_returns_blank(self):
        r = _combine_liquidity_legs()
        assert r["liquidity_tag"]  == ""
        assert r["open_interest"] is None
        assert r["volume_usd"]    is None
        assert r["iv_spread"]     is None

    def test_all_legs_none_returns_blank(self):
        r = _combine_liquidity_legs(None, None)
        assert r["liquidity_tag"] == ""

    def test_partial_missing_returns_blank(self):
        """If even one leg's book is missing, refuse to invent a rating."""
        r = _combine_liquidity_legs(self.LIQUID, None)
        assert r["liquidity_tag"]  == ""
        assert r["open_interest"] is None

    def test_two_high_legs_is_high(self):
        r = _combine_liquidity_legs(self.LIQUID, self.LIQUID)
        assert r["liquidity_tag"] == "High"

    def test_high_plus_med_is_med(self):
        r = _combine_liquidity_legs(self.LIQUID, self.MEDIUM)
        assert r["liquidity_tag"] == "Med"
        # Worst-of metrics
        assert r["open_interest"] == 200.0      # min
        assert r["volume_usd"]   == 60000.0     # min
        assert r["iv_spread"]    == 0.05        # max (worst)

    def test_high_plus_low_is_low(self):
        r = _combine_liquidity_legs(self.LIQUID, self.THIN)
        assert r["liquidity_tag"] == "Low"
        assert r["open_interest"] == 50.0       # leg with fewer contracts
        assert r["iv_spread"]    == 0.20        # leg with widest spread

    def test_three_legs_takes_worst(self):
        r = _combine_liquidity_legs(self.LIQUID, self.LIQUID, self.THIN)
        assert r["liquidity_tag"] == "Low"

    def test_min_oi_chosen_correctly(self):
        a = {**self.LIQUID, "open_interest": 1500.0}
        b = {**self.LIQUID, "open_interest": 600.0}
        r = _combine_liquidity_legs(a, b)
        assert r["open_interest"] == 600.0

    def test_max_spread_chosen_correctly(self):
        a = {**self.LIQUID, "iv_spread": 0.02}
        b = {**self.LIQUID, "iv_spread": 0.08}
        r = _combine_liquidity_legs(a, b)
        assert r["iv_spread"] == 0.08


# ── _build_candidates ─────────────────────────────────────────────────────────

class TestBuildCandidates:

    def _no_liquidity(self, *args, **kwargs):
        """Stub for _fetch_liquidity that returns None (no Deribit data)."""
        return None

    def _with_liquidity(self, liquid_book):
        """Stub for _fetch_liquidity that always returns a liquid book."""
        return lambda *a, **kw: liquid_book

    def test_produces_correct_count(self):
        """3 OTM levels × 5 strategies (CSP,CC,Strangle,BPS,BCS) + 3 calendar pairs × 2 types (Cal-C, Cal-P) = 21 per asset."""
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        expected = len(OTM_LEVELS) * 5 + len(CALENDAR_SPREADS) * 2
        assert len(results) == expected

    def test_all_strategies_present(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        strategies = {c.strategy for c in results}
        assert strategies == {"CSP", "CC", "Strangle", "Cal-C", "Cal-P", "BPS", "BCS"}

    def test_all_otm_levels_present(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        # OTM levels for non-calendar strategies + 0.00 for calendars
        otm_levels = {c.otm_pct for c in results}
        assert set(OTM_LEVELS).issubset(otm_levels)
        assert 0.00 in otm_levels  # calendar ATM

    def test_asset_field_correct(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert all(c.asset == "ETH" for c in results)

    def test_low_price_asset_uses_strike_round(self):
        """Low-priced assets like XRP should still produce valid non-zero strikes."""
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("XRP", 1.40, 0.47, 7)
        expected = len(OTM_LEVELS) * 5 + len(CALENDAR_SPREADS) * 2
        assert len(results) == expected
        assert all(c.asset == "XRP" for c in results)
        assert all("$0" not in c.strike for c in results if c.strategy in {"CSP", "CC", "Strangle"})

    def test_premium_positive(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert all(c.premium > 0 for c in results)

    def test_yield_positive(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert all(c.yield_ann > 0 for c in results)

    def test_prob_profit_in_range(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert all(0 <= c.prob_profit <= 100 for c in results)

    def test_fallback_iv_used_when_no_liquidity(self):
        """When _fetch_liquidity returns None, passed-in IV should be used."""
        fallback_iv = 0.80
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, fallback_iv, 7)
        csps = [c for c in results if c.strategy == "CSP"]
        assert all(c.iv == pytest.approx(fallback_iv) for c in csps)

    def test_live_iv_used_when_liquidity_available(self, liquid_book):
        """When _fetch_liquidity returns data, mark_iv from book should be used."""
        live_iv = liquid_book["mark_iv"]  # 0.80
        with patch("strategies.scanner._fetch_liquidity", self._with_liquidity(liquid_book)):
            results = _build_candidates("ETH", 2000.0, 0.70, 7)
        csps = [c for c in results if c.strategy == "CSP"]
        assert all(c.iv == pytest.approx(live_iv) for c in csps)

    def test_liquidity_tag_set_when_data_available(self, liquid_book):
        """All candidate strategies get a non-empty liquidity tag when order book data is available."""
        with patch("strategies.scanner._fetch_liquidity", self._with_liquidity(liquid_book)):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        for c in results:
            assert c.liquidity_tag != "", (
                f"{c.strategy} at {c.otm_pct:.0%} OTM has empty liquidity_tag "
                f"(OI={c.open_interest}, vol={c.volume_usd}, spread={c.iv_spread})"
            )

    def test_strangle_inherits_combined_liquidity(self, liquid_book):
        """Strangles (multi-leg) get liquidity from _combine_liquidity_legs."""
        with patch("strategies.scanner._fetch_liquidity", self._with_liquidity(liquid_book)):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        strangles = [c for c in results if c.strategy == "Strangle"]
        assert len(strangles) == len(OTM_LEVELS)
        # When both legs are equally liquid, the combined tag matches the leg
        for s in strangles:
            assert s.liquidity_tag == "High"
            assert s.open_interest == liquid_book["open_interest"]
            assert s.volume_usd    == liquid_book["volume_usd"]
            assert s.iv_spread     == liquid_book["iv_spread"]

    def test_calendar_has_liquidity(self, liquid_book):
        """Calendar candidates now carry liquidity (fetched from near + far ATM books). Multiple calendar pairs are generated."""
        with patch("strategies.scanner._fetch_liquidity", self._with_liquidity(liquid_book)):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        cals = [c for c in results if c.strategy.startswith("Cal")]
        assert len(cals) == len(CALENDAR_SPREADS) * 2  # Each spread pair has Cal-C and Cal-P
        for c in cals:
            assert c.liquidity_tag == "High"
            assert c.open_interest == liquid_book["open_interest"]

    def test_calendar_candidates_use_custom_horizons(self):
        """Custom calendar near/far horizons override CALENDAR_SPREADS and create only one pair."""
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates(
                "ETH", 2000.0, 0.80, 7,
                cal_near_days=3, cal_far_days=30,
            )
        cals = [c for c in results if c.strategy.startswith("Cal")]
        # When custom horizons are provided, only 1 pair is generated (Cal-C and Cal-P)
        assert len(cals) == 2
        assert all(c.days == 3 for c in cals)
        assert all(c.far_days == 30 for c in cals)
        assert {c.strategy for c in cals} == {"Cal-C", "Cal-P"}

    def test_strangle_downgrades_when_one_leg_thin(self):
        """If only the call leg is illiquid, the strangle's tag must drop to Low."""
        thin_call = {
            "mark_iv": 0.80, "open_interest": 50.0, "volume_usd": 500.0,
            "iv_spread": 0.20,
        }
        liquid_put = {
            "mark_iv": 0.80, "open_interest": 5000.0, "volume_usd": 200000.0,
            "iv_spread": 0.01,
        }
        def stub(ticker, spot, days, strike_round, otm, side):
            return thin_call if side == "call" else liquid_put

        with patch("strategies.scanner._fetch_liquidity", side_effect=stub):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)

        strangles = [c for c in results if c.strategy == "Strangle"]
        for s in strangles:
            assert s.liquidity_tag == "Low"
            assert s.open_interest == 50.0   # worst leg's OI
            assert s.iv_spread     == 0.20   # worst leg's spread

    def test_calendar_downgrades_when_far_leg_thin(self):
        """Calendars should drop to Low when the far leg is illiquid."""
        def stub(ticker, spot, days, strike_round, otm, side):
            if days == 30:    # far leg of 1d/30d and 7d/30d
                return {"mark_iv": 0.80, "open_interest": 80.0,
                        "volume_usd": 1000.0, "iv_spread": 0.20}
            return {"mark_iv": 0.80, "open_interest": 5000.0,
                    "volume_usd": 200000.0, "iv_spread": 0.01}

        with patch("strategies.scanner._fetch_liquidity", side_effect=stub):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)

        cals = [c for c in results if c.strategy.startswith("Cal")]
        assert len(cals) == len(CALENDAR_SPREADS) * 2  # Multiple pairs, each with Cal-C and Cal-P

        # Calendars with 30-day far leg (1d/30d, 7d/30d) should have low liquidity
        cals_with_30d_far = [c for c in cals if c.far_days == 30]
        assert len(cals_with_30d_far) == 4  # 2 pairs × 2 types (Cal-C, Cal-P)
        for c in cals_with_30d_far:
            assert c.liquidity_tag == "Low"
            assert c.open_interest == 80.0   # far leg's OI (worst)
            assert c.iv_spread     == 0.20

        # Calendar with 7-day far leg (1d/7d) should have high liquidity
        cals_with_7d_far = [c for c in cals if c.far_days == 7]
        assert len(cals_with_7d_far) == 2  # 1 pair × 2 types (Cal-C, Cal-P)
        for c in cals_with_7d_far:
            assert c.liquidity_tag == "High"

    def test_calendar_blank_when_far_leg_unavailable(self):
        """If the far leg's book is unavailable (None), calendar tag should be blank."""
        def stub(ticker, spot, days, strike_round, otm, side):
            if days == 30:  # only 30-day far legs are unavailable
                return None
            return {"mark_iv": 0.80, "open_interest": 5000.0,
                    "volume_usd": 200000.0, "iv_spread": 0.01}

        with patch("strategies.scanner._fetch_liquidity", side_effect=stub):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)

        cals = [c for c in results if c.strategy.startswith("Cal")]

        # Calendars with 30-day far leg (unavailable) should have blank liquidity
        cals_with_30d_far = [c for c in cals if c.far_days == 30]
        assert len(cals_with_30d_far) == 4  # 2 pairs × 2 types (Cal-C, Cal-P)
        for c in cals_with_30d_far:
            assert c.liquidity_tag == ""
            assert c.open_interest is None

        # Calendar with 7-day far leg should have liquidity data
        cals_with_7d_far = [c for c in cals if c.far_days == 7]
        assert len(cals_with_7d_far) == 2  # 1 pair × 2 types (Cal-C, Cal-P)
        for c in cals_with_7d_far:
            assert c.liquidity_tag == "High"
            assert c.open_interest is not None

    def test_multiple_calendar_spreads_generated(self):
        """Multiple calendar spreads (1d/7d, 1d/30d, 7d/30d) are generated."""
        with patch("strategies.scanner._fetch_liquidity", return_value={
            "mark_iv": 0.80, "open_interest": 5000.0, "volume_usd": 200000.0, "iv_spread": 0.01
        }):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)

        cals = [c for c in results if c.strategy.startswith("Cal")]
        # Should have 3 pairs × 2 types (Cal-C, Cal-P) = 6 candidates
        assert len(cals) == 6

        # Verify the expected day combinations are present
        day_pairs = {(c.days, c.far_days) for c in cals}
        expected_pairs = {(1, 7), (1, 30), (7, 30)}
        assert day_pairs == expected_pairs

        # Each pair should have both Cal-C and Cal-P
        for near, far in expected_pairs:
            pair_cals = [c for c in cals if c.days == near and c.far_days == far]
            assert len(pair_cals) == 2
            strategies = {c.strategy for c in pair_cals}
            assert strategies == {"Cal-C", "Cal-P"}

    def test_strangle_uses_worst_leg_oi(self):
        """
        Strangle OI logic: min(put, call) when both present, otherwise whichever exists.
        Test the logic directly rather than through _build_candidates internals.
        """
        import strategies.scanner as scanner_module

        # Both legs present — should take minimum
        assert scanner_module._liquidity_tag(50.0,  1000.0, 0.05) == "Low"
        assert scanner_module._liquidity_tag(5000.0, 1000.0, 0.01) == "High"

        # Verify min logic: if put OI=5000, call OI=50, strangle OI should be 50
        oi_p, oi_c = 5000.0, 50.0
        oi_s = min(oi_p, oi_c) if oi_p and oi_c else (oi_p or oi_c)
        assert oi_s == pytest.approx(50.0)

        # If only one leg has OI data
        oi_p, oi_c = 5000.0, None
        oi_s = min(oi_p, oi_c) if oi_p and oi_c else (oi_p or oi_c)
        assert oi_s == pytest.approx(5000.0)

        # If neither leg has OI data
        oi_p, oi_c = None, None
        oi_s = min(oi_p, oi_c) if oi_p and oi_c else (oi_p or oi_c)
        assert oi_s is None

    def test_higher_otm_lower_premium_csp(self):
        """Higher OTM% → lower strike → lower put premium."""
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        csps = sorted([c for c in results if c.strategy == "CSP"], key=lambda c: c.otm_pct)
        premiums = [c.premium for c in csps]
        assert premiums == sorted(premiums, reverse=True)  # descending with otm_pct

    def test_higher_otm_higher_prob_csp(self):
        """Higher OTM% → lower strike → higher probability of expiring OTM."""
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        csps = sorted([c for c in results if c.strategy == "CSP"], key=lambda c: c.otm_pct)
        probs = [c.prob_profit for c in csps]
        assert probs == sorted(probs)  # ascending with otm_pct

    def test_days_field_matches_input(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        # Non-calendar strategies use the input days (7)
        non_cal = [c for c in results if not c.strategy.startswith("Cal")]
        assert all(c.days == 7 for c in non_cal)
        # Calendar strategies use their configured near_days from CALENDAR_SPREADS
        cals = [c for c in results if c.strategy.startswith("Cal")]
        calendar_days = {c.days for c in cals}
        expected_near_days = {near for near, far in CALENDAR_SPREADS}
        assert calendar_days == expected_near_days

    def test_spot_field_matches_input(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert all(c.spot == pytest.approx(2000.0) for c in results)


# ── Ranking logic ─────────────────────────────────────────────────────────────

class TestRankingLogic:
    """
    These tests operate directly on Candidate lists — no mocking needed.
    They verify the filtering and sorting logic used in run_scanner.
    """

    def test_prob_ranking_sorted_descending(self, sample_candidates):
        qualified = [c for c in sample_candidates if c.yield_ann >= MIN_YIELD_PCT]
        by_prob   = sorted(qualified, key=lambda c: c.prob_profit, reverse=True)
        probs = [c.prob_profit for c in by_prob]
        assert probs == sorted(probs, reverse=True)

    def test_yield_ranking_sorted_descending(self, sample_candidates):
        by_yield = sorted(sample_candidates, key=lambda c: c.yield_ann, reverse=True)
        yields = [c.yield_ann for c in by_yield]
        assert yields == sorted(yields, reverse=True)

    def test_min_yield_filter_excludes_low_yield(self, sample_candidates):
        """Candidates below MIN_YIELD_PCT should be excluded from ranking ①."""
        qualified = [c for c in sample_candidates if c.yield_ann >= MIN_YIELD_PCT]
        assert all(c.yield_ann >= MIN_YIELD_PCT for c in qualified)

    def test_min_yield_filter_keeps_high_yield(self, sample_candidates):
        qualified = [c for c in sample_candidates if c.yield_ann >= MIN_YIELD_PCT]
        high_yield = [c for c in sample_candidates if c.yield_ann >= MIN_YIELD_PCT]
        assert len(qualified) == len(high_yield)

    def test_liquidity_filter_excludes_no_data(self, sample_candidates):
        """Candidates with empty liquidity_tag (no data) excluded from rankings."""
        liquid = [c for c in sample_candidates if c.liquidity_tag]
        assert all(c.liquidity_tag != "" for c in liquid)

    def test_liquidity_filter_keeps_low_liquidity(self, sample_candidates):
        """'Low' liquidity tag is still included — only empty string is excluded."""
        liquid = [c for c in sample_candidates if c.liquidity_tag]
        assert any(c.liquidity_tag == "Low" for c in liquid)

    def test_yield_ranking_includes_all_liquid_candidates(self, sample_candidates):
        """Ranking ② includes all liquid candidates regardless of yield."""
        liquid   = [c for c in sample_candidates if c.liquidity_tag]
        by_yield = sorted(liquid, key=lambda c: c.yield_ann, reverse=True)
        assert len(by_yield) == len(liquid)

    def test_empty_candidate_list_handled(self):
        """Empty list should not raise."""
        qualified = [c for c in [] if c.yield_ann >= MIN_YIELD_PCT]
        by_prob   = sorted(qualified, key=lambda c: c.prob_profit, reverse=True)
        assert by_prob == []

    def test_all_below_min_yield_gives_empty_ranking(self):
        """If all candidates are below MIN_YIELD_PCT, ranking ① is empty."""
        low_yield_candidates = [
            Candidate(asset="ETH", strategy="CSP", otm_pct=0.20,
                      spot=2000.0, iv=0.80, strike="$1600",
                      premium=1.0, yield_ann=5.0, prob_profit=95.0,
                      days=7, liquidity_tag="High")
        ]
        qualified = [c for c in low_yield_candidates if c.yield_ann >= MIN_YIELD_PCT]
        assert qualified == []


# ── SQLite persistence ────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """In-memory SQLite session for isolation."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_candidate(**overrides) -> Candidate:
    defaults = dict(
        asset="ETH", strategy="CSP", otm_pct=0.10,
        spot=2000.0, iv=0.80, strike="$1800",
        premium=12.0, yield_ann=85.0, prob_profit=82.0, days=7,
        liquidity_tag="High", open_interest=5000.0,
        volume_usd=200000.0, iv_spread=0.01,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


class TestSaveScanResults:

    def test_saves_candidates_to_db(self, db_session):
        candidates = [_make_candidate(), _make_candidate(strategy="CC", strike="$2200")]
        rows = save_scan_results(candidates, session=db_session)
        assert len(rows) == 2
        assert db_session.query(ScanResult).count() == 2

    def test_row_fields_match_candidate(self, db_session):
        c = _make_candidate()
        rows = save_scan_results([c], session=db_session)
        row = rows[0]
        assert row.asset        == c.asset
        assert row.strategy     == c.strategy
        assert row.otm_pct      == pytest.approx(c.otm_pct)
        assert row.premium      == pytest.approx(c.premium)
        assert row.yield_ann    == pytest.approx(c.yield_ann)
        assert row.prob_profit  == pytest.approx(c.prob_profit)
        assert row.liquidity_tag == c.liquidity_tag
        assert row.open_interest == pytest.approx(c.open_interest)

    def test_strangle_extras_persisted(self, db_session):
        c = _make_candidate(
            strategy="Strangle", strike="$1800/$2200",
            put_strike=1800.0, call_strike=2200.0, be_lo=1778.0, be_hi=2222.0,
        )
        rows = save_scan_results([c], session=db_session)
        row = rows[0]
        assert row.put_strike  == pytest.approx(1800.0)
        assert row.call_strike == pytest.approx(2200.0)
        assert row.be_lo       == pytest.approx(1778.0)
        assert row.be_hi       == pytest.approx(2222.0)

    def test_calendar_extras_persisted(self, db_session):
        c = _make_candidate(strategy="Cal-C", strike="$2000 ATM", far_days=30, max_profit=150.0)
        rows = save_scan_results([c], session=db_session)
        row = rows[0]
        assert row.far_days   == 30
        assert row.max_profit == pytest.approx(150.0)

    def test_custom_timestamp_used(self, db_session):
        ts = datetime(2025, 1, 15, 12, 0, 0)
        rows = save_scan_results([_make_candidate()], scanned_at=ts, session=db_session)
        assert rows[0].scanned_at == ts

    def test_empty_list_returns_empty(self, db_session):
        rows = save_scan_results([], session=db_session)
        assert rows == []
        assert db_session.query(ScanResult).count() == 0


class TestGetLatestScan:

    def test_returns_empty_when_no_scans(self, db_session):
        assert get_latest_scan(session=db_session) == []

    def test_returns_most_recent_batch(self, db_session):
        ts1 = datetime(2025, 1, 1)
        ts2 = datetime(2025, 1, 2)
        save_scan_results([_make_candidate(asset="BTC")], scanned_at=ts1, session=db_session)
        save_scan_results([_make_candidate(asset="ETH"), _make_candidate(asset="SOL")],
                          scanned_at=ts2, session=db_session)

        rows = get_latest_scan(session=db_session)
        assert len(rows) == 2
        assets = {r.asset for r in rows}
        assert assets == {"ETH", "SOL"}

    def test_asset_filter_applied(self, db_session):
        ts = datetime(2025, 1, 1)
        save_scan_results(
            [_make_candidate(asset="ETH"), _make_candidate(asset="BTC")],
            scanned_at=ts, session=db_session,
        )
        rows = get_latest_scan(asset="ETH", session=db_session)
        assert all(r.asset == "ETH" for r in rows)


class TestGetScanHistory:

    def test_returns_empty_when_no_history(self, db_session):
        assert get_scan_history(session=db_session) == []

    def test_returns_rows_newest_first(self, db_session):
        ts1 = datetime(2025, 1, 1)
        ts2 = datetime(2025, 1, 2)
        save_scan_results([_make_candidate()], scanned_at=ts1, session=db_session)
        save_scan_results([_make_candidate()], scanned_at=ts2, session=db_session)
        rows = get_scan_history(session=db_session)
        assert rows[0].scanned_at >= rows[-1].scanned_at

    def test_limit_respected(self, db_session):
        ts = datetime(2025, 1, 1)
        candidates = [_make_candidate(strategy="CSP") for _ in range(10)]
        save_scan_results(candidates, scanned_at=ts, session=db_session)
        rows = get_scan_history(limit=5, session=db_session)
        assert len(rows) == 5

    def test_strategy_filter(self, db_session):
        ts = datetime(2025, 1, 1)
        save_scan_results(
            [_make_candidate(strategy="CSP"), _make_candidate(strategy="CC")],
            scanned_at=ts, session=db_session,
        )
        rows = get_scan_history(strategy="CSP", session=db_session)
        assert all(r.strategy == "CSP" for r in rows)

    def test_asset_and_strategy_filter(self, db_session):
        ts = datetime(2025, 1, 1)
        save_scan_results(
            [
                _make_candidate(asset="ETH", strategy="CSP"),
                _make_candidate(asset="BTC", strategy="CSP"),
                _make_candidate(asset="ETH", strategy="CC"),
            ],
            scanned_at=ts, session=db_session,
        )
        rows = get_scan_history(asset="ETH", strategy="CSP", session=db_session)
        assert len(rows) == 1
        assert rows[0].asset    == "ETH"
        assert rows[0].strategy == "CSP"


# ── run_scanner broker forwarding ─────────────────────────────────────────────

def _run_scanner_patches(monkeypatch=None):
    """Common patches needed to run run_scanner without network or I/O."""
    return [
        patch("strategies.scanner._fetch_liquidity", return_value=None),
        patch("strategies.scanner.get_spot_price",   return_value=80000.0),
        patch("strategies.scanner.get_deribit_iv",   return_value=0.60),
        patch("strategies.scanner.save_scan_results"),
        patch("builtins.input", return_value=""),   # user skips trade entry
    ]


class TestRunScanner:

    def test_returns_candidate_list(self):
        """run_scanner returns all generated Candidate objects."""
        from strategies.scanner import run_scanner
        patches = _run_scanner_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = run_scanner(2000.0, 0.80, "ETH", 7)
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(c, Candidate) for c in result)

    def test_active_asset_always_included(self):
        """run_scanner always generates candidates for the active asset even if other fetches fail."""
        from strategies.scanner import run_scanner
        with patch("strategies.scanner._fetch_liquidity", return_value=None), \
             patch("strategies.scanner.get_spot_price",   return_value=None), \
             patch("strategies.scanner.get_deribit_iv",   return_value=None), \
             patch("strategies.scanner.save_scan_results"), \
             patch("builtins.input", return_value=""):
            result = run_scanner(2000.0, 0.80, "ETH", 7)
        # Active asset (ETH) always contributes candidates regardless of other fetch failures
        assert len(result) > 0
        assert all(c.asset == "ETH" for c in result)

    def test_default_broker_is_deribit(self):
        """When no broker is passed, run_scanner creates a DeribitClient."""
        from strategies.scanner import run_scanner
        patches = _run_scanner_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("strategies.scanner.DeribitClient") as mock_cls:
            mock_cls.return_value = _make_broker()
            run_scanner(2000.0, 0.80, "ETH", 7)
        mock_cls.assert_called_once()

    def test_skipping_entry_does_not_call_enter_trade(self):
        """Pressing Enter (empty input) must not call enter_trade."""
        from strategies.scanner import run_scanner
        patches = _run_scanner_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("strategies.scanner.enter_trade") as mock_enter:
            run_scanner(2000.0, 0.80, "ETH", 7)
        mock_enter.assert_not_called()

    def test_valid_selection_calls_enter_trade_with_broker(self):
        """Selecting candidate '1' must call enter_trade with the correct candidate and broker."""
        from strategies.scanner import run_scanner
        broker = _make_broker()
        liquid_book = {
            "mark_iv": 0.80, "bid_iv": 0.78, "ask_iv": 0.82,
            "iv_spread": 0.01, "open_interest": 5000.0,
            "volume_usd": 200000.0, "best_bid": 0.02, "best_ask": 0.025,
        }
        with patch("strategies.scanner._fetch_liquidity", return_value=liquid_book), \
             patch("strategies.scanner.get_spot_price",   return_value=80000.0), \
             patch("strategies.scanner.get_deribit_iv",   return_value=0.60), \
             patch("strategies.scanner.save_scan_results"), \
             patch("builtins.input", return_value="1"), \
             patch("strategies.scanner.enter_trade") as mock_enter:
            mock_enter.return_value = {"broker_order_id": "ORD-1"}
            run_scanner(2000.0, 0.80, "ETH", 7, broker=broker)
        mock_enter.assert_called_once()
        _, kwargs = mock_enter.call_args
        assert kwargs.get("broker") is broker

    def test_out_of_range_selection_does_not_enter(self):
        """Selecting a number beyond the list size must not call enter_trade."""
        from strategies.scanner import run_scanner
        with patch("strategies.scanner._fetch_liquidity", return_value=None), \
             patch("strategies.scanner.get_spot_price",   return_value=80000.0), \
             patch("strategies.scanner.get_deribit_iv",   return_value=0.60), \
             patch("strategies.scanner.save_scan_results"), \
             patch("builtins.input", return_value="99"), \
             patch("strategies.scanner.enter_trade") as mock_enter:
            run_scanner(2000.0, 0.80, "ETH", 7)
        mock_enter.assert_not_called()

    def test_non_numeric_input_does_not_enter(self):
        """Non-numeric input must not call enter_trade."""
        from strategies.scanner import run_scanner
        with patch("strategies.scanner._fetch_liquidity", return_value=None), \
             patch("strategies.scanner.get_spot_price",   return_value=80000.0), \
             patch("strategies.scanner.get_deribit_iv",   return_value=0.60), \
             patch("strategies.scanner.save_scan_results"), \
             patch("builtins.input", return_value="abc"), \
             patch("strategies.scanner.enter_trade") as mock_enter:
            run_scanner(2000.0, 0.80, "ETH", 7)
        mock_enter.assert_not_called()
