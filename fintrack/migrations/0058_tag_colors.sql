-- Colors you choose for tags (user's request 2026-11-02): on the card's swatch, share bars and calendar bars. NULL = the
-- automatic color by creation order (web/tags.tag_slots). Stored as '#rrggbb', so one in use survives leaving the list.
ALTER TABLE tags ADD COLUMN color TEXT CHECK (color IS NULL OR color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]');

-- Your own colors, offered on every tag beside the predefined ones (tags.COLOR_PALETTE).
CREATE TABLE tag_custom_colors (
    color  TEXT PRIMARY KEY CHECK (color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]')
);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_tag_custom_colors_ins AFTER INSERT ON tag_custom_colors BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_custom_colors_upd AFTER UPDATE ON tag_custom_colors BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_tag_custom_colors_del AFTER DELETE ON tag_custom_colors BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
