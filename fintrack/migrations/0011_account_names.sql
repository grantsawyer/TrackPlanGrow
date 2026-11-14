-- Readable account names (milestone 12).
-- `name` stays the name every page shows. For Plaid accounts it's now derived
-- from Plaid's name (kept here in `plaid_name`) unless you renamed the account.
ALTER TABLE accounts ADD COLUMN plaid_name TEXT;
ALTER TABLE accounts ADD COLUMN name_is_custom INTEGER NOT NULL DEFAULT 0
    CHECK (name_is_custom IN (0, 1));

-- Until now `name` held Plaid's name verbatim. The next sync (or
-- account_names.refresh_auto_names) shortens it.
UPDATE accounts SET plaid_name = name WHERE is_manual = 0;
