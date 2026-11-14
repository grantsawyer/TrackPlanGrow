-- category_rules hasn't been read or written since the rules redesign (0054), but its rows still pointed at categories, so a
-- category an old rule named couldn't be deleted (a foreign key failure; found 2026-11-02). Rules live in rules / rule_conditions /
-- rule_actions. The table stays, empty, because transactions.rule_id's foreign key names it.
UPDATE transactions SET rule_id = NULL WHERE rule_id IS NOT NULL;
DELETE FROM category_rules;
