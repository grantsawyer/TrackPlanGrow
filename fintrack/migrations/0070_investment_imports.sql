-- Investment files (docs/import-plan.md phase 5, user's decision 2026-11-13): activity (buys, sells, dividends,
-- contributions...) into a connected account's investment activity or a manual account's position lines; holdings
-- statements into a manual account's lines. imports/engine.py.

-- What kind of file an import was.
ALTER TABLE import_batches ADD COLUMN kind TEXT NOT NULL DEFAULT 'transactions'
    CHECK (kind IN ('transactions', 'activity', 'positions'));

-- A connected account's imported activity (ids 'imp-…'), so undo can remove it.
ALTER TABLE investment_transactions ADD COLUMN import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL;

-- A manual line's change: how much of it was money paid in from outside (+) or taken out (-), for the Savings page's
-- retirement contributions. NULL = as before: a 'contribution' event's own dollars. An imported buy moves shares and
-- cash but contributes 0; a contribution that bought shares moves shares and contributes its dollars.
ALTER TABLE manual_position_changes ADD COLUMN contributed_cents INTEGER;
ALTER TABLE manual_position_changes ADD COLUMN import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL;
ALTER TABLE manual_positions ADD COLUMN import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL;

-- The activity rows imported into a manual account (their made-up ids), so the same rows again add nothing and an
-- overlapping file's likely duplicates are found (date, Plaid-signed cash amount).
CREATE TABLE import_rows (
    id            TEXT PRIMARY KEY,           -- 'imp-<hash>'
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    batch_id      INTEGER REFERENCES import_batches(id) ON DELETE CASCADE,
    date          TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    symbol        TEXT
);
CREATE INDEX import_rows_account ON import_rows (account_id, date);
