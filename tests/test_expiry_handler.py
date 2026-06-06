"""
tests/test_expiry_handler.py
============================
Unit tests for trading/expiry_handler.py — shared option expiry logic.

Covers:
- is_expired_worthless(): call/put, spot-strike relationships
- handle_expires_worthless(): short/long, calls/puts, P&L calculations
- handle_expires_itm(): short/long, calls/puts, intrinsic calculations
"""

import pytest

from trading.expiry_handler import (
    is_expired_worthless,
    handle_expires_worthless,
    handle_expires_itm,
)


# ── is_expired_worthless ──────────────────────────────────────────────────────

class TestIsExpiredWorthless:
    """Test expiry determination logic."""

    def test_call_worthless_spot_below_strike(self):
        """Call expires worthless when spot < strike."""
        assert is_expired_worthless("Call", 1900.0, 2000.0) is True

    def test_call_itm_spot_above_strike(self):
        """Call is ITM when spot > strike."""
        assert is_expired_worthless("Call", 2100.0, 2000.0) is False

    def test_call_at_strike(self):
        """Call at the strike is ITM (spot not below)."""
        assert is_expired_worthless("Call", 2000.0, 2000.0) is False

    def test_put_worthless_spot_above_strike(self):
        """Put expires worthless when spot > strike."""
        assert is_expired_worthless("Put", 2100.0, 2000.0) is True

    def test_put_itm_spot_below_strike(self):
        """Put is ITM when spot < strike."""
        assert is_expired_worthless("Put", 1900.0, 2000.0) is False

    def test_put_at_strike(self):
        """Put at the strike is ITM (spot not above)."""
        assert is_expired_worthless("Put", 2000.0, 2000.0) is False

    def test_call_lowercase(self):
        """Lowercase input should work."""
        assert is_expired_worthless("call", 1900.0, 2000.0) is True

    def test_put_lowercase(self):
        """Lowercase input should work."""
        assert is_expired_worthless("put", 2100.0, 2000.0) is True


# ── handle_expires_worthless ──────────────────────────────────────────────────

class TestHandleExpiresWorthless:
    """Test worthless expiration P&L calculations."""

    def test_short_call_worthless_keeps_premium(self):
        """Short call expires worthless → keep full premium."""
        result = handle_expires_worthless(
            position_type="Call",
            spot=1900.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=0.1,
        )
        assert result["expired_worthless"] is True
        assert result["pnl"] == pytest.approx(5.0)  # 50 * 0.1
        assert result["total_intrinsic"] == 0.0
        assert "keeps" in result["notes"].lower()

    def test_long_call_worthless_loses_premium(self):
        """Long call expires worthless → lose full premium paid."""
        result = handle_expires_worthless(
            position_type="Call",
            spot=1900.0,
            strike=2000.0,
            is_short=False,
            premium=50.0,
            qty=0.1,
        )
        assert result["expired_worthless"] is True
        assert result["pnl"] == pytest.approx(-5.0)  # -50 * 0.1
        assert result["total_intrinsic"] == 0.0
        assert "loses" in result["notes"].lower()

    def test_short_put_worthless_keeps_premium(self):
        """Short put expires worthless → keep full premium."""
        result = handle_expires_worthless(
            position_type="Put",
            spot=2100.0,
            strike=2000.0,
            is_short=True,
            premium=40.0,
            qty=0.125,
        )
        assert result["expired_worthless"] is True
        assert result["pnl"] == pytest.approx(5.0)  # 40 * 0.125
        assert result["total_intrinsic"] == 0.0

    def test_long_put_worthless_loses_premium(self):
        """Long put expires worthless → lose full premium paid."""
        result = handle_expires_worthless(
            position_type="Put",
            spot=2100.0,
            strike=2000.0,
            is_short=False,
            premium=40.0,
            qty=0.125,
        )
        assert result["expired_worthless"] is True
        assert result["pnl"] == pytest.approx(-5.0)  # -40 * 0.125
        assert result["total_intrinsic"] == 0.0

    def test_worthless_with_qty_1(self):
        """Default qty=1.0 should work."""
        result = handle_expires_worthless(
            position_type="Call",
            spot=1800.0,
            strike=2000.0,
            is_short=True,
            premium=25.0,
        )
        assert result["pnl"] == pytest.approx(25.0)

    def test_worthless_rejects_itm_call(self):
        """Should reject ITM call with helpful error."""
        with pytest.raises(ValueError, match="in-the-money"):
            handle_expires_worthless(
                position_type="Call",
                spot=2100.0,  # ITM
                strike=2000.0,
                is_short=True,
                premium=50.0,
            )

    def test_worthless_rejects_itm_put(self):
        """Should reject ITM put with helpful error."""
        with pytest.raises(ValueError, match="in-the-money"):
            handle_expires_worthless(
                position_type="Put",
                spot=1900.0,  # ITM
                strike=2000.0,
                is_short=True,
                premium=40.0,
            )


# ── handle_expires_itm ────────────────────────────────────────────────────────

