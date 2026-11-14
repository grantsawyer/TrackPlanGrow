-- Plaid's subcategories added one at a time (user's request 2026-11-02; the Categories page's + panel), rather than a whole
-- category split at once (is_split, which stays for categories already split). 1 = this subcategory is a Plaid detailed code you
-- added: its transactions land here at every sync (categories.split_set, plaid_key).
ALTER TABLE categories ADD COLUMN plaid_added INTEGER NOT NULL DEFAULT 0 CHECK (plaid_added IN (0, 1));
