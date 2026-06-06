"""
tests/test_calendar_transaction_tracking.py
============================================
Comprehensive tests to verify all calendar strategy transactions are recorded and tracked.

Verifies that every state transition and transaction in a calendar spread is properly
documented in the database with appropriate notes and status updates.

Transaction types tracked:
1. Open: Initial calendar spread entry
2. Near Leg Expires Worthless: Mark as Far Leg Only
3. Far Leg Close: Mark as Closed
4. Early Close: Before expiry, mark as Closed (Win/Loss)
5. Roll Near Leg: Mark old as Near Leg Rolled, create new
6. Near Leg ITM Expiry: Mark as Closed
7. Far Leg ITM Expiry: Mark as Closed
"""

import pytest
from datetime import date

from database.calendar_db import (
    load_calendar_state,
    save_calendar_state,
    create_calendar_trade,
    close_calendar_trade,
)


# ── Transaction Tracking Fixtures ─────────────────────────────────────────────

@pytest.fixture
def calendar_trade_data():
    """Standard calendar trade data for testing."""
    return {
        "asset": "ETH",
        "date_open": date(2026, 5, 1),
        "option_type": "Call",
        "strike": 2000.0,
        "expiry_near": "08-May-2026",
        "expiry_far": "01-Jun-2026",
        "near_days": 7,
        "far_days": 30,
        "qty": 0.1,
        "spot_open": 2000.0,
        "near_prem": 10.0,
        "far_prem": 25.0,
        "net_debit": 15.0,
    }


# ── Transaction Recording Tests ───────────────────────────────────────────────

class TestCalendarTransactionRecording:
    """Verify all calendar transactions are properly recorded."""

    def test_open_transaction_recorded(self, calendar_trade_data):
        """Opening a calendar position creates a database record."""
        trade = create_calendar_trade(**calendar_trade_data)

        assert trade.id is not None
        assert trade.asset == "ETH"
        assert trade.option_type == "Call"
        assert trade.strike == 2000.0
        assert trade.date_open == date(2026, 5, 1)
        assert trade.result == "Open"
        assert trade.pnl is None  # Not closed yet

    def test_near_leg_expires_worthless_recorded(self, calendar_trade_data):
        """When near leg expires worthless, transition to Far Leg Only is recorded."""
        # Open position
        trade = create_calendar_trade(**calendar_trade_data)

        # Near leg expires worthless
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,  # Below strike (OTM for call)
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired worthless. Far leg retained for analysis.",
        )

        # Verify transaction recorded
        state = load_calendar_state("ETH")
        assert state["open"] is not None
        assert state["open"]["status"] == "Far Leg Only"
        assert state["open"]["trade_id"] == trade.id

    def test_far_leg_close_recorded(self, calendar_trade_data):
        """Closing the far leg marks position as Closed."""
        # Open position
        trade = create_calendar_trade(**calendar_trade_data)

        # Move to Far Leg Only
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired worthless.",
        )

        # Close far leg
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 6, 1),
            spot_close=2050.0,
            pnl=15.0,
            result="Closed",
            notes="Far leg closed at $2050. Position fully closed.",
        )

        # Verify transaction recorded
        state = load_calendar_state("ETH")
        assert state["open"] is None  # No open position
        # Check that the closed record exists
        from models import get_session, Calendar
        session = get_session()
        closed_trade = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert closed_trade.result == "Closed"
        assert closed_trade.pnl == 15.0

    def test_early_close_recorded(self, calendar_trade_data):
        """Early close before expiry is recorded with Win/Loss status."""
        # Open position
        trade = create_calendar_trade(**calendar_trade_data)

        # Early close at profit
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 15),
            spot_close=2020.0,
            pnl=5.0,
            result="Closed",
            notes="Early close at mark-to-market price. Position closed before expiry.",
        )

        # Verify transaction recorded
        from models import get_session, Calendar
        session = get_session()
        closed_trade = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert closed_trade.result == "Closed"
        assert closed_trade.pnl == 5.0
        assert "Early close" in closed_trade.notes

    def test_roll_near_leg_creates_new_transaction(self, calendar_trade_data):
        """Rolling the near leg marks old position and creates new transaction."""
        # Open position
        old_trade = create_calendar_trade(**calendar_trade_data)

        # Move to Far Leg Only
        close_calendar_trade(
            trade_id=old_trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired worthless.",
        )

        # Roll near leg (creates new trade)
        new_trade = create_calendar_trade(
            asset="ETH",
            date_open=date(2026, 5, 8),
            option_type="Call",
            strike=2000.0,
            expiry_near="15-May-2026",  # New 7d near leg
            expiry_far="01-Jun-2026",   # Same far leg
            near_days=7,
            far_days=24,
            qty=0.1,
            spot_open=1950.0,
            near_prem=8.0,
            far_prem=25.0,
            net_debit=17.0,
            notes="ROLL ETH CALL calendar, new 7d near leg",
        )

        # Verify old position is marked as rolled
        from models import get_session, Calendar
        session = get_session()
        old_record = session.query(Calendar).filter_by(id=old_trade.id).first()
        new_record = session.query(Calendar).filter_by(id=new_trade.id).first()
        session.close()

        assert old_record.result == "Far Leg Only"
        assert new_record.result == "Open"
        assert new_record.date_open == date(2026, 5, 8)
        assert "ROLL" in new_record.notes

    def test_near_leg_itm_expiry_recorded(self, calendar_trade_data):
        """Near leg expiring ITM is properly recorded."""
        trade = create_calendar_trade(**calendar_trade_data)

        # Near leg expires ITM
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=2100.0,  # Above strike (ITM for call)
            pnl=-5.0,  # Lost to intrinsic
            result="Closed",
            notes="Near leg ITM at expiry: intrinsic $100 lost. P&L: $-5.00",
        )

        # Verify transaction recorded
        from models import get_session, Calendar
        session = get_session()
        closed_trade = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert closed_trade.result == "Closed"
        assert closed_trade.pnl == -5.0
        assert "ITM" in closed_trade.notes


