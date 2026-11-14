-- Reward rates by category and/or merchant (user's request 2026-10-26): e.g. airfare from United earns a different rate
-- from other airfare and from other United spending. card_reward_rates (0039) is rebuilt with its own id, an optional
-- category and an optional merchant (at least one). The rates you had (category only) are copied over.
--
-- merchant_key is the Spending page's merchant key (spending.merchant_key_of): "e:" + Plaid's merchant_entity_id, else
-- "k:" + the normalized merchant / bank text. merchant_name is how it was shown when you picked it.

CREATE TABLE card_reward_rates_new (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES card_rewards(account_id) ON DELETE CASCADE,
    category_key    TEXT REFERENCES categories(key) ON DELETE CASCADE,
    merchant_key    TEXT,
    merchant_name   TEXT,
    rate_x100       INTEGER NOT NULL CHECK (rate_x100 BETWEEN 0 AND 10000),
    CHECK (category_key IS NOT NULL OR merchant_key IS NOT NULL),
    CHECK ((merchant_key IS NULL) = (merchant_name IS NULL))
);

INSERT INTO card_reward_rates_new (account_id, category_key, rate_x100)
    SELECT account_id, category_key, rate_x100 FROM card_reward_rates;

DROP TABLE card_reward_rates;                       -- its revision triggers go with it; recreated below
ALTER TABLE card_reward_rates_new RENAME TO card_reward_rates;

-- One rate per card per (category, merchant); a missing one counts as "any" (NULLs would never clash in a plain UNIQUE).
CREATE UNIQUE INDEX card_reward_rates_target
    ON card_reward_rates (account_id, COALESCE(category_key, ''), COALESCE(merchant_key, ''));

-- The Credit cards page shows these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_card_reward_rates_ins AFTER INSERT ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_upd AFTER UPDATE ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_del AFTER DELETE ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
