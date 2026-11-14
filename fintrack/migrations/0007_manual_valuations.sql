-- Milestone 7: dated values for manual accounts (car, home, unsupported 401(k), cash...).
--
-- A manual account's current_balance_cents is always its latest valuation
-- (kept in sync by fintrack/manual.py). Liabilities are stored as the positive
-- amount owed, the same convention Plaid uses. Valuations can't be future-dated.
CREATE TABLE manual_valuations (
    id           INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,                 -- YYYY-MM-DD
    value_cents  INTEGER NOT NULL CHECK (value_cents >= 0),
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, date)
);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_valuations_ins AFTER INSERT ON manual_valuations BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_valuations_upd AFTER UPDATE ON manual_valuations BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_valuations_del AFTER DELETE ON manual_valuations BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
