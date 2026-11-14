"""Tags (user's request 2026-10-28): your own labels for related transactions (a trip, a project, a renovation),
emoji welcome. A transaction can have any number of tags and a tag any number of transactions (transaction_tags).

Tags only group: they never change a transaction's category or kind. The Tags page (Planning) sums each tag
(transactions_effective): spent = Σ amounts of kind `expense` (refunds lower it) plus transfers out; received = −Σ
amounts of kind `income` plus transfers in (user's request 2026-10-28: a tag's transfers count, e.g. your share of a
trip paid by Venmo; card payments are transfers too). Loan entries and transactions waiting for review (kind
`excluded`) are listed but not added up. Hidden (removed-connection) accounts are left out, as on Spending.

A tag follows its transaction when Plaid replaces a pending one with the posted one (sync.carry_over_tags) and when
you use a removed transaction's return (removals.use_return).

Investment activity can be tagged too (migration 0052, user's request 2026-10-31): its links are in
investment_transaction_tags, and here, as in the Transactions list (investment_activity.as_transactions), an activity's
id is its own id negated. Every function takes and returns those signed ids; the Tags page sums both.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from fintrack import investment_activity
from fintrack.money import format_cents, parse_amount_formula

NAME_LIMIT = 40

# The colors offered for a tag (user's request 2026-11-02): 12 hues in 3 shades, light to dark. You choose them, so they
# aren't assigned in order like chart series; a tag without one keeps its automatic color (web/tags.tag_slots).
COLOR_PALETTE = [
    (hue, shades) for hue, shades in (
        ("Red", ("#f87171", "#dc2626", "#991b1b")), ("Orange", ("#fb923c", "#ea580c", "#9a3412")),
        ("Amber", ("#fbbf24", "#d97706", "#92400e")), ("Lime", ("#a3e635", "#65a30d", "#3f6212")),
        ("Green", ("#4ade80", "#16a34a", "#166534")), ("Teal", ("#2dd4bf", "#0d9488", "#115e59")),
        ("Sky", ("#38bdf8", "#0284c7", "#075985")), ("Blue", ("#60a5fa", "#2563eb", "#1e40af")),
        ("Violet", ("#a78bfa", "#7c3aed", "#5b21b6")), ("Fuchsia", ("#e879f9", "#c026d3", "#86198f")),
        ("Pink", ("#f472b6", "#db2777", "#9d174d")), ("Gray", ("#a1a1aa", "#71717a", "#52525b")),
    )
]
SHADES = ("Light", "Medium", "Dark")

# Every tag link, activity's under negative ids (see the module's docstring).
LINKS_SQL = """(SELECT transaction_id, tag_id FROM transaction_tags
    UNION ALL SELECT -investment_transaction_id, tag_id FROM investment_transaction_tags)"""


def _split(transaction_ids: list[int]) -> tuple[list[int], list[int]]:
    """(transaction ids, investment activity ids made positive)."""
    return [i for i in transaction_ids if i > 0], [-i for i in transaction_ids if i < 0]


class TagError(ValueError):
    """A tag change was refused; the message is shown to you."""


@dataclass(frozen=True)
class Tag:
    id: int
    name: str
    start_date: str | None = None     # your dates for it (migration 0050; both or neither), e.g. when a trip ran
    end_date: str | None = None
    budget_cents: int | None = None   # your total for the whole tag (migration 0057); see TagSummary.budget_total_cents
    color: str | None = None          # your color, '#rrggbb' (migration 0058); None = automatic


def clean_name(name: str) -> str:
    """A tag's name as stored: spaces collapsed, 1 to NAME_LIMIT characters (emoji count as characters)."""
    name = " ".join((name or "").split())
    if not name:
        raise TagError("Give the tag a name.")
    if len(name) > NAME_LIMIT:
        raise TagError(f"Keep tag names to {NAME_LIMIT} characters.")
    return name


def all_tags(conn: sqlite3.Connection) -> list[Tag]:
    """Every tag, by name (for pickers and suggestions)."""
    return [Tag(r[0], r[1], r[2], r[3], r[4], r[5])
            for r in conn.execute("SELECT id, name, start_date, end_date, budget_cents, color FROM tags ORDER BY name COLLATE NOCASE")]


INACTIVE_AFTER_DAYS = 365


