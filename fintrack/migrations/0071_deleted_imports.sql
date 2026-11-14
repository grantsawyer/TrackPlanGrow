-- Imported transactions you deleted one at a time (docs/parity-plan.md phase 1, user's rule 2026-11-14: an imported
-- row is yours, like one you added). Kept so importing the same file again doesn't bring them back (imports/engine.py).
CREATE TABLE deleted_imports (
    id          TEXT PRIMARY KEY,          -- the row's made-up id ('csv-…')
    account_id  INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    deleted_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
