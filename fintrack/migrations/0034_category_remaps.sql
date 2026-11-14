-- Deleted categories that Plaid still sends (user's request 2026-10-13: categories and subcategories can be deleted on
-- the Categories page). Deleting one moves its transactions to a category you choose; this table remembers that, so
-- syncs send Plaid's future transactions for it there too and never re-create it (categories.remaps).
CREATE TABLE category_remaps (
    from_key  TEXT PRIMARY KEY,                -- the deleted category's key (a Plaid primary or detailed code)
    to_key    TEXT NOT NULL REFERENCES categories(key) ON UPDATE CASCADE
);

-- Pages show categories built from this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_category_remaps_ins AFTER INSERT ON category_remaps BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_category_remaps_upd AFTER UPDATE ON category_remaps BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_category_remaps_del AFTER DELETE ON category_remaps BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