def split_by_activity(conn: sqlite3.Connection, today: date | None = None) -> tuple[list[Tag], list[Tag]]:
    """(active, inactive) tags, each by name (user's request 2026-11-02). Inactive = its latest transaction in a visible
    account, investment activity included, is more than INACTIVE_AFTER_DAYS ago. A tag with none hasn't gone quiet: it's
    active, or one you just made would never be offered. Pickers offer only active ones, except the Transactions filter."""
    cutoff = ((today or date.today()) - timedelta(days=INACTIVE_AFTER_DAYS)).isoformat()
    old = {r[0] for r in conn.execute(
        """SELECT tag_id FROM (
               SELECT x.tag_id, t.date AS day FROM transaction_tags x
               JOIN transactions t ON t.id = x.transaction_id JOIN accounts a ON a.id = t.account_id WHERE a.is_hidden = 0
               UNION ALL
               SELECT x.tag_id, i.date FROM investment_transaction_tags x
               JOIN investment_transactions i ON i.id = x.investment_transaction_id
               JOIN accounts a ON a.id = i.account_id WHERE a.is_hidden = 0)
           GROUP BY tag_id HAVING MAX(day) < ?""", (cutoff,))}
    everything = all_tags(conn)
    return [t for t in everything if t.id not in old], [t for t in everything if t.id in old]


def active_tags(conn: sqlite3.Connection, today: date | None = None) -> list[Tag]:
    return split_by_activity(conn, today)[0]


# Overview's two countdowns (user's requests 2026-11-02): what you chose for each and the date it was on, in app_settings
# 'overview_countdown_<slot>' as 'YYYY-MM-DD|key'; none = automatic, the next thing coming up.
COUNTDOWN_SLOTS = (1, 2)


def countdown_choice(conn: sqlite3.Connection, slot: int) -> tuple[str, str] | None:
    """(the date it was on when chosen, its key), or None."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (f"overview_countdown_{slot}",)).fetchone()
    return tuple(row[0].split("|", 1)) if row else None


def set_countdown_choice(conn: sqlite3.Connection, slot: int, key: str, day: str = "") -> None:
    """Choose what a countdown follows ('tag:3', 'holiday:Thanksgiving', 'label:2') and the date it's on; blank = automatic."""
    if slot not in COUNTDOWN_SLOTS:
        raise TagError("Choose a countdown.")
    if key:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)"
                     " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (f"overview_countdown_{slot}", f"{day}|{key}"))
    else:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (f"overview_countdown_{slot}",))
    conn.commit()


