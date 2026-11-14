-- Milestone 10: loan terms (for payoff schedules), what-if scenarios, projection settings.

-- Interest rate and monthly payment per liability account. Plaid's Liabilities
-- product covers credit cards, mortgages, and student loans; others (auto loans,
-- HELOCs, manual liabilities) can be entered by you. source = 'you' rows are
-- never overwritten by a sync.
CREATE TABLE liability_terms (
    account_id              INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    apr_percent             REAL CHECK (apr_percent IS NULL OR (apr_percent >= 0 AND apr_percent < 100)),
    monthly_payment_cents   INTEGER CHECK (monthly_payment_cents IS NULL OR monthly_payment_cents >= 0),
    source                  TEXT NOT NULL CHECK (source IN ('plaid', 'you')),
    detail                  TEXT,     -- e.g. 'mortgage, matures 2045-07-31'
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Named what-if scenarios. adjustments is JSON (see fintrack/projection.py).
-- Scenarios never change real data; they only adjust projection inputs.
CREATE TABLE scenarios (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    adjustments  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Projection assumptions (blank monthly savings = use your 12-month average).
INSERT INTO app_settings (key, value) VALUES
    ('proj_monthly_savings_cents', ''),
    ('proj_investment_return_pct', '5'),
    ('proj_property_growth_pct', '0'),
    ('proj_redirect_paid_off', '1');

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_terms_ins AFTER INSERT ON liability_terms BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_terms_upd AFTER UPDATE ON liability_terms BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_terms_del AFTER DELETE ON liability_terms BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_scenarios_ins AFTER INSERT ON scenarios BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_scenarios_upd AFTER UPDATE ON scenarios BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_scenarios_del AFTER DELETE ON scenarios BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
