"""
strategies/scanner.py
=====================
Cross-asset, cross-strategy trade recommendation scanner.

Scans every (asset, strategy, OTM level) combination, computes key
metrics for each candidate, and ranks them by two criteria:

  1. Highest probability of profit with a reasonable return
  2. Best annualised return regardless of probability

Generates exactly 3 propositions per instrument/strategy pair
(one per OTM level in config.OTM_LEVELS), consistent with the
analysis sections in wheel.py and strangle.py.

Public API
----------
run_scanner(active_spot, active_iv, active_asset, days)
    Fetch live data, compute all candidates, display ranked results.

Internal helpers
----------------
_build_candidates(asset, spot, iv, days)  Build all candidates for one asset
_display_candidates(candidates)           Print the full candidate table
_display_ranked(label, candidates)        Print a ranked recommendation block
"""

from dataclasses import dataclass

from config  import (
    SUPPORTED_ASSETS, BUDGET_USD, RISK_FREE_RATE, OTM_LEVELS,
)
from pricing import bs_put, bs_call, prob_otm_put, prob_otm_call
from display import hdr, sub, inf, warn, ok, GR, RD, CY, YL, GY, WH, B, R


# ── Minimum yield threshold for "high probability" ranking ───────────────────
# Candidates below this annualised yield are excluded from ranking #1
# even if they have high probability — too cheap to bother.
MIN_YIELD_PCT = 20.0   # 20% annualised


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """A single tradeable proposition with all relevant metrics."""
    asset:        str
    strategy:     str    # "CSP", "CC", "Strangle"
    otm_pct:      float  # e.g. 0.10, 0.15, 0.20
    spot:         float
    iv:           float
    strike:       str    # formatted string e.g. "$1800" or "$1700/$2000"
    premium:      float  # total premium in USD
    yield_ann:    float  # annualised yield as a percentage
    prob_profit:  float  # probability of full profit (0–100)
    days:         int

    # Strangle-only fields (None for wheel)
    put_strike:   float = None
    call_strike:  float = None
    be_lo:        float = None
    be_hi:        float = None


# ── Candidate builder ─────────────────────────────────────────────────────────

def _build_candidates(
    asset: str,
    spot:  float,
    iv:    float,
    days:  int,
) -> list[Candidate]:
    """
    Build all (strategy, OTM level) candidates for one asset.

    Generates 3 propositions per strategy (one per OTM level),
    matching the analysis tables in show_strikes() and show_strangle_analysis().

    Returns a flat list of Candidate objects.
    """
    T   = days / 365.0
    r   = RISK_FREE_RATE
    candidates = []

    for otm in OTM_LEVELS:

        # ── Cash-Secured Put ──────────────────────────────────────────────────
        Kp  = round(spot * (1 - otm) / 10) * 10
        qty = BUDGET_USD / Kp
        pp  = bs_put(spot, Kp, T, r, iv) * qty
        yld = (pp / BUDGET_USD) * (365 / days) * 100
        pop = prob_otm_put(spot, Kp, T, r, iv) * 100
        candidates.append(Candidate(
            asset       = asset,
            strategy    = "CSP",
            otm_pct     = otm,
            spot        = spot,
            iv          = iv,
            strike      = f"${Kp:,.0f}",
            premium     = round(pp, 2),
            yield_ann   = round(yld, 1),
            prob_profit = round(pop, 1),
            days        = days,
        ))

        # ── Covered Call ──────────────────────────────────────────────────────
        Kc   = round(spot * (1 + otm) / 10) * 10
        qty2 = BUDGET_USD / spot
        cp   = bs_call(spot, Kc, T, r, iv) * qty2
        yld2 = (cp / BUDGET_USD) * (365 / days) * 100
        pop2 = prob_otm_call(spot, Kc, T, r, iv) * 100
        candidates.append(Candidate(
            asset       = asset,
            strategy    = "CC",
            otm_pct     = otm,
            spot        = spot,
            iv          = iv,
            strike      = f"${Kc:,.0f}",
            premium     = round(cp, 2),
            yield_ann   = round(yld2, 1),
            prob_profit = round(pop2, 1),
            days        = days,
        ))

        # ── Short Strangle ────────────────────────────────────────────────────
        qty3          = BUDGET_USD / spot
        sp            = bs_put (spot, Kp, T, r, iv) * qty3
        sc            = bs_call(spot, Kc, T, r, iv) * qty3
        tot           = sp + sc
        yld3          = (tot / BUDGET_USD) * (365 / days) * 100
        prem_per_unit = tot / qty3 if qty3 else 0
        be_lo         = Kp - prem_per_unit
        be_hi         = Kc + prem_per_unit
        p_lo          = prob_otm_put (spot, Kp, T, r, iv)
        p_hi          = prob_otm_call(spot, Kc, T, r, iv)
        pop3          = max(0.0, (p_lo + p_hi - 1) * 100)
        candidates.append(Candidate(
            asset       = asset,
            strategy    = "Strangle",
            otm_pct     = otm,
            spot        = spot,
            iv          = iv,
            strike      = f"${Kp:,.0f}/${Kc:,.0f}",
            premium     = round(tot, 2),
            yield_ann   = round(yld3, 1),
            prob_profit = round(pop3, 1),
            days        = days,
            put_strike  = Kp,
            call_strike = Kc,
            be_lo       = round(be_lo, 0),
            be_hi       = round(be_hi, 0),
        ))

    return candidates


# ── Display helpers ───────────────────────────────────────────────────────────

