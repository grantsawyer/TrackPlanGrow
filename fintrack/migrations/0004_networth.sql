-- Milestone 4: daily balance snapshots (net-worth history) and investment holdings.

-- One row per account per local calendar day, written at the end of each sync.
-- Re-syncing the same day overwrites that day's row (the last balance wins).
-- classification and account_type are copied in so history stays correct even
-- if an account is later hidden (Item removed) or reclassified.
CREATE TABLE balance_snapshots (
    date            TEXT NOT NULL,                      -- YYYY-MM-DD, local time
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    balance_cents   INTEGER NOT NULL,                   -- Plaid `current`, as reported
    classification  TEXT NOT NULL CHECK (classification IN ('asset', 'liability')),
    account_type    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, account_id)
);

-- Current positions per investment account, replaced wholesale on each sync
-- (Plaid returns the complete current list). Informational: the account's
-- balance already includes these, so net worth never adds them separately.
CREATE TABLE holdings (
    id                 INTEGER PRIMARY KEY,
    account_id         INTEGER NOT NULL REFERENCES accounts(id),
    security_id        TEXT NOT NULL,
    name               TEXT,
    ticker             TEXT,
    security_type      TEXT,             -- equity, etf, mutual fund, cash, ...
    is_cash            INTEGER NOT NULL DEFAULT 0 CHECK (is_cash IN (0, 1)),
    quantity           REAL,             -- shares can be fractional; not money
    price              REAL,             -- per-share price as reported; not summed
    value_cents        INTEGER,
    cost_basis_cents   INTEGER,
    price_as_of        TEXT,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, security_id)
);
