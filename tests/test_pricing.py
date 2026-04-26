"""
tests/test_pricing.py
=====================
Tests for pricing.py — Black-Scholes pricing and probability helpers.

Test strategy
-------------
ncdf        : boundary values, symmetry, known points
_d1 / _d2   : formula correctness, d2 = d1 - v√T relationship
bs_put      : put-call parity, boundary conditions, moneyness
bs_call     : put-call parity, boundary conditions, moneyness
prob_otm_*  : range [0,1], ATM ≈ 0.5, deep OTM/ITM limits,
              complementary relationship
"""

import math
import pytest
from pricing import ncdf, _d1, _d2, bs_put, bs_call, prob_otm_put, prob_otm_call


# ── Tolerance ─────────────────────────────────────────────────────────────────

PRICE_TOL = 1e-6    # absolute tolerance for price comparisons
PROB_TOL  = 1e-4    # absolute tolerance for probability comparisons


# ── ncdf ──────────────────────────────────────────────────────────────────────

class TestNcdf:

    def test_zero_returns_half(self):
        """N(0) = 0.5 by symmetry of the normal distribution."""
        assert abs(ncdf(0) - 0.5) < PROB_TOL

    def test_positive_infinity(self):
        """N(+∞) → 1.0."""
        assert abs(ncdf(10) - 1.0) < PROB_TOL

    def test_negative_infinity(self):
        """N(-∞) → 0.0."""
        assert abs(ncdf(-10) - 0.0) < PROB_TOL

    def test_symmetry(self):
        """N(x) + N(-x) = 1 for all x."""
        for x in (0.5, 1.0, 1.96, 2.58):
            assert abs(ncdf(x) + ncdf(-x) - 1.0) < PROB_TOL

    def test_known_values(self):
        """Check against standard normal table values."""
        assert abs(ncdf(1.96) - 0.9750) < 1e-3   # 95% CI upper bound
        assert abs(ncdf(-1.96) - 0.0250) < 1e-3
        assert abs(ncdf(1.0) - 0.8413) < 1e-3

    def test_returns_float(self):
        assert isinstance(ncdf(0.5), float)

    def test_strictly_increasing(self):
        """CDF must be monotonically increasing."""
        xs = [-3, -2, -1, 0, 1, 2, 3]
        vals = [ncdf(x) for x in xs]
        assert all(vals[i] < vals[i+1] for i in range(len(vals)-1))


# ── _d1 and _d2 ───────────────────────────────────────────────────────────────

class TestD1D2:

    def test_d2_equals_d1_minus_vol_sqrt_T(self, spot, T_weekly, r, iv):
        """d2 = d1 - v * √T is the core relationship."""
        K = spot  # ATM
        d1_val = _d1(spot, K, T_weekly, r, iv)
        d2_val = _d2(spot, K, T_weekly, r, iv)
        assert abs(d2_val - (d1_val - iv * math.sqrt(T_weekly))) < PRICE_TOL

    def test_atm_d1_positive(self, spot, T_weekly, r, iv):
        """For ATM options with positive r and v, d1 > 0."""
        d1_val = _d1(spot, spot, T_weekly, r, iv)
        assert d1_val > 0

    def test_deep_itm_put_d1_large_positive(self, spot, T_weekly, r, iv):
        """Deep ITM put (low strike) → large positive d1."""
        K = spot * 0.5   # very deep ITM put
        assert _d1(spot, K, T_weekly, r, iv) > 2.0

    def test_deep_otm_put_d1_large_negative(self, spot, T_weekly, r, iv):
        """Deep OTM put (very high strike vs spot) → negative d1."""
        K = spot * 2.0
        assert _d1(spot, K, T_weekly, r, iv) < 0

    def test_d2_less_than_d1(self, spot, T_weekly, r, iv):
        """d2 < d1 always (since v√T > 0)."""
        K = spot
        assert _d2(spot, K, T_weekly, r, iv) < _d1(spot, K, T_weekly, r, iv)

    def test_longer_expiry_increases_spread(self, spot, r, iv):
        """The gap d1 - d2 = v√T grows with time."""
        T_short = 7 / 365.0
        T_long  = 90 / 365.0
        K = spot
        spread_short = _d1(spot, K, T_short, r, iv) - _d2(spot, K, T_short, r, iv)
        spread_long  = _d1(spot, K, T_long,  r, iv) - _d2(spot, K, T_long,  r, iv)
        assert spread_long > spread_short