def _display_candidates(candidates: list[Candidate], days: int) -> None:
    """Print the full candidate table grouped by asset."""

    current_asset = None
    for c in candidates:
        if c.asset != current_asset:
            current_asset = c.asset
            sub(f"{c.asset}  spot=${c.spot:,.2f}   IV={c.iv*100:.0f}%   {days}d")
            print(
                f"\n  {'Strategy':<12}{'OTM%':<7}{'Strike(s)':<22}"
                f"{'Premium':<11}{'Yield/yr':<11}{'P(Profit)':<12}{'Notes'}"
            )
            print(f"  {'─' * 85}")

        notes = ""
        if c.strategy == "Strangle":
            notes = f"B/E ${c.be_lo:,.0f}–${c.be_hi:,.0f}"

        colour = GR if c.prob_profit >= 70 else YL if c.prob_profit >= 55 else WH
        print(
            f"  {colour}{c.strategy:<12}{c.otm_pct*100:.0f}%{'':4}"
            f"{c.strike:<22}${c.premium:<9.2f}"
            f"{c.yield_ann:>6.1f}%/yr  "
            f"{c.prob_profit:>5.1f}%     {notes}{R}"
        )
    print(f"\n  {GY}Premiums estimated via Black-Scholes | Budget: ${BUDGET_USD:.0f}{R}")


def _display_ranked(
    rank_label: str,
    emoji: str,
    candidates: list[Candidate],
    limit: int = 5,
) -> None:
    """Print a ranked recommendation block with medal positions."""
    medals = ["🥇", "🥈", "🥉", "  4.", "  5."]
    sub(rank_label)
    print(
        f"\n  {'':4}{'Asset':<7}{'Strategy':<12}{'OTM%':<7}{'Strike(s)':<22}"
        f"{'Premium':<11}{'Yield/yr':<11}{'P(Profit)':<12}{'IV'}"
    )
    print(f"  {'─' * 95}")

    for i, c in enumerate(candidates[:limit]):
        medal  = medals[i] if i < len(medals) else f"  {i+1}."
        colour = GR if i == 0 else YL if i == 1 else WH
        print(
            f"  {medal} {colour}{c.asset:<7}{c.strategy:<12}"
            f"{c.otm_pct*100:.0f}%{'':4}{c.strike:<22}"
            f"${c.premium:<9.2f}{c.yield_ann:>6.1f}%/yr  "
            f"{c.prob_profit:>5.1f}%     {c.iv*100:.0f}%{R}"
        )


# ── Public API ────────────────────────────────────────────────────────────────

def run_scanner(
    active_spot:  float,
    active_iv:    float,
    active_asset: str,
    days:         int,
) -> None:
    """
    Scan every (asset, strategy, OTM level) combination and display
    ranked trade recommendations.

    Fetches live spot and IV per asset, reusing already-fetched values
    for the active asset to avoid a redundant API call.

    Parameters
    ----------
    active_spot  : float  Current spot for the active asset
    active_iv    : float  Current IV for the active asset
    active_asset : str    Currently selected asset
    days         : int    Days to expiry
    """
    from market_data import get_spot_price, get_deribit_iv

    hdr("Trade Recommendation Scanner")
    print(f"  {GY}Fetching live data and computing all candidates...{R}\n")

    all_candidates = []

    for asset in SUPPORTED_ASSETS:
        # Reuse active asset data; fetch fresh for others
        if asset == active_asset:
            spot = active_spot
            iv   = active_iv
        else:
            spot = get_spot_price(asset)
            if not spot:
                warn(f"Could not fetch {asset} price — skipping")
                continue
            iv = get_deribit_iv(asset, spot, days)
            if not iv:
                warn(f"Could not fetch {asset} IV — using fallback")
                iv = active_iv

        ok(f"{asset}: spot=${spot:,.2f}  IV={iv*100:.0f}%")
        all_candidates.extend(_build_candidates(asset, spot, iv, days))

    if not all_candidates:
        warn("No candidates generated — check your connection.")
        return

    # ── Full candidate table ──────────────────────────────────────────────────
    print()
    hdr("All Candidates")
    _display_candidates(all_candidates, days)

    # ── Ranking 1: Highest probability with reasonable return ─────────────────
    print()
    hdr("Ranked Recommendations")
    qualified = [c for c in all_candidates if c.yield_ann >= MIN_YIELD_PCT]
    by_prob   = sorted(qualified, key=lambda c: c.prob_profit, reverse=True)
    _display_ranked(
        rank_label = f"① Highest Probability  {GY}(yield ≥ {MIN_YIELD_PCT:.0f}%/yr){R}",
        emoji      = "🎯",
        candidates = by_prob,
    )

    # ── Ranking 2: Best annualised return ─────────────────────────────────────
    print()
    by_yield = sorted(all_candidates, key=lambda c: c.yield_ann, reverse=True)
    _display_ranked(
        rank_label = "② Best Return",
        emoji      = "💰",
        candidates = by_yield,
    )

    print(f"""
  {GY}Strategy key:
  CSP      = Cash-Secured Put (wheel leg 1)
  CC       = Covered Call     (wheel leg 2)
  Strangle = Short Strangle   (simultaneous OTM put + call)

  {YL}⚠ Strangles carry unlimited loss potential on the call side.
  {YL}⚠ All figures are Black-Scholes estimates — not financial advice.{R}
""")
    warn(f"Min yield filter for ranking ①: {MIN_YIELD_PCT:.0f}%/yr  "
         f"— adjust MIN_YIELD_PCT in scanner.py to change this.")
