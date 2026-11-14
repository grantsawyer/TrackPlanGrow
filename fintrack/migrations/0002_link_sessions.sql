-- Audit log of every Plaid Link session the app starts.
-- For Production "create" sessions this is the record of explicit consent:
-- when you confirmed, and how many Items were already used at that moment.
-- Rows are never deleted.
CREATE TABLE link_sessions (
    id                    INTEGER PRIMARY KEY,
    purpose               TEXT NOT NULL CHECK (purpose IN ('create', 'update')),
    plaid_env             TEXT NOT NULL CHECK (plaid_env IN ('sandbox', 'production')),
    kind                  TEXT,                          -- 'banking' or 'investments' (create only)
    item_id               INTEGER REFERENCES items(id),  -- target Item for update; resulting Item for create
    consented_at          TEXT,                          -- Production create only
    items_used_at_consent INTEGER,                       -- Production create only
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at          TEXT,                          -- Link finished successfully
    -- A Production create session without recorded consent must be impossible.
    CHECK (NOT (purpose = 'create' AND plaid_env = 'production' AND consented_at IS NULL))
);

CREATE TRIGGER link_sessions_never_deleted
BEFORE DELETE ON link_sessions
BEGIN
    SELECT RAISE(ABORT, 'link_sessions is an audit log; rows cannot be deleted');
END;
