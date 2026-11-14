-- A bank charge partly paid with rewards (user's request 2026-11-04): a $50 charge that was a $200 ticket with $150 paid in
-- points. The $150 is a manual rewards pair (0062's cost + rebate, ids 'manual-part-…'); its cost points at the bank's
-- charge here and follows its date, category and tags (manual_transactions.sync_parts), so the category counts the $50
-- that left and a tag the $200 it cost. Sync moves the link from a pending charge to its posted version; a pair whose
-- charge is gone with no successor is deleted.
ALTER TABLE transactions ADD COLUMN rewards_part_of INTEGER REFERENCES transactions(id) ON DELETE SET NULL;
CREATE INDEX transactions_rewards_part_of ON transactions (rewards_part_of) WHERE rewards_part_of IS NOT NULL;
