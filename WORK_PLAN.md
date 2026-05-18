# Implementation Plan: Trade States Removal, Fee Integration, and Live Deribit Trading

## Context

This plan addresses three interconnected automation initiatives for the optionsStrat options trading system:

1. **Remove TradeState Model** — Simplify state management by eliminating the centralized `TradeState` database model. Instead of a unified state table tracking strategy stages and open positions, each strategy will maintain its own state exclusively through its strategy-specific table (Singles, Strangles, Calendars, Spreads).

2. **Integrate Trading Fees** — Implement the Deribit fee structure (0.04% of spot or 0.0004 BTC, whichever is lower; max 12.5% of option price) into all trade execution paths. Fees will be calculated on entry, deducted from effective premium, and included in P&L calculations.

3. **Setup Live Deribit Trading** — Enable live trading via Deribit mainnet while maintaining paper trading capability. Configuration will be environment-based (`.env` file) with `DERIBIT_PAPER=true/false` to toggle between testnet and mainnet.

---

## Phase 1: Remove TradeState Model

### 1.1 Delete TradeState Model & Database

**Files to delete:**

- `models/trade_state.py` — Remove entirely
- Database migration: Drop the `trade_state` table from `optionsStrat.db`

**Files to modify:**

- `models/__init__.py` — Remove `TradeState` import/export
- `database/__init__.py` — Remove any `TradeState` references

### 1.2 Migrate State to Strategy-Specific Tables

Each strategy now owns its state exclusively:

#### Wheel Strategy (Singles Table)

**Current:** `stage` in `TradeState`  
**New:** Add `stage` column to `Singles` table (default: "closed"; becomes "short_put", "holding", "short_call" for open positions)

**File:** `models/singles.py`

- Add: `stage = Column(String(20), default="closed")`
- Track all wheel cycles in the same Singles rows (one row per put-sell or call-sell)

**Update:** `database/wheel_db.py`

- `load_wheel_state(asset)` now queries the most recent open or in-progress Single
- Extract stage, premium, asset_held, cost_basis from Singles columns
- Return dict with same format as before (for backward compatibility with executor)

#### Strangle Strategy (Strangles Table)

**Current:** `open_position` in `TradeState` to track if open  
**New:** Already has dedicated columns — add `status = Column(String(20), default="closed")` if needed

**File:** `models/strangles.py`

- Ensure `date_open` and `date_close` columns exist to determine if currently open
- If `date_close` is NULL, position is open

**Update:** `database/strangle_db.py`

- `load_strangle_state(asset)` queries for `date_close IS NULL`
- Returns dict with `open=True` if found, `False` otherwise

#### Calendar Strategy (Calendars Table)

**Current:** `open_position` in `TradeState`  
**New:** Use `date_close` to determine open/closed status

**File:** `models/calendars.py`

- Ensure `date_close` column exists (nullable)
- Position is open if `date_close IS NULL`

**Update:** `database/calendar_db.py`

- Same pattern as strangle: check `date_close IS NULL`

#### Spread Strategy (Spreads Table)

**Current:** `open_position` in `TradeState`  
**New:** Use `date_close` to determine open/closed status

**File:** `models/spreads.py`

- Ensure `date_close` column exists (nullable)
- Position is open if `date_close IS NULL`

**Update:** `database/spread_db.py`

- Same pattern as strangle and calendar

### 1.3 Update Blocking Logic

**File:** `automation/automator.py` — Rewrite `_blocked_strategies(asset)`

**Old logic:** Query `TradeState` table for each strategy  
**New logic:** Query strategy-specific tables directly

