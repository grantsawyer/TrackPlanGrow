-- Investment activity you can edit (user's request 2026-10-31), like a bank or card transaction: your own description,
-- your own category, and tags. Sync's upsert never writes these columns, so they survive later syncs. A category you
-- pick decides the row's kind on the Transactions page (investment_activity.as_transactions); blank = the category the
-- activity gets by its type. Like transactions.user_category, a deleted category's choices move with it
-- (categories.delete_category).
ALTER TABLE investment_transactions ADD COLUMN user_description TEXT;
ALTER TABLE investment_transactions ADD COLUMN user_category TEXT;

-- Tags on investment activity: transaction_tags' twin (its transaction_id references transactions). Deleting an
-- activity row or a tag removes its links. tags.py reads both tables, activity under negative ids.
CREATE TABLE investment_transaction_tags (
    investment_transaction_id  INTEGER NOT NULL REFERENCES investment_transactions(id) ON DELETE CASCADE,
    tag_id                     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (investment_transaction_id, tag_id)
);
CREATE INDEX investment_transaction_tags_by_tag ON investment_transaction_tags (tag_id);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_investment_transaction_tags_ins AFTER INSERT ON investment_transaction_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_investment_transaction_tags_upd AFTER UPDATE ON investment_transaction_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_investment_transaction_tags_del AFTER DELETE ON investment_transaction_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
