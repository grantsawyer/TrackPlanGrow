-- Daily prices of the securities you hold (Overview "movers"), recorded at each sync
-- from Plaid holdings. Plaid only reports the current price, so history starts here.
-- One row per security per *price date* (Plaid's institution_price_as_of, else the
-- sync day): a weekend sync re-records Friday's price instead of inventing a flat day.
CREATE TABLE security_prices (
    security_id  TEXT NOT NULL,
    price_date   TEXT NOT NULL,          -- YYYY-MM-DD
    ticker       TEXT,
    name         TEXT,
    price        REAL NOT NULL,          -- per share, as reported; not money we sum
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (security_id, price_date)
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_security_prices_ins AFTER INSERT ON security_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_security_prices_upd AFTER UPDATE ON security_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_security_prices_del AFTER DELETE ON security_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
