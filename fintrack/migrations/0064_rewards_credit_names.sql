-- A rewards pair's credit is named after its cost with " (Credit)" at the end (user's request 2026-11-04), no longer
-- "Paid with rewards: " in front (manual_transactions.credit_name).
UPDATE transactions SET name = substr(name, 20) || ' (Credit)'
WHERE plaid_transaction_id LIKE 'manual-%' AND name LIKE 'Paid with rewards: %';
