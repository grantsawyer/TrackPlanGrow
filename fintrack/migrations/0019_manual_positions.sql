-- Positions in manual investment accounts (user's request 2026-10-07).
-- Instead of typing a value, a manual Investment / retirement account can hold lines:
-- a ticker with a quantity (valued at its price) or another asset with a dollar amount
-- (cash, a fund without a ticker, ...). Quantities and amounts are dated, so changes over
-- time are kept and any day can be valued with what you held then. The account's value
-- history (manual_valuations) is then written from the lines; see manual_positions.py.
CREATE TABLE manual_positions (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    ticker      TEXT,                     -- NULL = a non-stock line with a dollar amount
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('stock', 'etf', 'fund', 'bond', 'cash', 'other')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- "From this date, this line is N shares / $X." The amount on a day is the latest row on
-- or before it. Ticker lines use quantity, other lines value_cents; 0 = sold / emptied.
CREATE TABLE manual_position_changes (
    position_id  INTEGER NOT NULL REFERENCES manual_positions(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,           -- YYYY-MM-DD
    quantity     REAL CHECK (quantity >= 0),
    value_cents  INTEGER CHECK (value_cents >= 0),
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (position_id, date),
    CHECK ((quantity IS NULL) <> (value_cents IS NULL))
);

-- One price per ticker per day for these lines, so past days can be (re)valued.
-- source: finnhub (live quote), plaid (the same ticker in a connected account), you.
CREATE TABLE ticker_prices (
    ticker      TEXT NOT NULL,
    price_date  TEXT NOT NULL,            -- YYYY-MM-DD
    price       REAL NOT NULL CHECK (price > 0),
    source      TEXT NOT NULL CHECK (source IN ('finnhub', 'plaid', 'you')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, price_date)
);

-- Pages show these tables' data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_manual_positions_ins AFTER INSERT ON manual_positions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_manual_positions_upd AFTER UPDATE ON manual_positions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_manual_positions_del AFTER DELETE ON manual_positions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_position_changes_ins AFTER INSERT ON manual_position_changes BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_position_changes_upd AFTER UPDATE ON manual_position_changes BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_position_changes_del AFTER DELETE ON manual_position_changes BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_ticker_prices_ins AFTER INSERT ON ticker_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_ticker_prices_upd AFTER UPDATE ON ticker_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_ticker_prices_del AFTER DELETE ON ticker_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
