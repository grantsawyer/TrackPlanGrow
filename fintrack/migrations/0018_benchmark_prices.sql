-- Benchmark prices for the Investments performance cards (user's request 2026-10-07).
-- Finnhub's free tier has live quotes but no price history, so fintrack records one price
-- per benchmark ETF per market day (SPY = S&P 500, IWM = Russell 2000, URTH = MSCI World)
-- whenever it fetches quotes: on the Investments page and after each sync. The day's last
-- recorded price wins, so after the 18:00 sync it is effectively that day's close.
CREATE TABLE benchmark_prices (
    symbol      TEXT NOT NULL,           -- the ETF ticker, e.g. SPY
    price_date  TEXT NOT NULL,           -- YYYY-MM-DD, local date of Finnhub's quote time
    price       REAL NOT NULL,           -- per share, as quoted; not money we sum
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, price_date)
);

-- Pages show this table's data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_benchmark_prices_ins AFTER INSERT ON benchmark_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_benchmark_prices_upd AFTER UPDATE ON benchmark_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_benchmark_prices_del AFTER DELETE ON benchmark_prices BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
