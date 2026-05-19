# Migration Module

Utilities for migrating data from Excel to the SQLite database.

## Overview

The migration module reads trade data from the Excel workbook (`crypto_options_trade_tracker.xlsx`) and populates the SQLite database tables (`optionsStrat.db`).

## What Gets Migrated

| Excel Sheet | Target Table | Trades |
|---|---|---|
| 📝 Paper Trades | `singles` | Wheel strategy trades |
| 🔀 Strangles | `strangles` | Short strangle trades |
| 📅 Calendars | `calendars` | Calendar spread trades |

## Running the Migration

### One-shot migration (all sheets)

```bash
python -m migration.excel_to_db
```

This will:
1. Read all three sheets from `crypto_options_trade_tracker.xlsx`
2. Map column values to the appropriate database fields
3. Insert rows into `singles`, `strangles`, and `calendars` tables
4. Print a summary of rows migrated

### Programmatic usage

```python
from migration import migrate_all, migrate_singles, migrate_strangles, migrate_calendars
from models import get_session, init_db

# Initialize the database (creates schema)
init_db()

# Run all migrations
migrate_all()

# Or migrate each sheet individually
session = get_session()
singles_count = migrate_singles(session)
strangles_count = migrate_strangles(session)
calendars_count = migrate_calendars(session)
```

## Column Mappings

### Paper Trades → singles

| Excel | Database | Notes |
|---|---|---|
| Date | `date_open` | Entry date |
| Type | `option_type` | Extracted: "Put" or "Call" |
| Stage | `stage` | "short_put" / "holding" / "short_call" / "closed" |
| Days | `days` | Days to expiry |
| Strike | `strike` | Strike price |
| Spot (Open) | `spot_open` | Spot price at entry |
| Spot (Close) | `spot_close` | Spot price at settlement (if closed) |
| Premium | `premium` | Premium collected |
| P&L | `pnl` | Profit/loss (if closed) |
| Result | `result` | "Win" / "Loss" / "Open" |
| Notes | `notes` | Trade notes (used to infer asset) |
| _(derived)_ | `asset` | Extracted from notes; defaults to "ETH" |
| _(derived)_ | `date_close` | Set if Result is not "Open" |

### Strangles → strangles

| Excel | Database | Notes |
|---|---|---|
| Date | `date_open` | Entry date |
| Type | `strategy` | Used to detect open vs. closed |
| Put Strike | `put_strike` | Put strike price |
| Call Strike | `call_strike` | Call strike price |
| Spot (Open) | `spot_open` | Spot price at entry |
| Spot (Close) | `spot_close` | Spot price at close |
| Premium | `total_premium` | Combined premium from both legs |
| P&L | `pnl` | Profit/loss (if closed) |
| Days | `days` | Days to expiry |
| Result | `result` | "Win" / "Loss" / "Open" |
| Notes | `notes` | Trade notes |
| _(derived)_ | `asset` | Extracted from notes; defaults to "ETH" |
| _(derived)_ | `date_close` | Set if Result is not "Open" |

### Calendars → calendars

| Excel | Database | Notes |
|---|---|---|
| Date | `date_open` | Entry date |
| Strike | `strike` | Strike price |
| Option Type | `option_type` | "Call" or "Put" |
| Spot (Open) | `spot_open` | Spot price at entry |
| Spot (Close) | `spot_close` | Spot price at close |
| Near Premium | `near_prem` | Short leg premium |
| Far Premium | `far_prem` | Long leg premium |
| Net Debit | `net_debit` | Max loss (far_prem - near_prem) |
| P&L | `pnl` | Profit/loss (if closed) |
| Near Days / Far Days | `near_days`, `far_days` | Parsed from "7/30" format |
| Result | `result` | "Win" / "Loss" / "Open" |
| Notes | `notes` | Trade notes |
| _(derived)_ | `asset` | Extracted from notes; defaults to "ETH" |
| _(derived)_ | `date_close` | Set if Result is not "Open" |

## Helper Functions

### Asset Extraction

```python
from migration.excel_to_db import _extract_asset_from_notes

asset = _extract_asset_from_notes("Short strangle on ETH, 15% OTM")
# Returns: "ETH"

asset = _extract_asset_from_notes("some notes without asset")
# Returns: "ETH" (default)
```

### Date Parsing

Supports multiple formats:

```python
from migration.excel_to_db import _parse_date

_parse_date("06-May-2026")       # → datetime.date(2026, 5, 6)
_parse_date("2026-05-06")        # → datetime.date(2026, 5, 6)
_parse_date("05/06/2026")        # → datetime.date(2026, 5, 6)
_parse_date(datetime.date(2026, 5, 6))  # → datetime.date(2026, 5, 6)
_parse_date(None)                # → None
```

### Near/Far Days Parsing

```python
from migration.excel_to_db import _parse_near_far_days

near, far = _parse_near_far_days("7/30")
# Returns: (7, 30)

near, far = _parse_near_far_days("7")
# Returns: (7, None)
```

## Notes

- The migration **does not delete or modify** the Excel workbook.
- Rows are inserted in order as they appear in the sheets.
- If the Excel sheet is missing or the file doesn't exist, the migration skips that sheet with a warning.
- Asset symbols (ETH, BTC, SOL, XRP) are extracted from the Notes field via case-insensitive substring matching; if not found, the default is "ETH".
- `qty` (quantity) is not stored in the Excel sheets, so it remains `NULL` in the database.
- Run `migrate_all()` once to populate the database from scratch; subsequent updates to the database should use the ORM directly to avoid duplicate inserts.
