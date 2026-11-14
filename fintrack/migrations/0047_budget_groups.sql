-- Custom budget groups you add and name on the page (user's request 2026-10-28): any number of spending groups
-- (budgets.MAX_GROUPS), replacing 0046's fixed pair. Keys are 'custom1', 'custom2', ...; a removed group's key
-- can be used again (its lines were let go: ON DELETE SET NULL). `position` is the display order. The first two take your names from 0046's settings.
CREATE TABLE budget_groups (
    key       TEXT PRIMARY KEY CHECK (key LIKE 'custom%'),
    name      TEXT NOT NULL,
    position  INTEGER NOT NULL
);
INSERT INTO budget_groups (key, name, position) VALUES
    ('custom1', COALESCE((SELECT value FROM app_settings WHERE key = 'budget_custom1_name'), 'Group 1'), 1),
    ('custom2', COALESCE((SELECT value FROM app_settings WHERE key = 'budget_custom2_name'), 'Group 2'), 2);
DELETE FROM app_settings WHERE key IN ('budget_custom1_name', 'budget_custom2_name');

-- budget_lines.custom_group was limited to 'custom1' / 'custom2' (0046). SQLite can't drop a CHECK, so the table is
-- rebuilt with every row, now pointing at budget_groups (a removed group's lines go back to their default group).
CREATE TABLE budget_lines_new (
    category_key  TEXT PRIMARY KEY REFERENCES categories(key) ON UPDATE CASCADE ON DELETE CASCADE,
    section       TEXT NOT NULL CHECK (section IN ('income', 'fixed', 'flexible')),
    need_want     TEXT CHECK (need_want IN ('needs', 'wants')),
    custom_group  TEXT REFERENCES budget_groups(key) ON DELETE SET NULL
);
INSERT INTO budget_lines_new (category_key, section, need_want, custom_group)
SELECT category_key, section, need_want, custom_group FROM budget_lines;
DROP TABLE budget_lines;
ALTER TABLE budget_lines_new RENAME TO budget_lines;

-- Stale-page protection (see migration 0006): budget_lines' triggers went with the old table.
CREATE TRIGGER rev_budget_lines_ins AFTER INSERT ON budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_lines_upd AFTER UPDATE ON budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_lines_del AFTER DELETE ON budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_groups_ins AFTER INSERT ON budget_groups BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_groups_upd AFTER UPDATE ON budget_groups BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_groups_del AFTER DELETE ON budget_groups BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