# ── bs_put ────────────────────────────────────────────────────────────────────

class TestBsPut:

    def test_returns_positive(self, spot, T_weekly, r, iv):
        """Put price must always be non-negative."""
        assert bs_put(spot, spot, T_weekly, r, iv) >= 0

    def test_otm_put_cheaper_than_itm(self, spot, T_weekly, r, iv):
        """OTM put (low strike) costs less than ITM put (high strike)."""
        K_otm = spot * 0.90
        K_itm = spot * 1.10
        assert bs_put(spot, K_otm, T_weekly, r, iv) < bs_put(spot, K_itm, T_weekly, r, iv)

    def test_boundary_zero_time(self, spot, r, iv):
        """At expiry (T=0), put = max(K - S, 0)."""
        K_itm = spot * 1.10
        K_otm = spot * 0.90
        assert abs(bs_put(spot, K_itm, 0, r, iv) - (K_itm - spot)) < PRICE_TOL
        assert abs(bs_put(spot, K_otm, 0, r, iv) - 0.0) < PRICE_TOL

    def test_boundary_zero_vol(self, spot, T_weekly, r):
        """At zero vol, put = max(K*e^(-rT) - S, 0) ≈ max(K - S, 0)."""
        K_itm = spot * 1.10
        K_otm = spot * 0.90
        assert bs_put(spot, K_otm, T_weekly, r, 0) == 0.0
        assert bs_put(spot, K_itm, T_weekly, r, 0) > 0.0

    def test_put_call_parity(self, spot, T_weekly, r, iv):
        """
        Put-call parity: C - P = S - K * e^(-rT)
        This is the single most important invariant in options pricing.
        """
        for K in (spot * 0.90, spot, spot * 1.10):
            C = bs_call(spot, K, T_weekly, r, iv)
            P = bs_put (spot, K, T_weekly, r, iv)
            parity_lhs = C - P
            parity_rhs = spot - K * math.exp(-r * T_weekly)
            assert abs(parity_lhs - parity_rhs) < PRICE_TOL, (
                f"Put-call parity failed at K={K}: LHS={parity_lhs:.6f} RHS={parity_rhs:.6f}"
            )

    def test_higher_vol_increases_price(self, spot, T_weekly, r):
        """Higher IV always increases option value (vega > 0)."""
        P_low  = bs_put(spot, spot, T_weekly, r, 0.40)
        P_high = bs_put(spot, spot, T_weekly, r, 0.80)
        assert P_high > P_low

    def test_higher_spot_decreases_put(self, T_weekly, r, iv):
        """Higher spot price reduces put value (delta < 0 for puts)."""
        K = 2000.0
        P_low_spot  = bs_put(1800.0, K, T_weekly, r, iv)
        P_high_spot = bs_put(2200.0, K, T_weekly, r, iv)
        assert P_low_spot > P_high_spot

    def test_longer_expiry_increases_price(self, spot, r, iv):
        """More time always increases option value (theta > 0 for long options)."""
        P_short = bs_put(spot, spot, 7/365,  r, iv)
        P_long  = bs_put(spot, spot, 90/365, r, iv)
        assert P_long > P_short

    def test_deep_otm_near_zero(self, spot, T_weekly, r, iv):
        """Very deep OTM put should have near-zero value."""
        K_deep_otm = spot * 0.50
        assert bs_put(spot, K_deep_otm, T_weekly, r, iv) < 0.01


# ── bs_call ───────────────────────────────────────────────────────────────────

