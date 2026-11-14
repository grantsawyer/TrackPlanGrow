-- Institution logos (user's request 2026-10-13: the Balances page's account rows show each account's institution logo).
-- Plaid's /institutions/get_by_id returns a base64 152x152 PNG, or none ("Not all institutions' logos are available");
-- logos.fetch_institution_logos saves it once per institution as FINTRACK_HOME/logos/<sha256>.png (the existing
-- /logos/<hash>.png route serves it). status: 'ok' (file set), 'none' (Plaid has no logo) or 'failed' (the call
-- failed); 'none' and 'failed' are tried again after 30 days.
CREATE TABLE institution_logos (
    institution_id  TEXT PRIMARY KEY,
    file            TEXT,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'none', 'failed')),
    fetched_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_institution_logos_ins AFTER INSERT ON institution_logos BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_institution_logos_upd AFTER UPDATE ON institution_logos BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_institution_logos_del AFTER DELETE ON institution_logos BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
