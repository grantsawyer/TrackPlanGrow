-- Merchant logos (user's request 2026-10-08). Plaid's /transactions/sync (and /transactions/get)
-- return a logo address per transaction (logo_url, a 100x100 PNG on plaid-merchant-logos.plaid.com,
-- else a counterparty's on plaid-counterparty-logos.plaid.com) and a stable merchant id
-- (merchant_entity_id, the same for every location of a merchant).
ALTER TABLE transactions ADD COLUMN logo_url TEXT;
ALTER TABLE transactions ADD COLUMN merchant_entity_id TEXT;
CREATE INDEX transactions_merchant_entity ON transactions (merchant_entity_id);

-- Logos fintrack downloaded (once each) into FINTRACK_HOME/logos/<file>; the browser only ever
-- loads them from fintrack. status 'failed' remembers a bad download so it isn't retried on every
-- sync (logos.RETRY_FAILED_DAYS later it is).
CREATE TABLE logo_files (
    url         TEXT PRIMARY KEY,
    file        TEXT,                    -- <sha256 of url>.png, NULL when failed
    status      TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    fetched_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_logo_files_ins AFTER INSERT ON logo_files BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_logo_files_upd AFTER UPDATE ON logo_files BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_logo_files_del AFTER DELETE ON logo_files BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
