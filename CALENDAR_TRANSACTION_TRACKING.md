# Calendar Strategy Transaction Tracking

## Overview

All calendar strategy transactions are comprehensively recorded in the database with complete audit trails. Each transaction captures the state transition, financial details, fees, and instrument information.

## Tracked Transactions

### 1. Opening a Calendar Spread

**Database Record**: `create_calendar_trade()`

**Fields Captured**:

- `date_open` - Opening date
- `asset` - Underlying asset (ETH, BTC, SOL, etc.)
- `option_type` - Call or Put
- `strike` - Strike price
- `expiry_near` - Near leg expiration date
- `expiry_far` - Far leg expiration date
- `near_days` - Days to near leg expiry
- `far_days` - Days to far leg expiry
- `qty` - Quantity (e.g., 0.125)
- `spot_open` - Underlying price at entry
- `near_prem` - Premium received for short near leg
- `far_prem` - Premium paid for long far leg
- `net_debit` - Net cost (far_prem - near_prem)
- `open_fees` - Fees for entering position
- `near_instrument` - Broker instrument name for near leg
- `far_instrument` - Broker instrument name for far leg
- `result` - Status: "Open"
- `notes` - Entry details

**Example Record**:

``` code
Calendar(
  id=42,
  asset="ETH",
  option_type="Call",
  strike=2000.0,
  date_open=2026-05-01,
  spot_open=2000.0,
  near_prem=10.00,
  far_prem=25.00,
  net_debit=15.00,
  open_fees=2.50,
  result="Open",
  notes="Opened ETH call calendar 7d/30d spread at $2000 strike"
)
```

---

### 2. Near Leg Expires Worthless

**Database Update**: `close_calendar_trade()` with `result="Far Leg Only"`

**Fields Updated**:

- `date_close` - Date near leg expires
- `spot_close` - Underlying price at near leg expiry
- `pnl` - Set to 0.0 (no P&L yet, far leg still open)
- `result` - Changed to "Far Leg Only"
- `close_fees` - Fees for near leg close (typically 0)
- `notes` - Description of worthless expiry

**Example Update**:

``` code
Calendar(
  id=42,
  date_close=2026-05-08,
  spot_close=1950.0,  # Below strike, call is OTM
  pnl=0.0,
  result="Far Leg Only",
  close_fees=0.0,
  notes="Near leg expired worthless at spot $1,950. Far leg retained for analysis."
)
```

**Impact on State**:

- Position remains open (tracked by `load_calendar_state()`)
- Status displays as "Far Leg Only" in summary
- Far leg can be closed manually or monitored for further action

---

### 3. Roll Near Leg

**Database Operations**:

1. Mark old position: `close_calendar_trade()` with `result="Near Leg Rolled"`
2. Create new position: `create_calendar_trade()` with new near leg

**Old Position Update**:

``` code
Calendar(
  id=42,
  date_close=2026-05-08,
  spot_close=1950.0,
  pnl=0.0,
  result="Near Leg Rolled",
  close_fees=roll_fee,
  notes="Rolled near leg: new 7d near leg at $2000, premium $8.00"
)
```

**New Position Creation**:

``` code
Calendar(
  id=43,  # New record
  date_open=2026-05-08,
  spot_open=1950.0,
  result="Open",
  notes="ROLL ETH CALL calendar, new 7d near leg"
)
```

**Why Separate Records**:

- Maintains complete audit trail
- Tracks multiple calendar positions per asset
- Enables historical analysis of all rolling activity
- Supports reporting on calendar cycles

---

### 4. Closing Far Leg (From Far Leg Only)

**Database Update**: `close_calendar_trade()` with `result="Closed"`

**Fields Updated**:

- `date_close` - Date far leg is closed
- `spot_close` - Underlying price when closing
- `pnl` - Calculated P&L for entire position
- `result` - Changed to "Closed"
- `close_fees` - Fees for closing far leg
- `notes` - Description of far leg close

**Example Update**:

``` code
Calendar(
  id=42,
  date_close=2026-06-01,
  spot_close=2050.0,
  pnl=15.00,  # Win
  result="Closed",
  close_fees=1.50,
  notes="Far leg closed at spot $2,050. Position fully closed. P&L: $15.00"
)
```

