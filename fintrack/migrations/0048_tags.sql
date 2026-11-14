-- Tags (user's request 2026-10-28): your own labels (emoji welcome) to group related transactions, e.g. a trip or a
-- project, beside their categories. A transaction can have any number; a tag never changes what a transaction counts as.
-- The Tags page (Analysis) sums each one. Names are unique ignoring case (budgets' group names follow the same rule).
CREATE TABLE tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) BETWEEN 1 AND 40),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Which transactions have which tags. Deleting a transaction or a tag removes its links.
CREATE TABLE transaction_tags (
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    tag_id          INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (transaction_id, tag_id)
);
CREATE INDEX transaction_tags_by_tag ON transaction_tags (tag_id);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_tags_ins AFTER INSERT ON tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tags_upd AFTER UPDATE ON tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tags_del AFTER DELETE ON tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_tags_ins AFTER INSERT ON transaction_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_tags_upd AFTER UPDATE ON transaction_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_tags_del AFTER DELETE ON transaction_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
