-- Alpaca replaces Finnhub for stock prices (user's decision 2026-10-07). ticker_prices
-- (migration 0019) only allowed sources finnhub / plaid / you; SQLite can't change a CHECK
-- constraint in place, so the table is rebuilt with 'alpaca' allowed. Existing rows are kept,
-- including 'finnhub' ones recorded before the switch.
CREATE TABLE ticker_prices_new (
    ticker      TEXT NOT NULL,
    price_date  TEXT NOT NULL,            -- YYYY-MM-DD
    price       REAL NOT NULL CHECK (price > 0),
    -- alpaca (live quote or daily history), finnhub (before 2026-10-07), plaid (the same ticker
    -- in a connected account), you (a price you set)
    source      TEXT NOT NULL CHECK (source IN ('alpaca', 'finnhub', 'plaid', 'you')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, price_date)
);
INSERT INTO ticker_prices_new (ticker, price_date, price, source, created_at)
    SELECT ticker, price_date, price, source, created_at FROM ticker_prices;
DROP TABLE ticker_prices;
ALTER TABLE ticker_prices_new RENAME TO ticker_prices;

-- Pages show this table's data, so writes bump the page revision (see 0006). Dropping the old
-- table dropped its triggers.
CREATE TRIGGER rev_ticker_prices_ins AFTER INSERT ON ticker_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_ticker_prices_upd AFTER UPDATE ON ticker_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_ticker_prices_del AFTER DELETE ON ticker_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
