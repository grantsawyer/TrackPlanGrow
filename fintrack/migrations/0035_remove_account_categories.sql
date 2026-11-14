-- Account categories removed (user's request 2026-10-13): your account types (migration 0032, account_types.py) do
-- everything they did, with an explicit kind and two levels. No account used one when this ran; any assignment is
-- cleared and the categories deleted. The table and accounts.category_id stay in the schema, unused: SQLite can't drop
-- a column that's part of a foreign key, and dropping the table would make every later write to accounts fail ("no
-- such table", tested 2026-10-13).
UPDATE accounts SET category_id = NULL WHERE category_id IS NOT NULL;
DELETE FROM account_categories;