class TestHandleExpiresITM:
    """Test in-the-money expiration P&L calculations."""

    def test_short_call_itm_loses_intrinsic(self):
        """Short call expires ITM → owe intrinsic value minus premium."""
        result = handle_expires_itm(
            position_type="Call",
            spot=2100.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=0.1,
        )
        assert result["expired_worthless"] is False
        assert result["total_intrinsic"] == pytest.approx(10.0)  # (2100-2000) * 0.1
        # P&L = premium - intrinsic = 5 - 10 = -5
        assert result["pnl"] == pytest.approx(-5.0)

    def test_long_call_itm_gains_intrinsic(self):
        """Long call expires ITM → gain intrinsic minus premium paid."""
        result = handle_expires_itm(
            position_type="Call",
            spot=2100.0,
            strike=2000.0,
            is_short=False,
            premium=50.0,
            qty=0.1,
        )
        assert result["expired_worthless"] is False
        assert result["total_intrinsic"] == pytest.approx(10.0)
        # P&L = intrinsic - premium = 10 - 5 = 5
        assert result["pnl"] == pytest.approx(5.0)

    def test_short_put_itm_loses_intrinsic(self):
        """Short put expires ITM → owe intrinsic minus premium."""
        result = handle_expires_itm(
            position_type="Put",
            spot=1900.0,
            strike=2000.0,
            is_short=True,
            premium=40.0,
            qty=0.125,
        )
        assert result["expired_worthless"] is False
        assert result["total_intrinsic"] == pytest.approx(12.5)  # (2000-1900) * 0.125
        # P&L = premium - intrinsic = 5 - 12.5 = -7.5
        assert result["pnl"] == pytest.approx(-7.5)

    def test_long_put_itm_gains_intrinsic(self):
        """Long put expires ITM → gain intrinsic minus premium paid."""
        result = handle_expires_itm(
            position_type="Put",
            spot=1900.0,
            strike=2000.0,
            is_short=False,
            premium=40.0,
            qty=0.125,
        )
        assert result["expired_worthless"] is False
        assert result["total_intrinsic"] == pytest.approx(12.5)
        # P&L = intrinsic - premium = 12.5 - 5 = 7.5
        assert result["pnl"] == pytest.approx(7.5)

    def test_itm_call_at_strike_zero_intrinsic(self):
        """Call at strike has zero intrinsic at expiry."""
        result = handle_expires_itm(
            position_type="Call",
            spot=2000.0,
            strike=2000.0,
            is_short=True,
            premium=30.0,
            qty=0.1,
        )
        # At the strike, a call is NOT worthless (would be ITM in practice)
        # But intrinsic = 0 when exactly at strike
        assert result["total_intrinsic"] == pytest.approx(0.0)
        assert result["pnl"] == pytest.approx(3.0)  # premium - 0

    def test_itm_rejects_worthless_call(self):
        """Should reject OTM call with helpful error."""
        with pytest.raises(ValueError, match="out-of-the-money"):
            handle_expires_itm(
                position_type="Call",
                spot=1900.0,  # OTM
                strike=2000.0,
                is_short=True,
                premium=50.0,
            )

    def test_itm_rejects_worthless_put(self):
        """Should reject OTM put with helpful error."""
        with pytest.raises(ValueError, match="out-of-the-money"):
            handle_expires_itm(
                position_type="Put",
                spot=2100.0,  # OTM
                strike=2000.0,
                is_short=True,
                premium=40.0,
            )


# ── Edge cases and integrations ───────────────────────────────────────────────

class TestExpiryEdgeCases:
    """Test edge cases and real-world scenarios."""

    def test_large_premium_short_call(self):
        """Large premium short call expires worthless."""
        result = handle_expires_worthless(
            position_type="Call",
            spot=1000.0,
            strike=5000.0,
            is_short=True,
            premium=500.0,
            qty=1.0,
        )
        assert result["pnl"] == 500.0

    def test_small_premium_long_put(self):
        """Small premium long put expires worthless."""
        result = handle_expires_worthless(
            position_type="Put",
            spot=3000.0,
            strike=1000.0,
            is_short=False,
            premium=2.0,
            qty=0.01,
        )
        assert result["pnl"] == pytest.approx(-0.02)

    def test_itm_call_deep_in_the_money(self):
        """Deep ITM call with large intrinsic."""
        result = handle_expires_itm(
            position_type="Call",
            spot=3000.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        assert result["total_intrinsic"] == 1000.0
        assert result["pnl"] == pytest.approx(-950.0)

    def test_itm_put_deep_in_the_money(self):
        """Deep ITM put with large intrinsic."""
        result = handle_expires_itm(
            position_type="Put",
            spot=500.0,
            strike=2000.0,
            is_short=False,
            premium=100.0,
            qty=1.0,
        )
        assert result["total_intrinsic"] == 1500.0
        assert result["pnl"] == pytest.approx(1400.0)

    def test_pnl_rounding(self):
        """P&L should be rounded to 4 decimals."""
        result = handle_expires_worthless(
            position_type="Call",
            spot=1900.0,
            strike=2000.0,
            is_short=True,
            premium=33.333333,
            qty=0.3,
        )
        # 33.333333 * 0.3 = 9.9999999, rounded to 4 decimals = 10.0000
        assert result["pnl"] == 10.0
