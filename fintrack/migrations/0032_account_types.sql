-- Your account types (user's request 2026-10-13): a table of types and subtypes, two levels, that you can add to,
-- rename, reorder, move (a subtype to another type) and delete, starting from a default hierarchy. Each account can be
-- given a type or a subtype (Balances account page); with none, it keeps what Plaid (or its manual kind) says.
--
-- A type's `kind` is its behaviour (account_types.KINDS): asset or liability, which Balances / Net worth card, and
-- retirement vs investment. Subtypes have no kind of their own: they follow their type.
CREATE TABLE account_type_nodes (
    id         INTEGER PRIMARY KEY,
    parent_id  INTEGER REFERENCES account_type_nodes(id) ON DELETE CASCADE,   -- NULL = a type, else a subtype
    name       TEXT NOT NULL CHECK (length(trim(name)) > 0),
    kind       TEXT CHECK (kind IN ('cash', 'retirement', 'investment', 'lent', 'property', 'other_asset',
                                    'credit', 'loan', 'other_liability')),
    position   INTEGER NOT NULL DEFAULT 0,                                    -- order among its siblings
    CHECK ((parent_id IS NULL) = (kind IS NOT NULL))                           -- types have a kind, subtypes don't
);

-- The account's choice (a type or a subtype; NULL = automatic) and what it means, kept in step by account_types.py:
-- type_kind = its type's kind, type_label = its own name (shown on charts and pages).
ALTER TABLE accounts ADD COLUMN type_node_id INTEGER REFERENCES account_type_nodes(id) ON DELETE SET NULL;
ALTER TABLE accounts ADD COLUMN type_kind TEXT;
ALTER TABLE accounts ADD COLUMN type_label TEXT;

-- The default hierarchy.
INSERT INTO account_type_nodes (id, parent_id, name, kind, position) VALUES
    (1, NULL, 'Cash', 'cash', 1),
    (2, NULL, 'Retirement', 'retirement', 2),
    (3, NULL, 'Investment', 'investment', 3),
    (4, NULL, 'Other assets', 'lent', 4),
    (5, NULL, 'Property', 'property', 5),
    (6, NULL, 'Credit card', 'credit', 6),
    (7, NULL, 'Loan', 'loan', 7),
    (8, NULL, 'Other asset', 'other_asset', 8),
    (9, NULL, 'Other liability', 'other_liability', 9);
INSERT INTO account_type_nodes (parent_id, name, kind, position) VALUES
    (1, 'Checking', NULL, 1), (1, 'Savings', NULL, 2), (1, 'Money market', NULL, 3), (1, 'CD', NULL, 4),
    (2, '401(k)', NULL, 1), (2, '403(b)', NULL, 2), (2, 'IRA', NULL, 3), (2, 'Roth IRA', NULL, 4), (2, 'HSA', NULL, 5),
    (2, 'Pension', NULL, 6),
    (3, 'Brokerage', NULL, 1), (3, '529 plan', NULL, 2), (3, 'Crypto', NULL, 3),
    (4, 'Money lent', NULL, 1),
    (5, 'Home', NULL, 1), (5, 'Vehicle', NULL, 2),
    (6, 'Credit card', NULL, 1),
    (7, 'Mortgage', NULL, 1), (7, 'Auto loan', NULL, 2), (7, 'Student loan', NULL, 3), (7, 'Personal loan', NULL, 4);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_account_type_nodes_ins AFTER INSERT ON account_type_nodes BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_account_type_nodes_upd AFTER UPDATE ON account_type_nodes BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_account_type_nodes_del AFTER DELETE ON account_type_nodes BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
