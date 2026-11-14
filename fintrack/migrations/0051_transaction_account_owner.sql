-- Who used the card (user's request 2026-10-29): Plaid's `account_owner` on a transaction, for a sub-account, "in cases
-- where a single account is linked to multiple cards, each with its own card number and card holder name" (e.g. an
-- authorized user's card). Plaid says it "is not typically populated" (checked in Plaid's docs 2026-10-29), so this is
-- stored from now on to see whether a bank fills it. Stored as sent (a name); a value once known is kept when a later
-- update leaves it out. Never logged.
ALTER TABLE transactions ADD COLUMN account_owner TEXT;
