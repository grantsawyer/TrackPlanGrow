-- Fees on manual position lines (user's request 2026-10-12): the Add form's types are Contribution / Withdrawal / Fee /
-- Balance. A fee is stored like a withdrawal (entry = 'contribution' with a negative cash_cents or delta_quantity: it
-- reduces the line by that many dollars or shares) with fee = 1, so the Savings page leaves it out: a fee is a cost of
-- the investment, not money you took out. (SQLite can't change the CHECK on `entry`, hence a flag.)
ALTER TABLE manual_position_changes ADD COLUMN fee INTEGER NOT NULL DEFAULT 0 CHECK (fee IN (0, 1));
