-- Custom account categories (user's request 2026-10-07).
-- A category is your own name for a group of accounts (e.g. "Retirement", "Emergency fund")
-- and is based on one built-in kind. Its accounts are grouped under its name AND treated
-- as that kind everywhere (net worth side, projection growth, cash-flow forecast, card
-- statements, spending rules). See account_categories.py.
CREATE TABLE account_categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    -- One of account_categories.BASE_KINDS: cash, investment, property, other_asset,
    -- credit, loan, other_liability.
    base_kind   TEXT NOT NULL CHECK (base_kind IN
                    ('cash', 'investment', 'property', 'other_asset', 'credit', 'loan', 'other_liability')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- An account's category (NULL = none). accounts.type / classification stay the
-- *effective* values every page reads; Plaid's own type moves to plaid_type so a sync
-- never overwrites your choice, and removing the category puts Plaid's type back.
ALTER TABLE accounts ADD COLUMN category_id INTEGER REFERENCES account_categories(id) ON DELETE SET NULL;
ALTER TABLE accounts ADD COLUMN plaid_type TEXT;
UPDATE accounts SET plaid_type = type WHERE is_manual = 0;

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_account_categories_ins AFTER INSERT ON account_categories BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_account_categories_upd AFTER UPDATE ON account_categories BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_account_categories_del AFTER DELETE ON account_categories BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
