-- Core Plaid mirror: Items (institution logins), accounts, transactions.
-- Later milestones add their own tables in new migration files.
-- All money columns are integer cents.

-- One row per Plaid Item (= one institution login).
-- Production rows are NEVER deleted: Plaid's Trial plan does not refund an
-- Item slot when an Item is removed, so this table doubles as the permanent
-- ledger used to count how many of the 10 Production Items have been used.
CREATE TABLE items (
    id                   INTEGER PRIMARY KEY,
    plaid_item_id        TEXT NOT NULL UNIQUE,
    plaid_env            TEXT NOT NULL CHECK (plaid_env IN ('sandbox', 'production')),
    institution_id       TEXT,
    institution_name     TEXT,
    access_token_enc     BLOB,          -- Fernet-encrypted; NULL once the Item is removed
    transactions_cursor  TEXT,          -- /transactions/sync cursor
    status               TEXT NOT NULL DEFAULT 'ok'
                         CHECK (status IN ('ok', 'login_required', 'error', 'removed')),
    error_code           TEXT,
    error_message        TEXT,
    last_synced_at       TEXT,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    removed_at           TEXT
);

CREATE TRIGGER items_production_never_deleted
BEFORE DELETE ON items
WHEN OLD.plaid_env = 'production'
BEGIN
    SELECT RAISE(ABORT, 'Production items are a permanent ledger; mark them removed instead');
END;

-- Plaid accounts and (from milestone 7) manual accounts.
-- `classification` decides the net-worth sign: assets add, liabilities subtract.
CREATE TABLE accounts (
    id                       INTEGER PRIMARY KEY,
    item_id                  INTEGER REFERENCES items(id),
    plaid_account_id         TEXT UNIQUE,
    name                     TEXT NOT NULL,
    official_name            TEXT,
    mask                     TEXT CHECK (mask IS NULL OR length(mask) <= 4),  -- last 4 only
    type                     TEXT NOT NULL,   -- depository / credit / investment / loan / other
    subtype                  TEXT,
    classification           TEXT NOT NULL CHECK (classification IN ('asset', 'liability')),
    current_balance_cents    INTEGER,
    available_balance_cents  INTEGER,
    iso_currency_code        TEXT,
    is_manual                INTEGER NOT NULL DEFAULT 0 CHECK (is_manual IN (0, 1)),
    is_hidden                INTEGER NOT NULL DEFAULT 0 CHECK (is_hidden IN (0, 1)),
    balance_updated_at       TEXT,
    created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- A Plaid account must belong to an Item; a manual account must not.
    CHECK (
        (is_manual = 0 AND item_id IS NOT NULL AND plaid_account_id IS NOT NULL)
        OR (is_manual = 1 AND item_id IS NULL AND plaid_account_id IS NULL)
    )
);

-- amount_cents follows Plaid's sign convention: positive = money leaving the
-- account (a purchase), negative = money coming in (a deposit or refund).
CREATE TABLE transactions (
    id                      INTEGER PRIMARY KEY,
    plaid_transaction_id    TEXT NOT NULL UNIQUE,
    account_id              INTEGER NOT NULL REFERENCES accounts(id),
    date                    TEXT NOT NULL,   -- YYYY-MM-DD
    authorized_date         TEXT,
    amount_cents            INTEGER NOT NULL,
    iso_currency_code       TEXT,
    name                    TEXT NOT NULL,   -- raw description from the bank
    merchant_name           TEXT,
    plaid_category_primary  TEXT,            -- personal_finance_category.primary
    plaid_category_detailed TEXT,            -- personal_finance_category.detailed
    user_category           TEXT,            -- manual override or rule result; wins when set
    pending                 INTEGER NOT NULL DEFAULT 0 CHECK (pending IN (0, 1)),
    pending_transaction_id  TEXT,
    is_transfer             INTEGER NOT NULL DEFAULT 0 CHECK (is_transfer IN (0, 1)),
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX transactions_account_date ON transactions (account_id, date);
CREATE INDEX transactions_date ON transactions (date);
