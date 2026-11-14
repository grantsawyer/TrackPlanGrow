"""Rules (redesigned on the user's request 2026-11-02; migration 0054): if ALL of a rule's conditions match a transaction, its
actions apply.

Conditions test what the bank and Plaid sent, never what rules decided, so the result doesn't depend on rules feeding each
other: description (merchant or bank text), merchant, bank text, amount (its size), direction (money in or out), date,
category (Plaid's; picking a category includes its subcategories), account.

Actions:
  * set_description, set_category: the first rule (in `position` order) to set one wins. Kept in the transaction's own
    columns (rule_description, rule_category), which transactions_effective reads below your own choice.
  * add_tag: every matching rule's tags, marked from_rule. They leave when no rule adds them any more; one you took off stays
    off (rule_tag_removals).
  * recurring, purpose, not_transfer, accept: one-time. Done once per rule and transaction (rule_applied), never over your
    own link, so a rule doesn't redo what you undid. Deleting a rule undoes them, unless you keep its results (`delete`).

Everything but the one-time actions is recomputed from scratch by `apply_all`: after a sync and whenever a rule changes, so
editing or deleting a rule updates the transactions it applied to. Only rows that change are written, so a quiet run doesn't
bump the page revision. Investment activity isn't touched (its own table).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from fintrack.money import format_cents, to_cents

TEXT_OPS = {"contains": "contains", "not_contains": "doesn't contain", "is": "is", "starts": "starts with"}
AMOUNT_OPS = {"over": "is over", "under": "is under", "equals": "is", "between": "is between"}
DATE_OPS = {"on_or_after": "is on or after", "on_or_before": "is on or before", "between": "is between"}
LIST_OPS = {"is": "is", "not": "isn't"}
OPS = {"description": TEXT_OPS, "merchant": TEXT_OPS, "bank_text": TEXT_OPS, "amount": AMOUNT_OPS,
       "direction": {"is": "is"}, "date": DATE_OPS, "category": LIST_OPS, "account": LIST_OPS}
FIELD_LABELS = {"description": "Description", "amount": "Amount", "direction": "Direction", "date": "Date",
                "category": "Category", "account": "Account", "merchant": "Merchant", "bank_text": "Bank text"}
ACTION_LABELS = {"set_description": "Set description", "set_category": "Set category", "add_tag": "Add tag",
                 "recurring": "Recurring", "purpose": "Link to purpose", "not_transfer": "Mark as not a transfer",
                 "accept": "Accept category (skip review)"}
ONE_TIME = ("recurring", "purpose", "not_transfer", "accept")
NOT_RECURRING = "none"


@dataclass
class Condition:
    field: str
    op: str
    values: list


@dataclass
class Action:
    kind: str
    value: str = ""


@dataclass
class Rule:
    id: int
    position: int = 0
    conditions: list[Condition] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)


def _in_order(field_name: str, values: list) -> list:
    """A condition's text alternatives alphabetically (user's request 2026-11-02): they're all "or" (for "doesn't contain", all
    "and"), so the order never changes what matches. Other values are left as they are."""
    return sorted(values, key=lambda v: str(v).casefold()) if field_name in ("description", "merchant", "bank_text") else values


def is_keyword(rule: Rule) -> bool:
    """A category keyword rule (the Rules page's first section; user's request 2026-11-02): text contains any of these -> this
    category, nothing else. Anything else is a custom rule."""
    return (len(rule.conditions) == 1 and rule.conditions[0].op == "contains"
            and rule.conditions[0].field in ("description", "merchant", "bank_text")
            and len(rule.actions) == 1 and rule.actions[0].kind == "set_category")


def _run_order(rule: Rule) -> tuple:
    """Custom rules first, as they're more specific, so one can override a keyword; each kind by `position`."""
    return (is_keyword(rule), rule.position, rule.id)


def load(conn: sqlite3.Connection) -> list[Rule]:
    """Every rule, in the order they run."""
    rules = {r[0]: Rule(r[0], r[1]) for r in conn.execute("SELECT id, position FROM rules ORDER BY position, id")}
    for r in conn.execute('SELECT rule_id, field, op, "values" FROM rule_conditions ORDER BY id'):
        rules[r[0]].conditions.append(Condition(r[1], r[2], _in_order(r[1], json.loads(r[3]))))
    for r in conn.execute("SELECT rule_id, kind, value FROM rule_actions ORDER BY id"):
        rules[r[0]].actions.append(Action(r[1], r[2]))
    return sorted(rules.values(), key=_run_order)


