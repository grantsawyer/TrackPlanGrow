-- Milestone 5: categories with a "kind", categorization rules, transfer matching.

-- Every category has a kind that decides how it's counted:
--   expense  -> counts as spending
--   income   -> counts as income
--   transfer -> neither (moves between your own accounts, card payments, loans received)
-- Defaults follow Plaid's PFC v2 primaries, with two detailed categories split
-- out because they behave differently from their primary:
--   LOAN_PAYMENTS_CREDIT_CARD_PAYMENT -> CREDIT_CARD_PAYMENT (transfer; purchases already counted)
--   TRANSFER_OUT_WITHDRAWAL           -> CASH_WITHDRAWAL (expense; the cash gets spent)
-- You can change any category's kind, and add your own categories.
CREATE TABLE categories (
    key         TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('expense', 'income', 'transfer')),
    is_custom   INTEGER NOT NULL DEFAULT 0 CHECK (is_custom IN (0, 1)),
    sort_order  INTEGER NOT NULL DEFAULT 100
);

INSERT INTO categories (key, label, kind, sort_order) VALUES
    ('INCOME',                    'Income',                     'income',   1),
    ('FOOD_AND_DRINK',            'Food & drink',               'expense', 10),
    ('GENERAL_MERCHANDISE',       'Shopping',                   'expense', 11),
    ('RENT_AND_UTILITIES',        'Rent & utilities',           'expense', 12),
    ('TRANSPORTATION',            'Transportation',             'expense', 13),
    ('TRAVEL',                    'Travel',                     'expense', 14),
    ('ENTERTAINMENT',             'Entertainment',              'expense', 15),
    ('PERSONAL_CARE',             'Personal care',              'expense', 16),
    ('MEDICAL',                   'Medical',                    'expense', 17),
    ('HOME_IMPROVEMENT',          'Home improvement',           'expense', 18),
    ('GENERAL_SERVICES',          'Services',                   'expense', 19),
    ('GOVERNMENT_AND_NON_PROFIT', 'Government & non-profit',    'expense', 20),
    ('BANK_FEES',                 'Bank fees',                  'expense', 21),
    ('LOAN_PAYMENTS',             'Loan payments',              'expense', 22),
    ('CASH_WITHDRAWAL',           'Cash & ATM',                 'expense', 23),
    ('OTHER',                     'Other',                      'expense', 24),
    ('UNCATEGORIZED',             'Uncategorized',              'expense', 25),
    ('CREDIT_CARD_PAYMENT',       'Credit card payment',        'transfer', 30),
    ('TRANSFER_IN',               'Transfer in',                'transfer', 31),
    ('TRANSFER_OUT',              'Transfer out',               'transfer', 32),
    ('LOAN_DISBURSEMENTS',        'Loan received',              'transfer', 33);

-- "merchant/description contains X -> category Y". Case-insensitive substring.
-- Priority: lowest id (first created) wins.
CREATE TABLE category_rules (
    id            INTEGER PRIMARY KEY,
    pattern       TEXT NOT NULL CHECK (length(trim(pattern)) > 0),
    field         TEXT NOT NULL DEFAULT 'either' CHECK (field IN ('merchant', 'description', 'either')),
    category_key  TEXT NOT NULL REFERENCES categories(key) ON UPDATE CASCADE,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Plaid's category mapped to our key (primary, with the two splits above).
ALTER TABLE transactions ADD COLUMN plaid_category_key TEXT;
-- The rule that categorized this transaction. Deleting the rule falls back to Plaid's category.
ALTER TABLE transactions ADD COLUMN rule_id INTEGER REFERENCES category_rules(id) ON DELETE SET NULL;
-- Auto-matched other half of a transfer between your own accounts (is_transfer = 1 when set).
ALTER TABLE transactions ADD COLUMN transfer_pair_id INTEGER;
-- You said "Not a transfer": never auto-match this transaction.
ALTER TABLE transactions ADD COLUMN transfer_ignore INTEGER NOT NULL DEFAULT 0 CHECK (transfer_ignore IN (0, 1));

UPDATE transactions SET plaid_category_key = CASE
    WHEN plaid_category_detailed = 'LOAN_PAYMENTS_CREDIT_CARD_PAYMENT' THEN 'CREDIT_CARD_PAYMENT'
    WHEN plaid_category_detailed = 'TRANSFER_OUT_WITHDRAWAL' THEN 'CASH_WITHDRAWAL'
    ELSE COALESCE(plaid_category_primary, 'UNCATEGORIZED')
END;

CREATE INDEX transactions_rule ON transactions (rule_id);

-- The single place that decides each transaction's category and how it counts.
--   category: your choice > rule > Plaid
--   kind:     'excluded' for loan accounts' own entries (they mirror the payment
--             made from checking), 'transfer' when auto-matched, else the category's kind.
CREATE VIEW transactions_effective AS
SELECT
    t.*,
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
