-- Milestone 8: monthly budgets per spending category.
--
-- A budget applies from start_month onward until the next row for the same
-- category. The budget for month M is the row with the greatest start_month <= M.
-- amount_cents NULL means "no budget from this month on". Past months keep the
-- budget that applied then, so budget-vs-actual history stays truthful.
-- Rollover carries (computed, never stored) are derived from transactions, so
-- recategorizing an old transaction correctly changes later carries.
CREATE TABLE budgets (
    id            INTEGER PRIMARY KEY,
    category_key  TEXT NOT NULL REFERENCES categories(key) ON UPDATE CASCADE ON DELETE CASCADE,
    start_month   TEXT NOT NULL CHECK (start_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
    amount_cents  INTEGER CHECK (amount_cents IS NULL OR amount_cents >= 0),
    rollover      INTEGER NOT NULL DEFAULT 0 CHECK (rollover IN (0, 1)),
    UNIQUE (category_key, start_month)
);

-- Small key/value settings (budget warning threshold now; forecast settings later).
CREATE TABLE app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
INSERT INTO app_settings (key, value) VALUES ('budget_warn_threshold', '0.8');

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_budgets_ins AFTER INSERT ON budgets BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budgets_upd AFTER UPDATE ON budgets BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budgets_del AFTER DELETE ON budgets BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_settings_ins AFTER INSERT ON app_settings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_settings_upd AFTER UPDATE ON app_settings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_settings_del AFTER DELETE ON app_settings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
