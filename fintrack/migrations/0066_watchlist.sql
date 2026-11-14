-- Tickers you watch without holding them (user's request 2026-11-04): the Investments card's Watchlist tab. Their daily
-- closes go in ticker_prices (as manual positions' do, source 'alpaca'), live prices come with the page's quotes.
CREATE TABLE watchlist (
    ticker    TEXT PRIMARY KEY,                    -- upper case, as Alpaca knows it (watchlist.clean_ticker)
    name      TEXT NOT NULL DEFAULT '',            -- yours, optional; the ticker shows when blank
    added_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The Investments page shows it, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_watchlist_ins AFTER INSERT ON watchlist BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_watchlist_upd AFTER UPDATE ON watchlist BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_watchlist_del AFTER DELETE ON watchlist BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
