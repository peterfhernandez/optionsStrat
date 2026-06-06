# Calendar Strategies improvements

## Summary

The app has incomplete implementations for calendar strategy statuses and option expiry handling. When a calendar near leg expires, the app incorrectly marks it as "Closed" instead of "Far Leg Only". Rolling calculations are broken (dates are nonsensical). Recommendations to close positions lack execution capability. Expiry workflows (worthless and in-the-money) need to be implemented at a shared module level.

---

## TODO: Calendar Strategy & Options Trading

### Calendar Strategy Statuses

- [x] Implement "Far Leg Only" status (when near leg expires/rolls)
- [x] Implement "Near Leg Rolled" status
- [x] Implement "Closed" status (both legs closed/expired)
- [x] Ensure `monitor` correctly marks near leg expiry as "Far Leg Only" instead of "Closed"
- [x] Ensure `summary` and `portfolio` display "Far Leg Only" records
- [x] Update `monitor` and `calendar` to generate correct recommendations for "Far Leg Only" state

### Trading Action: Close Position Early

- [x] Test early position closing (currently implemented but untested)
- [x] Verify individual leg closing in calendar spreads works independently
- [x] Verify simultaneous leg closing works correctly

### Trading Action: Expires Worthless (Shared Implementation)

- [x] Move expires-worthless logic to common module level (used by: calendar, scanner, spread, strangle, executor, monitor)
- [x] Implement for both calls and puts
- [x] Correctly handle premium retention/loss based on position side (long/short)

### Trading Action: Expires In-The-Money (IMPLEMENTED)

- [x] Implement expires-in-the-money logic at common module level (used by: calendar, scanner, spread, strangle, executor, monitor)
- [x] Calculate intrinsic value: calls `(spot - strike) × multiplier`, puts `(strike - spot) × multiplier`
- [x] Handle assignment scenarios for short positions
- [x] Calculate settlement for long positions

### Recommendations Execution

- [x] Add execution capability when app recommends closing position
- [x] Integrate deribit API to sell far leg on user confirmation
- [x] Display recommendation options with clear user prompts:
  - Close far leg (execute with deribit) ✓
  - Keep position open ✓
  - Roll position (with calculated options) [pending]
- [ ] Show probability of profit and justification for each recommendation

### Roll Options Calculation (CRITICAL BUG)

- [x] Fix expiry date calculation — count from today, not from far leg expiry
  - 1d near leg should expire in 1–2 days (not 18d) ✓
  - 3d near leg should expire in 3–4 days (not 16d) ✓
  - 7d near leg should expire in 7–8 days (not 12d) ✓
- [x] Ensure roll options are ordered logically (1d expires before 3d, 3d before 7d) ✓
- [x] Make roll options executable — app should enter the chosen position via deribit ✓
  - [x] roll_near_leg function creates new position via Deribit ✓
  - [x] Menu calls roll_near_leg on user selection ✓
  - [x] Test coverage for roll menu option execution ✓
- [x] Display probability of profit and justification for each roll option ✓
  - [x] calculate_roll_options function with PoP calculations ✓
  - [x] Display expiry dates, premium, PoP%, and expected P&L ✓
  - [x] Show justification based on PoP level ✓
  - [x] 9 comprehensive tests for roll option calculations ✓

### Monitoring & Display

- [x] Update Position Monitor output to correctly label "Far Leg Only" state ✓
  - [x] Display "📌 FAR LEG ONLY" status in monitor non-silent output ✓
  - [x] Display "🔄 NEAR LEG ROLLED" status in monitor non-silent output ✓
  - [x] Show current mark value and days remaining for special states ✓
- [x] Ensure Greek and P&L calculations remain valid for far-leg-only positions ✓
  - [x] P&L calculated using only far leg value (not spread) for Far Leg Only ✓
  - [x] P&L calculated using spread value for Near Leg Rolled ✓
  - [x] Greeks fetched from Deribit API (already correct for far leg only) ✓
  - [x] 2 comprehensive tests verifying P&L calculation logic ✓
- [x] Verify recommendation thresholds apply correctly to rolled positions ✓
  - [x] Stop-loss thresholds work with corrected P&L calculations ✓
  - [x] Take-profit thresholds work with corrected P&L calculations ✓
  - [x] Verified through test cases and full test suite ✓