def find_or_create(conn: sqlite3.Connection, name: str) -> Tag:
    """The tag with this name (ignoring case), created if it's new."""
    name = clean_name(name)
    row = conn.execute("SELECT id, name FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return Tag(row[0], row[1])
    cursor = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    return Tag(cursor.lastrowid, name)


def create(conn: sqlite3.Connection, name: str) -> Tag:
    """A new tag with no transactions yet, from the Tags page's + (user's request 2026-10-29); an error if the name is
    taken (ignoring case), so a typo doesn't quietly land on an old tag."""
    name = clean_name(name)
    if tag_by_name(conn, name):
        raise TagError(f"There's already a tag named {name}.")
    tag = find_or_create(conn, name)
    conn.commit()
    return tag


def tags_of(conn: sqlite3.Connection, transaction_ids: list[int]) -> dict[int, list[Tag]]:
    """{transaction id: its tags, by name} for these transactions (those without tags are left out)."""
    if not transaction_ids:
        return {}
    found: dict[int, list[Tag]] = {}
    marks = ",".join("?" * len(transaction_ids))
    for r in conn.execute(
            f"""SELECT tt.transaction_id, t.id, t.name FROM {LINKS_SQL} tt JOIN tags t ON t.id = tt.tag_id
                WHERE tt.transaction_id IN ({marks}) ORDER BY t.name COLLATE NOCASE""", transaction_ids):
        found.setdefault(r[0], []).append(Tag(r[1], r[2]))
    return found


def add(conn: sqlite3.Connection, transaction_ids: list[int], name: str) -> tuple[Tag, int]:
    """Tag these transactions (creating the tag if it's new). Returns (the tag, how many weren't tagged with it)."""
    tag = find_or_create(conn, name)
    bank, activity = _split(transaction_ids)
    # counted, not taken from total_changes (the page-revision trigger adds a change per insert)
    tagged = lambda: conn.execute(f"SELECT COUNT(*) FROM {LINKS_SQL} WHERE tag_id = ?", (tag.id,)).fetchone()[0]  # noqa: E731
    before = tagged()
    conn.executemany("INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                     [(i, tag.id) for i in bank])
    # yours now, even if a rule added it: it stays when the rule goes, and a rule's removal is forgotten
    conn.executemany("UPDATE transaction_tags SET from_rule = 0 WHERE transaction_id = ? AND tag_id = ? AND from_rule = 1",
                     [(i, tag.id) for i in bank])
    conn.executemany("DELETE FROM rule_tag_removals WHERE transaction_id = ? AND tag_id = ?", [(i, tag.id) for i in bank])
    conn.executemany("INSERT OR IGNORE INTO investment_transaction_tags (investment_transaction_id, tag_id) VALUES (?, ?)",
                     [(i, tag.id) for i in activity])
    added = tagged() - before
    conn.commit()
    from fintrack import manual_transactions
    manual_transactions.sync_parts(conn)        # a charge's rewards pair follows it (2026-11-04)
    return tag, added


def remove(conn: sqlite3.Connection, transaction_ids: list[int], tag_id: int) -> int:
    """Take a tag off these transactions (the tag itself stays). Returns how many had it."""
    removed = 0
    bank = _split(transaction_ids)[0]
    conn.executemany("""INSERT OR IGNORE INTO rule_tag_removals (transaction_id, tag_id)
                        SELECT transaction_id, tag_id FROM transaction_tags
                        WHERE transaction_id = ? AND tag_id = ? AND from_rule = 1""", [(i, tag_id) for i in bank])
    for table, column, ids in zip(("transaction_tags", "investment_transaction_tags"),
                                  ("transaction_id", "investment_transaction_id"), _split(transaction_ids)):
        if ids:
            marks = ",".join("?" * len(ids))
            removed += conn.execute(f"DELETE FROM {table} WHERE tag_id = ? AND {column} IN ({marks})",
                                    [tag_id, *ids]).rowcount
    conn.commit()
    from fintrack import manual_transactions
    manual_transactions.sync_parts(conn)        # a charge's rewards pair follows it (2026-11-04)
    return removed


def tag_by_name(conn: sqlite3.Connection, name: str) -> Tag | None:
    row = conn.execute("SELECT id, name FROM tags WHERE name = ?", (" ".join((name or "").split()),)).fetchone()
    return Tag(row[0], row[1]) if row else None


def rename(conn: sqlite3.Connection, tag_id: int, name: str) -> None:
    """Rename a tag (its transactions keep it). Refused if another tag already has the name."""
    name = clean_name(name)
    if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
        raise TagError("That tag no longer exists.")
    if conn.execute("SELECT 1 FROM tags WHERE name = ? AND id != ?", (name, tag_id)).fetchone():
        raise TagError(f"There's already a tag called {name}.")
    # an unchanged name (exactly, case included) writes nothing
    conn.execute("UPDATE tags SET name = ? WHERE id = ? AND name != ? COLLATE BINARY", (name, tag_id, name))
    conn.commit()


def set_dates(conn: sqlite3.Connection, tag_id: int, start: str, end: str) -> None:
    """A tag's own dates (user's request 2026-10-28), ISO; blank = none. Each is saved as it's entered (the card saves on
    every change), so one alone is kept; the calendar uses them only once both are set. Start must not be after End."""
    from datetime import date as _date

    start, end = (start or "").strip(), (end or "").strip()
    try:
        first, last = (_date.fromisoformat(start) if start else None), (_date.fromisoformat(end) if end else None)
    except ValueError:
        raise TagError("Those don't look like dates.") from None
    if any(d and not 1900 <= d.year <= 2200 for d in (first, last)):
        raise TagError("Use a year between 1900 and 2200.")      # a half-typed year (e.g. 0002) never gets saved
    if first and last and first > last:
        raise TagError("The start date has to be on or before the end date.")
    if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
        raise TagError("That tag no longer exists.")
    conn.execute("UPDATE tags SET start_date = ?, end_date = ? WHERE id = ?"
                 " AND (start_date IS NOT ? OR end_date IS NOT ?)",
                 (start or None, end or None, tag_id, start or None, end or None))
    conn.commit()


def _clean_color(color: str) -> str:
    color = (color or "").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        raise TagError("Choose a color.")
    return color


def set_color(conn: sqlite3.Connection, tag_id: int, color: str) -> None:
    """A tag's color; blank = automatic again."""
    color = _clean_color(color) if (color or "").strip() else None
    conn.execute("UPDATE tags SET color = ? WHERE id = ? AND color IS NOT ?", (color, tag_id, color))
    conn.commit()


def custom_colors(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT color FROM tag_custom_colors ORDER BY rowid")]


def add_custom_color(conn: sqlite3.Connection, tag_id: int, color: str) -> None:
    """Add a color of your own to the list every tag offers (unless it's already offered), and give it to this tag."""
    color = _clean_color(color)
    if not any(color in shades for _, shades in COLOR_PALETTE):
        conn.execute("INSERT OR IGNORE INTO tag_custom_colors (color) VALUES (?)", (color,))
    set_color(conn, tag_id, color)


def remove_custom_color(conn: sqlite3.Connection, color: str) -> None:
    """Take a color off the list. Tags using it keep it until you choose another."""
    conn.execute("DELETE FROM tag_custom_colors WHERE color = ?", ((color or "").strip().lower(),))
    conn.commit()


def _budget_amount(text: str) -> int | None:
    """Typed amount or calculation -> cents; blank = None."""
    try:
        return parse_amount_formula(text) if (text or "").strip() else None
    except ValueError as exc:
        raise TagError(str(exc)) from None


class BudgetTooSmall(TagError):
    """A category budget that would take the categories past the tag's total (user's request 2026-11-04): the card asks
    whether to raise the total to `needed` first."""
    def __init__(self, needed: int, total: int):
        super().__init__(f"Category budgets would add up to {format_cents(needed)}, more than the {format_cents(total)} budget.")
        self.needed, self.total = needed, total


def _categories_total(conn: sqlite3.Connection, tag_id: int, except_category: str | None = None) -> int:
    return conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM tag_category_budgets WHERE tag_id = ? AND category_key IS NOT ?",
                        (tag_id, except_category)).fetchone()[0]


def set_budget(conn: sqlite3.Connection, tag_id: int, amount_text: str) -> None:
    """A tag's total budget, from its card (user's request 2026-11-02); blank = none (then the category budgets' sum).
    Its category budgets are part of it (user's request 2026-11-04), so it can't be less than they add up to."""
    amount = _budget_amount(amount_text)
    floor = _categories_total(conn, tag_id)
    if amount is not None and amount < floor:
        raise TagError(f"Its category budgets add up to {format_cents(floor)}: the budget can't be less.")
    conn.execute("UPDATE tags SET budget_cents = ? WHERE id = ? AND budget_cents IS NOT ?", (amount, tag_id, amount))
    conn.commit()


def set_category_budget(conn: sqlite3.Connection, tag_id: int, category: str, amount_text: str,
                        raise_total: bool = False) -> None:
    """A tag's budget for one spending category, from its card; blank removes it and what was allocated to it (that
    category's spending under the tag then counts on the tag's own line). Category budgets are part of the tag's total
    (user's request 2026-11-04): one that would take them past it raises BudgetTooSmall, unless `raise_total` (you
    agreed), which raises the total to their sum."""
    amount = _budget_amount(amount_text)
    if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
        raise TagError("That tag no longer exists.")
    if not conn.execute("SELECT 1 FROM categories WHERE key = ? AND kind = 'expense'", (category,)).fetchone():
        raise TagError("Choose a spending category.")
    total = conn.execute("SELECT budget_cents FROM tags WHERE id = ?", (tag_id,)).fetchone()[0]
    needed = _categories_total(conn, tag_id, category) + (amount or 0)
    if amount is not None and total is not None and needed > total and not raise_total:
        raise BudgetTooSmall(needed, total)
    with conn:
        if amount is not None and total is not None and needed > total:
            conn.execute("UPDATE tags SET budget_cents = ? WHERE id = ?", (needed, tag_id))
        if amount is None:
            conn.execute("DELETE FROM tag_category_budgets WHERE tag_id = ? AND category_key = ?", (tag_id, category))
            conn.execute("DELETE FROM tag_budget_allocations WHERE tag_id = ? AND category_key = ?", (tag_id, category))
            conn.execute("DELETE FROM tag_budget_lines WHERE tag_id = ? AND category_key = ?", (tag_id, category))
        else:
            conn.execute("""INSERT INTO tag_category_budgets (tag_id, category_key, amount_cents) VALUES (?, ?, ?)
                            ON CONFLICT (tag_id, category_key) DO UPDATE SET amount_cents = excluded.amount_cents""",
                         (tag_id, category, amount))


def delete(conn: sqlite3.Connection, tag_id: int) -> None:
    """Delete a tag; its transactions just lose it (nothing else about them changes)."""
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()


# ---------------------------------------------------------------- the Tags page

@dataclass
class TagLine:
    category: str
    label: str
    kind: str                   # expense, income or transfer (the transactions' kind, for the link to them)
    side: str                   # "spent" or "received": which table and total it's in
    cents: int                  # spent or received, signed so refunds lower spending
    count: int
    bucket: str | None = None   # the category budget it counts toward (its own or its parent's), if any


@dataclass
class TagCategoryBudget:
    category: str
    label: str
    budget_cents: int
    spent_cents: int            # the tag's spending in this category and subcategories without a budget of their own
    count: int = 0              # ...in how many transactions


def budget_status(spent: int, budget: int | None) -> dict | None:
    """What a card shows for a budget: the bar's share, and over or the amount left. None without a budget."""
    if budget is None:
        return None
    return {"share": min(spent / budget, 1) if budget > 0 else (1 if spent > 0 else 0),
            "over": spent > budget, "left": budget - spent, "used": spent / budget if budget > 0 else None}


@dataclass
class TagSummary:
    tag: Tag
    count: int                  # every tagged transaction in visible accounts, counted or not
    spent_cents: int            # Σ expense + transfers out
    received_cents: int         # −Σ income + transfers in
    first: str | None           # the first and last tagged dates
    last: str | None
    lines: list[TagLine] = field(default_factory=list)   # by category, largest first: spent, then received
    category_budgets: list[TagCategoryBudget] = field(default_factory=list)

    @property
    def budget_total_cents(self) -> int | None:
        """Your total, else the sum of the category budgets, else None."""
        if self.tag.budget_cents is not None:
            return self.tag.budget_cents
        return sum(b.budget_cents for b in self.category_budgets) if self.category_budgets else None

    @property
    def budget(self) -> dict | None:
        return budget_status(self.spent_cents, self.budget_total_cents)

    # Category budgets are part of the total (user's request 2026-11-04); the rest of it covers every other category.
    @property
    def other_lines(self) -> list[TagLine]:
        """What was spent in categories without a budget of their own (nor a parent's)."""
        return [line for line in self.lines if line.side == "spent" and line.bucket is None]

    @property
    def other_spent_cents(self) -> int:
        return sum(line.cents for line in self.other_lines)

    @property
    def spent_count(self) -> int:
        """How many transactions make up Spent (the table's Total row)."""
        return sum(line.count for line in self.lines if line.side == "spent")

    @property
    def other_budget_cents(self) -> int | None:
        """The total less the category budgets; None without a total of your own."""
        if self.tag.budget_cents is None:
            return None
        return self.tag.budget_cents - sum(b.budget_cents for b in self.category_budgets)

    @property
    def not_counted(self) -> int:
        """Tagged loan entries and transactions waiting for review (listed on Transactions, not added up)."""
        return self.count - sum(line.count for line in self.lines)


def summaries(conn: sqlite3.Connection) -> list[TagSummary]:
    """Every tag with its totals and its spending and income by category, newest first (its End date, else its latest
    tagged date; then by name)."""
    result = {t.id: TagSummary(t, 0, 0, 0, None, None) for t in all_tags(conn)}
    source, params = investment_activity.as_transactions(conn)
    for r in conn.execute(
            f"""SELECT tt.tag_id, COUNT(*), MIN(e.date), MAX(e.date) FROM {LINKS_SQL} tt
               JOIN ({source}) e ON e.id = tt.transaction_id
               WHERE e.account_hidden = 0 GROUP BY tt.tag_id""", params):
        s = result[r[0]]
        s.count, s.first, s.last = r[1], r[2], r[3]
    # Which side a row is on: spending and income by their kind (a refund stays on its category's side, lowering it);
    # a transfer by its direction (Plaid's sign: positive = money out).
    for r in conn.execute(
            f"""SELECT tt.tag_id, e.category, e.category_label, e.kind,
                      CASE WHEN e.kind = 'expense' OR (e.kind = 'transfer' AND e.amount_cents > 0)
                           THEN 'spent' ELSE 'received' END AS side,
                      SUM(e.amount_cents), COUNT(*)
               FROM {LINKS_SQL} tt JOIN ({source}) e ON e.id = tt.transaction_id
               WHERE e.account_hidden = 0 AND e.kind IN ('expense', 'income', 'transfer')
               GROUP BY tt.tag_id, e.category, e.kind, side""", params):
        s = result[r[0]]
        cents = r[5] if r[4] == "spent" else -r[5]
        s.lines.append(TagLine(r[1], r[2], r[3], r[4], cents, r[6]))
        if r[4] == "spent":
            s.spent_cents += cents
        else:
            s.received_cents += cents
    for s in result.values():
        s.lines.sort(key=lambda line: (line.side != "spent", -line.cents, line.label))
    # category budgets, by name, with what's been spent toward each (spent side only, expense or transfer out)
    parents = {r[0]: r[1] for r in conn.execute("SELECT key, parent_key FROM categories WHERE parent_key IS NOT NULL")}
    for r in conn.execute("""SELECT b.tag_id, b.category_key, c.label, b.amount_cents FROM tag_category_budgets b
                             JOIN categories c ON c.key = b.category_key ORDER BY lower(c.label)"""):
        result[r[0]].category_budgets.append(TagCategoryBudget(r[1], r[2], r[3], 0))
    for s in result.values():
        own = {b.category: b for b in s.category_budgets}
        for line in s.lines:
            bucket = own.get(line.category) or own.get(parents.get(line.category))
            if bucket and line.side == "spent":
                bucket.spent_cents += line.cents
                bucket.count += line.count
                line.bucket = bucket.category
    # Newest first (user's request 2026-10-28): by the tag's own End date, else its latest tagged date; tags with
    # neither last; ties by name.
    ordered = sorted(result.values(), key=lambda s: s.tag.name.lower())
    return sorted(ordered, key=lambda s: s.tag.end_date or s.last or "", reverse=True)


# ---------------------------------------------------------------- the Tags page's calendar

@dataclass(frozen=True)
class Run:
    """One bar on the Tags calendar: a tag's own dates (its range), a day with its transactions outside them (`loose`),
    or, for a tag without dates, back-to-back days with its transactions (a day on its own is a run of one)."""
    first: str
    last: str
    spent_cents: int            # spent on those days (the summaries' rule: expense, refunds lower it, + transfers out)
    loose: bool = False         # a single day outside the tag's own dates


# The calendar's setting (its gear popup, user's request 2026-10-29): draw the days with tagged transactions too (on, the
# default), or only each tag's own Start - End range. app_settings 'tags_calendar_days' = '1' / '0'.
def calendar_shows_days(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'tags_calendar_days'").fetchone()
    return not row or row[0] != "0"


def set_calendar_shows_days(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('tags_calendar_days', ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value WHERE value != excluded.value",
                 ("1" if on else "0",))
    conn.commit()


def calendar_items(conn: sqlite3.Connection, ranges_only: bool = False) -> dict[int, list[Run]]:
    """{tag id: its calendar bars, oldest first} (user's requests 2026-10-28). With its own dates: one bar for them and
    a single-day bar (`loose`) for each other day with tagged transactions. Without: the days with tagged transactions
    (visible accounts) grouped into runs of consecutive calendar days. `ranges_only` (the calendar's setting, unticked):
    just the tags' own ranges; a tag without both dates has no bars."""
    from datetime import date as _date, timedelta as _timedelta

    days: dict[int, list[tuple[str, int]]] = {}
    source, params = investment_activity.as_transactions(conn)
    for r in conn.execute(
            f"""SELECT tt.tag_id, e.date,
                      SUM(CASE WHEN e.kind = 'expense' OR (e.kind = 'transfer' AND e.amount_cents > 0)
                               THEN e.amount_cents ELSE 0 END)
               FROM {LINKS_SQL} tt JOIN ({source}) e ON e.id = tt.transaction_id
               WHERE e.account_hidden = 0 GROUP BY tt.tag_id, e.date ORDER BY tt.tag_id, e.date""", params):
        days.setdefault(r[0], []).append((r[1], r[2]))
    found: dict[int, list[Run]] = {}
    for tag in all_tags(conn):
        dated = days.get(tag.id, [])
        if tag.start_date and tag.end_date:
            inside = sum(c for d, c in dated if tag.start_date <= d <= tag.end_date)
            items = [Run(tag.start_date, tag.end_date, inside)]
            if not ranges_only:
                items += [Run(d, d, c, loose=True) for d, c in dated if not tag.start_date <= d <= tag.end_date]
            found[tag.id] = sorted(items, key=lambda r: r.first)
            continue
        if not dated or ranges_only:
            continue
        out: list[Run] = []
        start, end, spent = dated[0][0], dated[0][0], dated[0][1]
        for day, cents in dated[1:]:
            if _date.fromisoformat(day) - _date.fromisoformat(end) == _timedelta(days=1):
                end, spent = day, spent + cents                  # the next day: the run goes on
            else:
                out.append(Run(start, end, spent))
                start, end, spent = day, day, cents
        out.append(Run(start, end, spent))
        found[tag.id] = out
    return found
