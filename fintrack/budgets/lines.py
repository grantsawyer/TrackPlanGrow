"""Budget sections, frameworks, your custom groups, and lines: which categories (and tags) are on the budget,
in which section.

The model (sections, amounts, rollover, the views, tags as lines) is explained in fintrack/budgets/__init__.py."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fintrack import categories
from fintrack.money import parse_amount_formula as parse_amount  # amounts or =795/12 (web layer too)


# Section key -> (label, the category kind its lines must have). Display order within a framework.
SECTIONS = {
    "income": ("Income", "income"),
    "fixed": ("Fixed expenses", "expense"),
    "flexible": ("Flexible expenses", "expense"),
    "needs": ("Needs", "expense"),
    "wants": ("Wants", "expense"),
}
# The two ways to group spending (user's request 2026-10-28): key -> (toggle label, its sections in order).
# The last spending section also holds "Not in budget" in the 1-year / 10-year views.
FRAMEWORKS = {
    "fixed": ("Fixed / Flexible", ("income", "fixed", "flexible")),
    "needs": ("Needs / Wants", ("income", "needs", "wants")),
    "custom": ("Custom", ("income",)),          # + your groups (budget_groups), in their order
}
# Which budget_lines column holds a line's place in each framework.
_COLUMNS = {"fixed": "section", "needs": "need_want", "custom": "custom_group"}
GROUP_NAME_LIMIT = 40
MAX_GROUPS = 6           # custom spending groups: each keeps its own colour on the summary bar (Savings has its own)
MIN_GROUPS = 2           # lines you haven't placed go to the first two (fixed -> first, flexible -> second)
# Default needs (a line you haven't moved): these categories and their subcategories, and any category whose key
# ends with one of NEED_ENDINGS (custom subcategories too, e.g. FOOD_AND_DRINK__GROCERIES). The rest are wants.
NEED_CATEGORIES = {"RENT_AND_UTILITIES", "LOAN_PAYMENTS", "MEDICAL", "TRANSPORTATION", "BANK_FEES"}
NEED_ENDINGS = ("GROCERIES", "INSURANCE", "CHILDCARE", "DRUGSTORES", "PHARMACIES_AND_SUPPLEMENTS", "TAX_PAYMENT")
# Suggestion windows offered when you click a line: key -> (label, months back).
SUGGESTIONS = {"prior": ("Prior month", 1), "avg3": ("3-month average", 3), "avg6": ("6-month average", 6)}
# Insurance is a Plaid detailed subcategory of Services; it's a fixed line by default.
SERVICES, INSURANCE = "GENERAL_SERVICES", "GENERAL_SERVICES_INSURANCE"


class BudgetError(ValueError):
    """A budget change was refused; the message is shown to you."""



# ---------------------------------------------------------------- lines (which categories, which section)

def framework(conn: sqlite3.Connection) -> str:
    """The Budgets page's grouping: 'fixed' (Fixed / Flexible) or 'needs' (Needs / Wants)."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'budget_framework'").fetchone()
    return row[0] if row and row[0] in FRAMEWORKS else "fixed"


