-- Rewards spent and rewards received outside spending (user's request 2026-11-03; fintrack/rewards.py). Amounts in a card's
-- rewards unit are as in 0042 / 0044: points or miles, or cents of cash back.

-- A redemption: rewards taken off a card's balance. `kind`:
--   purchase  points paid for something (an award flight): no money moved; `value_cents` is the cash price you enter.
--             It may link the transaction that went with it (the taxes charged on the card).
--   cash      points turned into money: a credit on the card, `transaction_id` (required), and the value is its amount.
-- A transaction belongs to one redemption at most.
CREATE TABLE reward_redemptions (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date            TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    kind            TEXT NOT NULL CHECK (kind IN ('purchase', 'cash')),
    amount          INTEGER NOT NULL CHECK (amount > 0 AND amount <= 10000000000),   -- rewards used, in the card's unit
    unit            TEXT NOT NULL CHECK (unit IN ('points', 'miles', 'cash')),
    value_cents     INTEGER NOT NULL CHECK (value_cents >= 0),
    description     TEXT NOT NULL,
    category_key    TEXT REFERENCES categories(key) ON DELETE SET NULL,
    transaction_id  INTEGER UNIQUE REFERENCES transactions(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX reward_redemptions_card ON reward_redemptions (account_id, date);

-- A redemption's tags (the Tags page counts it as paid with rewards).
CREATE TABLE redemption_tags (
    redemption_id  INTEGER NOT NULL REFERENCES reward_redemptions(id) ON DELETE CASCADE,
    tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (redemption_id, tag_id)
);

-- Bonus rewards not tied to a purchase (a sign-up bonus paid in points): they add to the balance only.
CREATE TABLE reward_bonus_points (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date        TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    amount      INTEGER NOT NULL CHECK (amount > 0 AND amount <= 100000000),
    unit        TEXT NOT NULL CHECK (unit IN ('points', 'miles', 'cash')),
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX reward_bonus_points_card ON reward_bonus_points (account_id, date);

-- A card's page, Credit cards and Tags show these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_reward_redemptions_ins AFTER INSERT ON reward_redemptions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_redemptions_upd AFTER UPDATE ON reward_redemptions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_redemptions_del AFTER DELETE ON reward_redemptions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_redemption_tags_ins AFTER INSERT ON redemption_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_redemption_tags_del AFTER DELETE ON redemption_tags BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_bonus_points_ins AFTER INSERT ON reward_bonus_points BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_bonus_points_upd AFTER UPDATE ON reward_bonus_points BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_bonus_points_del AFTER DELETE ON reward_bonus_points BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
