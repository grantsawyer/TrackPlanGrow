-- Live prices reach net worth (user's decision 2026-10-12, reversing the earlier "live prices never reach net worth"
-- rule). A connected investment account's balance is kept as the institution reported it at the last sync
-- (reported_balance_cents), and current_balance_cents - what net worth and every page read - becomes that balance plus
-- the market move of its quotable holdings since the sync (live_values.py). Other accounts: the two are equal.
ALTER TABLE accounts ADD COLUMN reported_balance_cents INTEGER;
UPDATE accounts SET reported_balance_cents = current_balance_cents WHERE is_manual = 0;
