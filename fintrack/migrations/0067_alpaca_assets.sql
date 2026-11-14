-- Alpaca's list of US stocks and ETFs (user's decision 2026-11-04), for finding a ticker by company name on the
-- watchlist: fetched about weekly (watchlist.refresh_assets) and searched here, so typing sends nothing anywhere. Public
-- market data: no data of yours. When it was last fetched is app_settings 'alpaca_assets_at'.
CREATE TABLE alpaca_assets (
    symbol    TEXT PRIMARY KEY,
    name      TEXT NOT NULL,                -- as Alpaca names it, less a trailing "Common Stock"
    exchange  TEXT NOT NULL DEFAULT ''
);