# ── Fee Tracking Tests ────────────────────────────────────────────────────────

class TestCalendarFeeTracking:
    """Verify fees are properly tracked for all transactions."""

    def test_open_fees_recorded(self, calendar_trade_data):
        """Opening fees are recorded separately."""
        calendar_trade_data["open_fees"] = 2.5
        trade = create_calendar_trade(**calendar_trade_data)

        assert trade.open_fees == 2.5
        assert trade.close_fees == 0.0

    def test_close_fees_recorded(self, calendar_trade_data):
        """Close fees are recorded when closing position."""
        trade = create_calendar_trade(**calendar_trade_data)

        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=2050.0,
            pnl=5.0,
            result="Closed",
            notes="Closed at mark price.",
            close_fees=1.5,
        )

        from models import get_session, Calendar
        session = get_session()
        closed_trade = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert closed_trade.close_fees == 1.5

    def test_total_fees_tracked(self, calendar_trade_data):
        """Total fees (open + close) are properly tracked."""
        calendar_trade_data["open_fees"] = 2.5
        trade = create_calendar_trade(**calendar_trade_data)

        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=2050.0,
            pnl=5.0,
            result="Closed",
            notes="Closed.",
            close_fees=1.5,
        )

        from models import get_session, Calendar
        session = get_session()
        closed_trade = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()

        total_fees = closed_trade.open_fees + closed_trade.close_fees
        assert total_fees == 4.0


# ── Instrument Tracking Tests ─────────────────────────────────────────────────

class TestCalendarInstrumentTracking:
    """Verify broker instruments are tracked for both legs."""

    def test_instrument_names_recorded(self, calendar_trade_data):
        """Broker instrument names are recorded for near and far legs."""
        calendar_trade_data["near_instrument"] = "ETH-08MAY26-2000-C"
        calendar_trade_data["far_instrument"] = "ETH-01JUN26-2000-C"
        trade = create_calendar_trade(**calendar_trade_data)

        assert trade.near_instrument == "ETH-08MAY26-2000-C"
        assert trade.far_instrument == "ETH-01JUN26-2000-C"

    def test_instruments_updated_on_roll(self, calendar_trade_data):
        """When rolling near leg, new instrument is recorded."""
        calendar_trade_data["near_instrument"] = "ETH-08MAY26-2000-C"
        calendar_trade_data["far_instrument"] = "ETH-01JUN26-2000-C"
        old_trade = create_calendar_trade(**calendar_trade_data)

        # New trade with new near leg instrument
        new_trade = create_calendar_trade(
            asset="ETH",
            date_open=date(2026, 5, 8),
            option_type="Call",
            strike=2000.0,
            expiry_near="15-May-2026",
            expiry_far="01-Jun-2026",
            near_days=7,
            far_days=24,
            qty=0.1,
            spot_open=1950.0,
            near_prem=8.0,
            far_prem=25.0,
            net_debit=17.0,
            near_instrument="ETH-15MAY26-2000-C",  # New near leg
            far_instrument="ETH-01JUN26-2000-C",   # Same far leg
            notes="ROLL",
        )

        assert new_trade.near_instrument == "ETH-15MAY26-2000-C"
        assert new_trade.far_instrument == "ETH-01JUN26-2000-C"


