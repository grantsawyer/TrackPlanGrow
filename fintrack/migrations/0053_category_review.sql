-- The Transactions page's "Categories to review" card (user's request 2026-11-01): a new transaction Plaid put in a
-- first-level category that has subcategories (Food & drink rather than Restaurants) waits there until you pick a
-- subcategory or dismiss the card, which accepts the category as it is (1 = accepted). Sync's upsert never writes this.
ALTER TABLE transactions ADD COLUMN category_reviewed INTEGER NOT NULL DEFAULT 0;

-- Only new transactions are reviewed: everything already here counts as accepted.
UPDATE transactions SET category_reviewed = 1;
