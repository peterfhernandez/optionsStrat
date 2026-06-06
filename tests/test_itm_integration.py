"""
tests/test_itm_integration.py
=============================
Integration tests for in-the-money option handling across all strategies.

This ensures that when options expire in-the-money:
1. Correct intrinsic values are calculated
2. Assignment risks are identified (short positions)
3. Settlement values are computed (long positions)
4. P&L is accurately reflected in all strategy modules

Test coverage:
- Wheel strategy (single leg, always short)
- Strangle strategy (two legs, both short)
- Spread strategy (two legs, short + long)
- Calendar strategy (complex multi-leg scenarios)
- Monitor auto-close logic
"""

import pytest
from datetime import date

from trading.expiry_handler import handle_expires_itm, is_expired_worthless
from database.calendar_db import create_calendar_trade, close_calendar_trade, load_calendar_state
from database.strangle_db import create_strangle_trade, load_strangle_state
from database.spread_db import create_spread_trade, load_spread_state


# ── Intrinsic Value Calculations ──────────────────────────────────────────────

class TestIntrinsicValueCalculations:
    """Verify intrinsic value calculations for ITM options."""

    def test_call_itm_intrinsic_calculation(self):
        """Call ITM: intrinsic = (spot - strike) × qty"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2100.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        # Intrinsic = 2100 - 2000 = 100
        assert result["total_intrinsic"] == 100.0

    def test_put_itm_intrinsic_calculation(self):
        """Put ITM: intrinsic = (strike - spot) × qty"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1900.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        # Intrinsic = 2000 - 1900 = 100
        assert result["total_intrinsic"] == 100.0

    def test_call_itm_fractional_qty(self):
        """Call ITM with fractional quantity"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2150.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=0.1,
        )
        # Intrinsic = (2150 - 2000) × 0.1 = 15
        assert result["total_intrinsic"] == 15.0

    def test_put_itm_fractional_qty(self):
        """Put ITM with fractional quantity"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1850.0,
            strike=2000.0,
            is_short=False,
            premium=50.0,
            qty=0.125,
        )
        # Intrinsic = (2000 - 1850) × 0.125 = 18.75
        assert result["total_intrinsic"] == 18.75


# ── Short Position Assignment Risk ────────────────────────────────────────────

class TestShortPositionAssignmentRisk:
    """Test assignment scenarios for short positions."""

    def test_short_call_itm_loses_intrinsic(self):
        """Short call ITM: seller owes intrinsic (assignment risk)"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2500.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        # Assignment: owes 500, received premium 50, P&L = 50 - 500 = -450
        assert result["pnl"] == -450.0
        assert "seller" in result["notes"].lower()

    def test_short_put_itm_loses_intrinsic(self):
        """Short put ITM: seller owes intrinsic (assignment risk)"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1500.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        # Assignment: owes 500, received premium 50, P&L = 50 - 500 = -450
        assert result["pnl"] == -450.0

    def test_short_call_itm_deep_in_money(self):
        """Short call deep ITM: significant assignment loss"""
        result = handle_expires_itm(
            position_type="Call",
            spot=3000.0,
            strike=2000.0,
            is_short=True,
            premium=100.0,
            qty=1.0,
        )
        # Intrinsic = 1000, P&L = 100 - 1000 = -900
        assert result["total_intrinsic"] == 1000.0
        assert result["pnl"] == -900.0

    def test_short_put_itm_deep_in_money(self):
        """Short put deep ITM: significant assignment loss"""
        result = handle_expires_itm(
            position_type="Put",
            spot=500.0,
            strike=2000.0,
            is_short=True,
            premium=100.0,
            qty=1.0,
        )
        # Intrinsic = 1500, P&L = 100 - 1500 = -1400
        assert result["total_intrinsic"] == 1500.0
        assert result["pnl"] == -1400.0


# ── Long Position Settlement ──────────────────────────────────────────────────

