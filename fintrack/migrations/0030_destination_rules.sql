-- Destination rules (user's request 2026-10-12): a bank-side transfer to an investment / retirement institution says
-- which bank account it came from but not which investment account received it. When it can't be paired with that
-- account's own activity (contributions.py; e.g. before the institution's history starts), a rule names the account:
-- "transfers whose raw bank text contains `pattern` went to this account".
--
-- The raw text is Plaid's original_description (e.g. "BROKER BUY INVESTMENT ***********1234"), which carries a
-- reference the cleaned-up name drops. Stored from now on at every sync; `fintrack descriptions --backfill` fills it in
-- for history synced before (read-only /transactions/get).
ALTER TABLE transactions ADD COLUMN original_description TEXT;

CREATE TABLE destination_rules (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    pattern     TEXT NOT NULL CHECK (length(trim(pattern)) > 0),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_destination_rules_ins AFTER INSERT ON destination_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_destination_rules_upd AFTER UPDATE ON destination_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_destination_rules_del AFTER DELETE ON destination_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
