-- Optional subcategories (user's request 2026-10-05). A Plaid category can be "split":
-- its transactions then use Plaid's detailed subcategory (e.g. RENT_AND_UTILITIES_RENT ->
-- "Rent") instead of the top level. Subcategories are ordinary categories with a parent.
ALTER TABLE categories ADD COLUMN parent_key TEXT REFERENCES categories(key);
ALTER TABLE categories ADD COLUMN is_split INTEGER NOT NULL DEFAULT 0 CHECK (is_split IN (0, 1));
