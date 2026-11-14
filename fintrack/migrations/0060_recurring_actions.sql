-- Actions on recurring items (user's request 2026-11-03): a confirmed item's "Then do this" applies to each of its
-- transactions, like a rule's (fintrack/rules.py applies both). Same kinds and values as rule_actions, except "recurring".
CREATE TABLE recurring_actions (
    id       INTEGER PRIMARY KEY,
    item_id  INTEGER NOT NULL REFERENCES recurring_items(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,              -- set_description, set_category, add_tag, purpose, not_transfer, accept
    value    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX recurring_actions_item ON recurring_actions (item_id);

-- One-time actions (purpose, not a transfer, accept): done once per item and transaction, so an item never redoes what you
-- undid (as rule_applied does for rules).
CREATE TABLE recurring_applied (
    item_id         INTEGER NOT NULL REFERENCES recurring_items(id) ON DELETE CASCADE,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    PRIMARY KEY (item_id, transaction_id, kind)
);

-- The recurring item that set rule_category (NULL: a rule did, or nothing), so the category reads "set by a recurring item".
ALTER TABLE transactions ADD COLUMN rule_category_item INTEGER REFERENCES recurring_items(id) ON DELETE SET NULL;

-- As in 0054, with category_source 'recurring' when a recurring item set the category.
DROP VIEW transactions_effective;
CREATE VIEW transactions_effective AS
SELECT
    t.*,
    COALESCE(NULLIF(TRIM(t.user_description), ''), t.rule_description, t.merchant_name, t.name) AS description,
    a.name            AS account_name,
    a.mask            AS account_mask,
    a.type            AS account_type,
    a.is_hidden       AS account_hidden,
    COALESCE(t.user_category, t.rule_category, t.plaid_category_key, 'UNCATEGORIZED') AS category,
    COALESCE(c.label, t.user_category, t.rule_category, t.plaid_category_key, 'Uncategorized') AS category_label,
    CASE
        WHEN a.type = 'loan' THEN 'excluded'
        WHEN EXISTS (SELECT 1 FROM transactions o WHERE o.returned_id = t.id AND o.removed_at IS NOT NULL) THEN 'excluded'
        WHEN t.is_transfer = 1 THEN 'transfer'
        ELSE COALESCE(c.kind, 'expense')
    END AS kind,
    CASE
        WHEN t.user_category IS NOT NULL THEN 'you'
        WHEN t.rule_category IS NOT NULL AND t.rule_category_item IS NOT NULL THEN 'recurring'
        WHEN t.rule_category IS NOT NULL THEN 'rule'
        ELSE 'plaid'
    END AS category_source
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN categories c
    ON c.key = COALESCE(t.user_category, t.rule_category, t.plaid_category_key, 'UNCATEGORIZED');

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_recurring_actions_ins AFTER INSERT ON recurring_actions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_recurring_actions_upd AFTER UPDATE ON recurring_actions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_recurring_actions_del AFTER DELETE ON recurring_actions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
