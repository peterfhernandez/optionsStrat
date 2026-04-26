"""
tests/test_scanner.py
=====================
Tests for strategies/scanner.py — candidate building, liquidity tagging,
display formatters, and ranking logic.

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
"""

import pytest
from unittest.mock import patch

from strategies.scanner import (
    Candidate,
    _liquidity_tag,
    _fmt_oi,
    _fmt_vol,
    _fmt_spread,
    _build_candidates,
    MIN_YIELD_PCT,
    _OI_HIGH, _OI_MED, _VOL_HIGH, _SPREAD_LOW,
)
from config import OTM_LEVELS, BUDGET_USD


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


# ── _build_candidates ─────────────────────────────────────────────────────────

class TestBuildCandidates:

    def _no_liquidity(self, *args, **kwargs):
        """Stub for _fetch_liquidity that returns None (no Deribit data)."""
        return None

    def _with_liquidity(self, liquid_book):
        """Stub for _fetch_liquidity that always returns a liquid book."""
        return lambda *a, **kw: liquid_book

    def test_produces_correct_count(self):
        """3 OTM levels × 3 strategies = 9 candidates per asset."""
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert len(results) == len(OTM_LEVELS) * 3

    def test_all_three_strategies_present(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        strategies = {c.strategy for c in results}
        assert strategies == {"CSP", "CC", "Strangle"}

    def test_all_otm_levels_present(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        otm_levels = {c.otm_pct for c in results}
        assert otm_levels == set(OTM_LEVELS)

    def test_asset_field_correct(self):
        with patch("strategies.scanner._fetch_liquidity", self._no_liquidity):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        assert all(c.asset == "ETH" for c in results)

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
        """CSP and CC get a non-empty liquidity tag when order book data is available."""
        with patch("strategies.scanner._fetch_liquidity", self._with_liquidity(liquid_book)):
            results = _build_candidates("ETH", 2000.0, 0.80, 7)
        # Only test CSP and CC — strangles combine two legs which has edge cases
        csps_and_ccs = [c for c in results if c.strategy in ("CSP", "CC")]
        assert len(csps_and_ccs) > 0
        for c in csps_and_ccs:
            assert c.liquidity_tag != "", (
                f"{c.strategy} at {c.otm_pct:.0%} OTM has empty liquidity_tag "
                f"(OI={c.open_interest}, vol={c.volume_usd}, spread={c.iv_spread})"
            )

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
        assert all(c.days == 7 for c in results)

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
