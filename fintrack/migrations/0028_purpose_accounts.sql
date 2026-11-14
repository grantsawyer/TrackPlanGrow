-- Purpose accounts (user's request 2026-10-12): money moved out of your bank accounts toward a defined purpose, e.g.
-- money lent to someone for school. A purpose account is a manual account of kind "Money lent" (accounts.type
-- 'receivable', an asset) whose balance is never typed: it is the running total of the transfers linked to it plus
-- your adjustments (purposes.py writes it into manual_valuations, so net worth and its history include it).
--
-- Links: one row per transfer-kind transaction. account_id NULL = you unlinked it (rules then leave it alone).
CREATE TABLE purpose_links (
    transaction_id  INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    account_id      INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    source          TEXT NOT NULL CHECK (source IN ('you', 'rule'))
);

-- Rules: transfers whose bank text (name or merchant) contains `pattern` link to the account. Applied to past
-- transactions and after every sync; a link you set or removed by hand wins.
CREATE TABLE purpose_rules (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    pattern     TEXT NOT NULL CHECK (length(trim(pattern)) > 0),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Adjustments: changes that aren't money moving through your bank, e.g. part of a loan written off (negative) or
-- an opening balance. Not counted on the Savings page (a change in value, like a fee).
CREATE TABLE purpose_adjustments (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date          TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL CHECK (amount_cents <> 0),
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pages show these tables' data, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_purpose_links_ins AFTER INSERT ON purpose_links BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_links_upd AFTER UPDATE ON purpose_links BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_links_del AFTER DELETE ON purpose_links BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_rules_ins AFTER INSERT ON purpose_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_rules_upd AFTER UPDATE ON purpose_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_rules_del AFTER DELETE ON purpose_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_adj_ins AFTER INSERT ON purpose_adjustments BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_adj_upd AFTER UPDATE ON purpose_adjustments BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_purpose_adj_del AFTER DELETE ON purpose_adjustments BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
