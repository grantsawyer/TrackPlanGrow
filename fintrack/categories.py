"""Categories, categorization rules, and per-transaction recategorizing.

A transaction's category is decided in this order (see the
`transactions_effective` view):

    1. your manual choice      (transactions.user_category)
    2. the first matching rule that sets one (transactions.rule_category; fintrack/rules.py)
    3. Plaid's category        (transactions.plaid_category_key)

Each category has a kind (expense / income / transfer) that decides whether
it counts as spending, income, or neither.

Subcategories (optional, per category): "splitting" a Plaid category makes its
transactions use Plaid's DETAILED subcategory instead (Rent & utilities -> Rent,
Gas & electricity, Internet & cable, ...). Subcategories are ordinary categories with
`parent_key` set; their key is Plaid's detailed code. On Budgets, a split category's
lines are its subcategories (fintrack/budgets).
"""

from __future__ import annotations

import json
import re
import sqlite3

KINDS = ("expense", "income", "transfer")
# The order categories are listed and offered in, by what they count as (user's request 2026-10-25): spending,
# transfers, income.
KIND_ORDER = ("expense", "transfer", "income")
# What each is called on screen (the Categories page's sections and Counts as, the Transactions category filter's
# groups); "transfer" was "Not counted" until the user's request 2026-10-30.
KIND_LABELS = {"expense": "Spending", "transfer": "Transfers", "income": "Income"}

# Plaid detailed categories split out from their primary (see migration 0005).
DETAILED_SPLITS = {
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": "CREDIT_CARD_PAYMENT",
    "TRANSFER_OUT_WITHDRAWAL": "CASH_WITHDRAWAL",
}


# Categories that can't be split: fixed splits and catch-alls.
NOT_SPLITTABLE = {"CREDIT_CARD_PAYMENT", "CASH_WITHDRAWAL", "UNCATEGORIZED", "OTHER"}


def plaid_key(primary: str | None, detailed: str | None, split: frozenset[str] | set[str] = frozenset()) -> str:
    """Map Plaid's personal_finance_category to our category key. For a split primary,
    the detailed subcategory's code (e.g. RENT_AND_UTILITIES_RENT)."""
    if detailed in DETAILED_SPLITS:
        return DETAILED_SPLITS[detailed]
    if primary in split and detailed and detailed.startswith(primary + "_"):
        return detailed
    if detailed in split:                # a Plaid subcategory you added on its own (add_plaid_subcategory)
        return detailed
    if primary == "OTHER":              # Plaid couldn't classify it: Uncategorized, not Other (user's rule 2026-10-25;
        return "UNCATEGORIZED"          # transactions stored as Other before then stay there, see sync._upsert_transaction)
    return primary or "UNCATEGORIZED"


def subcategory_label(primary: str, detailed: str) -> str:
    """'RENT_AND_UTILITIES_GAS_AND_ELECTRICITY' -> 'Gas & electricity'."""
    words = detailed[len(primary) + 1:].lower().split("_") if detailed.startswith(primary + "_") else [detailed.lower()]
    text = " ".join("&" if w == "and" else w for w in words)
    return text[:1].upper() + text[1:]


def split_set(conn: sqlite3.Connection) -> frozenset[str]:
    """What plaid_key sends to a subcategory: categories split whole, and Plaid subcategories added one at a time."""
    return frozenset(r[0] for r in conn.execute("SELECT key FROM categories WHERE is_split = 1 OR plaid_added = 1"))


def _same_name(label: str) -> str:
    """A name for comparing: case aside, and "and" or "&" alike."""
    return " ".join(label.casefold().replace("&", " and ").split())


