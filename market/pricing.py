"""
pricing.py
==========
Black-Scholes option pricing and probability helpers for ETH options.

Functions
---------
ncdf(x)                         Standard normal CDF
bs_put(S, K, T, r, v)          Black-Scholes put price
bs_call(S, K, T, r, v)         Black-Scholes call price
prob_otm_put(S, K, T, r, v)    Probability put expires OTM (worthless)
prob_otm_call(S, K, T, r, v)   Probability call expires OTM (worthless)

All prices are in USD. Volatility (v) and risk-free rate (r) are decimals,
e.g. 80% IV → v=0.80, 5% rate → r=0.05. Time (T) is in years.
"""

import math


# ── Normal CDF ────────────────────────────────────────────────────────────────

def ncdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * math.erfc(-x / math.sqrt(2))

# ── d1 / d2 helpers ───────────────────────────────────────────────────────────
 
def _d1(S: float, K: float, T: float, r: float, v: float) -> float:
    """
    Black-Scholes d1 term.
 
    d1 = [ ln(S/K) + (r + v²/2) * T ] / (v * √T)
    """
    return (math.log(S / K) + (r + 0.5 * v ** 2) * T) / (v * math.sqrt(T))
 
 
def _d2(S: float, K: float, T: float, r: float, v: float) -> float:
    """
    Black-Scholes d2 term.
 
    d2 = d1 - v * √T
    """
    return _d1(S, K, T, r, v) - v * math.sqrt(T)


# ── Black-Scholes pricing ─────────────────────────────────────────────────────

def bs_put(S: float, K: float, T: float, r: float, v: float) -> float:
    """
    Black-Scholes price of a European put option.

    Parameters
    ----------
    S : float  Current underlying price (USD)
    K : float  Strike price (USD)
    T : float  Time to expiry in years (e.g. 7/365 for one week)
    r : float  Risk-free rate as a decimal (e.g. 0.05 for 5%)
    v : float  Implied volatility as a decimal (e.g. 0.80 for 80%)

    Returns
    -------
    float  Put option price per unit of underlying
    """
    if T <= 0 or v <= 0:
        return max(K - S, 0)
    d1 = _d1(S, K, T, r, v)
    d2 = _d2(S, K, T, r, v)
    return K * math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1)


def bs_call(S: float, K: float, T: float, r: float, v: float) -> float:
    """
    Black-Scholes price of a European call option.

    Parameters
    ----------
    S : float  Current underlying price (USD)
    K : float  Strike price (USD)
    T : float  Time to expiry in years
    r : float  Risk-free rate as a decimal
    v : float  Implied volatility as a decimal

    Returns
    -------
    float  Call option price per unit of underlying
    """
    if T <= 0 or v <= 0:
        return max(S - K, 0)
    d1 = _d1(S, K, T, r, v)
    d2 = _d2(S, K, T, r, v)
    return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)


# ── Probability helpers ───────────────────────────────────────────────────────

def prob_otm_put(S: float, K: float, T: float, r: float, v: float) -> float:
    """
    Probability that a put expires out-of-the-money (i.e. spot > strike).

    Uses the risk-neutral probability N(d2) from Black-Scholes.
    Returns a value in [0, 1].
    """
    if T <= 0 or v <= 0:
        return 1.0 if S > K else 0.0
    return ncdf(_d2(S, K, T, r, v))


def prob_otm_call(S: float, K: float, T: float, r: float, v: float) -> float:
    """
    Probability that a call expires out-of-the-money (i.e. spot < strike).

    Uses the risk-neutral probability N(-d2) from Black-Scholes.
    Returns a value in [0, 1].
    """
    if T <= 0 or v <= 0:
        return 1.0 if S < K else 0.0
    return ncdf(-_d2(S, K, T, r, v))
