-- Pre-tax contributions (user's request 2026-10-11): money taken from pay before tax and paid straight into a
-- retirement account (401(k) deferrals, HSA payroll deposits, and an employer match if you include it). It never
-- passes through a bank account, and the accounts it reaches are often manual, so you enter it: "from this month,
-- $X a month into this account", until your next row for the account. NULL amount = stopped from that month.
-- The Savings page adds it to income and to retirement savings (pretax.py, savings.py). Same effective-from
-- model as budgets.
CREATE TABLE pretax_contributions (
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    start_month   TEXT NOT NULL CHECK (start_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    amount_cents  INTEGER CHECK (amount_cents IS NULL OR amount_cents >= 0),
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, start_month)
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_pretax_ins AFTER INSERT ON pretax_contributions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_pretax_upd AFTER UPDATE ON pretax_contributions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_pretax_del AFTER DELETE ON pretax_contributions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
