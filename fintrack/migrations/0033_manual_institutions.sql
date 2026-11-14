-- Manual accounts' institution (user's request 2026-10-13): a name you type on the account page ("Acme Investments"), shown
-- like a connected account's institution, with its logo when Plaid's /institutions/search finds it (free, no Item;
-- logos.find_manual_institution_logos). Alpaca's logo endpoint was checked the same day and refused on the free plan
-- ("Subscription does not permit querying logos").
ALTER TABLE accounts ADD COLUMN manual_institution TEXT;

-- What a search for a name found, so each name is looked up once: the matched Plaid institution (its logo is in
-- institution_logos), or none. 'none' / 'failed' are searched again after 30 days.
CREATE TABLE institution_searches (
    query           TEXT PRIMARY KEY,          -- the name as typed, lower-cased
    institution_id  TEXT,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'none', 'failed')),
    checked_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_institution_searches_ins AFTER INSERT ON institution_searches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_institution_searches_upd AFTER UPDATE ON institution_searches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_institution_searches_del AFTER DELETE ON institution_searches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
