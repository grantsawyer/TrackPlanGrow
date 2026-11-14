-- Merchant reward rates match text you type (user's request 2026-10-26): "United" applies to every transaction on the
-- card whose description (transactions_effective.description: yours > merchant > bank text) contains it, ignoring
-- case, instead of one merchant picked from a list (0040). card_reward_rates is rebuilt with `merchant_text` in place
-- of merchant_key / merchant_name; a picked merchant becomes its shown name as the text.

CREATE TABLE card_reward_rates_new (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES card_rewards(account_id) ON DELETE CASCADE,
    category_key    TEXT REFERENCES categories(key) ON DELETE CASCADE,
    merchant_text   TEXT CHECK (merchant_text IS NULL OR TRIM(merchant_text) <> ''),
    rate_x100       INTEGER NOT NULL CHECK (rate_x100 BETWEEN 0 AND 10000),
    CHECK (category_key IS NOT NULL OR merchant_text IS NOT NULL)
);

INSERT INTO card_reward_rates_new (id, account_id, category_key, merchant_text, rate_x100)
    SELECT id, account_id, category_key, merchant_name, rate_x100 FROM card_reward_rates;

DROP TABLE card_reward_rates;                       -- its index and revision triggers go with it; recreated below
ALTER TABLE card_reward_rates_new RENAME TO card_reward_rates;

-- One rate per card per (category, text); a missing one counts as "any", and "United" and "united" are the same text.
CREATE UNIQUE INDEX card_reward_rates_target
    ON card_reward_rates (account_id, COALESCE(category_key, ''), LOWER(COALESCE(merchant_text, '')));

-- The Credit cards page shows these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_card_reward_rates_ins AFTER INSERT ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_upd AFTER UPDATE ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_card_reward_rates_del AFTER DELETE ON card_reward_rates BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
