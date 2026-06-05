# Issues and bugs

## Statuses of calendars

Expectation: The app should correctly mark the status of the calendar strategy within the `result` column.The statuses are:

- Open (IMPLEMENTED)
- Far Leg Only (NOT IMPLENTED)
- Near Leg Rolled (NOT IMPLEMENTED)
- Closed (NOT IMPLEMENTED)

### Open

A calendar strategy is composed of 2 legs. When opening a calendar strategy one opens 2 calls or 2 puts:

- With calls: sell the near leg call, and buy the far leg call
- With puts: sell the near leg put, and buy the far leg call

### Near Leg Expired

BUG: When running `monitor` and when it finds the near leg has expired it marks the `result` column as Closed.

Expectation: the calendar record's `result` column should be marked as something like "Far Leg Only". This status/result should lead to `summary` and `portfolio` displaying this record. This status should also lead to `monitor` and `calendar` modules making new recommendations. Refer to the Recommendations section for what these should be.

### Near Leg Rolled

The calendar position now has 2 open legs: a near leg is sold, and the far leg remains open. The `result` columns is marked as "Near Leg Rolled".

### Closed

The calendar has no open legs. The 2 legs are either:

- both closed
- one closed, one expired
- both expired

## Trading actions

There are 4 actions one can take with an option trade:

- opening (IMPLEMENTED)
- closing the position early (NOT IMPLEMENTED)
- expiring worthless (IMPLEMENTED, BUG)
- expiring in the money (NOT IMPLEMENTED)

The action of letting an option position expire is a typical action with options.

### Opening a position

IMPLEMENTED: Opening a position means to enter a position by either buying an option, or selling it. This requires a connection to the broker, and implies a flow of money to settle the transaction.

### Closing a position (early)

IMPLEMENTED, NOT TESTED: One may choose to close the position previously entered into. This means either selling (if previously bought) or buying (if previously sold) the option. This results in a connection to the broker and money flows to settle the transaction.
When it comes to closing a calendar spread, each leg would be closed indvidually at a different time, or at the same time. These are two separate transactions in any case.

### Expires worthless

When an option expires worthless, here is what happens:

1. For a Call, the underlying spot price is below strike

    - if you sold the Call, you keep the premium.
    - if you bought the Call, you lost the premium.

2. For a Put, the underlying spot price is above strike.

    - if you sold a Put, you keep the premium
    - if you bought a Put, you keep the premium

BUG: This function should happen at a level common to all the modules that manage strategies and automation: calendar, scanner, spread, strangle, executor, monitor.

### Expires in the money

When an option expires in the money, here is what happens:

1. For a Call, the underlying spot price is above strike

    - if you sold the Call, you will be assigned. You will owe the intrinsic value: `(spot - strike) * (contract multiplier)`
    - if you bought the Call, you  will be owed the intrinsic value.

2. For a Put, the underlying spot price is above strike.

    - if you sold a Put, you will be assigned. You will owe the intrinsic value: `(strike - spot) * (contract multiplier)`
    - if you bought a Put, you  will be owed the intrinsic value.

This function should happen at a level common to all the modules that manage strategies and automation: calendar, scanner, spread, strangle, executor, monitor.

## Recommendations

BUG: When the app makes a Recommendation to close the position: nothing happens here!
EXPECTATION: the app should be able to go and sell the far leg on deribit, but I expect the app to ask me what I want to do:

- close the far leg (and do that with deribit)
- keep the position
- provide the roll options (1d, 3d, 7d expiries)

=> these options should be presented with their probability of profit, and justification as to why the recommendation was made.

BUG: In the roll options, how can a 1d near leag expire in 18d? It should expire in 1 or 2 days, at most, though that may depend on the asset.
Further how can a 3d near leg expire before the 1d near leg, and how can the 3d expire in 16d?
Similar questions for the 7d near: how can it expire in 12d, and how can it expire before the 7d near leg?
EXPECTATION: The app should make correct calculations, and count from today, not back from the far leg's Expiry.
EXPECTATION: The app should actually be able to execute the roll options by entering the position as chosen by the app user.
 ▸ Roll Options (New Near Legs)
  • 1d near leg (expires in 18d)
  • 3d near leg (expires in 16d)
  • 7d near leg (expires in 12d)

## Example Outputs from the app

────────────────────────────────────────────────────────────
 Position Monitor
────────────────────────────────────────────────────────────
  Checking all open positions across all assets...

  ⚡ AUTO-CLOSE [EXPIRY] ETH Call Calendar
  Strike $2,100  |  Net debit: $6.61  |  Near leg EXPIRED WORTHLESS

────────────────────────────────────────────────────────────
 Far Leg Analysis — ETH Call $2,100
────────────────────────────────────────────────────────────

 ▸ Market Data
 Instrument                        ETH-19JUN26-2100-C
 Underlying Price                  $2,021.23
 Mark Price                        $0.0247
 Bid / Ask                         $0.0240 / $0.0255

 ▸ Implied Volatility
 Mark IV                           LOW (44.1%)
 Bid / Ask IV                      43.3% / 45.0%

 ▸ Greeks & Greeks-Based Analysis
 Delta                             0.3725  Moderately ITM (delta=0.37) — some profit potential
 Gamma                             0.001840  Price-sensitive
 Vega                              1.7612  Long vol
 Theta (daily)                     -0.0007  Time-decay negative
 Rho                               0.3728

 ▸ Position Analysis
 Current Mark Value                $0.02
 Current P&L                       $-6.60  (0% of original debit)
 Open Interest                     424 contracts

 ▸ Recommendation
  CLOSE — Position is losing money. Exit and avoid further losses.

 ▸ Roll Options (New Near Legs)
  • 1d near leg (expires in 18d)
  • 3d near leg (expires in 16d)
  • 7d near leg (expires in 12d)

 ✓ ETH Call calendar near leg marked as expired worthless. Far leg retained for analysis.

  Thresholds:  Stop-loss 2.0x strangle  |  Take-profit <5% remaining (>2d from expiry)  |  Calendar stop 50% of debit