def set_framework(conn: sqlite3.Connection, key: str) -> None:
    if key not in FRAMEWORKS:
        raise BudgetError("Choose Fixed / Flexible, Needs / Wants, or Custom.")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('budget_framework', ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value WHERE value != excluded.value", (key,))
    conn.commit()


def custom_groups(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Your custom groups, in order: [(key, name), ...] (at least MIN_GROUPS)."""
    return [(r[0], r[1]) for r in conn.execute("SELECT key, name FROM budget_groups ORDER BY position, key")]


def _group_name(conn: sqlite3.Connection, name: str, key: str | None = None) -> str:
    """A cleaned group name: non-empty, at most GROUP_NAME_LIMIT characters, unlike your other groups."""
    name = " ".join(name.split())
    if not name:
        raise BudgetError("Give the group a name.")
    if len(name) > GROUP_NAME_LIMIT:
        raise BudgetError(f"Keep group names to {GROUP_NAME_LIMIT} characters.")
    if any(other.lower() == name.lower() and k != key for k, other in custom_groups(conn)):
        raise BudgetError(f"There's already a group called {name}.")
    return name


def add_group(conn: sqlite3.Connection, name: str) -> str:
    """Add a custom spending group at the end (user's request 2026-10-28). Returns its key."""
    name = _group_name(conn, name)
    groups = custom_groups(conn)
    if len(groups) >= MAX_GROUPS:
        raise BudgetError(f"Up to {MAX_GROUPS} groups.")
    # The next key ('custom3', ...). A removed group's key can come back: its lines were already let go (SET NULL).
    used = conn.execute("SELECT COALESCE(MAX(CAST(substr(key, 7) AS INTEGER)), 0) FROM budget_groups").fetchone()[0]
    key = f"custom{max(used, len(groups)) + 1}"
    conn.execute("INSERT INTO budget_groups (key, name, position)"
                 " VALUES (?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM budget_groups))", (key, name))
    conn.commit()
    return key


def rename_group(conn: sqlite3.Connection, key: str, name: str) -> None:
    """Rename a custom group (typed in its section header). An unchanged name writes nothing."""
    if key not in dict(custom_groups(conn)):
        raise BudgetError("That group no longer exists.")
    conn.execute("UPDATE budget_groups SET name = ? WHERE key = ? AND name != ?",
                 (n := _group_name(conn, name, key), key, n))
    conn.commit()


def remove_group(conn: sqlite3.Connection, key: str) -> None:
    """Remove an empty custom group (no lines in it), keeping at least MIN_GROUPS."""
    groups = custom_groups(conn)
    if key not in dict(groups):
        raise BudgetError("That group no longer exists.")
    if len(groups) <= MIN_GROUPS:
        raise BudgetError(f"Keep at least {MIN_GROUPS} groups.")
    if key in (set(line_sections(conn, framework_key="custom").values()) | set(split_sections(conn, "custom").values())
               | {line.section for line in tag_lines(conn, "custom").values()}):
        raise BudgetError("Move the group's lines to another group first.")
    conn.execute("DELETE FROM budget_groups WHERE key = ?", (key,))
    conn.commit()


def active_sections(conn: sqlite3.Connection, key: str | None = None) -> dict[str, tuple[str, str]]:
    """The sections the page shows, in order: {key: (label, kind)} for the framework (default: the chosen one);
    Custom's are your groups."""
    key = key or framework(conn)
    sections = {k: SECTIONS[k] for k in FRAMEWORKS[key][1]}
    if key == "custom":
        sections.update({k: (name, "expense") for k, name in custom_groups(conn)})
    return sections


def section_kind(section: str) -> str:
    """The category kind a section's lines have: income for Income, else expense."""
    return "income" if section == "income" else "expense"


def default_need_want(category: str, parent: str | None) -> str:
    """'needs' or 'wants' for a spending line you haven't moved (see NEED_CATEGORIES)."""
    if category in NEED_CATEGORIES or parent in NEED_CATEGORIES or category.endswith(NEED_ENDINGS):
        return "needs"
    return "wants"


def line_sections(conn: sqlite3.Connection, split: bool = False, framework_key: str | None = None) -> dict[str, str]:
    """{category: section} for every line, in the chosen framework (or `framework_key`'s): a spending
    line's section is fixed / flexible, or needs / wants. A line whose category kind no longer fits its
    section (e.g. you changed Travel to a transfer) is left out until it fits again.
    A split category is never a line itself (user's request 2026-10-06): its subcategories
    are. Its row remembers the section new subcategories join; `split=True` returns those
    rows instead (see split_sections)."""
    chosen = framework_key or framework(conn)
    groups = [k for k, _ in custom_groups(conn)] if chosen == "custom" else []
    result = {}
    for r in conn.execute(
            """SELECT l.category_key, l.section, l.need_want, l.custom_group, c.parent_key FROM budget_lines l
               JOIN categories c ON c.key = l.category_key
               WHERE c.kind = CASE l.section WHEN 'income' THEN 'income' ELSE 'expense' END
                 AND c.is_split = ?""",
            (1 if split else 0,)):
        section = r["section"]
        if chosen == "needs" and section != "income":
            section = r["need_want"] or default_need_want(r["category_key"], r["parent_key"])
        elif chosen == "custom" and section != "income":
            section = r["custom_group"] or groups[0 if section == "fixed" else 1]
        result[r["category_key"]] = section
    return result


def split_sections(conn: sqlite3.Connection, framework_key: str | None = None) -> dict[str, str]:
    """{split category: section}. Spending still filed under a split category itself (by hand,
    by a rule, or a Plaid code outside its subcategories) shows in that section as a
    non-budgeted item (user's request 2026-10-06), not under "Not in budget". It can't
    have a budget: split categories are budgeted through their subcategories."""
    return line_sections(conn, split=True, framework_key=framework_key)


def _check_section(conn: sqlite3.Connection, section: str) -> str:
    if section not in SECTIONS and section not in dict(custom_groups(conn)):
        raise BudgetError("Choose a section.")
    return section_kind(section)


def _framework_of(section: str) -> str:
    """The framework a spending section belongs to ('fixed' for income too)."""
    if section.startswith("custom"):
        return "custom"
    return next((key for key, (_, keys) in FRAMEWORKS.items() if section in keys[1:]), "fixed")


def _label(conn: sqlite3.Connection, section: str) -> str:
    return SECTIONS[section][0] if section in SECTIONS else dict(custom_groups(conn)).get(section, section)


def add_line(conn: sqlite3.Connection, section: str, category: str = "", new_label: str = "") -> str:
    """Add a category to a section: an existing one (`category`) or a new custom category
    named `new_label` (created with the section's kind). Returns the category key. Added to Needs / Wants or a
    custom group, a line is Flexible in Fixed / Flexible; its other places take their defaults. `category` can be a
    tag's line key ('tag:3'; user's request 2026-11-02)."""
    if category.startswith(TAG_PREFIX):
        return _add_tag_line(conn, section, category)
    kind = _check_section(conn, section)
    if new_label.strip():
        try:
            category = categories.add_category(conn, new_label, kind)
        except ValueError as exc:
            raise BudgetError(str(exc)) from None
    row = conn.execute("SELECT kind, is_split FROM categories WHERE key = ?", (category,)).fetchone()
    if row is None:
        raise BudgetError("Choose a category to add, or type a name for a new one.")
    if row["is_split"]:
        raise BudgetError("That category is split: add its subcategories instead.")
    if row["kind"] != kind:
        raise BudgetError(f"Only {kind} categories can go in {_label(conn, section)}.")
    current = line_sections(conn).get(category)
    if current:
        raise BudgetError(f"That category is already in {_label(conn, current)}.")
    column = _COLUMNS[_framework_of(section)]
    stored = section if column == "section" else "flexible"
    need_want, custom_group = (section if column == "need_want" else None), (section if column == "custom_group" else None)
    # A leftover line whose kind stopped fitting its section is simply re-pointed.
    conn.execute(
        "INSERT INTO budget_lines (category_key, section, need_want, custom_group) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (category_key) DO UPDATE SET section = excluded.section,"
        " need_want = COALESCE(excluded.need_want, need_want),"
        " custom_group = COALESCE(excluded.custom_group, custom_group)",
        (category, stored, need_want, custom_group),
    )
    conn.commit()
    return category


def move_line(conn: sqlite3.Connection, category: str, section: str) -> None:
    """Move a spending line to another section of its framework (Fixed ↔ Flexible, Needs ↔ Wants, or
    between your groups); its budget amounts and its places in the other frameworks stay."""
    key = _framework_of(section)
    pair = list(active_sections(conn, key))[1:]
    is_tag = category.startswith(TAG_PREFIX)
    current = tag_lines(conn, key).get(category) if is_tag else None
    place = current.section if current else line_sections(conn, framework_key=key).get(category)
    if _check_section(conn, section) != "expense" or place not in pair:
        raise BudgetError("Only spending lines can move, between the two spending sections.")
    if is_tag:
        conn.execute(f"UPDATE tag_budget_lines SET {_COLUMNS[key]} = ? WHERE tag_id = ? AND category_key = ?",
                     (section, current.tag_id, current.category or ""))
    else:
        conn.execute(f"UPDATE budget_lines SET {_COLUMNS[key]} = ? WHERE category_key = ?", (section, category))
    conn.commit()


def remove_line(conn: sqlite3.Connection, category: str) -> None:
    """Take a category off the budget. Its amounts stay stored (adding it back restores them);
    its actuals show under its parent's line, or "Not in budget". A tag's spending goes back to its categories."""
    if category.startswith(TAG_PREFIX):
        tag_id, tag_category = parse_tag_key(category)
        conn.execute("DELETE FROM tag_budget_lines WHERE tag_id = ? AND category_key = ?", (tag_id, tag_category or ""))
    else:
        conn.execute("DELETE FROM budget_lines WHERE category_key = ?", (category,))
    conn.commit()


def add_insurance_line(conn: sqlite3.Connection) -> bool:
    """Default (user's request): Insurance is a fixed expense. Insurance is a Plaid detailed
    subcategory of Services, so this splits Services (once, when migration 0015 is applied)
    and adds Insurance as a Fixed line. The rest of Services stays a Flexible line.
    Returns False if Services isn't a spending category any more (nothing done)."""
    row = conn.execute("SELECT kind, is_split FROM categories WHERE key = ?", (SERVICES,)).fetchone()
    if row is None or row["kind"] != "expense":
        return False
    if not row["is_split"]:
        categories.set_split(conn, SERVICES, True)
    with conn:
        categories.ensure_subcategory(conn, SERVICES, INSURANCE)   # even before any insurance payment
        # The split put every Services subcategory in Services' section; Insurance goes to Fixed.
        conn.execute("INSERT INTO budget_lines (category_key, section) VALUES (?, 'fixed')"
                     " ON CONFLICT (category_key) DO UPDATE SET section = 'fixed'", (INSURANCE,))
    return True


# ---------------------------------------------------------------- tags as lines

TAG_PREFIX = "tag:"


def tag_line_key(tag_id: int, category: str | None = None) -> str:
    return f"{TAG_PREFIX}{tag_id}:{category}" if category else f"{TAG_PREFIX}{tag_id}"


def parse_tag_key(key: str) -> tuple[int, str | None]:
    """'tag:3' -> (3, None); 'tag:3:TRAVEL' -> (3, 'TRAVEL')."""
    _, tag_id, *category = key.split(":", 2)
    try:
        return int(tag_id), (category[0] if category else None)
    except ValueError:
        raise BudgetError("That tag no longer exists.") from None


@dataclass
class TagBudgetLine:
    key: str
    tag_id: int
    category: str | None        # None: the tag's own line
    label: str
    section: str
    total_cents: int | None     # the tag's line: the tag's total (its own, else the sum of its category budgets); a
                                # category line: that category budget
    allocated_cents: int        # allocated to any month: the tag's, across its lines; a category line's, its own
    amounts: dict[str, int]     # {month: allocated to this line}

    @property
    def unfinished(self) -> bool:
        """Still to be allocated (or no total yet), so it stays on the page in months with nothing on it."""
        return self.total_cents is None or self.allocated_cents < self.total_cents


def tag_lines(conn: sqlite3.Connection, framework_key: str | None = None) -> dict[str, TagBudgetLine]:
    """{line key: line} for every tag on the budget, in the chosen framework's sections."""
    chosen = framework_key or framework(conn)
    groups = [k for k, _ in custom_groups(conn)] if chosen == "custom" else []
    amounts: dict[str, dict[str, int]] = {}
    allocated: dict[int, int] = {}
    by_key: dict[str, int] = {}
    for r in conn.execute("SELECT tag_id, category_key, month, amount_cents FROM tag_budget_allocations"):
        key = tag_line_key(r["tag_id"], r["category_key"])
        amounts.setdefault(key, {})[r["month"]] = r["amount_cents"]
        allocated[r["tag_id"]] = allocated.get(r["tag_id"], 0) + r["amount_cents"]
        by_key[key] = by_key.get(key, 0) + r["amount_cents"]
    by_tag: dict[int, list] = {}
    for r in conn.execute("""SELECT b.tag_id, b.category_key, b.amount_cents, c.label FROM tag_category_budgets b
                             JOIN categories c ON c.key = b.category_key ORDER BY lower(c.label)"""):
        by_tag.setdefault(r["tag_id"], []).append(r)
    result = {}
    for r in conn.execute("""SELECT l.*, t.name, t.budget_cents FROM tag_budget_lines l JOIN tags t ON t.id = l.tag_id
                             ORDER BY t.id, l.category_key"""):
        section = r["section"]
        if chosen == "needs":
            section = r["need_want"] or "wants"
        elif chosen == "custom":
            section = r["custom_group"] or groups[0 if section == "fixed" else 1]
        tag_id, own = r["tag_id"], by_tag.get(r["tag_id"], [])
        if not r["category_key"]:                     # the tag's own line
            total = r["budget_cents"] if r["budget_cents"] is not None else (sum(b["amount_cents"] for b in own) if own else None)
            key = tag_line_key(tag_id)
            result[key] = TagBudgetLine(key, tag_id, None, r["name"], section, total, allocated.get(tag_id, 0),
                                        amounts.get(key, {}))
            continue
        budget = next((b for b in own if b["category_key"] == r["category_key"]), None)
        if budget is None:                            # its category budget is gone from the tag's card
            continue
        key = tag_line_key(tag_id, r["category_key"])
        result[key] = TagBudgetLine(key, tag_id, r["category_key"], f"{r['name']} › {budget['label']}", section,
                                    budget["amount_cents"], by_key.get(key, 0), amounts.get(key, {}))
    return result


def _add_tag_line(conn: sqlite3.Connection, section: str, key: str) -> str:
    """Add a tag's line ('tag:<id>') or one of its category budgets' ('tag:<id>:<category>'), each on its own."""
    tag_id, category = parse_tag_key(key)
    if _check_section(conn, section) != "expense":
        raise BudgetError("Tags go in a spending section.")
    if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
        raise BudgetError("That tag no longer exists.")
    if category and not conn.execute("SELECT 1 FROM tag_category_budgets WHERE tag_id = ? AND category_key = ?",
                                     (tag_id, category)).fetchone():
        raise BudgetError("That tag has no budget for that category (set one on its Tags card).")
    current = tag_lines(conn).get(tag_line_key(tag_id, category))
    if current:
        raise BudgetError(f"That's already in {_label(conn, current.section)}.")
    column = _COLUMNS[_framework_of(section)]
    conn.execute("INSERT INTO tag_budget_lines (tag_id, category_key, section, need_want, custom_group) VALUES (?, ?, ?, ?, ?)",
                 (tag_id, category or "", section if column == "section" else "flexible",
                  section if column == "need_want" else None, section if column == "custom_group" else None))
    conn.commit()
    return tag_line_key(tag_id, category)


def set_tag_allocation(conn: sqlite3.Connection, key: str, month: str, amount_text: str) -> None:
    """What a tag's line gets in `month`, that month only; blank = nothing. You choose the amount: going over the total is
    allowed, and shows as a negative amount left to allocate."""
    tag_id, category = parse_tag_key(key)
    if key not in tag_lines(conn):
        raise BudgetError("That tag isn't on the budget.")
    try:
        amount = parse_amount(amount_text) if amount_text.strip() else None
    except ValueError as exc:
        raise BudgetError(str(exc)) from None
    with conn:
        conn.execute("DELETE FROM tag_budget_allocations WHERE tag_id = ? AND category_key IS ? AND month = ?",
                     (tag_id, category, month))
        if amount is not None:
            conn.execute("INSERT INTO tag_budget_allocations (tag_id, category_key, month, amount_cents) VALUES (?, ?, ?, ?)",
                         (tag_id, category, month, amount))
