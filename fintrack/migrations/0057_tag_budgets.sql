-- Budgets for tags (user's requests 2026-11-02). On a tag's card: a total for the whole tag (a trip, a project; NULL =
-- none, the sum of its category budgets if it has any) and totals for categories within it.
ALTER TABLE tags ADD COLUMN budget_cents INTEGER CHECK (budget_cents IS NULL OR budget_cents >= 0);

-- Deleting a category removes these, as it removes the category's own budgets (categories.delete_category).
CREATE TABLE tag_category_budgets (
    tag_id        INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    category_key  TEXT NOT NULL REFERENCES categories(key) ON DELETE CASCADE ON UPDATE CASCADE,
    amount_cents  INTEGER NOT NULL CHECK (amount_cents >= 0),
    PRIMARY KEY (tag_id, category_key)
);

-- Tags you've added as lines on the Budgets page, with their place in each framework, like budget_lines (which can't
-- hold them: its category_key references categories). While a tag is here, its spending counts on its lines and not on
-- its categories' lines (budgets.py). Deleting a tag removes its line.
CREATE TABLE tag_budget_lines (
    tag_id        INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
    section       TEXT NOT NULL DEFAULT 'flexible' CHECK (section IN ('fixed', 'flexible')),
    need_want     TEXT CHECK (need_want IN ('needs', 'wants')),           -- NULL = wants
    custom_group  TEXT REFERENCES budget_groups(key) ON DELETE SET NULL   -- NULL = your first group if fixed, else second
);

-- What you allocate to a month on the Budgets page, for that month only (never spread or carried): to the tag
-- (category_key NULL) or to one of its category budgets.
CREATE TABLE tag_budget_allocations (
    id            INTEGER PRIMARY KEY,
    tag_id        INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    category_key  TEXT REFERENCES categories(key) ON DELETE CASCADE ON UPDATE CASCADE,
    month         TEXT NOT NULL,                                          -- 'YYYY-MM'
    amount_cents  INTEGER NOT NULL CHECK (amount_cents >= 0)
);
CREATE UNIQUE INDEX tag_budget_allocations_one ON tag_budget_allocations (tag_id, COALESCE(category_key, ''), month);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_tag_category_budgets_ins AFTER INSERT ON tag_category_budgets BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_category_budgets_upd AFTER UPDATE ON tag_category_budgets BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_category_budgets_del AFTER DELETE ON tag_category_budgets BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_lines_ins AFTER INSERT ON tag_budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_lines_upd AFTER UPDATE ON tag_budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_lines_del AFTER DELETE ON tag_budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_allocations_ins AFTER INSERT ON tag_budget_allocations BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_allocations_upd AFTER UPDATE ON tag_budget_allocations BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_allocations_del AFTER DELETE ON tag_budget_allocations BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
