✅ Task completed
☐ Task not started
⊙ Task in progress

## Phase 1: Remove TradeState Model (8 tasks)

1. ✅ Phase 1: Remove TradeState Model
2. ✅ Delete models/trade_state.py and remove imports from models/__init__.py and database/__init__.py
3. ✅ Add stage column to models/singles.py and update load_wheel_state in database/wheel_db.py
4. ✅ Verify date_close columns in Strangles, Calendars, Spreads models and update their load functions
5. ✅ Rewrite _blocked_strategies() in automation/automator.py to query strategy tables directly
6. ✅ Update automation/monitor.py and trading/executor.py to remove TradeState references
7. ✅ Remove TradeState tests from tests/test_models.py and update automator/monitor tests
8. ✅ Drop trade_state table from database and verify no regressions

## Phase 2: Integrate Trading Fees (6 tasks)

9.  ☐ Phase 2: Integrate Trading Fees
10. ☐ Create trading/fee_calculator.py with fee calculation logic
11. ☐ Update all entry functions in trading/executor.py to calculate and apply fees
12. ☐ Update P&L calculations in trading/portfolio.py to subtract fees
13. ☐ Create tests/test_fee_calculator.py with comprehensive fee calculation tests
14. ☐ Update tests/test_executor.py and tests/test_portfolio.py for fee logic

## Phase 3: Setup Live Deribit Trading (7 tasks)

15. ☐ Phase 3: Setup Live Deribit Trading
16. ☐ Create/update .env and .env.example with paper/live credentials and DERIBIT_PAPER flag
17. ☐ Update config.py to load credentials from .env based on DERIBIT_PAPER flag
18. ☐ Update access/deribit.py to use config credentials and endpoints
19. ☐ Update main.py to display current trading mode (PAPER vs LIVE) at startup
20. ☐ Update tests/test_access_deribit.py for paper/live mode scenarios
21. ☐ Add live trading setup section to README.md
22. ☐ Run full test suite and verify no regressions