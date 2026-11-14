-- Budget lines for split categories are their subcategories (user's request 2026-10-06):
-- "Rent & utilities" is replaced by Rent, Telephone, Gas & electricity, ... The split
-- parent's own row stays in budget_lines but isn't shown (budgets.line_sections); it only
-- remembers the section new subcategories join (categories.ensure_subcategory).
-- Subcategories that already have a line (e.g. Insurance in Fixed) keep it.
INSERT INTO budget_lines (category_key, section)
SELECT c.key, l.section
FROM categories c
JOIN categories p ON p.key = c.parent_key
JOIN budget_lines l ON l.category_key = p.key
WHERE p.is_split = 1
ON CONFLICT (category_key) DO NOTHING;