def plaid_subcategory_options(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """{category: the Plaid subcategories to offer, largest first}: detailed codes seen on your transactions under it that aren't
    categories now (user's requests 2026-11-02). One you deleted is offered again; one a subcategory of yours already has the
    name of isn't. Not fixed splits."""
    rows = conn.execute("SELECT key, label, parent_key FROM categories").fetchall()
    known = {r[0] for r in rows}
    names_under: dict[str, set[str]] = {}
    for _, label, parent in rows:
        if parent:
            names_under.setdefault(parent, set()).add(_same_name(label))
    found: dict[str, list[dict]] = {}
    for primary, detailed, n in conn.execute(
            """SELECT plaid_category_primary, plaid_category_detailed, COUNT(*) FROM transactions
               WHERE substr(plaid_category_detailed, 1, length(plaid_category_primary) + 1) = plaid_category_primary || '_'
               GROUP BY 1, 2 ORDER BY 3 DESC, 2"""):
        label = subcategory_label(primary, detailed)
        if (detailed not in known and detailed not in DETAILED_SPLITS and primary not in NOT_SPLITTABLE
                and _same_name(label) not in names_under.get(primary, set())):
            found.setdefault(primary, []).append({"code": detailed, "label": label, "count": n})
    return found


def add_plaid_subcategory(conn: sqlite3.Connection, primary: str, detailed: str) -> None:
    """Add one Plaid subcategory: its row, its transactions that are on Plaid's category, and later syncs. Your choices and rules
    still win, as they read above Plaid's category. One you'd deleted: its transactions come back from where you sent them."""
    if not any(o["code"] == detailed for o in plaid_subcategory_options(conn).get(primary, [])):
        raise ValueError("That Plaid subcategory isn't available.")
    sent_to = remaps(conn).get(detailed)
    with conn:
        conn.execute("DELETE FROM category_remaps WHERE from_key = ?", (detailed,))
        ensure_subcategory(conn, primary, detailed)
        conn.execute("UPDATE categories SET plaid_added = 1 WHERE key = ?", (detailed,))
        conn.execute("""UPDATE transactions SET plaid_category_key = ?
                        WHERE plaid_category_primary = ? AND plaid_category_detailed = ? AND plaid_category_key IN (?, ?)""",
                     (detailed, primary, detailed, primary, sent_to or primary))


def ensure_subcategory(conn: sqlite3.Connection, primary: str, detailed: str) -> None:
    """Create the subcategory row the first time a detailed code shows up (kind and order
    from the parent). Call inside the caller's transaction. A subcategory you deleted isn't re-created
    (category_remaps)."""
    if conn.execute("SELECT 1 FROM category_remaps WHERE from_key = ?", (detailed,)).fetchone():
        return
    conn.execute(
        """INSERT INTO categories (key, label, kind, is_custom, sort_order, parent_key)
           SELECT ?, ?, p.kind, 0, p.sort_order, p.key FROM categories p WHERE p.key = ?
           ON CONFLICT (key) DO NOTHING""",
        (detailed, subcategory_label(primary, detailed), primary),
    )
    # A split category's budget lines are its subcategories: a new one joins the parent's
    # budget section (Budgets page; see migration 0016).
    conn.execute(
        """INSERT INTO budget_lines (category_key, section)
           SELECT ?, section FROM budget_lines WHERE category_key = ?
           ON CONFLICT (category_key) DO NOTHING""",
        (detailed, primary),
    )


def set_split(conn: sqlite3.Connection, key: str, on: bool) -> None:
    """Split a category into Plaid's detailed subcategories, or merge them back.

    On: creates a subcategory for each detailed code seen under it and moves those
    transactions' Plaid category there. Off: moves them back to the parent and removes
    subcategories nothing else uses (kept if a budget, rule, recurring item, or your own
    choice on a transaction points at them). Your choices and rules always win, as before.
    """
    row = conn.execute("SELECT * FROM categories WHERE key = ?", (key,)).fetchone()
    if row is None or row["parent_key"] or row["is_custom"] or key in NOT_SPLITTABLE:
        raise ValueError("That category can't be split.")
    with conn:
        conn.execute("UPDATE categories SET is_split = ? WHERE key = ?", (int(on), key))
        if not on:
            conn.execute("UPDATE categories SET plaid_added = 0 WHERE parent_key = ?", (key,))
        if on:
            detailed = [r[0] for r in conn.execute(
                "SELECT DISTINCT plaid_category_detailed FROM transactions"
                " WHERE plaid_category_primary = ?"
                "   AND substr(plaid_category_detailed, 1, length(?) + 1) = ? || '_'",   # starts with KEY_
                (key, key, key)) if r[0] not in DETAILED_SPLITS and r[0] not in remaps(conn)]
            for d in detailed:
                ensure_subcategory(conn, key, d)
            conn.execute(
                f"""UPDATE transactions SET plaid_category_key = plaid_category_detailed
                    WHERE plaid_category_primary = ? AND plaid_category_key = ?
                      AND plaid_category_detailed IN ({','.join('?' * len(detailed)) or 'NULL'})""",
                [key, key, *detailed])
        else:
            subs = [r[0] for r in conn.execute("SELECT key FROM categories WHERE parent_key = ?", (key,))]
            if subs:
                marks = ",".join("?" * len(subs))
                conn.execute(f"UPDATE transactions SET plaid_category_key = ? WHERE plaid_category_key IN ({marks})",
                             [key, *subs])
                conn.execute(
                    f"""DELETE FROM categories WHERE key IN ({marks})
                        AND key NOT IN (SELECT category_key FROM budgets)
                        AND key NOT IN (SELECT value FROM rule_actions WHERE kind = 'set_category')
                        AND key NOT IN (SELECT category_key FROM recurring_items WHERE category_key IS NOT NULL)
                        AND key NOT IN (SELECT user_category FROM transactions WHERE user_category IS NOT NULL)
                        AND key NOT IN (SELECT user_category FROM investment_transactions WHERE user_category IS NOT NULL)""",
                    subs)


def remaps(conn: sqlite3.Connection) -> dict[str, str]:
    """{deleted category key: the category its transactions go to} (migration 0034): applied to Plaid's mapping at
    every sync, after plaid_key."""
    return dict(conn.execute("SELECT from_key, to_key FROM category_remaps").fetchall())


def parent_map(conn: sqlite3.Connection) -> dict[str, str]:
    """{subcategory key: parent key}."""
    return {r[0]: r[1] for r in conn.execute("SELECT key, parent_key FROM categories WHERE parent_key IS NOT NULL")}


# ---------------------------------------------------------------- categories

def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All categories grouped by what they count as (KIND_ORDER: spending, transfers, income; 2026-10-25), then
    alphabetically, each subcategory right after its parent, also alphabetically (`parent_label` set on them). Every
    category list and drop-down uses this order (user's requests 2026-10-23/25; was `sort_order`)."""
    return conn.execute(
        """SELECT c.*, p.label AS parent_label, COALESCE(p.kind, c.kind) AS group_kind
           FROM categories c LEFT JOIN categories p ON p.key = c.parent_key
           ORDER BY CASE COALESCE(p.kind, c.kind) WHEN 'expense' THEN 0 WHEN 'transfer' THEN 1 ELSE 2 END,
                    lower(COALESCE(p.label, c.label)), COALESCE(p.key, c.key),
                    c.parent_key IS NOT NULL, lower(c.label), c.key"""
    ).fetchall()


def category_exists(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("SELECT 1 FROM categories WHERE key = ?", (key,)).fetchone() is not None


def add_category(conn: sqlite3.Connection, label: str, kind: str) -> str:
    """Create a custom category; returns its key (e.g. 'Kids activities' -> 'KIDS_ACTIVITIES')."""
    label = label.strip()
    if not label:
        raise ValueError("Category name can't be empty.")
    if kind not in KINDS:
        raise ValueError(f"Kind must be one of {KINDS}.")
    if conn.execute("SELECT 1 FROM categories WHERE lower(label) = lower(?) AND parent_key IS NULL", (label,)).fetchone():
        raise ValueError(f"A category called {label!r} already exists.")
    # a fresh key, never one taken or a deleted category's (whose Plaid transactions go elsewhere, category_remaps)
    key = _new_key(conn, label, None)
    conn.execute(
        "INSERT INTO categories (key, label, kind, is_custom, sort_order) VALUES (?, ?, ?, 1, 50)",
        (key, label, kind),
    )
    conn.commit()
    return key


# The categories Plaid's data maps to, as first installed (migration 0005): {key: (label, group, order)}. Left out: the
# two the app relies on (PROTECTED, never deleted) and OTHER, which plaid_key sends to Uncategorized (user's rule
# 2026-10-25), so it would never receive anything.
PLAID_CATEGORIES = {
    "INCOME": ("Income", "income", 1),
    "FOOD_AND_DRINK": ("Food & drink", "expense", 10),
    "GENERAL_MERCHANDISE": ("Shopping", "expense", 11),
    "RENT_AND_UTILITIES": ("Rent & utilities", "expense", 12),
    "TRANSPORTATION": ("Transportation", "expense", 13),
    "TRAVEL": ("Travel", "expense", 14),
    "ENTERTAINMENT": ("Entertainment", "expense", 15),
    "PERSONAL_CARE": ("Personal care", "expense", 16),
    "MEDICAL": ("Medical", "expense", 17),
    "HOME_IMPROVEMENT": ("Home improvement", "expense", 18),
    "GENERAL_SERVICES": ("Services", "expense", 19),
    "GOVERNMENT_AND_NON_PROFIT": ("Government & non-profit", "expense", 20),
    "BANK_FEES": ("Bank fees", "expense", 21),
    "LOAN_PAYMENTS": ("Loan payments", "expense", 22),
    "CASH_WITHDRAWAL": ("Cash & ATM", "expense", 23),
    "TRANSFER_IN": ("Transfer in", "transfer", 31),
    "TRANSFER_OUT": ("Transfer out", "transfer", 32),
    "LOAN_DISBURSEMENTS": ("Loan received", "transfer", 33),
}


def plaid_category_options(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """{group: Plaid's categories to offer, largest first} for the Add category card (user's request 2026-11-02): the ones
    that aren't categories now, whatever you've since moved them to. One a category of yours has the name of isn't offered."""
    rows = conn.execute("SELECT key, label, parent_key FROM categories").fetchall()
    known = {r[0] for r in rows}
    names = {_same_name(r[1]) for r in rows if not r[2]}
    counts: dict[str, int] = {}
    for primary, detailed, n in conn.execute(
            "SELECT plaid_category_primary, plaid_category_detailed, COUNT(*) FROM transactions GROUP BY 1, 2"):
        key = plaid_key(primary, detailed)
        counts[key] = counts.get(key, 0) + n
    found: dict[str, list[dict]] = {}
    for key, (label, kind, _) in PLAID_CATEGORIES.items():
        if key not in known and _same_name(label) not in names:
            found.setdefault(kind, []).append({"key": key, "label": label, "count": counts.get(key, 0)})
    for options in found.values():
        options.sort(key=lambda o: (-o["count"], o["label"]))
    return found


def add_plaid_category(conn: sqlite3.Connection, key: str) -> None:
    """Add one of Plaid's categories back: its row, later syncs, and its transactions from where you'd sent them. Only those
    sync would file here: a subcategory you deleted and sent elsewhere stays there. Your choices and rules still win."""
    if not any(o["key"] == key for options in plaid_category_options(conn).values() for o in options):
        raise ValueError("That Plaid category isn't available.")
    label, kind, order = PLAID_CATEGORIES[key]
    moved = remaps(conn)
    sent_to = moved.get(key)
    with conn:
        conn.execute("DELETE FROM category_remaps WHERE from_key = ?", (key,))
        conn.execute("INSERT INTO categories (key, label, kind, is_custom, sort_order) VALUES (?, ?, ?, 0, ?)",
                     (key, label, kind, order))
        for tid, primary, detailed in conn.execute(
                "SELECT id, plaid_category_primary, plaid_category_detailed FROM transactions WHERE plaid_category_key = ?",
                (sent_to,)).fetchall():
            if plaid_key(primary, detailed) == key and detailed not in moved:
                conn.execute("UPDATE transactions SET plaid_category_key = ? WHERE id = ?", (key, tid))


def set_kind(conn: sqlite3.Connection, key: str, kind: str) -> None:
    """What a category counts as; its subcategories follow it (user's request 2026-10-13: like account types)."""
    if kind not in KINDS:
        raise ValueError(f"Kind must be one of {KINDS}.")
    if conn.execute("UPDATE categories SET kind = ? WHERE key = ?", (kind, key)).rowcount == 0:
        raise LookupError(f"No category {key!r}")
    conn.execute("UPDATE categories SET kind = ? WHERE parent_key = ?", (kind, key))
    conn.commit()


# ---------------------------------------------------------------- the category table (user's request 2026-10-13)
# Categories and subcategories can be added, renamed, reordered, moved (a subcategory to another category) and
# deleted, like your account types (account_types.py). Keys never change (transactions, rules and budgets point at
# them); a deleted category's transactions move to one you choose.

LABEL_MAX = 40
# Categories the app relies on by key: the fallback, and card payments (cards.py, savings.py).
PROTECTED = {"UNCATEGORIZED", "CREDIT_CARD_PAYMENT"}


def _row(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM categories WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise ValueError("That category no longer exists.")
    return row


def _clean_label(conn: sqlite3.Connection, label: str, parent: str | None, key: str | None = None) -> str:
    label = " ".join((label or "").split())
    if not label:
        raise ValueError("Category name can't be empty.")
    if len(label) > LABEL_MAX:
        raise ValueError(f"Keep names to {LABEL_MAX} characters.")
    if conn.execute("SELECT 1 FROM categories WHERE lower(label) = lower(?) AND parent_key IS ? AND key IS NOT ?",
                    (label, parent, key)).fetchone():
        raise ValueError(f"“{label}” already exists {'there' if parent else 'as a category'}.")
    return label


def _new_key(conn: sqlite3.Connection, label: str, parent: str | None) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_") or "CUSTOM"
    base = f"{parent}__{base}" if parent else base
    key, n = base, 2
    while conn.execute("SELECT 1 FROM categories WHERE key = ? UNION SELECT 1 FROM category_remaps WHERE from_key = ?",
                       (key, key)).fetchone():
        key, n = f"{base}_{n}", n + 1
    return key


def _next_order(conn: sqlite3.Connection, parent: str | None) -> int:
    return conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories WHERE parent_key IS ?",
                        (parent,)).fetchone()[0]


def add_subcategory(conn: sqlite3.Connection, parent: str, label: str) -> str:
    """Your own subcategory (kind from its category); transactions reach it by your choice or a rule. Returns its key."""
    p = _row(conn, parent)
    if p["parent_key"]:
        raise ValueError("Subcategories go under a category.")
    label = _clean_label(conn, label, parent)
    key = _new_key(conn, label, parent)
    with conn:
        conn.execute("INSERT INTO categories (key, label, kind, is_custom, sort_order, parent_key) VALUES (?, ?, ?, 1, ?, ?)",
                     (key, label, p["kind"], _next_order(conn, parent), parent))
        # under a split category the budget lines are subcategories: the new one joins its section (as ensure_subcategory)
        conn.execute("""INSERT INTO budget_lines (category_key, section) SELECT ?, section FROM budget_lines
                        WHERE category_key = ? AND ? IN (SELECT key FROM categories WHERE is_split = 1)
                        ON CONFLICT (category_key) DO NOTHING""", (key, parent, parent))
    return key


def rename_category(conn: sqlite3.Connection, key: str, label: str) -> None:
    row = _row(conn, key)
    label = _clean_label(conn, label, row["parent_key"], key)
    with conn:
        conn.execute("UPDATE categories SET label = ? WHERE key = ?", (label, key))


GROUP_PREFIX = "group:"


def move(conn: sqlite3.Connection, key: str, target: str) -> None:
    """The Categories page's Move to (user's request 2026-11-02): under a category (`target` = its key), or "group:<kind>" = its
    own category in that group (spending, transfers, income). Its subcategories come along."""
    if target.startswith(GROUP_PREFIX):
        kind = target[len(GROUP_PREFIX):]
        if kind not in KINDS:
            raise ValueError("Choose where to move it.")
        move_to(conn, key, None)
        set_kind(conn, key, kind)
    else:
        move_to(conn, key, target or None)


def move_to(conn: sqlite3.Connection, key: str, new_parent: str | None) -> None:
    """Move a category or subcategory (user's requests 2026-10-13):

    * under a category (`new_parent`): a subcategory to another category, or a category (without subcategories of its
      own: two levels only) into one. It goes last there and takes that category's kind and budget section; a category
      that used Plaid's subcategories stops (they'd be a third level).
    * to the top (`new_parent` None / ""): a subcategory becomes a category, last in the list; it keeps its kind.

    Transactions, rules and budgets keep pointing at it (keys never change)."""
    row = _row(conn, key)
    if not new_parent:
        if row["parent_key"] is None:
            return
        _clean_label(conn, row["label"], None, key)
        with conn:
            conn.execute("UPDATE categories SET parent_key = NULL, sort_order = ? WHERE key = ?",
                         (_next_order(conn, None), key))
        return
    parent = _row(conn, new_parent)
    if parent["parent_key"] or new_parent == key:
        raise ValueError("Subcategories go under a category.")
    if new_parent == row["parent_key"]:
        return
    if conn.execute("SELECT 1 FROM categories WHERE parent_key = ?", (key,)).fetchone():
        raise ValueError(f"{row['label']} has subcategories: move or delete them first (two levels only).")
    _clean_label(conn, row["label"], new_parent, key)
    with conn:
        conn.execute("UPDATE categories SET parent_key = ?, kind = ?, sort_order = ?, is_split = 0 WHERE key = ?",
                     (new_parent, parent["kind"], _next_order(conn, new_parent), key))
        conn.execute("UPDATE budget_lines SET section = (SELECT section FROM budget_lines WHERE category_key = ?)"
                     " WHERE category_key = ? AND EXISTS (SELECT 1 FROM budget_lines WHERE category_key = ?)",
                     (new_parent, key, new_parent))


def delete_category(conn: sqlite3.Connection, key: str, target: str) -> int:
    """Delete a category (with its subcategories) or a subcategory. Its transactions (Plaid's mapping and your own
    choices), rules and recurring items move to `target`; its budgets are removed; Plaid's future transactions for it
    go to `target` too (category_remaps). Returns how many transactions moved."""
    row = _row(conn, key)
    if key in PROTECTED:
        raise ValueError(f"{row['label']} can't be deleted: the app relies on it.")
    keys = [key] + [r[0] for r in conn.execute("SELECT key FROM categories WHERE parent_key = ?", (key,))]
    if target in keys or not category_exists(conn, target):
        raise ValueError("Choose another category for its transactions.")
    if set(keys) & PROTECTED:
        raise ValueError("It has a subcategory the app relies on.")
    marks = ",".join("?" * len(keys))
    moved = conn.execute(f"SELECT COUNT(*) FROM transactions_effective WHERE category IN ({marks})", keys).fetchone()[0]
    with conn:
        conn.execute(f"UPDATE transactions SET plaid_category_key = ? WHERE plaid_category_key IN ({marks})", [target, *keys])
        conn.execute(f"UPDATE transactions SET user_category = ? WHERE user_category IN ({marks})", [target, *keys])
        # your choices on investment activity too (migration 0052)
        conn.execute(f"UPDATE investment_transactions SET user_category = ? WHERE user_category IN ({marks})",
                     [target, *keys])
        # rules (fintrack/rules.py): what they set and test, and what they've already decided
        conn.execute(f"UPDATE rule_actions SET value = ? WHERE kind = 'set_category' AND value IN ({marks})", [target, *keys])
        conn.execute(f"UPDATE transactions SET rule_category = ? WHERE rule_category IN ({marks})", [target, *keys])
        for cid, values in conn.execute("""SELECT id, "values" FROM rule_conditions WHERE field = 'category'""").fetchall():
            old_values = json.loads(values)
            if set(old_values) & set(keys):
                new_values = list(dict.fromkeys(target if v in keys else v for v in old_values))
                conn.execute("""UPDATE rule_conditions SET "values" = ? WHERE id = ?""", (json.dumps(new_values), cid))
        conn.execute(f"UPDATE recurring_items SET category_key = ? WHERE category_key IN ({marks})", [target, *keys])
        conn.execute(f"DELETE FROM budgets WHERE category_key IN ({marks})", keys)
        conn.execute(f"DELETE FROM budget_lines WHERE category_key IN ({marks})", keys)
        # Plaid keeps sending them: remember where they go (and where earlier deletions into them go now)
        conn.execute(f"UPDATE category_remaps SET to_key = ? WHERE to_key IN ({marks})", [target, *keys])
        conn.executemany("INSERT INTO category_remaps (from_key, to_key) VALUES (?, ?)"
                         " ON CONFLICT (from_key) DO UPDATE SET to_key = excluded.to_key",
                         [(k, target) for k in keys])
        conn.execute(f"DELETE FROM categories WHERE parent_key = ? AND key IN ({marks})", [key, *keys])
        conn.execute("DELETE FROM categories WHERE key = ?", (key,))
    return moved


def transaction_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """{category key: transactions in it} (the Categories page)."""
    return dict(conn.execute("SELECT category, COUNT(*) FROM transactions_effective GROUP BY category").fetchall())


# ---------------------------------------------------------------- many transactions (milestone 14)

def set_categories(conn: sqlite3.Connection, transaction_ids: list[int], key: str | None) -> int:
    """Group edit: your own category for these transactions, or None = back to automatic (rule or
    Plaid). Returns how many rows were updated."""
    if key is not None and not category_exists(conn, key):
        raise ValueError(f"Unknown category {key!r}.")
    if not transaction_ids:
        return 0
    with conn:
        changed = conn.executemany(
            "UPDATE transactions SET user_category = ?, updated_at = CURRENT_TIMESTAMP"
            " WHERE id = ? AND user_category IS NOT ?",           # unchanged rows aren't rewritten
            [(key, i, key) for i in transaction_ids]).rowcount
    from fintrack import manual_transactions
    manual_transactions.sync_parts(conn)        # a charge's rewards pair follows it (2026-11-04)
    return changed


# ---------------------------------------------------------------- the "Categories to review" card (user's request 2026-11-01)

def review_rows(conn: sqlite3.Connection) -> list[dict]:
    """New spending and income Plaid put in a first-level category that has subcategories, not yet accepted (migration
    0053), newest first. A category you or a rule chose isn't listed
    (that's already a decision), nor transfers, which aren't counted."""
    return [dict(r) for r in conn.execute(
        """SELECT e.* FROM transactions_effective e JOIN transactions t ON t.id = e.id
           WHERE t.category_reviewed = 0 AND e.account_hidden = 0 AND e.removed_at IS NULL
             AND e.category_source = 'plaid' AND e.kind IN ('expense', 'income')
             AND EXISTS (SELECT 1 FROM categories s WHERE s.parent_key = e.category)
           ORDER BY e.date DESC, e.id DESC""")]


def accept_reviewed(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Dismissing the card: these keep their categories and leave it. Only the rows it showed, so one that arrived since
    still gets reviewed."""
    with conn:
        conn.executemany("UPDATE transactions SET category_reviewed = 1 WHERE id = ?", [(i,) for i in ids])


# ---------------------------------------------------------------- single transactions

DESCRIPTION_MAX = 120


def set_transaction_description(conn: sqlite3.Connection, transaction_id: int, text: str | None) -> None:
    """Your own description for one transaction (shown everywhere), or blank/None = the bank's.
    Plaid's name and merchant are kept as reported, so rules still match on them."""
    text = (text or "").strip()[:DESCRIPTION_MAX] or None
    if conn.execute(
        "UPDATE transactions SET user_description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        " AND user_description IS NOT ?", (text, transaction_id, text),
    ).rowcount == 0 and conn.execute("SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)).fetchone() is None:
        raise LookupError(f"No transaction {transaction_id}")
    conn.commit()


def set_transaction_category(conn: sqlite3.Connection, transaction_id: int, key: str | None) -> None:
    """Set your own category for one transaction, or None to go back to automatic."""
    if key is not None and not category_exists(conn, key):
        raise ValueError(f"Unknown category {key!r}.")
    if conn.execute(
        "UPDATE transactions SET user_category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (key, transaction_id),
    ).rowcount == 0:
        raise LookupError(f"No transaction {transaction_id}")
    conn.commit()
    from fintrack import manual_transactions
    manual_transactions.sync_parts(conn)        # a charge's rewards pair follows it (2026-11-04)