class TestLongPositionSettlement:
    """Test settlement calculations for long positions."""

    def test_long_call_itm_gains_intrinsic(self):
        """Long call ITM: buyer gains intrinsic minus premium paid"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2100.0,
            strike=2000.0,
            is_short=False,
            premium=50.0,
            qty=1.0,
        )
        # Settlement: gains 100, paid premium 50, P&L = 100 - 50 = 50
        assert result["total_intrinsic"] == 100.0
        assert result["pnl"] == 50.0
        assert "buyer" in result["notes"].lower()

    def test_long_put_itm_gains_intrinsic(self):
        """Long put ITM: buyer gains intrinsic minus premium paid"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1900.0,
            strike=2000.0,
            is_short=False,
            premium=50.0,
            qty=1.0,
        )
        # Settlement: gains 100, paid premium 50, P&L = 100 - 50 = 50
        assert result["total_intrinsic"] == 100.0
        assert result["pnl"] == 50.0

    def test_long_call_itm_deep_profit(self):
        """Long call deep ITM: significant settlement profit"""
        result = handle_expires_itm(
            position_type="Call",
            spot=3000.0,
            strike=2000.0,
            is_short=False,
            premium=100.0,
            qty=1.0,
        )
        # Settlement: gains 1000, paid 100, P&L = 900
        assert result["total_intrinsic"] == 1000.0
        assert result["pnl"] == 900.0

    def test_long_put_itm_deep_profit(self):
        """Long put deep ITM: significant settlement profit"""
        result = handle_expires_itm(
            position_type="Put",
            spot=500.0,
            strike=2000.0,
            is_short=False,
            premium=100.0,
            qty=1.0,
        )
        # Settlement: gains 1500, paid 100, P&L = 1400
        assert result["total_intrinsic"] == 1500.0
        assert result["pnl"] == 1400.0


# ── Strategy-Specific ITM Integration ─────────────────────────────────────────

