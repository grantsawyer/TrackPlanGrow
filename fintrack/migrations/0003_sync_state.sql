-- Milestone 3: transaction sync state.

-- Products enabled on the Item at Plaid (JSON array from /item/get, e.g. ["transactions"]).
-- Sync only runs for Items that already have "transactions": calling
-- /transactions/sync on any other Item would ADD Transactions to it at Plaid.
ALTER TABLE items ADD COLUMN products TEXT;

-- Last transactions_update_status from /transactions/sync:
-- NOT_READY, INITIAL_UPDATE_COMPLETE, or HISTORICAL_UPDATE_COMPLETE.
ALTER TABLE items ADD COLUMN transactions_status TEXT;

-- Plaid's confidence in personal_finance_category (VERY_HIGH ... LOW, UNKNOWN).
ALTER TABLE transactions ADD COLUMN plaid_category_confidence TEXT;

CREATE INDEX transactions_pending_id ON transactions (pending_transaction_id);
