-- Dollar events on manual position lines (user's request 2026-10-12), replacing the Savings page's pre-tax calculator.
--
-- A line's change can now be entered in dollars:
--   entry = 'contribution': cash_cents = +$X (money in) or -$X (a withdrawal) on that date; a ticker line gains or
--                           loses X / (the ticker's price that day) shares on top of what it held;
--   entry = 'opening':      cash_cents = the line's value on that date (its starting amount): shares = value / price.
-- `quantity` (or `value_cents` for lines without a ticker) stays the amount in effect from that date; for these rows
-- it is derived from cash_cents and the price, and re-derived when a better price for that day arrives
-- (manual_positions.rederive), so the dollars you typed stay exact. NULL entry = a plain amount, as before.
-- Contributions on retirement accounts are the Savings page's pre-tax income (savings.py).
ALTER TABLE manual_position_changes ADD COLUMN cash_cents INTEGER;
ALTER TABLE manual_position_changes ADD COLUMN entry TEXT CHECK (entry IS NULL OR entry IN ('contribution', 'opening'));

-- The monthly pre-tax amounts of 0024 are replaced by the contribution events above (user's decision 2026-10-12).
DROP TRIGGER IF EXISTS rev_pretax_ins;
DROP TRIGGER IF EXISTS rev_pretax_upd;
DROP TRIGGER IF EXISTS rev_pretax_del;
DROP TABLE IF EXISTS pretax_contributions;