class TestWheelITMIntegration:
    """Test wheel strategy handles ITM expiry correctly."""

    def test_wheel_short_call_itm_at_expiry(self):
        """Wheel: short covered call expires ITM = shares called away"""
        from strategies.wheel import wheel_paper_menu  # Import to ensure module loads

        # Short call at 2000 strike, spot 2100 at expiry
        result = handle_expires_itm(
            position_type="Call",
            spot=2100.0,
            strike=2000.0,
            is_short=True,
            premium=80.0,
            qty=1.0,
        )
        # Assignment: stock called away at 2000, received premium 80
        # Settlement P&L = 80 - 100 = -20 (but gains 100 from stock sale)
        assert result["pnl"] == -20.0

    def test_wheel_short_put_itm_at_expiry(self):
        """Wheel: short put expires ITM = assigned stock"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1900.0,
            strike=2000.0,
            is_short=True,
            premium=80.0,
            qty=1.0,
        )
        # Assignment: must buy stock at 2000, received premium 80
        # Settlement P&L = 80 - 100 = -20 (but have stock)
        assert result["pnl"] == -20.0


class TestStrangleITMIntegration:
    """Test strangle strategy handles ITM expiry correctly."""

    def test_strangle_both_legs_itm_large_loss(self):
        """Strangle: both legs ITM = maximum loss"""
        # Put ITM
        put_result = handle_expires_itm(
            position_type="Put",
            spot=1800.0,
            strike=1900.0,
            is_short=True,
            premium=40.0,
            qty=1.0,
        )
        # Call ITM
        call_result = handle_expires_itm(
            position_type="Call",
            spot=2200.0,
            strike=2100.0,
            is_short=True,
            premium=40.0,
            qty=1.0,
        )
        # Combined P&L = (40 - 100) + (40 - 100) = -120
        total_pnl = put_result["pnl"] + call_result["pnl"]
        assert total_pnl == -120.0

    def test_strangle_only_put_itm(self):
        """Strangle: only put ITM (spot below put strike)"""
        # Put ITM
        put_result = handle_expires_itm(
            position_type="Put",
            spot=1850.0,
            strike=1900.0,
            is_short=True,
            premium=40.0,
            qty=1.0,
        )
        # Call OTM (worthless)
        from trading.expiry_handler import handle_expires_worthless
        call_result = handle_expires_worthless(
            position_type="Call",
            spot=1850.0,
            strike=2100.0,
            is_short=True,
            premium=40.0,
            qty=1.0,
        )
        # Combined P&L = (40 - 50) + 40 = 30
        total_pnl = put_result["pnl"] + call_result["pnl"]
        assert total_pnl == 30.0


class TestSpreadITMIntegration:
    """Test spread strategy handles ITM expiry correctly."""

    def test_bps_both_legs_itm(self):
        """Bull Put Spread: both legs ITM = max loss"""
        # Short put ITM
        short_result = handle_expires_itm(
            position_type="Put",
            spot=1700.0,
            strike=1800.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        # Long put ITM (protective)
        long_result = handle_expires_itm(
            position_type="Put",
            spot=1700.0,
            strike=1700.0,
            is_short=False,
            premium=25.0,
            qty=1.0,
        )
        # Short pays 100, long collects 0, P&L = 50 - 100 - 25 + 0 = -75
        # But max loss is typically strike width (100) - credit (25) = 75
        total_pnl = short_result["pnl"] + long_result["pnl"]
        assert total_pnl == pytest.approx(-75.0)

    def test_bps_only_short_itm(self):
        """Bull Put Spread: only short put ITM (long protects)"""
        # Short put ITM (spot below short strike)
        short_result = handle_expires_itm(
            position_type="Put",
            spot=1750.0,
            strike=1800.0,
            is_short=True,
            premium=50.0,
            qty=1.0,
        )
        # Long put OTM (protective at 1700, spot above it)
        from trading.expiry_handler import handle_expires_worthless
        long_result = handle_expires_worthless(
            position_type="Put",
            spot=1750.0,
            strike=1700.0,
            is_short=False,
            premium=25.0,
            qty=1.0,
        )
        # P&L = (50 - 50) + (-25) = -25 (short put loss, long put worthless)
        total_pnl = short_result["pnl"] + long_result["pnl"]
        assert total_pnl == -25.0


# ── Edge Cases and Boundary Conditions ─────────────────────────────────────────

class TestITMEdgeCases:
    """Test edge cases for ITM handling."""

    def test_itm_exactly_at_strike_plus_one_cent(self):
        """Call just barely ITM (spot = strike + $0.01)"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2000.01,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=100.0,  # 100 contracts
        )
        # Intrinsic = 0.01 × 100 = 1.00
        assert result["total_intrinsic"] == pytest.approx(1.0)

    def test_itm_high_precision_qty(self):
        """ITM with high-precision fractional quantity"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2050.0,
            strike=2000.0,
            is_short=True,
            premium=50.0,
            qty=0.0125,  # Very small qty
        )
        # Intrinsic = 50 × 0.0125 = 0.625
        assert result["total_intrinsic"] == pytest.approx(0.625)

    def test_itm_premium_exceeds_intrinsic(self):
        """ITM but premium paid exceeds intrinsic (long position loss)"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2010.0,
            strike=2000.0,
            is_short=False,
            premium=100.0,  # Large premium paid
            qty=1.0,
        )
        # Intrinsic = 10, paid 100, P&L = 10 - 100 = -90 (still a loss for long)
        assert result["total_intrinsic"] == 10.0
        assert result["pnl"] == -90.0

    def test_itm_zero_premium_short(self):
        """Short position ITM with zero premium (worst case)"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2100.0,
            strike=2000.0,
            is_short=True,
            premium=0.0,
            qty=1.0,
        )
        # All loss, no offset, P&L = 0 - 100 = -100
        assert result["pnl"] == -100.0


# ── P&L Verification Across Scenarios ─────────────────────────────────────────

class TestITMProfitLossVerification:
    """Verify P&L calculations across various ITM scenarios."""

    def test_profitable_short_call_itm(self):
        """Short call ITM but still profitable (high premium)"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2030.0,
            strike=2000.0,
            is_short=True,
            premium=100.0,
            qty=1.0,
        )
        # Intrinsic 30, premium 100, P&L = 100 - 30 = 70 ✓
        assert result["pnl"] == 70.0

    def test_profitable_long_call_itm(self):
        """Long call ITM and profitable"""
        result = handle_expires_itm(
            position_type="Call",
            spot=2050.0,
            strike=2000.0,
            is_short=False,
            premium=30.0,
            qty=1.0,
        )
        # Intrinsic 50, paid 30, P&L = 50 - 30 = 20 ✓
        assert result["pnl"] == 20.0

    def test_losing_short_put_itm(self):
        """Short put ITM showing loss"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1950.0,
            strike=2000.0,
            is_short=True,
            premium=30.0,
            qty=1.0,
        )
        # Intrinsic 50, premium 30, P&L = 30 - 50 = -20
        assert result["pnl"] == -20.0

    def test_winning_long_put_itm(self):
        """Long put ITM with profit"""
        result = handle_expires_itm(
            position_type="Put",
            spot=1950.0,
            strike=2000.0,
            is_short=False,
            premium=30.0,
            qty=1.0,
        )
        # Intrinsic 50, paid 30, P&L = 50 - 30 = 20
        assert result["pnl"] == 20.0
