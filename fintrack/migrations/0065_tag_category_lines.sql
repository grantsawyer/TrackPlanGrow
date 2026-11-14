-- A tag's category budgets are no longer lines on the Budgets page by themselves (user's request 2026-11-04): you add the
-- tag, or any of its category budgets ("Trip › Airfare", offered under the tag in + Add budget), each as its own line.
-- So a line is (tag, category): category_key '' = the tag's own line. The tags already on the budget keep their line;
-- their category lines, which came with them, go (no month had an amount on one).
CREATE TABLE tag_budget_lines_new (
    tag_id        INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    category_key  TEXT NOT NULL DEFAULT '',                               -- '' = the tag's own line
    section       TEXT NOT NULL DEFAULT 'flexible' CHECK (section IN ('fixed', 'flexible')),
    need_want     TEXT CHECK (need_want IN ('needs', 'wants')),           -- NULL = wants
    custom_group  TEXT REFERENCES budget_groups(key) ON DELETE SET NULL,  -- NULL = your first group if fixed, else second
    PRIMARY KEY (tag_id, category_key)
);
INSERT INTO tag_budget_lines_new (tag_id, section, need_want, custom_group)
    SELECT tag_id, section, need_want, custom_group FROM tag_budget_lines;
DROP TABLE tag_budget_lines;
ALTER TABLE tag_budget_lines_new RENAME TO tag_budget_lines;

-- Stale-page protection (see migration 0006); the old table's triggers went with it.
CREATE TRIGGER rev_tag_budget_lines_ins AFTER INSERT ON tag_budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_lines_upd AFTER UPDATE ON tag_budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_budget_lines_del AFTER DELETE ON tag_budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
