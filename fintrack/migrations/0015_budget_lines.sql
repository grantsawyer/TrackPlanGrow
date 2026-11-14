-- Budget sections (user's request 2026-10-06): the Budgets page is organized as
-- Income, Fixed expenses, and Flexible expenses. Each section holds budget lines;
-- a line is one category, and its actuals are that category's transactions plus any
-- subcategory of it that isn't a line of its own (budgets.py).
--
-- Which section a category is in applies to every month (it isn't dated); the
-- amounts stay in `budgets`, effective from a month until changed, as before.
CREATE TABLE budget_lines (
    category_key  TEXT PRIMARY KEY REFERENCES categories(key) ON UPDATE CASCADE ON DELETE CASCADE,
    section       TEXT NOT NULL CHECK (section IN ('income', 'fixed', 'flexible'))
);

-- Default lines: every top-level income and spending category. Rent & utilities (incl.
-- phone) and loan payments are fixed; everything else is flexible. Insurance gets its
-- own fixed line after migrating (cli._migrate -> budgets.add_insurance_line), because
-- that needs Services split into Plaid's subcategories.
INSERT INTO budget_lines (category_key, section)
SELECT key,
       CASE WHEN kind = 'income' THEN 'income'
            WHEN key IN ('RENT_AND_UTILITIES', 'LOAN_PAYMENTS') THEN 'fixed'
            ELSE 'flexible' END
FROM categories
WHERE parent_key IS NULL AND kind IN ('income', 'expense');

-- Subcategories that already have a budget keep it, as a line in their parent's section.
INSERT INTO budget_lines (category_key, section)
SELECT DISTINCT c.key, p.section
FROM budgets b
JOIN categories c ON c.key = b.category_key
JOIN budget_lines p ON p.category_key = c.parent_key
WHERE c.key NOT IN (SELECT category_key FROM budget_lines);

-- Stale-page protection (see migration 0006).
CREATE TRIGGER rev_budget_lines_ins AFTER INSERT ON budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_lines_upd AFTER UPDATE ON budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER rev_budget_lines_del AFTER DELETE ON budget_lines BEGIN UPDATE app_revision SET n = n + 1 WHERE id = 1; END;
