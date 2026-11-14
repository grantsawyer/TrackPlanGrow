-- Transfer pairs you match yourself on the Transfers page (user's request 2026-10-26). Matching is recomputed from scratch
-- (transfers.detect_transfers), so a pair you chose has to be stored to survive: these are applied first, then the
-- automatic matching pairs the rest. out_id is the money out (amount > 0), in_id the money in. A transaction is in at
-- most one pair; deleting either transaction deletes the pair. "Not a transfer" on either half deletes it too.

CREATE TABLE transfer_matches (
    out_id      INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    in_id       INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (out_id <> in_id)
);

-- The Transfers page shows these, so writes bump the page revision (see 0006).
CREATE TRIGGER rev_transfer_matches_ins AFTER INSERT ON transfer_matches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transfer_matches_upd AFTER UPDATE ON transfer_matches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_transfer_matches_del AFTER DELETE ON transfer_matches BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
