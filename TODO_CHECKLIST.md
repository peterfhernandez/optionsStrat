# Prepare for Live Trading

✅ Task completed
☐ Task not started
⊙ Task in progress

## Phase 1: Remove TradeState Model (8 tasks)

1. ✅ Delete models/trade_state.py and remove imports from models/__init__.py and database/__init__.py
2. ✅ Add stage column to models/singles.py and update load_wheel_state in database/wheel_db.py
3. ✅ Verify date_close columns in Strangles, Calendars, Spreads models and update their load functions
4. ✅ Rewrite _blocked_strategies() in automation/automator.py to query strategy tables directly
5. ✅ Update automation/monitor.py and trading/executor.py to remove TradeState references
6. ✅ Remove TradeState tests from tests/test_models.py and update automator/monitor tests
7. ✅ Drop trade_state table from database and verify no regressions

## Phase 2: Integrate Trading Fees (6 tasks)

1. ✅ Create trading/fee_calculator.py with fee calculation logic
2. ✅ Update models to add open_fee and close_fee as defined in WORK_PLAN.md section 2.3
3. ✅ Update all entry functions in trading/executor.py to calculate and apply open_fees
4. ✅ Update all close functions in trading/executor.py to calculate and apply close_fees
5. ✅ Update P&L calculations in trading/portfolio.py to subtract open and close fees
6. ✅ Update Strategies and automator.py to include open and close fees in yield calculations
7. ✅ Create tests/test_fee_calculator.py with comprehensive fee calculation tests
8. ☐ Update tests/test_executor.py and tests/test_portfolio.py for fee logic on entry functions
9. ☐ Update tests/test_executor.py and tests/test_portfolio.py for fee logic on close functions
10. ☐ Update Strategies and automator tests to include open and close fees in yield calculations
11. ☐ Ensure trade_ledger table is fully deprecated
12. ☐ Update README.md to ensure Deribit fees information and structure is included and trade_ledger references are removed

## Phase 3: Setup Live Deribit Trading (7 tasks)

1. ☐ Phase 3: Setup Live Deribit Trading
2. ☐ Create/update .env and .env.example with paper/live credentials and DERIBIT_PAPER flag
3. ☐ Update config.py to load credentials from .env based on DERIBIT_PAPER flag
4. ☐ Update access/deribit.py to use config credentials and endpoints
5. ☐ Update main.py to display current trading mode (PAPER vs LIVE) at startup
6. ☐ Update tests/test_access_deribit.py for paper/live mode scenarios
7. ☐ Add live trading setup section to README.md
8. ☐ Run full test suite and verify no regressions
