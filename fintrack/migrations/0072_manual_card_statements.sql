-- A manual card's statement (docs/parity-plan.md phase 1, user's decision 2026-11-14): you set the day it closes and
-- the day it's due; each statement's balance is the card's balance at the end of its closing date (it follows the
-- card's transactions, manual.value_on), written into card_statements like Plaid's (cards.refresh_manual_statements),
-- so every page reads it as it reads a connected card's. Changing a statement's closing date changes its balance.
CREATE TABLE card_statement_settings (
    account_id      INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    closing_day     INTEGER NOT NULL CHECK (closing_day BETWEEN 1 AND 31),
    due_day         INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 31),
    minimum_cents   INTEGER CHECK (minimum_cents IS NULL OR minimum_cents >= 0),   -- the current statement's, if you give it
    statement_date  TEXT,              -- the current statement's closing date when you changed it (YYYY-MM-DD)
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER rev_card_statement_settings_ins AFTER INSERT ON card_statement_settings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_statement_settings_upd AFTER UPDATE ON card_statement_settings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_statement_settings_del AFTER DELETE ON card_statement_settings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