```python
def _blocked_strategies(asset: str) -> set[str]:
    blocked: set[str] = set()
    
    # Wheel: check most recent Single for open position
    wheel_open = db.query(Single).filter(
        Single.asset == asset,
        Single.date_close.is_(None)
    ).first()
    if wheel_open:
        if wheel_open.stage == "short_put":
            blocked.add("CC")
        elif wheel_open.stage == "holding":
            blocked.add("CSP")
        else:
            blocked.update({"CSP", "CC"})
    else:
        blocked.add("CC")  # Can't sell call without holding
    
    # Strangle: check if any open
    strangle_open = db.query(Strangle).filter(
        Strangle.asset == asset,
        Strangle.date_close.is_(None)
    ).first()
    if strangle_open:
        blocked.add("Strangle")
    
    # Calendar: check if any open
    calendar_open = db.query(Calendar).filter(
        Calendar.asset == asset,
        Calendar.date_close.is_(None)
    ).first()
    if calendar_open:
        blocked.update({"Cal-C", "Cal-P"})
    
    # Spread: check if any open
    spread_open = db.query(Spread).filter(
        Spread.asset == asset,
        Spread.date_close.is_(None)
    ).first()
    if spread_open:
        blocked.update({"BPS", "BCS"})
    
    return blocked
```

### 1.4 Update Monitor & Executor

**File:** `automation/monitor.py`

- Replace `TradeState` queries with strategy-specific table queries
- When closing positions, update `date_close` in the strategy table (not TradeState)

**File:** `trading/executor.py`

- Remove `TradeState` persistence when entering/exiting trades
- Update strategy-specific tables only
- Keep the same return format (for CLI compatibility)

### 1.5 Update Tests

**Files to modify:**

- `tests/test_models.py` — Remove `TradeState` test cases
- `tests/test_automator.py` — Update mock queries to use strategy-specific tables
- `tests/test_monitor.py` — Update to query Singles/Strangles/Calendars/Spreads directly
- Add new tests for the updated `load_*_state()` functions

---

## Phase 2: Integrate Trading Fees

### 2.1 Define Fee Calculation Function

**New file:** `trading/fee_calculator.py`

```python
def calculate_fee(spot: float, option_price: float, asset: str = "BTC") -> float:
    """
    Calculate trading fee per Deribit structure:
    - 0.04% of underlying spot price
    - OR 0.0004 BTC (if asset is BTC or BTC-denominated)
    - Whichever is LOWER
    - But cannot exceed 12.5% of option price
    
    Args:
        spot: Underlying spot price (USD for BTC/ETH, or coin value)
        option_price: Premium of the option being traded (USD or coin value)
        asset: Asset ticker ("BTC", "ETH", "SOL", "XRP")
    
    Returns:
        Fee amount in same units as option_price
    """
    fee_spot_pct = spot * 0.0004  # 0.04% of spot
    fee_btc_fixed = 0.0004  # 0.0004 BTC (only applies if asset is BTC)
    
    # For BTC, use minimum of spot-pct and fixed 0.0004 BTC
    if asset == "BTC":
        fee = min(fee_spot_pct, fee_btc_fixed)
    else:
        fee = fee_spot_pct
    
    # Cap at 12.5% of option price
    max_fee = option_price * 0.125
    return min(fee, max_fee)
```

### 2.2 Apply Fees at Trade Entry

**File:** `trading/executor.py` — Modify all entry functions

For each strategy entry (_enter_csp, _enter_cc, _enter_strangle, _enter_calendar, _enter_spread):

1. Calculate fee: `fee = calculate_fee(c.spot, calculated_premium, c.asset)`
2. Adjust effective premium: `effective_premium = calculated_premium - fee`
3. Update budget/amount check: Ensure budget covers fee + premium
4. Store fee in database: Pass `fees=fee` to `create_*_trade()` functions

**Example (CSP):**