# ---------------------------------------------------------------- matching

def _parents(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, parent_key FROM categories WHERE parent_key IS NOT NULL").fetchall())


def _text_matches(op: str, phrases: list, texts: list[str]) -> bool:
    """Any phrase, against any of the texts, ignoring case; "doesn't contain": none of them, in any."""
    lowered = [str(p).lower() for p in phrases]
    if op == "not_contains":
        return not any(p in t for p in lowered for t in texts)
    if op == "is":
        return any(p == t for p in lowered for t in texts)
    if op == "starts":
        return any(t.startswith(p) for p in lowered for t in texts)
    return any(p in t for p in lowered for t in texts)


def condition_matches(c: Condition, row, parents: dict[str, str]) -> bool:
    if c.field in ("description", "merchant", "bank_text"):
        merchant, name = (row["merchant_name"] or "").lower(), (row["name"] or "").lower()
        texts = {"description": [merchant, name], "merchant": [merchant], "bank_text": [name]}[c.field]
        return _text_matches(c.op, c.values, texts)
    if c.field == "amount":
        size = abs(row["amount_cents"])
        if c.op == "over":
            return size > c.values[0]
        if c.op == "under":
            return size < c.values[0]
        if c.op == "between":
            return c.values[0] <= size <= c.values[1]
        return size == c.values[0]
    if c.field == "direction":                       # Plaid's sign: positive = money out
        return row["amount_cents"] < 0 if c.values[0] == "in" else row["amount_cents"] > 0
    if c.field == "date":
        if c.op == "on_or_after":
            return row["date"] >= c.values[0]
        if c.op == "on_or_before":
            return row["date"] <= c.values[0]
        return c.values[0] <= row["date"] <= c.values[1]
    if c.field == "category":
        key = row["plaid_category_key"] or "UNCATEGORIZED"
        found = key in c.values or parents.get(key) in c.values
        return not found if c.op == "not" else found
    if c.field == "account":
        found = row["account_id"] in c.values
        return not found if c.op == "not" else found
    return False


def matches(rule: Rule, row, parents: dict[str, str]) -> bool:
    return bool(rule.conditions) and all(condition_matches(c, row, parents) for c in rule.conditions)


@dataclass
class Decided:
    """What a list of rules decides for one transaction."""
    category: str | None = None
    category_by: int | None = None
    description: str | None = None
    description_by: int | None = None
    tags: set = field(default_factory=set)
    one_time: list = field(default_factory=list)      # (rule id, Action)
    matched: list = field(default_factory=list)       # rule ids


def decide(rules: list[Rule], row, parents: dict[str, str]) -> Decided:
    out = Decided()
    for rule in rules:
        if not matches(rule, row, parents):
            continue
        out.matched.append(rule.id)
        for a in rule.actions:
            if a.kind == "set_category" and out.category is None:
                out.category, out.category_by = a.value, rule.id
            elif a.kind == "set_description" and out.description is None:
                out.description, out.description_by = a.value, rule.id
            elif a.kind == "add_tag" and a.value.isdigit():
                out.tags.add(int(a.value))
            elif a.kind in ONE_TIME:
                out.one_time.append((rule.id, a))
    return out


_ROW_SQL = """SELECT id, account_id, date, amount_cents, name, merchant_name, plaid_category_key, rule_category,
                     rule_category_by, rule_category_item, rule_description FROM transactions"""


# ---------------------------------------------------------------- recurring items' actions (user's request 2026-11-03)

def item_actions(conn: sqlite3.Connection) -> dict[int, tuple[int, list[Action]]]:
    """{transaction id: (item id, its actions)} for the transactions of each CONFIRMED recurring item that has actions
    (recurring.transaction_ids: your links, else matching by description, account, direction and an amount band's
    amount). A transaction two items claim goes to the older item."""
    from fintrack import recurring

    actions: dict[int, list[Action]] = {}
    for item_id, kind, value in conn.execute(
            """SELECT a.item_id, a.kind, a.value FROM recurring_actions a JOIN recurring_items r ON r.id = a.item_id
               WHERE r.status = 'confirmed' ORDER BY a.item_id, a.id"""):
        actions.setdefault(item_id, []).append(Action(kind, value))
    found: dict[int, tuple[int, list[Action]]] = {}
    for item in conn.execute(f"SELECT * FROM recurring_items WHERE id IN ({','.join('?' * len(actions))})"
                             " ORDER BY id", list(actions)).fetchall() if actions else []:
        for tid in recurring.transaction_ids(conn, item):
            found.setdefault(tid, (item["id"], actions[item["id"]]))
    return found


def _with_item(d: Decided, hit: tuple[int, list[Action]] | None) -> tuple[Decided, int | None, list]:
    """What the rules decided, with the transaction's recurring item on top: its category and description win over any
    rule's (an item is one payment, more specific than a rule); its tags add up. Returns (decided, the item that set the
    category, its one-time actions)."""
    if hit is None:
        return d, None, []
    item_id, actions = hit
    by_item, one_time = None, []
    for a in actions:
        if a.kind == "set_category":
            d.category, d.category_by, by_item = a.value, None, item_id
        elif a.kind == "set_description":
            d.description, d.description_by = a.value, None
        elif a.kind == "add_tag" and a.value.isdigit():
            d.tags.add(int(a.value))
        elif a.kind in ONE_TIME:
            one_time.append(a)
    return d, by_item, one_time


# ---------------------------------------------------------------- applying

def apply_all(conn: sqlite3.Connection) -> None:
    """Recompute what the rules and recurring items decide for every transaction and do what's new. Doesn't match
    transfers: `run` does."""
    rules, parents = load(conn), _parents(conn)
    items = item_actions(conn)
    known_tags = {r[0] for r in conn.execute("SELECT id FROM tags")}
    links: dict[int, dict[int, int]] = {}
    for tid, tag, from_rule in conn.execute("SELECT transaction_id, tag_id, from_rule FROM transaction_tags"):
        links.setdefault(tid, {})[tag] = from_rule
    removed = {tuple(r) for r in conn.execute("SELECT transaction_id, tag_id FROM rule_tag_removals")}
    done = {tuple(r) for r in conn.execute("SELECT rule_id, transaction_id, kind FROM rule_applied")}
    item_done = {tuple(r) for r in conn.execute("SELECT item_id, transaction_id, kind FROM recurring_applied")}
    with conn:
        for row in conn.execute(_ROW_SQL).fetchall():
            d, by_item, item_once = _with_item(decide(rules, row, parents), items.get(row["id"]))
            if ((d.category, d.category_by, by_item, d.description)
                    != (row["rule_category"], row["rule_category_by"], row["rule_category_item"], row["rule_description"])):
                conn.execute("""UPDATE transactions SET rule_category = ?, rule_category_by = ?, rule_category_item = ?,
                                    rule_description = ? WHERE id = ?""",
                             (d.category, d.category_by, by_item, d.description, row["id"]))
            for action in item_once:
                item_id = items[row["id"]][0]
                if action.kind in ("not_transfer", "accept") and (item_id, row["id"], action.kind) not in item_done:
                    column = "transfer_ignore" if action.kind == "not_transfer" else "category_reviewed"
                    conn.execute(f"UPDATE transactions SET {column} = 1 WHERE id = ?", (row["id"],))
                    conn.execute("INSERT INTO recurring_applied VALUES (?, ?, ?)", (item_id, row["id"], action.kind))

            have = links.get(row["id"], {})
            wanted = {t for t in d.tags if t in known_tags and (row["id"], t) not in removed}
            for tag in wanted - set(have):
                conn.execute("INSERT INTO transaction_tags (transaction_id, tag_id, from_rule) VALUES (?, ?, 1)", (row["id"], tag))
            for tag, from_rule in have.items():
                if from_rule and tag not in wanted:
                    conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ? AND tag_id = ?", (row["id"], tag))
            for rule_id, action in d.one_time:
                if action.kind in ("not_transfer", "accept") and (rule_id, row["id"], action.kind) not in done:
                    column = "transfer_ignore" if action.kind == "not_transfer" else "category_reviewed"
                    conn.execute(f"UPDATE transactions SET {column} = 1 WHERE id = ?", (row["id"],))
                    conn.execute("INSERT INTO rule_applied VALUES (?, ?, ?)", (rule_id, row["id"], action.kind))
    from fintrack import manual_transactions
    manual_transactions.sync_parts(conn)        # a charge's rewards pair follows its tags and category (2026-11-04)


def apply_links(conn: sqlite3.Connection) -> None:
    """The one-time recurring and purpose actions, once transfers are matched (only a transfer can have a purpose)."""
    from fintrack import purposes, recurring

    _item_purposes(conn)
    rules, parents = load(conn), _parents(conn)
    wanted = [r for r in rules if any(a.kind in ("recurring", "purpose") for a in r.actions)]
    if not wanted:
        return
    done = {tuple(r) for r in conn.execute("SELECT rule_id, transaction_id, kind FROM rule_applied")}
    for row in conn.execute(_ROW_SQL).fetchall():
        for rule_id, action in decide(wanted, row, parents).one_time:
            if action.kind not in ("recurring", "purpose") or (rule_id, row["id"], action.kind) in done:
                continue
            try:
                if action.kind == "recurring":
                    if conn.execute("SELECT 1 FROM recurring_links WHERE transaction_id = ?", (row["id"],)).fetchone():
                        continue                     # your own choice
                    recurring.set_link(conn, row["id"], None if action.value == NOT_RECURRING else int(action.value))
                else:
                    if conn.execute("SELECT 1 FROM purpose_links WHERE transaction_id = ? AND source = 'you'", (row["id"],)).fetchone():
                        continue
                    purposes.set_link(conn, row["id"], int(action.value))
            except (recurring.RecurringError, purposes.PurposeError, ValueError):
                continue                             # not a transfer (yet), or the item or account is gone: tried again later
            with conn:
                conn.execute("INSERT OR IGNORE INTO rule_applied VALUES (?, ?, ?)", (rule_id, row["id"], action.kind))


def _item_purposes(conn: sqlite3.Connection) -> None:
    """Recurring items' "Link to purpose", once per item and transaction, never over your own link; a transaction that
    isn't a transfer (yet) is tried again next time."""
    from fintrack import purposes

    done = {tuple(r) for r in conn.execute("SELECT item_id, transaction_id, kind FROM recurring_applied")}
    for tid, (item_id, actions) in item_actions(conn).items():
        for a in actions:
            if a.kind != "purpose" or (item_id, tid, "purpose") in done or not a.value.isdigit():
                continue
            if conn.execute("SELECT 1 FROM purpose_links WHERE transaction_id = ? AND source = 'you'", (tid,)).fetchone():
                continue
            try:
                purposes.set_link(conn, tid, int(a.value))
            except (purposes.PurposeError, ValueError):
                continue
            with conn:
                conn.execute("INSERT OR IGNORE INTO recurring_applied VALUES (?, ?, ?)", (item_id, tid, "purpose"))


def run(conn: sqlite3.Connection) -> None:
    """Everything a rule change sets off: apply, match transfers again (a category can change a pair), do the links."""
    from fintrack import purposes, transfers

    apply_all(conn)
    transfers.detect_transfers(conn)
    apply_links(conn)
    purposes.update(conn)


# ---------------------------------------------------------------- saving

def save(conn: sqlite3.Connection, rule_id: int | None, conditions: list[Condition], actions: list[Action]) -> int:
    """Add a rule (runs last) or replace one's conditions and actions, then apply. Returns its id."""
    if not conditions:
        raise ValueError("A rule needs at least one condition.")
    if not actions:
        raise ValueError("A rule needs at least one action.")
    with conn:
        if rule_id is None:
            position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM rules").fetchone()[0]
            rule_id = conn.execute("INSERT INTO rules (position) VALUES (?)", (position,)).lastrowid
        elif conn.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone() is None:
            raise ValueError("That rule no longer exists.")
        conn.execute("DELETE FROM rule_conditions WHERE rule_id = ?", (rule_id,))
        conn.execute("DELETE FROM rule_actions WHERE rule_id = ?", (rule_id,))
        conn.executemany('INSERT INTO rule_conditions (rule_id, field, op, "values") VALUES (?, ?, ?, ?)',
                         [(rule_id, c.field, c.op, json.dumps(c.values)) for c in conditions])
        conn.executemany("INSERT INTO rule_actions (rule_id, kind, value) VALUES (?, ?, ?)",
                         [(rule_id, a.kind, a.value) for a in actions])
    run(conn)
    return rule_id


def delete(conn: sqlite3.Connection, rule_id: int, keep: bool = False) -> None:
    """Delete a rule (the user chooses, 2026-11-02). Back to original: its description, category and tags leave its
    transactions and its one-time actions are undone. `keep`: what it decided becomes your own (your description, your category,
    your tag), so it stays; its one-time actions stay as they are."""
    from fintrack import recurring

    rule = next((r for r in load(conn) if r.id == rule_id), None)
    if rule is None:
        return
    if keep:
        _keep_results(conn, rule)
    else:
        action_of = {a.kind: a.value for a in rule.actions}
        with conn:
            for tid, kind in conn.execute("SELECT transaction_id, kind FROM rule_applied WHERE rule_id = ?", (rule_id,)).fetchall():
                value = action_of.get(kind, "")
                if kind == "not_transfer":
                    conn.execute("UPDATE transactions SET transfer_ignore = 0 WHERE id = ?", (tid,))
                elif kind == "accept":
                    conn.execute("UPDATE transactions SET category_reviewed = 0 WHERE id = ?", (tid,))
                elif kind == "purpose" and value.isdigit():
                    conn.execute("DELETE FROM purpose_links WHERE transaction_id = ? AND account_id = ?", (tid, int(value)))
                elif kind == "recurring":
                    item = None if value == NOT_RECURRING else (int(value) if value.isdigit() else -1)
                    conn.execute("DELETE FROM recurring_links WHERE transaction_id = ? AND item_id IS ?", (tid, item))
    with conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    run(conn)
    recurring.refresh(conn)


def _keep_results(conn: sqlite3.Connection, rule: Rule) -> None:
    """What this rule decided, as your own choices. A category or description it set only if it's the rule that won; its tags
    stop being a rule's."""
    every, parents = load(conn), _parents(conn)
    tag_ids = [int(a.value) for a in rule.actions if a.kind == "add_tag" and a.value.isdigit()]
    rows = conn.execute("""SELECT id, account_id, date, amount_cents, name, merchant_name, plaid_category_key, user_category,
                                  user_description FROM transactions""").fetchall()
    with conn:
        for row in rows:
            if not matches(rule, row, parents):
                continue
            d = decide(every, row, parents)
            if d.category_by == rule.id and row["user_category"] is None:
                conn.execute("UPDATE transactions SET user_category = ? WHERE id = ?", (d.category, row["id"]))
            if d.description_by == rule.id and not row["user_description"]:
                conn.execute("UPDATE transactions SET user_description = ? WHERE id = ?", (d.description, row["id"]))
            for tag in tag_ids:
                conn.execute("UPDATE transaction_tags SET from_rule = 0 WHERE transaction_id = ? AND tag_id = ?", (row["id"], tag))


# ---------------------------------------------------------------- is it like a rule you already have? (user's request 2026-11-02)

def _same_values(c: Condition) -> tuple:
    return (c.field, c.op, tuple(sorted(str(v).casefold() for v in c.values)))


def _actions_key(actions: list[Action]) -> list:
    return sorted((a.kind, a.value) for a in actions)


@dataclass
class Similar:
    """An existing rule a new one duplicates. `new_phrases`: what it would add; none = that rule already covers it."""
    rule: Rule
    text_field: str | None = None
    new_phrases: list = field(default_factory=list)


def find_similar(conn: sqlite3.Connection, conditions: list[Condition], actions: list[Action]) -> Similar | None:
    """The first rule with the same actions and the same conditions, apart from the keywords of one "contains": the new rule
    then only adds keywords (or adds nothing). A rule whose other conditions or actions differ does something else."""
    for rule in load(conn):
        if _actions_key(rule.actions) != _actions_key(actions) or len(rule.conditions) != len(conditions):
            continue
        theirs, ours = [_same_values(c) for c in rule.conditions], [_same_values(c) for c in conditions]
        if sorted(theirs) == sorted(ours):
            return Similar(rule)
        left = [c for c in rule.conditions if _same_values(c) not in ours]
        new = [c for c in conditions if _same_values(c) not in theirs]
        if len(left) == len(new) == 1 and left[0].op == new[0].op == "contains" and left[0].field == new[0].field:
            have = {str(v).casefold() for v in left[0].values}
            return Similar(rule, new[0].field, [v for v in new[0].values if str(v).casefold() not in have])
    return None


def update_keywords(conn: sqlite3.Connection, rule_id: int, category_key: str, text: str) -> None:
    """A keywords row edited in place: its category and keywords only; what it reads stays. Separated by new lines, | or the
    row's own dots."""
    rule = next((r for r in load(conn) if r.id == rule_id), None)
    if rule is None or not is_keyword(rule):
        raise ValueError("That rule no longer exists.")
    if not re.sub(r"[·|\s]", "", text or ""):
        raise ValueError("Enter at least one keyword. To remove this row, use the trash icon.")
    condition = parse_condition(conn, rule.conditions[0].field, "contains", [text.replace("·", "\n")])
    save(conn, rule_id, [condition], [parse_action(conn, "set_category", category_key)])


def add_keywords(conn: sqlite3.Connection, similar: Similar) -> int:
    """Add the new rule's keywords to the existing rule instead of creating one."""
    conditions = [Condition(c.field, c.op, _in_order(c.field, [*c.values, *similar.new_phrases]))
                  if c.field == similar.text_field and c.op == "contains" else c for c in similar.rule.conditions]
    return save(conn, similar.rule.id, conditions, similar.rule.actions)


def reorder(conn: sqlite3.Connection, ids: list[int]) -> None:
    """The order you dragged them into (any left out keep their order, after these)."""
    every = [r[0] for r in conn.execute("SELECT id FROM rules ORDER BY position, id")]
    order = [i for i in ids if i in every] + [i for i in every if i not in ids]
    with conn:
        conn.executemany("UPDATE rules SET position = ? WHERE id = ? AND position != ?",
                         [(n, i, n) for n, i in enumerate(order, 1)])
    run(conn)


def move(conn: sqlite3.Connection, rule_id: int, step: int) -> None:
    """One place up (-1) or down (+1) among the custom rules (keywords have no order of their own to set)."""
    every = [r.id for r in load(conn) if not is_keyword(r)]
    if rule_id in every:
        at = every.index(rule_id)
        to = at + step
        if 0 <= to < len(every):
            every[at], every[to] = every[to], every[at]
            reorder(conn, every)


def add_simple(conn: sqlite3.Connection, phrase: str, category_key: str, field_name: str = "description") -> int:
    """"Description contains this, set category to that": the commonest rule."""
    return save(conn, None, [parse_condition(conn, field_name, "contains", [phrase])],
                [parse_action(conn, "set_category", category_key)])


# ---------------------------------------------------------------- from the form

def _cents(text: str) -> int:
    try:
        amount = Decimal(str(text).replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError("Amounts need to be numbers, like 42.50.") from None
    if amount < 0:
        raise ValueError("Enter an amount without a minus; use Direction for money in or out.")
    return to_cents(amount)


def _iso(text: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(text).strip()):
        raise ValueError("Choose a date.")
    return str(text).strip()


def parse_condition(conn: sqlite3.Connection, field_name: str, op: str, values: list) -> Condition:
    """A condition from the form's text, checked. Text splits on new lines and | (either way, "Uber" and "Lyft" = either)."""
    if field_name not in OPS or op not in OPS[field_name]:
        raise ValueError("Choose what each condition checks.")
    raw = [str(v).strip() for v in values if str(v).strip()]
    if field_name in ("description", "merchant", "bank_text"):
        phrases = [p.strip() for v in raw for p in re.split(r"[|\r\n]+", v) if p.strip()]
        if not phrases:
            raise ValueError("Enter the text for each description condition.")
        return Condition(field_name, op, _in_order(field_name, phrases))
    if field_name in ("amount", "date"):
        needed = 2 if op == "between" else 1
        if len(raw) < needed:
            raise ValueError("Fill in both ends of a between." if needed == 2 else f"Enter a value for each {field_name} condition.")
        parse = _cents if field_name == "amount" else _iso
        found = [parse(v) for v in raw[:needed]]
        return Condition(field_name, op, sorted(found))
    if field_name == "direction":
        if raw[:1] not in (["in"], ["out"]):
            raise ValueError("Choose money in or money out.")
        return Condition(field_name, op, raw[:1])
    if not raw:
        raise ValueError(f"Choose at least one {field_name} in each {field_name} condition.")
    if field_name == "category":
        known = {r[0] for r in conn.execute("SELECT key FROM categories")}
        if not set(raw) <= known:
            raise ValueError("That category no longer exists.")
        return Condition(field_name, op, raw)
    known = {r[0] for r in conn.execute("SELECT id FROM accounts")}
    ids = [int(v) for v in raw if v.isdigit()]
    if not ids or not set(ids) <= known:
        raise ValueError("That account no longer exists.")
    return Condition(field_name, op, ids)


def parse_action(conn: sqlite3.Connection, kind: str, value: str) -> Action:
    """An action from the form's text, checked. A tag is made if it's new, so this can write."""
    from fintrack import tags

    value = (value or "").strip()
    if kind == "set_description":
        if not value:
            raise ValueError("Enter the description to set.")
        return Action(kind, " ".join(value.split())[:120])     # one line, whatever the box's line breaks
    if kind == "set_category":
        if not conn.execute("SELECT 1 FROM categories WHERE key = ?", (value,)).fetchone():
            raise ValueError("Choose the category to set.")
        return Action(kind, value)
    if kind == "add_tag":
        try:
            return Action(kind, str(tags.find_or_create(conn, value).id))
        except tags.TagError as exc:
            raise ValueError(str(exc)) from None
    if kind == "recurring":
        if value != NOT_RECURRING and not value.isdigit():
            raise ValueError("Choose a recurring item.")
        return Action(kind, value)
    if kind == "purpose":
        if not value.isdigit():
            raise ValueError("Choose a purpose account.")
        return Action(kind, value)
    if kind in ("not_transfer", "accept"):
        return Action(kind)
    raise ValueError("Choose what each action does.")


def from_form(conn: sqlite3.Connection, form, *, preview: bool = False) -> tuple[list[Condition], list[Action]]:
    """The builder's rows: cond-<n>-field / -op / -value (repeated), act-<n>-kind / -value. In a preview a half-filled row is
    skipped; on save it's an error."""
    def numbers(prefix: str) -> list[int]:
        return sorted({int(m.group(1)) for k in form.keys() if (m := re.fullmatch(prefix + r"-(\d+)-(?:field|kind)", k))})

    conditions, actions = [], []
    for n in numbers("cond"):
        try:
            conditions.append(parse_condition(conn, form.get(f"cond-{n}-field", ""), form.get(f"cond-{n}-op", ""),
                                              form.getlist(f"cond-{n}-value")))
        except ValueError:
            if not preview:
                raise
    for n in numbers("act"):
        kind, value = form.get(f"act-{n}-kind", ""), form.get(f"act-{n}-value", "")
        if preview and kind == "add_tag":
            actions.append(Action(kind, value.strip()))   # its name: a preview makes no tags
            continue
        try:
            actions.append(parse_action(conn, kind, value))
        except ValueError:
            if not preview:
                raise
    return conditions, actions


# ---------------------------------------------------------------- showing

def labels(conn: sqlite3.Connection) -> dict:
    """Names for describing rules."""
    return {
        "category": {r[0]: (f"{r[2]} › {r[1]}" if r[2] else r[1]) for r in conn.execute(
            "SELECT c.key, c.label, p.label FROM categories c LEFT JOIN categories p ON p.key = c.parent_key")},
        "account": dict(conn.execute("SELECT id, name FROM accounts").fetchall()),
        "tag": {str(r[0]): r[1] for r in conn.execute("SELECT id, name FROM tags")},
        "recurring": {str(r[0]): r[1] for r in conn.execute("SELECT id, name FROM recurring_items")},
    }


def _or(items: list[str]) -> str:
    return " or ".join(items)


def describe_condition(c: Condition, names: dict) -> str:
    op = OPS.get(c.field, {}).get(c.op, c.op)
    label = FIELD_LABELS.get(c.field, c.field).lower()
    if c.field == "amount":
        ends = [format_cents(v) for v in c.values]
        return f"amount {op} {' and '.join(ends)}"
    if c.field == "direction":
        return "money in" if c.values[0] == "in" else "money out"
    if c.field == "date":
        return f"date {op} {' and '.join(c.values)}"
    if c.field in ("category", "account"):
        return f"{label} {op} " + _or(sorted((str(names[c.field].get(v, v)) for v in c.values), key=str.casefold))
    return f"{label} {op} " + _or([f"“{v}”" for v in _in_order(c.field, c.values)])


def describe_action(a: Action, names: dict) -> str:
    if a.kind == "set_description":
        return f"set description to “{a.value}”"
    if a.kind == "set_category":
        return f"set category to {names['category'].get(a.value, a.value)}"
    if a.kind == "add_tag":
        return f"tag {names['tag'].get(a.value, a.value or 'it')}"
    if a.kind == "recurring":
        return "mark not recurring" if a.value == NOT_RECURRING else f"add to {names['recurring'].get(a.value, 'a recurring item')}"
    if a.kind == "purpose":
        return "link to " + str(names["account"].get(int(a.value) if a.value.isdigit() else a.value, "a purpose account"))
    if a.kind == "not_transfer":
        return "mark as not a transfer"
    return "accept its category"


def listing(conn: sqlite3.Connection) -> list[dict]:
    """The Rules page: each rule, in order, with its conditions and actions in words and how many transactions it matches."""
    rules, parents, names = load(conn), _parents(conn), labels(conn)
    rows = conn.execute(_ROW_SQL).fetchall()
    return [{"id": r.id, "is_keyword": is_keyword(r), "conditions": [describe_condition(c, names) for c in r.conditions],
             # a keyword rule's own wording: its category, its keywords, and which text it reads when not the usual
             "category": names["category"].get(r.actions[0].value, r.actions[0].value) if is_keyword(r) else "",
             "keywords": _in_order(r.conditions[0].field, r.conditions[0].values) if is_keyword(r) else [],
             "only_in": FIELD_LABELS[r.conditions[0].field] if is_keyword(r) and r.conditions[0].field != "description" else "",
             "actions": [describe_action(a, names) for a in r.actions],
             # for the page's filters
             "fields": sorted({c.field for c in r.conditions}), "kinds": sorted({a.kind for a in r.actions}),
             "count": sum(1 for row in rows if matches(r, row, parents))} for r in rules]


@dataclass
class Preview:
    matches: int = 0
    changes: int = 0          # would show a different category or description
    yours: int = 0            # your own choice wins
    earlier: int = 0          # an earlier rule sets the same thing first
    samples: list = field(default_factory=list)


def preview(conn: sqlite3.Connection, conditions: list[Condition], actions: list[Action], rule_id: int | None = None,
            limit: int = 10) -> Preview:
    """What saving this rule would do, in its place among the others (a new one runs last)."""
    out = Preview()
    if not conditions:
        return out
    parents, names = _parents(conn), labels(conn)
    others = load(conn)
    items = item_actions(conn)          # a recurring item's category or description wins over any rule's
    place = next((r.position for r in others if r.id == rule_id), 10 ** 9)    # a new rule runs last of its kind
    probe = Rule(rule_id or 0, place, conditions, actions)
    with_probe = sorted([*(r for r in others if r.id != rule_id), probe], key=_run_order)
    rows = conn.execute(f"""SELECT t.id, t.account_id, t.date, t.amount_cents, t.name, t.merchant_name, t.plaid_category_key,
                                   t.user_category, t.user_description, e.description, e.category, e.category_label, e.account_hidden
                            FROM transactions t JOIN transactions_effective e ON e.id = t.id
                            ORDER BY t.date DESC, t.id DESC""").fetchall()
    for row in rows:
        if not matches(probe, row, parents):
            continue
        out.matches += 1
        d = decide(with_probe, row, parents)
        by_item = {a.kind for a in items.get(row["id"], (0, []))[1]}
        said = []
        for a in actions:
            if a.kind == "set_category":
                if row["user_category"] is not None:
                    out.yours += 1
                    said.append("keeps your category")
                elif "set_category" in by_item:
                    said.append("keeps its recurring item's category")
                elif d.category_by != probe.id:
                    out.earlier += 1
                    said.append("keeps an earlier rule's category")
                else:
                    said.append("→ " + names["category"].get(a.value, a.value))
                    if a.value != row["category"]:
                        out.changes += 1
            elif a.kind == "set_description":
                if row["user_description"]:
                    said.append("keeps your description")
                elif "set_description" in by_item:
                    said.append("keeps its recurring item's description")
                elif d.description_by != probe.id:
                    said.append("keeps an earlier rule's description")
                else:
                    said.append(f"“{a.value}”")
                    if a.value != row["description"]:
                        out.changes += 1
            else:
                said.append(describe_action(a, names))
        if len(out.samples) < limit and not row["account_hidden"]:
            out.samples.append({"row": row, "outcome": "; ".join(said)})
    return out


def blank_from_transaction(conn: sqlite3.Connection, transaction_id: int):
    """The builder prefilled from a transaction: its text contains this, set its category."""
    t = conn.execute("SELECT merchant_name, name, category FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()
    if t is None:
        return None
    return Rule(0, 0, [Condition("description", "contains", [t["merchant_name"] or t["name"]])],
                [Action("set_category", t["category"])])