---

### 5. Early Close (Before Either Leg Expires)

**Database Update**: `close_calendar_trade()` with `result="Closed"`

**Fields Updated**:

- `date_close` - Early close date (before expiry)
- `spot_close` - Spot price at early close
- `pnl` - Mark-to-market P&L
- `result` - "Closed"
- `close_fees` - Fees for closing both legs
- `notes` - Reason for early close

**Example Update**:

``` code
Calendar(
  id=42,
  date_close=2026-05-15,
  spot_close=2020.0,
  pnl=8.50,
  result="Closed",
  close_fees=2.50,
  notes="Early close at 56% of debit captured. Stop-loss triggered."
)
```

---

### 6. Near Leg Expires ITM

**Database Update**: `close_calendar_trade()` with `result="Closed"`

**Fields Updated**:

- `date_close` - Date near leg expires
- `spot_close` - ITM underlying price
- `pnl` - Loss due to ITM near leg (intrinsic owed)
- `result` - "Closed"
- `close_fees` - Fees (if any)
- `notes` - ITM details with intrinsic value

**Example Update**:

``` code
Calendar(
  id=42,
  date_close=2026-05-08,
  spot_close=2100.0,  # Above strike, call is ITM
  pnl=-8.50,  # Lost to intrinsic
  result="Closed",
  notes="Near leg ITM at expiry: intrinsic $100 owed. P&L: -$8.50"
)
```

---

### 7. Far Leg Expires ITM (From Far Leg Only)

**Database Update**: `close_calendar_trade()` with `result="Closed"`

**Same as far leg close**, but notes indicate expiry rather than manual close

``` code
Calendar(
  id=42,
  date_close=2026-06-01,
  spot_close=2100.0,
  pnl=12.00,  # Gain from ITM far leg
  result="Closed",
  notes="Far leg expired ITM: intrinsic $100 gained. P&L: $12.00"
)
```

---

## Fee Tracking

All fees are recorded in separate columns:

- `open_fees` - Fees for entering calendar (both legs)
- `close_fees` - Fees for closing (near leg, far leg, or both)
- `fees` - Total fees (calculated)

**Example**:

``` python
# Opening: Enter near leg (sell) + far leg (buy)
open_fees = fee_for_near + fee_for_far  # E.g., 1.50 + 1.50 = 3.00

# Closing near leg
close_fees_near = fee_for_buying_back_near  # E.g., 1.00

# Closing far leg
close_fees_far = fee_for_selling_far  # E.g., 1.00

# Total fees for position
total_fees = open_fees + close_fees_near + close_fees_far  # 3.00 + 1.00 + 1.00 = 5.00
```

---

## Broker Instrument Tracking

Each leg's broker instrument is recorded for audit and order verification:

- `near_instrument` - E.g., "ETH-08MAY26-2000-C"
- `far_instrument` - E.g., "ETH-01JUN26-2000-C"

**Why Important**:

- Verify broker executed correct instruments
- Track if exchange snapped to different strike
- Enable order matching and reconciliation
- Support audit trails with broker

---

## State Transitions and Audit Trail

### Valid State Transitions

``` text
Open
  ↓
  ├─→ Far Leg Only (near expires worthless)
  │     ├─→ Closed (close far leg manually)
  │     ├─→ Closed (far leg expires)
  │     └─→ Near Leg Rolled (sell new near leg, create new record)
  │
  ├─→ Near Leg Rolled (roll from Far Leg Only, create new record)
  │     ├─→ Far Leg Only (new near leg expires)
  │     └─→ Closed (early close both legs)
  │
  └─→ Closed (early close before expiry)
```

### Audit Trail in Notes

Every transition is documented:

1. **Open**: Entry details

   ``` text
   "Opened ETH call calendar 7d/30d spread at $2000 strike"
   ```

2. **Far Leg Only**: Expiry details

   ``` text
   "Near leg expired worthless at spot $1,950. Far leg retained for analysis."
   ```

