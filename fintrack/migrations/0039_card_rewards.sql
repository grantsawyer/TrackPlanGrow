-- Credit card reward rates you enter (user's request 2026-10-25; Plaid doesn't report rewards). The Credit cards page
-- estimates each card's rewards from its spending by category: spending x the category's rate (rewards.py).
-- Rates are integer hundredths (150 = 1.5x points per dollar, or 1.5% cash back), like money in cents: no floats.

-- One row per card you've set up: what it earns and its rate for everything without a rate of its own.
CREATE TABLE card_rewards (
    account_id      INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    unit            TEXT NOT NULL DEFAULT 'points' CHECK (unit IN ('points', 'miles', 'cash')),
    base_rate_x100  INTEGER NOT NULL DEFAULT 100 CHECK (base_rate_x100 BETWEEN 0 AND 10000)
);

-- A card's rate for one category; it also covers the category's subcategories unless one has its own rate. Deleting
-- the category removes the rate (its transactions move to another category, which has its own rate or the base).
CREATE TABLE card_reward_rates (
    account_id      INTEGER NOT NULL REFERENCES card_rewards(account_id) ON DELETE CASCADE,
    category_key    TEXT NOT NULL REFERENCES categories(key) ON DELETE CASCADE,
    rate_x100       INTEGER NOT NULL CHECK (rate_x100 BETWEEN 0 AND 10000),
    PRIMARY KEY (account_id, category_key)
);

-- The Credit cards page shows these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_card_rewards_ins AFTER INSERT ON card_rewards BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_rewards_upd AFTER UPDATE ON card_rewards BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_rewards_del AFTER DELETE ON card_rewards BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_ins AFTER INSERT ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_upd AFTER UPDATE ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_del AFTER DELETE ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
