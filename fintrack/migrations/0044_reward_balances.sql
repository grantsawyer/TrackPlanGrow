-- A card's rewards balance as of a date (user's request 2026-10-26), typed on its account page: points or miles, or
-- cents of cash back. One per card per date; a later entry for the same date replaces it. The unit is kept on each row
-- (the card's rewards unit when you entered it), so changing the card's setup later doesn't relabel old balances.

CREATE TABLE reward_balances (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    as_of       TEXT NOT NULL CHECK (as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    unit        TEXT NOT NULL CHECK (unit IN ('points', 'miles', 'cash')),
    amount      INTEGER NOT NULL CHECK (amount >= 0 AND amount <= 10000000000),
    UNIQUE (account_id, as_of)
);

-- The account page shows these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_reward_balances_ins AFTER INSERT ON reward_balances BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_balances_upd AFTER UPDATE ON reward_balances BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_reward_balances_del AFTER DELETE ON reward_balances BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
