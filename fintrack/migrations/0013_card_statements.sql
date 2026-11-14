-- Credit card statements from Plaid Liabilities (/liabilities/get, saved at each sync)
-- and which account pays each card, for the cash-flow forecast (M13).

-- Plaid only reports the CURRENT statement, so this is one row per card, replaced each sync.
CREATE TABLE card_statements (
    account_id               INTEGER PRIMARY KEY REFERENCES accounts(id),
    statement_balance_cents  INTEGER,       -- last_statement_balance
    statement_date           TEXT,          -- last_statement_issue_date (YYYY-MM-DD)
    due_date                 TEXT,          -- next_payment_due_date
    minimum_payment_cents    INTEGER,       -- can be negative (seen on a new card): informational only
    last_payment_cents       INTEGER,
    last_payment_date        TEXT,
    is_overdue               INTEGER CHECK (is_overdue IN (0, 1)),
    updated_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Your choice of paying account per card. No row = automatic (the only checking/savings
-- account, if there is exactly one). A row with NULL = "don't forecast this card".
CREATE TABLE card_payment_accounts (
    card_account_id      INTEGER PRIMARY KEY REFERENCES accounts(id),
    pay_from_account_id  INTEGER REFERENCES accounts(id),
    updated_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pages show both tables' data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_card_statements_ins AFTER INSERT ON card_statements BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_statements_upd AFTER UPDATE ON card_statements BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_statements_del AFTER DELETE ON card_statements BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_payment_accounts_ins AFTER INSERT ON card_payment_accounts BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_payment_accounts_upd AFTER UPDATE ON card_payment_accounts BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_payment_accounts_del AFTER DELETE ON card_payment_accounts BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