class TestBsCall:

    def test_returns_positive(self, spot, T_weekly, r, iv):
        """Call price must always be non-negative."""
        assert bs_call(spot, spot, T_weekly, r, iv) >= 0

    def test_otm_call_cheaper_than_itm(self, spot, T_weekly, r, iv):
        """OTM call (high strike) costs less than ITM call (low strike)."""
        K_otm = spot * 1.10
        K_itm = spot * 0.90
        assert bs_call(spot, K_otm, T_weekly, r, iv) < bs_call(spot, K_itm, T_weekly, r, iv)

    def test_boundary_zero_time(self, spot, r, iv):
        """At expiry (T=0), call = max(S - K, 0)."""
        K_itm = spot * 0.90
        K_otm = spot * 1.10
        assert abs(bs_call(spot, K_itm, 0, r, iv) - (spot - K_itm)) < PRICE_TOL
        assert abs(bs_call(spot, K_otm, 0, r, iv) - 0.0) < PRICE_TOL

    def test_boundary_zero_vol(self, spot, T_weekly, r):
        """At zero vol, OTM call = 0."""
        K_otm = spot * 1.10
        assert bs_call(spot, K_otm, T_weekly, r, 0) == 0.0

    def test_put_call_parity(self, spot, T_weekly, r, iv):
        """Put-call parity — mirrors the put test."""
        for K in (spot * 0.90, spot, spot * 1.10):
            C = bs_call(spot, K, T_weekly, r, iv)
            P = bs_put (spot, K, T_weekly, r, iv)
            assert abs((C - P) - (spot - K * math.exp(-r * T_weekly))) < PRICE_TOL

    def test_higher_vol_increases_price(self, spot, T_weekly, r):
        """Vega > 0: higher IV → higher call price."""
        C_low  = bs_call(spot, spot, T_weekly, r, 0.40)
        C_high = bs_call(spot, spot, T_weekly, r, 0.80)
        assert C_high > C_low

    def test_higher_spot_increases_call(self, T_weekly, r, iv):
        """Higher spot increases call value (delta > 0 for calls)."""
        K = 2000.0
        C_low_spot  = bs_call(1800.0, K, T_weekly, r, iv)
        C_high_spot = bs_call(2200.0, K, T_weekly, r, iv)
        assert C_high_spot > C_low_spot

    def test_deep_otm_near_zero(self, spot, T_weekly, r, iv):
        """Very deep OTM call should have near-zero value."""
        K_deep_otm = spot * 2.0
        assert bs_call(spot, K_deep_otm, T_weekly, r, iv) < 0.01

    def test_atm_call_equals_atm_put_at_zero_rate(self, spot, T_weekly, iv):
        """At r=0, ATM call price equals ATM put price (put-call parity simplifies)."""
        C = bs_call(spot, spot, T_weekly, 0, iv)
        P = bs_put (spot, spot, T_weekly, 0, iv)
        assert abs(C - P) < PRICE_TOL


# ── prob_otm_put ──────────────────────────────────────────────────────────────

class TestProbOtmPut:

    def test_returns_between_0_and_1(self, spot, T_weekly, r, iv):
        """Probability must be in [0, 1]."""
        p = prob_otm_put(spot, spot, T_weekly, r, iv)
        assert 0.0 <= p <= 1.0

    def test_deep_otm_put_near_1(self, spot, T_weekly, r, iv):
        """Very low strike put → almost certain to expire OTM."""
        K_deep_otm = spot * 0.50
        assert prob_otm_put(spot, K_deep_otm, T_weekly, r, iv) > 0.95

    def test_deep_itm_put_near_0(self, spot, T_weekly, r, iv):
        """Very high strike put → almost certain to expire ITM."""
        K_deep_itm = spot * 1.50
        assert prob_otm_put(spot, K_deep_itm, T_weekly, r, iv) < 0.05

    def test_atm_put_near_half(self, spot, T_weekly, r, iv):
        """ATM put probability ≈ 0.5 (slightly above due to drift)."""
        p = prob_otm_put(spot, spot, T_weekly, r, iv)
        assert 0.45 < p < 0.65

    def test_boundary_zero_time_otm(self, spot, r, iv):
        """At T=0, OTM put (S > K) → prob = 1.0."""
        K_otm = spot * 0.90
        assert prob_otm_put(spot, K_otm, 0, r, iv) == 1.0

    def test_boundary_zero_time_itm(self, spot, r, iv):
        """At T=0, ITM put (S < K) → prob = 0.0."""
        K_itm = spot * 1.10
        assert prob_otm_put(spot, K_itm, 0, r, iv) == 0.0

    def test_lower_strike_higher_prob(self, spot, T_weekly, r, iv):
        """Lower strike → higher probability of expiring OTM."""
        p_low  = prob_otm_put(spot, spot * 0.80, T_weekly, r, iv)
        p_high = prob_otm_put(spot, spot * 0.95, T_weekly, r, iv)
        assert p_low > p_high

    def test_longer_expiry_reduces_certainty(self, spot, r, iv):
        """More time → prob moves toward 0.5 (more uncertainty)."""
        K_otm  = spot * 0.80
        p_short = prob_otm_put(spot, K_otm, 1/365,  r, iv)
        p_long  = prob_otm_put(spot, K_otm, 90/365, r, iv)
        assert p_short > p_long   # short-dated OTM more likely to stay OTM