```python
def _enter_csp(c, days, broker):
    K = ...  # strike calculation
    T = days / 365.0
    unit_price = bs_put(c.spot, K, T, RISK_FREE_RATE, c.iv)
    premium = unit_price * qty
    
    # NEW: Calculate and apply fee
    from trading.fee_calculator import calculate_fee
    fee = calculate_fee(c.spot, premium, c.asset)
    effective_premium = premium - fee
    
    # Verify budget covers fee + premium
    if effective_premium > BUDGET_USD:
        raise ValueError(f"Premium {effective_premium} exceeds budget {BUDGET_USD}")
    
    # Store to database with fee
    create_wheel_trade(
        asset=c.asset,
        strategy_type="CSP",
        strike=K,
        expiry=expiry_date,
        premium=premium,
        fees=fee,  # NEW
        qty=qty,
        broker_name=broker.broker_name if broker else None,
        broker_order_id=order_result.order_id if broker else None,
    )
```

### 2.3 Apply Fees in P&L Calculations

**File:** `trading/portfolio.py` — Update all P&L functions

For each strategy's `_calculate_pnl()`:

**Old:** `pnl = received_premium - current_value`  
**New:** `pnl = (received_premium - fee) - current_value`

```python
def collect_open_positions():
    for single in db.query(Single).filter(Single.date_close.is_(None)):
        premium = single.premium
        fee = single.fees
        current_value = bs_put(spot, single.strike, T, RISK_FREE_RATE, iv)
        unrealised_pnl = (premium - fee) - current_value  # NEW: subtract fee
        yield {
            "asset": single.asset,
            "strategy": "Wheel",
            "premium": premium - fee,  # Effective premium shown to user
            "fees": fee,  # Display fees separately
            "unrealised_pnl": unrealised_pnl,
        }
```

### 2.4 Update Trade Ledger & Reporting

**File:** `models/trade_ledger.py`

- Ensure `fees` column exists (already defined)

**File:** `database/trade_ledger.py` (if it exists) or update all `create_*_trade()` functions

- Ensure fees are recorded to TradeLedger on entry AND exit
- P&L calculations use: `pnl = (entry_premium - entry_fees) - (exit_price - exit_fees)`

### 2.5 Update Tests

**Files to create/modify:**

- `tests/test_fee_calculator.py` — Unit tests for fee calculation
  - Test BTC vs ETH fee calculations
  - Test 12.5% cap enforcement
  - Test edge cases (very small premiums, very large premiums)
  
- `tests/test_executor.py` — Update entry tests
  - Verify fees are calculated and stored
  - Verify effective premium respects budget
  
- `tests/test_portfolio.py` — Update P&L tests
  - Verify P&L calculations subtract fees
  - Verify fees are displayed in position output

---

## Phase 3: Setup Live Deribit Trading

### 3.1 Environment Configuration

**File:** `.env` (new/update)

``` Paper or Live
# Paper Trading (Testnet)
DERIBIT_PAPER_CLIENT_ID=your_paper_client_id
DERIBIT_PAPER_CLIENT_SECRET=your_paper_client_secret

# Live Trading (Mainnet)
DERIBIT_LIVE_CLIENT_ID=your_live_client_id
DERIBIT_LIVE_CLIENT_SECRET=your_live_client_secret

# Toggle paper vs live
DERIBIT_PAPER=true  # Set to false for live trading
```

**File:** `.env.example` (documentation)

- Show all required environment variables
- Explain paper=true vs false
- Document how to obtain credentials from Deribit

### 3.2 Update Config Module

**File:** `config.py`

Add/update:

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Paper vs Live toggle
DERIBIT_PAPER = os.getenv("DERIBIT_PAPER", "true").lower() == "true"

# Credentials loaded based on DERIBIT_PAPER flag
if DERIBIT_PAPER:
    DERIBIT_CLIENT_ID = os.getenv("DERIBIT_PAPER_CLIENT_ID")
    DERIBIT_CLIENT_SECRET = os.getenv("DERIBIT_PAPER_CLIENT_SECRET")
    DERIBIT_BASE_URL = "https://test.deribit.com/api/v2"
