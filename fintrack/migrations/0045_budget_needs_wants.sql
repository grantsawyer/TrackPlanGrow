-- Needs / wants / savings (user's request 2026-10-28): the Budgets page can group spending lines as Needs and
-- Wants instead of Fixed and Flexible. Each spending line keeps both: `section` (fixed / flexible, as before) and
-- `need_want`, NULL until you move the line, which means the default (budgets.default_need_want: essentials such
-- as housing, loans, medical, transportation and groceries are needs, the rest wants). Income lines ignore it.
ALTER TABLE budget_lines ADD COLUMN need_want TEXT CHECK (need_want IN ('needs', 'wants'));

-- Which grouping the Budgets page shows: 'fixed' (Fixed / Flexible) or 'needs' (Needs / Wants).
INSERT INTO app_settings (key, value) VALUES ('budget_framework', 'fixed');
