-- Custom groups (user's request 2026-10-28): a third Budgets framework whose two spending groups you name
-- (app_settings 'budget_custom1_name' / 'budget_custom2_name'; "Group 1" / "Group 2" until you do). Each spending
-- line remembers its custom group here; NULL until you move it, which means it follows Fixed (group 1) / Flexible
-- (group 2). Income lines ignore it.
ALTER TABLE budget_lines ADD COLUMN custom_group TEXT CHECK (custom_group IN ('custom1', 'custom2'));
