-- Investment transactions (user's request 2026-10-09, tested in the Plaid Sandbox first): the activity inside
-- brokerage / IRA / 401(k) / HSA accounts from /investments/transactions/get - contributions, withdrawals,
-- dividends, fees, buys and sells. Money moved INTO or OUT OF the account is what the Savings and Portfolio
-- "Transfers in and out" charts read (contributions.py); the rest is kept for detail.
--
-- Synced only when Settings.sync_investment_transactions is true: always in the Sandbox, in Production only with
-- INVESTMENT_TRANSACTIONS=on in .env, because the first call adds Plaid's separate "Investments Transactions"
-- subscription to an Item (billing).
--
-- amount_cents follows Plaid's investment sign: positive = cash debited from the account (a purchase, a withdrawal,
-- a fee), negative = cash credited (a contribution, a dividend, a sale).
CREATE TABLE investment_transactions (
    id                               INTEGER PRIMARY KEY,
    plaid_investment_transaction_id  TEXT NOT NULL UNIQUE,
    account_id                       INTEGER NOT NULL REFERENCES accounts(id),
    date                             TEXT NOT NULL,          -- YYYY-MM-DD
    name                             TEXT NOT NULL,
    type                             TEXT NOT NULL,          -- buy / sell / cancel / cash / fee / transfer
    subtype                          TEXT,                   -- contribution, deposit, withdrawal, dividend, ...
    security_id                      TEXT,
    quantity                         REAL,
    price                            REAL,
    amount_cents                     INTEGER NOT NULL,
    fees_cents                       INTEGER,
    iso_currency_code                TEXT,
    created_at                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX investment_transactions_account_date ON investment_transactions (account_id, date);

-- Pages that show this data must reload when it changes (see app_revision, migration 0006).
CREATE TRIGGER rev_investment_transactions_ins AFTER INSERT ON investment_transactions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_investment_transactions_upd AFTER UPDATE ON investment_transactions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_investment_transactions_del AFTER DELETE ON investment_transactions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
