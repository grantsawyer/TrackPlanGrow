-- Your own labels on the Tags calendar (user's request 2026-11-02), like a birthday or an anniversary: one day, or that
-- month and day every year from `date` on. Holidays aren't stored: calendar_labels.py works them out.
CREATE TABLE calendar_labels (
    id      INTEGER PRIMARY KEY,
    date    TEXT NOT NULL,                                       -- 'YYYY-MM-DD'
    label   TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 40),
    yearly  INTEGER NOT NULL DEFAULT 0 CHECK (yearly IN (0, 1))
);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_calendar_labels_ins AFTER INSERT ON calendar_labels BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_calendar_labels_upd AFTER UPDATE ON calendar_labels BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_calendar_labels_del AFTER DELETE ON calendar_labels BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
