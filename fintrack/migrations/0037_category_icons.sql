-- Category icons and transfer logos (user's request 2026-10-20).
--
-- A transaction without a logo shows its category's icon instead of a letter. `icon` is the name of an icon from
-- fintrack's own library (category_icons.ICONS); NULL = automatic (category_icons.resolve: built-in default, a match
-- on the category's name, its parent's icon, else one for its kind). Both tables already bump the page revision (0006).
ALTER TABLE categories ADD COLUMN icon TEXT;

-- A transfer's logo is the account on the other side when fintrack knows it (logos.transfer_logos), else the bank
-- Plaid names as its counterparty: a `financial_institution` counterparty's logo (on plaid-counterparty-logos), kept
-- apart from logo_url because it is used for transfers only (a purchase never shows a bank's logo).
ALTER TABLE transactions ADD COLUMN transfer_logo_url TEXT;
