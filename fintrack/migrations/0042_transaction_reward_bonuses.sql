-- A reward bonus on one transaction (user's request 2026-10-26): one-offs (a promotion, a retention offer), added in the
-- transaction's edit panel rather than by a rate. The amount is in its card's unit (rewards.py card_rewards.unit):
-- points or miles, or cents of cash back. The Credit cards page adds it to the card's estimate when the transaction's
-- date is in the range. Sync carries it from a pending transaction to its posted version (sync._upsert_transaction).

CREATE TABLE transaction_reward_bonuses (
    transaction_id  INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    amount          INTEGER NOT NULL CHECK (amount > 0 AND amount <= 100000000)
);

-- The Credit cards page and the Transactions rows show these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_transaction_reward_bonuses_ins AFTER INSERT ON transaction_reward_bonuses BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_reward_bonuses_upd AFTER UPDATE ON transaction_reward_bonuses BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_reward_bonuses_del AFTER DELETE ON transaction_reward_bonuses BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