else:
    DERIBIT_CLIENT_ID = os.getenv("DERIBIT_LIVE_CLIENT_ID")
    DERIBIT_CLIENT_SECRET = os.getenv("DERIBIT_LIVE_CLIENT_SECRET")
    DERIBIT_BASE_URL = "https://www.deribit.com/api/v2"

# Validation: ensure credentials are present
if not DERIBIT_CLIENT_ID or not DERIBIT_CLIENT_SECRET:
    mode = "paper" if DERIBIT_PAPER else "live"
    raise ValueError(f"Missing Deribit {mode} credentials in .env file")
```

### 3.3 Update Deribit Adapter

**File:** `access/deribit.py`

Update `__init__()` to use `config.DERIBIT_BASE_URL` and credentials:

```python
from config import DERIBIT_CLIENT_ID, DERIBIT_CLIENT_SECRET, DERIBIT_BASE_URL, DERIBIT_PAPER

class DeribitClient(BrokerBase):
    def __init__(self, paper: bool = None):
        # If paper param not specified, use config default
        if paper is None:
            paper = DERIBIT_PAPER
        
        self._paper = paper
        self._base = DERIBIT_BASE_URL
        self._client_id = DERIBIT_CLIENT_ID
        self._client_secret = DERIBIT_CLIENT_SECRET
        
        if not self._client_id or not self._client_secret:
            mode = "paper" if paper else "live"
            raise ValueError(f"Missing Deribit {mode} credentials")
        
        self._token = None
        self._token_expiry = None
```

### 3.4 Update Executor

**File:** `trading/executor.py` — Simplify broker initialization

**Old:**

```python
def enter_trade(c, days=None, broker=None):
    if broker is None:
        broker = DeribitClient(paper=config.DERIBIT_PAPER)
```

```python
def enter_trade(c, days=None, broker=None):
    if broker is None:
        broker = DeribitClient()  # Uses config.DERIBIT_PAPER by default
```

### 3.5 Update Automator

**File:** `automation/automator.py` — No changes needed if executor already handles it

Confirm that `run_automation()` doesn't hardcode `paper=True`:

```python
def run_automation(...):
    # ... candidate selection ...
    enter_trade(best_candidate, broker=DeribitClient())  # Uses config.DERIBIT_PAPER
```

### 3.6 Update Startup/Menu

**File:** `main.py` (if it has a menu)

Add display of current mode at startup:

```python
from config import DERIBIT_PAPER

mode = "PAPER (Testnet)" if DERIBIT_PAPER else "LIVE (Mainnet)"
print(f"\n🔴 Trading Mode: {mode}")
print("⚠️  Check your .env file before entering live trades!\n")
```

### 3.7 Update Tests

**Files to modify:**

- `tests/test_access_deribit.py`
  - Add test fixtures for both paper and live environments
  - Mock config.DERIBIT_PAPER = True/False scenarios
  - Verify correct endpoints and credentials are used

- `tests/test_executor.py`
  - Update to handle both paper and live DeribitClient instances
  - Verify broker_name stored as "deribit_paper" or "deribit_live"

- `tests/conftest.py` (if exists)
  - Add pytest fixture to set DERIBIT_PAPER for test isolation

### 3.8 Documentation

**File:** `README.md` — Add section on live trading

```markdown
## Live Trading Setup

### Prerequisites
1. Deribit account (paper trading available on testnet; live trading on mainnet)
2. API credentials from https://www.deribit.com/account/api

### Configuration
1. Copy `.env.example` to `.env`
2. Add your paper and live credentials
3. Set `DERIBIT_PAPER=true` for paper trading, `false` for live

