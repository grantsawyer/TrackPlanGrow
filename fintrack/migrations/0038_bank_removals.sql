-- Posted transactions the bank removes (user's request 2026-10-22, after Plaid removed 7 posted checking transactions
-- on 2026-10-22 and their money-lent links went with them). Plaid's /transactions/sync "removed" list is applied only
-- to PENDING transactions (a pending charge replaced by its posted version). A POSTED one is kept and flagged for your
-- review instead: your category, description and links stay on it (removals.py).
--
-- removed_at   when Plaid listed it as removed (UTC); NULL = not removed. Plaid sending the same id again clears it.
-- returned_id  the transaction that looks like it came back (same account, posted, arrived later, within 7 days, same
--              amount or same merchant / bank text; removals.match_returns). Cleared if that one is deleted.
ALTER TABLE transactions ADD COLUMN removed_at TEXT;
ALTER TABLE transactions ADD COLUMN returned_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL;
CREATE INDEX transactions_returned_id ON transactions (returned_id) WHERE returned_id IS NOT NULL;

-- Pairs you said are "Not the same": never suggested again.
CREATE TABLE removal_not_same (
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    candidate_id    INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    PRIMARY KEY (transaction_id, candidate_id)
);
CREATE TRIGGER rev_removal_not_same_ins AFTER INSERT ON removal_not_same BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_removal_not_same_upd AFTER UPDATE ON removal_not_same BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_removal_not_same_del AFTER DELETE ON removal_not_same BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

-- What counts: a removed transaction keeps counting (it was real money, and it carries your category and links, e.g.
-- a money-lent link). Its likely return is `excluded` until you review the pair, so the two are never counted twice
-- and your choices stay in force meanwhile; "Use the returned one" moves them across and deletes the removed one.
-- Otherwise identical to 0021.
DROP VIEW transactions_effective;
CREATE VIEW transactions_effective AS
SELECT
    t.*,
    COALESCE(NULLIF(TRIM(t.user_description), ''), t.merchant_name, t.name) AS description,
    a.name            AS account_name,
    a.mask            AS account_mask,
    a.type            AS account_type,
    a.is_hidden       AS account_hidden,
    COALESCE(t.user_category, r.category_key, t.plaid_category_key, 'UNCATEGORIZED') AS category,
    COALESCE(c.label, t.user_category, r.category_key, t.plaid_category_key, 'Uncategorized') AS category_label,
    CASE
        WHEN a.type = 'loan' THEN 'excluded'
        WHEN EXISTS (SELECT 1 FROM transactions o WHERE o.returned_id = t.id AND o.removed_at IS NOT NULL) THEN 'excluded'
        WHEN t.is_transfer = 1 THEN 'transfer'
        ELSE COALESCE(c.kind, 'expense')
    END AS kind,
    CASE
        WHEN t.user_category IS NOT NULL THEN 'you'
        WHEN t.rule_id IS NOT NULL THEN 'rule'
        ELSE 'plaid'
    END AS category_source
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN category_rules r ON r.id = t.rule_id
LEFT JOIN categories c
    ON c.key = COALESCE(t.user_category, r.category_key, t.plaid_category_key, 'UNCATEGORIZED');
