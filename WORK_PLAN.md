# Plan: Restructure optionsStrat for Automated Trading
TL;DR: Reorganize into organized packages (automation, trading, strategies, excel, market, ui), implement portfolio management with position listing and manual/auto close, add live/paper trading switch (prepared for future Deribit API), and clean up root folder to entry points only.

Steps (6 phases, 22 steps)
# PHASE 1: Create New Package Structure (parallel steps 1-5) - <font color="blue">DONE</font>
1. Create automation/ package — move automator.py here
2. Create trading/ package with executor.py (trade execution), portfolio.py (position aggregation), position.py (base classes)
3. Create excel/ package — move excel_tracker.py, add templates.py
4. Create market/ package — move market_data.py, pricing.py, add cache.py for session price caching
5. Create ui/ package — move display.py, add menus.py for menu system

# PHASE 2: Restructure Main Entry Points (depends on Phase 1) - <font color="blue">DONE</font>
6. Refactor main.py (600 lines → ~150 lines) — import from ui.menus, add [P] Portfolio and [L] Live/Paper Mode menus - DONE
7. Refactor automate.py (90 lines → ~20 lines) — import from automation.automator - DONE

# PHASE 3: Update Strategy Modules (parallel steps 8-12, depend on Phase 2)
8. Update wheel.py — replace _save/_load with trading.executor calls
9. Update strangle.py — same pattern
10. Update calendar.py — same pattern
11. Update monitor.py — use executor for auto-close
12. Update scanner.py — add price caching from market.cache

# PHASE 4: Update Tests & Configuration (depends on Phase 3)
13. Create tests/test_trading.py — test executor (paper mode + live stubs), portfolio manager, position P&L
14. Create tests/test_automation.py — move existing automator tests
15. Update conftest.py — add fixtures for new modules
16. Update config.py — add TRADING_MODE switch and Deribit placeholder credentials
17. Update all existing tests — fix import paths, no logic changes

# PHASE 5: Deprecation & Cleanup (depends on Phase 4)
18. Deprecate crypto_options_trade.py — add comment, don't import from it
19. Verify no circular dependencies — clean root module imports

# PHASE 6: New Features (depends on Phase 2)
20. Implement portfolio listing UI — display all open positions in table, P&L per position - <font color="blue">DONE</font>
21. Implement live/paper toggle UI — switch mode, validate credentials for live
22. Wire up manual close in strategy menus — user can close any position manually - <font color="blue">DONE</font>
23. Wire up automatic manual close in main and strategy menus — the correct positions are closed, due to end of strategy, or stop loss - <font color="blue">DONE</font>
24. Implement trades list, with p/l per trade, and cumulative p/l - <font color="blue">DONE</font>
25. Implement the same for 1 or 2 more Coins - <font color="green">DONE for XRP - ALL DONE</font>
26. Implement 1 or 2 more options strategies - <font color="red">TO DO for Credit spread</font>
27. Remove the Wheel from Strategies and automate.py

# Relevant Files
main.py — refactor to use ui.menus
automate.py — refactor to use automation.automator
config.py — add TRADING_MODE and live credentials
wheel.py, strangle.py, calendar.py, monitor.py — replace I/O with executor
scanner.py — add price caching

# New packages & modules:
automation/, trading/, excel/, market/, ui/ packages
trading/executor.py (order execution with paper/live routing), trading/portfolio.py (aggregated positions), market/cache.py (session price cache)
ui/menus.py (menu system), excel/templates.py (sheet definitions)
tests/test_trading.py, tests/test_automation.py
Verification (6 phases across automated tests, manual flow tests, and import verification)

# SUMMARY
Phase 1-2: Existing tests pass, main.py/automate.py have no syntax errors
Phase 3: Strategy tests pass, trades enter/close correctly
Phase 4: New test suite passes (executor, portfolio, automation)
Phase 5: No imports from deprecated modules, no circular deps
Phase 6: Portfolio menu lists positions, manual close works, live/paper toggle works
Integration: python main.py → all menus work; python automate.py → respects TRADING_MODE
Decisions
✅ Live trading deferred: Architecture ready; Deribit execution stubs only (raise NotImplementedError until credentials added)
✅ State files unchanged: Executor handles JSON read/write transparently
✅ Manual + auto close: Both options available; manual via portfolio menu, auto via monitor.py
✅ Root folder cleaned: Only entry points and config remain
✅ Legacy compat: crypto_options_trade.py kept but not imported (reference only)

# Further Considerations
Price cache expiry: Manual-refresh only (via menu option [3]), valid for entire session — balances freshness with simplicity
Portfolio P&L display: Show last-fetched price (matches current calc), add timestamp "Last updated: 2 min ago"
Live trading guard: NotImplementedError raised if live mode attempted without Deribit creds configured — prevents accidents