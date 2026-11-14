-- Your own description for a transaction (milestone 14, user's request 2026-10-08). Plaid's name
-- and merchant_name stay as reported (syncs keep updating them, and rules match on them), so an
-- edited description never changes which rules apply. NULL or blank = Plaid's text.
ALTER TABLE transactions ADD COLUMN user_description TEXT;

-- The view gains `description`: what every page shows (yours > merchant > the bank's text).
-- Otherwise identical to migration 0005's definition.
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
