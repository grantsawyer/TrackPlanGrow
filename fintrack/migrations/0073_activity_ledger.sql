-- One investment activity ledger for every investment account (docs/parity-plan.md phase 2, user's decisions 2026-11-14).

-- A manual account's position changes worked out from its activity rows (investment_transactions; fintrack/ledger.py
-- rebuilds them whenever its activity changes). 0: one you entered (a balance, or an event from before the ledger).
ALTER TABLE manual_position_changes ADD COLUMN from_activity INTEGER NOT NULL DEFAULT 0 CHECK (from_activity IN (0, 1));

-- Deleted imported rows are no longer kept out of a re-import (user's request 2026-11-14: re-importing the file brings a
-- deleted row back, to recreate it). The table from migration 0071 goes.
DROP TABLE deleted_imports;