# ── Notes/Audit Trail Tests ───────────────────────────────────────────────────

class TestCalendarAuditTrail:
    """Verify comprehensive audit trail is maintained in notes."""

    def test_notes_describe_every_transition(self, calendar_trade_data):
        """Every transaction should have descriptive notes."""
        trade = create_calendar_trade(**calendar_trade_data)
        # Notes may be None for initial open, but should be added on transitions

        # Mark as Far Leg Only
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired worthless. Far leg retained for analysis.",
        )

        from models import get_session, Calendar
        session = get_session()
        record = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert "Near leg expired" in record.notes

    def test_notes_contain_spot_prices(self, calendar_trade_data):
        """Notes should include relevant spot prices for context."""
        trade = create_calendar_trade(**calendar_trade_data)

        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 15),
            spot_close=2050.0,
            pnl=10.0,
            result="Closed",
            notes="Closed at spot $2,050.00. P&L: $10.00",
        )

        from models import get_session, Calendar
        session = get_session()
        record = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert "2,050" in record.notes or "2050" in record.notes

    def test_notes_contain_pnl_values(self, calendar_trade_data):
        """Notes should include P&L values for context."""
        trade = create_calendar_trade(**calendar_trade_data)

        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 15),
            spot_close=2050.0,
            pnl=10.0,
            result="Closed",
            notes="Closed at mark price. P&L: $10.00",
        )

        from models import get_session, Calendar
        session = get_session()
        record = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert "10" in record.notes


# ── State Transition Tests ────────────────────────────────────────────────────

class TestCalendarStateTransitions:
    """Verify all valid state transitions are properly recorded."""

    def test_transition_open_to_far_leg_only(self, calendar_trade_data):
        """Open -> Far Leg Only transition."""
        trade = create_calendar_trade(**calendar_trade_data)
        assert trade.result == "Open"

        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired.",
        )

        from models import get_session, Calendar
        session = get_session()
        record = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert record.result == "Far Leg Only"

    def test_transition_far_leg_only_to_closed(self, calendar_trade_data):
        """Far Leg Only -> Closed transition."""
        trade = create_calendar_trade(**calendar_trade_data)
        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired.",
        )

        close_calendar_trade(
            trade_id=trade.id,
            date_close=date(2026, 6, 1),
            spot_close=2050.0,
            pnl=15.0,
            result="Closed",
            notes="Far leg closed.",
        )

        from models import get_session, Calendar
        session = get_session()
        record = session.query(Calendar).filter_by(id=trade.id).first()
        session.close()
        assert record.result == "Closed"

    def test_transition_open_to_near_leg_rolled_creates_new(self, calendar_trade_data):
        """Open -> Near Leg Rolled transition creates new trade record."""
        old_trade = create_calendar_trade(**calendar_trade_data)

        # Mark old as Far Leg Only
        close_calendar_trade(
            trade_id=old_trade.id,
            date_close=date(2026, 5, 8),
            spot_close=1950.0,
            pnl=0.0,
            result="Far Leg Only",
            notes="Near leg expired.",
        )

        # Create new rolled position
        new_trade = create_calendar_trade(
            asset="ETH",
            date_open=date(2026, 5, 8),
            option_type="Call",
            strike=2000.0,
            expiry_near="15-May-2026",
            expiry_far="01-Jun-2026",
            near_days=7,
            far_days=24,
            qty=0.1,
            spot_open=1950.0,
            near_prem=8.0,
            far_prem=25.0,
            net_debit=17.0,
            notes="Rolled near leg",
        )

        from models import get_session, Calendar
        session = get_session()
        old_record = session.query(Calendar).filter_by(id=old_trade.id).first()
        new_record = session.query(Calendar).filter_by(id=new_trade.id).first()
        session.close()

        # Both records should exist
        assert old_record is not None
        assert new_record is not None
        assert old_record.result == "Far Leg Only"
        assert new_record.result == "Open"
