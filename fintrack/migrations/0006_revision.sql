-- A single counter that goes up whenever displayed data changes.
-- Pages embed the revision they were rendered from; ui.js compares it with
-- GET /revision when a page is shown again (Back button / back-forward cache,
-- or returning to a tab) and reloads if the data has changed since.
-- Triggers (rather than app code) guarantee every write path is covered,
-- including scheduled syncs.
CREATE TABLE app_revision (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    n   INTEGER NOT NULL
);
INSERT INTO app_revision (id, n) VALUES (1, 0);

CREATE TRIGGER rev_transactions_ins AFTER INSERT ON transactions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transactions_upd AFTER UPDATE ON transactions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transactions_del AFTER DELETE ON transactions BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

CREATE TRIGGER rev_accounts_ins AFTER INSERT ON accounts BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_accounts_upd AFTER UPDATE ON accounts BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_accounts_del AFTER DELETE ON accounts BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

CREATE TRIGGER rev_items_ins AFTER INSERT ON items BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_items_upd AFTER UPDATE ON items BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_items_del AFTER DELETE ON items BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

CREATE TRIGGER rev_categories_ins AFTER INSERT ON categories BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_categories_upd AFTER UPDATE ON categories BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_categories_del AFTER DELETE ON categories BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

CREATE TRIGGER rev_rules_ins AFTER INSERT ON category_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rules_upd AFTER UPDATE ON category_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_rules_del AFTER DELETE ON category_rules BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

CREATE TRIGGER rev_snapshots_ins AFTER INSERT ON balance_snapshots BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_snapshots_upd AFTER UPDATE ON balance_snapshots BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_snapshots_del AFTER DELETE ON balance_snapshots BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;

CREATE TRIGGER rev_holdings_ins AFTER INSERT ON holdings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_holdings_upd AFTER UPDATE ON holdings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_holdings_del AFTER DELETE ON holdings BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
