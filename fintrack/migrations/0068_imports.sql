-- Importing bank files of any layout (docs/import-plan.md, user's decisions 2026-11-13; fintrack/imports/).

-- Your confirmed layout for a file with this header, per account: which column holds what, the date format, the sign,
-- and your mapping of the bank's own categories. A file with the same header (`fingerprint`) reuses it.
CREATE TABLE import_profiles (
    id           INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    fingerprint  TEXT NOT NULL,              -- layout.fingerprint: the column names in order (or the width, no header)
    header       TEXT,                       -- JSON list of the column names, to show the old choices if a bank renames one
    layout       TEXT NOT NULL,              -- JSON (layout.Layout.to_json)
    categories   TEXT NOT NULL DEFAULT '{}', -- JSON {bank's category: [Plaid primary, Plaid detailed]}
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, fingerprint)
);

-- One row per import, so a whole import can be undone. The file itself is never kept (user's decision): only its name,
-- a fingerprint of its bytes, and the rows it added (transactions.import_batch_id).
CREATE TABLE import_batches (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    file_name       TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    rows_added      INTEGER NOT NULL,
    date_from       TEXT,                    -- the dates of the rows added
    date_to         TEXT,
    overlap_choice  TEXT CHECK (overlap_choice IS NULL OR overlap_choice IN ('outside', 'skip', 'review')),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    undone_at       TEXT                     -- set by undo, which deletes the batch's transactions
);
CREATE INDEX import_batches_account ON import_batches (account_id, created_at);

-- The import that added a transaction (NULL for Plaid's, yours, and rows imported before batches existed).
ALTER TABLE transactions ADD COLUMN import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL;
CREATE INDEX transactions_import_batch ON transactions (import_batch_id) WHERE import_batch_id IS NOT NULL;

-- Pages show these tables' data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_import_profiles_ins AFTER INSERT ON import_profiles BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_import_profiles_upd AFTER UPDATE ON import_profiles BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_import_profiles_del AFTER DELETE ON import_profiles BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_import_batches_ins AFTER INSERT ON import_batches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_import_batches_upd AFTER UPDATE ON import_batches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_import_batches_del AFTER DELETE ON import_batches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
