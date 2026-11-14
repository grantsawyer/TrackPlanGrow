-- Rules redesigned (user's request 2026-11-02): one or more conditions on description, amount, date, category or account,
-- all of which must match, and one or more actions (fintrack/rules.py). Rules run in `position` order. A transaction keeps
-- what its rules decided in its own columns, recomputed whenever a rule changes; your own description and category still win.
-- category_rules and transactions.rule_id are no longer read or written: kept as a record, because rule_id's foreign key
-- ties them to `transactions`, which SQLite can't change without rebuilding that table.
CREATE TABLE rules (
    id          INTEGER PRIMARY KEY,
    position    INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE rule_conditions (
    id       INTEGER PRIMARY KEY,
    rule_id  INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    field    TEXT NOT NULL,              -- description, merchant, bank_text, amount, direction, date, category, account
    op       TEXT NOT NULL,              -- rules.OPS
    "values" TEXT NOT NULL               -- a JSON list: phrases, cents, ISO dates, category keys or account ids
);
CREATE INDEX rule_conditions_rule ON rule_conditions (rule_id);
CREATE TABLE rule_actions (
    id       INTEGER PRIMARY KEY,
    rule_id  INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,              -- set_description, set_category, add_tag, recurring, purpose, not_transfer, accept
    value    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX rule_actions_rule ON rule_actions (rule_id);

-- What rules decided for a transaction. `rule_category_by`: the rule that set the category (ON DELETE: recomputed anyway).
ALTER TABLE transactions ADD COLUMN rule_category TEXT;
ALTER TABLE transactions ADD COLUMN rule_category_by INTEGER REFERENCES rules(id) ON DELETE SET NULL;
ALTER TABLE transactions ADD COLUMN rule_description TEXT;

-- Tags a rule added leave when the rule stops applying; ones you added stay. A rule's tag you took off stays off.
ALTER TABLE transaction_tags ADD COLUMN from_rule INTEGER NOT NULL DEFAULT 0;
CREATE TABLE rule_tag_removals (
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    tag_id          INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (transaction_id, tag_id)
);

-- One-time actions (recurring, purpose, not a transfer, accept): done once per rule and transaction, so a rule never redoes
-- what you undid. They stay when the rule is deleted.
CREATE TABLE rule_applied (
    rule_id         INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    PRIMARY KEY (rule_id, transaction_id, kind)
);

-- Your existing rules, same ids, oldest first. "either" (merchant or bank text) is the new description condition.
INSERT INTO rules (id, position, created_at) SELECT id, id, created_at FROM category_rules;
INSERT INTO rule_conditions (rule_id, field, op, "values")
    SELECT id, CASE field WHEN 'either' THEN 'description' WHEN 'description' THEN 'bank_text' ELSE 'merchant' END,
           'contains', json_array(pattern) FROM category_rules;
INSERT INTO rule_actions (rule_id, kind, value) SELECT id, 'set_category', category_key FROM category_rules;

UPDATE transactions SET rule_category_by = rule_id,
                        rule_category = (SELECT category_key FROM category_rules r WHERE r.id = transactions.rule_id)
    WHERE rule_id IS NOT NULL;

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
        WHEN t.rule_category IS NOT NULL THEN 'rule'
        ELSE 'plaid'
    END AS category_source
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN categories c
    ON c.key = COALESCE(t.user_category, t.rule_category, t.plaid_category_key, 'UNCATEGORIZED');

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_rules_new_ins AFTER INSERT ON rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rules_new_upd AFTER UPDATE ON rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rules_new_del AFTER DELETE ON rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rule_conditions_ins AFTER INSERT ON rule_conditions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rule_conditions_upd AFTER UPDATE ON rule_conditions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rule_conditions_del AFTER DELETE ON rule_conditions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rule_actions_ins AFTER INSERT ON rule_actions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rule_actions_upd AFTER UPDATE ON rule_actions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rule_actions_del AFTER DELETE ON rule_actions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