# ── prob_otm_call ─────────────────────────────────────────────────────────────

class TestProbOtmCall:

    def test_returns_between_0_and_1(self, spot, T_weekly, r, iv):
        """Probability must be in [0, 1]."""
        p = prob_otm_call(spot, spot, T_weekly, r, iv)
        assert 0.0 <= p <= 1.0

    def test_deep_otm_call_near_1(self, spot, T_weekly, r, iv):
        """Very high strike call → almost certain to expire OTM."""
        K_deep_otm = spot * 1.50
        assert prob_otm_call(spot, K_deep_otm, T_weekly, r, iv) > 0.95

    def test_deep_itm_call_near_0(self, spot, T_weekly, r, iv):
        """Very low strike call → almost certain to expire ITM."""
        K_deep_itm = spot * 0.50
        assert prob_otm_call(spot, K_deep_itm, T_weekly, r, iv) < 0.05

    def test_atm_call_near_half(self, spot, T_weekly, r, iv):
        """ATM call probability ≈ 0.5."""
        p = prob_otm_call(spot, spot, T_weekly, r, iv)
        assert 0.35 < p < 0.55

    def test_boundary_zero_time_otm(self, spot, r, iv):
        """At T=0, OTM call (S < K) → prob = 1.0."""
        K_otm = spot * 1.10
        assert prob_otm_call(spot, K_otm, 0, r, iv) == 1.0

    def test_boundary_zero_time_itm(self, spot, r, iv):
        """At T=0, ITM call (S > K) → prob = 0.0."""
        K_itm = spot * 0.90
        assert prob_otm_call(spot, K_itm, 0, r, iv) == 0.0

    def test_higher_strike_higher_prob(self, spot, T_weekly, r, iv):
        """Higher strike → higher probability of expiring OTM."""
        p_low  = prob_otm_call(spot, spot * 1.05, T_weekly, r, iv)
        p_high = prob_otm_call(spot, spot * 1.20, T_weekly, r, iv)
        assert p_high > p_low

    def test_complementary_to_put_approximately(self, spot, T_weekly, r, iv):
        """
        For a strangle, prob(put OTM) + prob(call OTM) - 1 ≈ prob(profit).
        At ATM, both should be close to 0.5 and sum close to 1.
        """
        pp = prob_otm_put (spot, spot, T_weekly, r, iv)
        pc = prob_otm_call(spot, spot, T_weekly, r, iv)
        assert abs(pp + pc - 1.0) < 0.15   # loose bound — not exact complements


# ── Cross-function invariants ─────────────────────────────────────────────────

class TestCrossFunctionInvariants:

    def test_put_call_parity_many_strikes(self, spot, T_weekly, r, iv):
        """Put-call parity holds across a range of strikes."""
        strikes = [spot * m for m in (0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30)]
        for K in strikes:
            C = bs_call(spot, K, T_weekly, r, iv)
            P = bs_put (spot, K, T_weekly, r, iv)
            parity = C - P - (spot - K * math.exp(-r * T_weekly))
            assert abs(parity) < PRICE_TOL, f"Parity failed at K={K}: {parity}"

    def test_higher_prob_otm_lower_premium_put(self, spot, T_weekly, r, iv):
        """
        Higher probability of expiring OTM (lower strike) should mean lower premium.
        This checks that probability and price are consistent.
        """
        K_low  = spot * 0.80
        K_high = spot * 0.95
        prob_low  = prob_otm_put(spot, K_low,  T_weekly, r, iv)
        prob_high = prob_otm_put(spot, K_high, T_weekly, r, iv)
        prem_low  = bs_put(spot, K_low,  T_weekly, r, iv)
        prem_high = bs_put(spot, K_high, T_weekly, r, iv)
        assert prob_low > prob_high    # lower strike → higher prob OTM
        assert prem_low < prem_high    # lower strike → lower premium

    def test_price_scales_with_spot(self, T_weekly, r, iv):
        """
        Black-Scholes is homogeneous of degree 1 in (S, K).
        Doubling both S and K should double the option price.
        """
        S1, K1 = 1000.0, 900.0
        S2, K2 = 2000.0, 1800.0
        P1 = bs_put(S1, K1, T_weekly, r, iv)
        P2 = bs_put(S2, K2, T_weekly, r, iv)
        assert abs(P2 / P1 - 2.0) < 1e-4
