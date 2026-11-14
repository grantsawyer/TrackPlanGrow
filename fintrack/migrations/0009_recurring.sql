-- Milestone 9: recurring income and bills, and forecast settings.
--
-- Plaid's Recurring Transactions product isn't on the Trial plan, so items are
-- detected locally from transaction history (source 'local') or added by you
-- ('manual'). Only CONFIRMED items are used in forecasts.
CREATE TABLE recurring_items (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    merchant_key        TEXT,            -- normalized merchant/description; links history (local items)
    account_id          INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    category_key        TEXT,            -- for month-end projection by category
    amount_cents        INTEGER NOT NULL,  -- Plaid sign: positive = money out, negative = money in
    amount_varies       INTEGER NOT NULL DEFAULT 0 CHECK (amount_varies IN (0, 1)),
    frequency           TEXT NOT NULL CHECK (frequency IN
                            ('weekly', 'biweekly', 'semimonthly', 'monthly', 'quarterly', 'annual')),
    days_of_month       TEXT,            -- semimonthly: '1,15'; monthly/quarterly/annual: the intended
                                         -- day, e.g. '31' (so Feb 28 goes back to Mar 31); weekly: NULL
    next_expected_date  TEXT NOT NULL,   -- YYYY-MM-DD; also the schedule's anchor
    type                TEXT NOT NULL CHECK (type IN ('income', 'bill', 'transfer')),
    status              TEXT NOT NULL DEFAULT 'detected' CHECK (status IN ('detected', 'confirmed', 'dismissed')),
    source              TEXT NOT NULL CHECK (source IN ('local', 'manual')),
    last_seen_date      TEXT,
    occurrences         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One detected item per (merchant, direction, account): re-detection updates it in place.
CREATE UNIQUE INDEX recurring_local_identity
    ON recurring_items (merchant_key, account_id, (amount_cents > 0)) WHERE source = 'local';

INSERT INTO app_settings (key, value) VALUES ('low_balance_threshold_cents', '50000');

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_recurring_ins AFTER INSERT ON recurring_items BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_recurring_upd AFTER UPDATE ON recurring_items BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_recurring_del AFTER DELETE ON recurring_items BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
