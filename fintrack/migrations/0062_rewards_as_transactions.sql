-- Rewards spent become transactions (user's request 2026-11-04), replacing 0061's redemptions table: what a trip costs is
-- the same whether cash or points paid, so a purchase paid with points is two manual transactions on its card (ids
-- 'manual-…', which no sync touches), in the same category as your own choice: the cost (with your tags) and a rebate of the
-- same amount carrying the points, so the category nets to zero and a tag counts the cost. A real credit on the card that
-- points paid (a statement credit) carries its points the same way. Neither earns rewards (rewards.py).

-- The rewards a transaction used (a rebate or credit: money in), in its card's unit; `charge_id`: the manual cost it pays.
CREATE TABLE transaction_rewards_used (
    transaction_id  INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    amount          INTEGER NOT NULL CHECK (amount > 0 AND amount <= 10000000000),
    unit            TEXT NOT NULL CHECK (unit IN ('points', 'miles', 'cash')),
    charge_id       INTEGER REFERENCES transactions(id) ON DELETE SET NULL
);
CREATE INDEX transaction_rewards_used_charge ON transaction_rewards_used (charge_id);

-- 0061's redemptions as transactions. A purchase: its cost (tags move to it) and its rebate.
INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key, user_category)
    SELECT 'manual-redemption-' || id || '-cost', account_id, date, value_cents, description,
           COALESCE(category_key, 'UNCATEGORIZED'), category_key
    FROM reward_redemptions WHERE kind = 'purchase';
INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key, user_category)
    SELECT 'manual-redemption-' || id || '-rebate', account_id, date, -value_cents, 'Paid with rewards: ' || description,
           COALESCE(category_key, 'UNCATEGORIZED'), category_key
    FROM reward_redemptions WHERE kind = 'purchase';
INSERT INTO transaction_tags (transaction_id, tag_id)
    SELECT t.id, rt.tag_id FROM redemption_tags rt
    JOIN transactions t ON t.plaid_transaction_id = 'manual-redemption-' || rt.redemption_id || '-cost';
INSERT INTO transaction_rewards_used (transaction_id, amount, unit, charge_id)
    SELECT rebate.id, r.amount, r.unit, cost.id FROM reward_redemptions r
    JOIN transactions rebate ON rebate.plaid_transaction_id = 'manual-redemption-' || r.id || '-rebate'
    JOIN transactions cost ON cost.plaid_transaction_id = 'manual-redemption-' || r.id || '-cost'
    WHERE r.kind = 'purchase';
-- A statement credit: its points on the credit.
INSERT OR IGNORE INTO transaction_rewards_used (transaction_id, amount, unit)
    SELECT transaction_id, amount, unit FROM reward_redemptions WHERE kind = 'cash' AND transaction_id IS NOT NULL;

DROP TABLE redemption_tags;
DROP TABLE reward_redemptions;

-- Transactions, a card's page and Credit cards show these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_transaction_rewards_used_ins AFTER INSERT ON transaction_rewards_used BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_rewards_used_upd AFTER UPDATE ON transaction_rewards_used BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transaction_rewards_used_del AFTER DELETE ON transaction_rewards_used BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