3. **Roll**: New near leg details

   ``` code
   "Rolled near leg: new 7d near leg at $2000, premium $8.00"
   ```

4. **Closed**: P&L and reason

   ``` code
   "Far leg closed at spot $2,050. Position fully closed. P&L: $15.00"
   ```

---

## Database Queries

### Load Open Positions

```python
state = load_calendar_state("ETH")
if state["open"]:
    op = state["open"]
    # op contains: trade_id, status, asset, strike, expiry_near, expiry_far, etc.
    print(f"Open {op['status']}: {op['asset']} ${op['strike']}")
```

### Load Closed Trades

```python
from models import get_session, Calendar
session = get_session()
closed_trades = session.query(Calendar).filter(
    Calendar.asset == "ETH",
    Calendar.result.in_(["Closed", "Win", "Loss"])
).all()

for trade in closed_trades:
    print(f"{trade.date_close}: {trade.result} P&L: ${trade.pnl}")
session.close()
```

### Get Statistics

```python
stats = get_calendar_stats("ETH")
print(f"Trades: {stats['trades']}")
print(f"Wins: {stats['wins']}, Losses: {stats['losses']}")
print(f"Win rate: {stats['win_rate']:.1f}%")
print(f"Total P&L: ${stats['total_pnl']:.2f}")
```

---

## Testing

All transaction tracking is verified by 17 comprehensive tests:

- ✅ Opening transactions recorded
- ✅ Near leg worthless transitions tracked
- ✅ Far leg closes recorded
- ✅ Early closes tracked
- ✅ Near leg rolls create new records
- ✅ ITM expiries documented
- ✅ Fees properly recorded
- ✅ Instrument names tracked
- ✅ Notes contain audit trail
- ✅ State transitions validated

**Test Coverage**: 17 tests in `test_calendar_transaction_tracking.py`

---

## Complete Transaction Lifecycle Example

### Scenario: Calendar spread that gets rolled once then closed

```text
Date: 2026-05-01
Action: Open ETH Call Calendar 7d/30d @ $2000
Record 1:
  - Open calendar spread
  - date_open: 2026-05-01
  - spot_open: $2000
  - near_prem: $10, far_prem: $25, net_debit: $15
  - result: "Open"
  - notes: "Opened ETH call calendar..."

───────────────────────────────────────────────────

Date: 2026-05-08
Action: Near leg expires worthless (spot $1950)
Record 1 Updated:
  - date_close: 2026-05-08
  - spot_close: $1950
  - pnl: $0
  - result: "Far Leg Only"
  - notes: "Near leg expired worthless..."

───────────────────────────────────────────────────

Date: 2026-05-08
Action: Roll new 7d near leg
Record 1 Updated Again:
  - result: "Near Leg Rolled" (for audit trail)
  - notes: "Rolled near leg: new 7d near leg..."

Record 2 Created:
  - Open new calendar with 7d near leg
  - date_open: 2026-05-08
  - spot_open: $1950
  - near_prem: $8, far_prem: $25, net_debit: $17
  - result: "Open"

───────────────────────────────────────────────────

Date: 2026-05-15
Action: Close far leg at spot $2050
Record 2 Updated:
  - date_close: 2026-05-15
  - spot_close: $2050
  - pnl: $10 (profit)
  - result: "Closed"
  - notes: "Far leg closed at spot $2,050..."

───────────────────────────────────────────────────

Result: Two complete records in database showing:
- Initial calendar spread: Far Leg Only → Near Leg Rolled
- Rolled calendar spread: Open → Closed with $10 profit
```

---

## Summary

✅ **All calendar transactions are tracked**:

- Opening and closing dates
- Spot prices at key events
- Premiums and net debit
- Fees for all operations
- Broker instruments for both legs
- P&L calculations
- Complete audit trail in notes
- State transitions documented

✅ **Multiple records per position**:

- Each calendar cycle is a separate record
- Rolling creates a new calendar record
- Maintains complete history

✅ **Queryable and reportable**:

- Load open positions by asset
- Get closed trade statistics
- Filter by result status
- Calculate cumulative P&L
- Analyze fee impact
