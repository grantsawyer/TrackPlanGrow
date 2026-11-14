-- Contributions and withdrawals entered as a number of shares (user's request 2026-10-12: the account page's Add form
-- is date / ticker / contribution or withdrawal / quantity or value). delta_quantity = the shares you typed (+ in,
-- - out) on an entry = 'contribution' row with no cash_cents; `quantity` stays the shares held from that date (derived:
-- previous + delta, manual_positions.rederive). The Savings page prices it at that day's price.
ALTER TABLE manual_position_changes ADD COLUMN delta_quantity REAL;