### Safety Checklist Before Going Live
- [ ] Tested on paper trading (testnet) first
- [ ] Verified fee calculations with small position sizes
- [ ] Reviewed all P&L calculations
- [ ] Confirmed budget limits in config.py
- [ ] Tested stop-loss and take-profit triggers
- [ ] Reviewed monitor.py auto-close logic
```

---

## Phase 4: Testing & Verification

### 4.1 Unit Tests

**Run all tests to ensure no regressions:**

```bash
pytest tests/ -v
```

**Key test coverage:**

- ✅ `test_models.py` — No TradeState tests; strategy-specific table queries pass
- ✅ `test_fee_calculator.py` — Fee calculations match spec for all assets
- ✅ `test_executor.py` — Fees deducted from premium on entry; stored in DB
- ✅ `test_portfolio.py` — P&L calculations include fees; unrealised PnL correct
- ✅ `test_automator.py` — Blocking logic works with new query patterns
- ✅ `test_monitor.py` — Monitor queries strategy-specific tables; closes positions correctly
- ✅ `test_access_deribit.py` — Paper and live modes use correct endpoints

### 4.2 Integration Tests

**Manual smoke tests:**

1. Start with paper trading (`DERIBIT_PAPER=true`)
2. Run automation on a single asset → should enter trade with fees applied
3. Check database: verify fees stored in strategy table
4. Run monitor → should calculate P&L including fees
5. Close position manually → verify exit fees applied
6. Switch to live (`DERIBIT_PAPER=false`) and repeat (with small position size)

### 4.3 Database Migration

**Create migration script** (if using Alembic):

```bash
alembic revision --autogenerate -m "Remove TradeState; add stage/status columns"
alembic upgrade head
```

**Manual migration (SQLite):**

```sql
-- Add columns to strategy tables if not present
ALTER TABLE singles ADD COLUMN stage STRING DEFAULT 'closed';

-- Copy data from trade_state to singles for wheel positions
-- (if any wheel trades were open)

-- Drop trade_state table
DROP TABLE IF EXISTS trade_state;
```

---

## Rollback Plan

If issues arise during implementation:

1. **TradeState removal:** Keep a backup of `models/trade_state.py` and the database before dropping table. Restore if blocking logic fails.
2. **Fee implementation:** Fees are stored in existing columns; disable fee calculations by setting `fees=0.0` in executor.
3. **Live trading:** Switch back to paper by setting `DERIBIT_PAPER=true` in `.env`.

---

## Critical Files to Review

Before implementation, verify:

1. ✅ `models/singles.py` — Has `stage` column or can add it
2. ✅ `models/strangles.py`, `calendars.py`, `spreads.py` — All have `date_close` columns
3. ✅ `database/wheel_db.py`, `strangle_db.py`, etc. — Load functions match new query pattern
4. ✅ `automation/automator.py` — `_blocked_strategies()` is the only blocker user; update logic there
5. ✅ `trading/executor.py` — Entry functions use strategy-specific `create_*_trade()` calls
6. ✅ `trading/portfolio.py` — P&L functions can be updated to subtract fees
7. ✅ `access/deribit.py` — Credential and endpoint handling matches plan
8. ✅ `.env` file — Ensure credentials are environment-based, not hardcoded

---

## Implementation Order

1. **Phase 1 (TradeState)** — Highest risk; impacts many files
   - Delete TradeState model and table
   - Update all `load_*_state()` functions
   - Rewrite `_blocked_strategies()`
   - Test thoroughly

2. **Phase 2 (Fees)** — Medium risk; well-isolated
   - Create fee calculator
   - Update executor entry functions
   - Update portfolio P&L calculations
   - Test with unit + integration tests

3. **Phase 3 (Live Trading)** — Low risk; configuration-only
   - Update `.env` and `config.py`
   - Update Deribit adapter credentials
   - Document in README
   - Small manual test before live use

---

## Success Criteria

- ✅ All tests pass; no TradeState model references remain
- ✅ Fees calculated correctly; stored in database; deducted from P&L
- ✅ Paper trading works via `DERIBIT_PAPER=true`
- ✅ Live trading works via `DERIBIT_PAPER=false` with correct Deribit mainnet credentials
- ✅ README updated with live trading setup instructions
- ✅ No hardcoded credentials; all credentials in `.env` file
