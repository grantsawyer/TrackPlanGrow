-- Transactions you tie to a recurring item by hand (user's request 2026-10-16), from a transaction's edit panel:
-- "Add to a recurring item", "Make it recurring", or "Not part of it". One row per transaction. item_id = the item it
-- belongs to; NULL = you said it isn't part of any recurring item (matching by description then leaves it out).
-- Without a row, a transaction belongs to a confirmed item when matching finds it (recurring.matching_transaction_ids).
CREATE TABLE recurring_links (
    transaction_id  INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    item_id         INTEGER REFERENCES recurring_items(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX recurring_links_item ON recurring_links (item_id);

-- Pages show these links (the Transactions page's recurring icon), so writes bump the page revision (see 0006).
CREATE TRIGGER rev_recurring_links_ins AFTER INSERT ON recurring_links BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_recurring_links_upd AFTER UPDATE ON recurring_links BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_recurring_links_del AFTER DELETE ON recurring_links BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
