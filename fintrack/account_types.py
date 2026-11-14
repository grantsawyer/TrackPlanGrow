"""Your account types (user's request 2026-10-13, migration 0032): a two-level table of types and subtypes you can add
to, rename, reorder, move (a subtype to another type) and delete, starting from a default hierarchy.

An account can be given a type or a subtype (on its Balances account page); with none it is "automatic": it keeps what
Plaid (or its manual kind, or its account category) says. A choice:

* is stored as `accounts.type_node_id`, with `type_kind` (its type's KINDS key) and `type_label` (its own name) kept in
  step here whenever a type is renamed, moved or deleted;
* decides the account's effective `type` / `classification` (asset or liability), written by _apply (which retakes
  today's snapshot or rebuilds a manual account's history); syncs then leave them alone (items.upsert_accounts);
* decides retirement vs investment and the label charts show (visuals.is_retirement_account / investment_type_label),
  and so its Balances / Net worth card.

Removing a choice puts a Plaid account back to Plaid's type; a manual account keeps the type it had. Types replaced
account categories (removed 2026-10-13, user's request).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

NAME_MAX = 40


@dataclass(frozen=True)
class Kind:
    label: str
    account_type: str           # accounts.type it gives an account
    classification: str


# A type's behaviour. Order = the "add a type" menu's order.
KINDS = {
    "cash": Kind("Cash", "depository", "asset"),
    "retirement": Kind("Retirement", "investment", "asset"),
    "investment": Kind("Investment", "investment", "asset"),
    "lent": Kind("Money lent", "receivable", "asset"),
    "property": Kind("Property", "property", "asset"),
    "other_asset": Kind("Other asset", "other", "asset"),
    "credit": Kind("Credit card", "credit", "liability"),
    "loan": Kind("Loan", "loan", "liability"),
    "other_liability": Kind("Other liability", "other", "liability"),
}


class TypeError_(ValueError):
    """A change to the type table was refused; the message is shown to you."""


@dataclass
class Node:
    id: int
    name: str
    kind: str | None
    parent_id: int | None
    position: int
    children: list[Node] = field(default_factory=list)
    accounts: int = 0                 # accounts given exactly this node (what a delete sends back to automatic)
    automatic: int = 0                # visible accounts with no choice whose automatic match is this node

    @property
    def shown(self) -> int:
        """Visible accounts on this node: given it, or matched automatically (the Account types page's count)."""
        return self.visible + self.automatic

    visible: int = 0                  # visible accounts given exactly this node


def tree(conn: sqlite3.Connection, with_automatic: bool = False) -> list[Node]:
    """Types in order, each with its subtypes in order and how many accounts use each node (`with_automatic`: also
    how many visible accounts without a choice match it automatically)."""
    used = dict(conn.execute("SELECT type_node_id, COUNT(*) FROM accounts WHERE type_node_id IS NOT NULL"
                             " GROUP BY type_node_id").fetchall())
    visible = dict(conn.execute("SELECT type_node_id, COUNT(*) FROM accounts WHERE type_node_id IS NOT NULL"
                                " AND is_hidden = 0 GROUP BY type_node_id").fetchall())
    nodes = {r["id"]: Node(r["id"], r["name"], r["kind"], r["parent_id"], r["position"], accounts=used.get(r["id"], 0),
                           visible=visible.get(r["id"], 0))
             for r in conn.execute("SELECT * FROM account_type_nodes ORDER BY position, id")}
    types = []
    for n in nodes.values():
        (nodes[n.parent_id].children if n.parent_id else types).append(n)
    if with_automatic:
        # accounts without a choice count where their automatic match is (user's request 2026-10-13)
        for a in conn.execute("SELECT * FROM accounts WHERE type_node_id IS NULL AND is_hidden = 0"):
            match = automatic(conn, a, types)
            if match in nodes:
                nodes[match].automatic += 1
    return types


def path(conn: sqlite3.Connection, node_id: int | None) -> str:
    """'Retirement › IRA' (a subtype) or 'Retirement' (a type); '' for none."""
    if node_id is None:
        return ""
    row = conn.execute("""SELECT n.name, p.name AS parent FROM account_type_nodes n
                          LEFT JOIN account_type_nodes p ON p.id = n.parent_id WHERE n.id = ?""", (node_id,)).fetchone()
    if row is None:
        return ""
    return f"{row['parent']} › {row['name']}" if row["parent"] else row["name"]


def _node(conn: sqlite3.Connection, node_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM account_type_nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise TypeError_("That type no longer exists.")
    return row


def _clean_name(conn: sqlite3.Connection, name: str, parent_id: int | None, node_id: int | None = None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TypeError_("Enter a name.")
    if len(name) > NAME_MAX:
        raise TypeError_(f"Keep names to {NAME_MAX} characters.")
    clash = conn.execute("SELECT id FROM account_type_nodes WHERE lower(name) = lower(?) AND parent_id IS ?"
                         " AND id IS NOT ?", (name, parent_id, node_id)).fetchone()
    if clash:
        raise TypeError_(f"“{name}” already exists {'there' if parent_id else 'as a type'}.")
    return name


def _next_position(conn: sqlite3.Connection, parent_id: int | None) -> int:
    return conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM account_type_nodes WHERE parent_id IS ?",
                        (parent_id,)).fetchone()[0]


# ---------------------------------------------------------------- changes to the table

def add_type(conn: sqlite3.Connection, name: str, kind: str) -> int:
    if kind not in KINDS:
        raise TypeError_("Choose what kind of account it is.")
    name = _clean_name(conn, name, None)
    with conn:
        cur = conn.execute("INSERT INTO account_type_nodes (parent_id, name, kind, position) VALUES (NULL, ?, ?, ?)",
                           (name, kind, _next_position(conn, None)))
    return cur.lastrowid


def add_subtype(conn: sqlite3.Connection, parent_id: int, name: str) -> int:
    parent = _node(conn, parent_id)
    if parent["parent_id"] is not None:
        raise TypeError_("Subtypes go under a type.")
    name = _clean_name(conn, name, parent_id)
    with conn:
        cur = conn.execute("INSERT INTO account_type_nodes (parent_id, name, kind, position) VALUES (?, ?, NULL, ?)",
                           (parent_id, name, _next_position(conn, parent_id)))
    return cur.lastrowid


def rename(conn: sqlite3.Connection, node_id: int, name: str) -> None:
    node = _node(conn, node_id)
    name = _clean_name(conn, name, node["parent_id"], node_id)
    with conn:
        conn.execute("UPDATE account_type_nodes SET name = ? WHERE id = ?", (name, node_id))
    _refresh_accounts(conn, _subtree(conn, node_id))


def set_kind(conn: sqlite3.Connection, type_id: int, kind: str) -> None:
    """Change a type's behaviour (asset / liability, which card): its accounts follow."""
    node = _node(conn, type_id)
    if node["parent_id"] is not None or kind not in KINDS:
        raise TypeError_("Choose what kind of account it is.")
    with conn:
        conn.execute("UPDATE account_type_nodes SET kind = ? WHERE id = ?", (kind, type_id))
    _refresh_accounts(conn, _subtree(conn, type_id))


def move(conn: sqlite3.Connection, node_id: int, direction: str) -> None:
    """Up or down among its siblings."""
    node = _node(conn, node_id)
    siblings = [r["id"] for r in conn.execute("SELECT id FROM account_type_nodes WHERE parent_id IS ? ORDER BY position, id",
                                              (node["parent_id"],))]
    i = siblings.index(node_id)
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(siblings):
        return
    siblings[i], siblings[j] = siblings[j], siblings[i]
    with conn:
        for position, sibling in enumerate(siblings, start=1):
            conn.execute("UPDATE account_type_nodes SET position = ? WHERE id = ?", (position, sibling))


def move_to(conn: sqlite3.Connection, subtype_id: int, new_parent_id: int) -> None:
    """Move a subtype under another type (last there); its accounts take that type's behaviour."""
    node = _node(conn, subtype_id)
    if node["parent_id"] is None:
        raise TypeError_("Only subtypes move to another type.")
    parent = _node(conn, new_parent_id)
    if parent["parent_id"] is not None:
        raise TypeError_("Subtypes go under a type.")
    if parent["id"] == node["parent_id"]:
        return
    _clean_name(conn, node["name"], new_parent_id, subtype_id)
    with conn:
        conn.execute("UPDATE account_type_nodes SET parent_id = ?, position = ? WHERE id = ?",
                     (new_parent_id, _next_position(conn, new_parent_id), subtype_id))
    _refresh_accounts(conn, [subtype_id])


def delete(conn: sqlite3.Connection, node_id: int) -> int:
    """Delete a type (with its subtypes) or a subtype. Accounts given any of them go back to automatic. Returns how
    many accounts did."""
    nodes = _subtree(conn, _node(conn, node_id)["id"])
    accounts = _accounts_of(conn, nodes)
    with conn:
        conn.executemany("UPDATE accounts SET type_node_id = NULL, type_kind = NULL, type_label = NULL WHERE id = ?",
                         [(a,) for a in accounts])
        conn.execute("DELETE FROM account_type_nodes WHERE id = ?", (node_id,))
    _apply(conn, accounts)
    return len(accounts)


# ---------------------------------------------------------------- accounts

def assign(conn: sqlite3.Connection, account_id: int, node_id: int | None) -> None:
    """Give an account a type or subtype (None = automatic)."""
    if node_id is not None:
        _node(conn, node_id)
    if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
        raise TypeError_("That account no longer exists.")
    with conn:
        conn.execute("UPDATE accounts SET type_node_id = ? WHERE id = ?", (node_id, account_id))
    _refresh_accounts(conn, [], [account_id])


def _subtree(conn: sqlite3.Connection, node_id: int) -> list[int]:
    return [node_id] + [r[0] for r in conn.execute("SELECT id FROM account_type_nodes WHERE parent_id = ?", (node_id,))]


def _accounts_of(conn: sqlite3.Connection, node_ids: list[int]) -> list[int]:
    if not node_ids:
        return []
    return [r[0] for r in conn.execute(f"SELECT id FROM accounts WHERE type_node_id IN ({','.join('?' * len(node_ids))})",
                                       node_ids)]


def _refresh_accounts(conn: sqlite3.Connection, node_ids: list[int], account_ids: list[int] | None = None) -> None:
    """Write type_kind / type_label for the accounts given these nodes (or these accounts), then their effective type."""
    accounts = sorted(set(_accounts_of(conn, node_ids)) | set(account_ids or []))
    with conn:
        for account_id in accounts:
            row = conn.execute(
                """SELECT n.name, COALESCE(n.kind, p.kind) AS kind FROM accounts a
                   JOIN account_type_nodes n ON n.id = a.type_node_id
                   LEFT JOIN account_type_nodes p ON p.id = n.parent_id WHERE a.id = ?""", (account_id,)).fetchone()
            conn.execute("UPDATE accounts SET type_kind = ?, type_label = ? WHERE id = ?",
                         (row["kind"] if row else None, row["name"] if row else None, account_id))
    _apply(conn, accounts)


def _effective(account: sqlite3.Row) -> tuple[str, str]:
    """(type, classification) an account should have: the type you gave it, else Plaid's (a manual account: its own)."""
    from fintrack import items                            # items imports modules that import this one

    if account["type_kind"] in KINDS:
        return KINDS[account["type_kind"]].account_type, KINDS[account["type_kind"]].classification
    if not account["is_manual"]:
        plaid_type = account["plaid_type"] or account["type"]
        return plaid_type, items.classify(plaid_type)
    return account["type"], account["classification"]   # manual: keeps its current kind


def _apply(conn: sqlite3.Connection, account_ids: list[int]) -> None:
    """Write the effective type of these accounts, then refresh what depends on it: a manual account rebuilds its
    history (manual._refresh); a Plaid account's past snapshots keep their recorded type, today's is retaken.
    (Moved here from account_categories.py when categories were removed, 2026-10-13.)"""
    from fintrack import manual, networth

    changed_manual, changed_plaid = [], False
    with conn:
        for account_id in account_ids:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                continue
            account_type, classification = _effective(row)
            if (account_type, classification) == (row["type"], row["classification"]):
                continue
            conn.execute("UPDATE accounts SET type = ?, classification = ? WHERE id = ?",
                         (account_type, classification, account_id))
            if row["is_manual"]:
                changed_manual.append(account_id)
            else:
                changed_plaid = True
    for account_id in changed_manual:
        manual._refresh(conn, account_id)                 # rebuilds its history + retakes today
    if changed_plaid and not changed_manual:
        networth.take_snapshot(conn)                      # past days keep their recorded type


def automatic(conn: sqlite3.Connection, account: sqlite3.Row, types: list[Node] | None = None) -> int | None:
    """The table's entry that matches what an account is without a choice (display only): the subtype whose name
    matches its Plaid subtype (or its account category's name), under a type of its kind; else that type."""
    from fintrack import visuals

    if account["type"] == "investment":
        kind = "retirement" if visuals.is_retirement_account({"subtype": account["subtype"]}) else "investment"
    else:
        by_type = {(k.account_type, k.classification): key for key, k in KINDS.items() if key not in ("retirement",)}
        kind = by_type.get((account["type"], account["classification"]))
    if kind is None:
        return None
    wanted = visuals._type_key(visuals.SUBTYPE_LABELS.get((account["subtype"] or "").lower(), account["subtype"] or ""))
    best = None
    for t in (types if types is not None else tree(conn)):
        if t.kind != kind:
            continue
        for s in t.children:
            if wanted and visuals._type_key(s.name) == wanted:
                return s.id
        best = best or t.id
    return best


def top_types(conn: sqlite3.Connection) -> dict[int, tuple[int, str]]:
    """{account id: (its top-level type's position, name)}: the type you gave it (a subtype's type), else its automatic
    match's type. The Overview's Accounts card groups by it (user's request 2026-10-13, replacing account categories)."""
    types = tree(conn)
    top = {t.id: (t.position, t.name) for t in types}
    top.update({c.id: (t.position, t.name) for t in types for c in t.children})
    result = {}
    for a in conn.execute("SELECT * FROM accounts"):
        node = a["type_node_id"] if a["type_node_id"] in top else automatic(conn, a, types)
        if node in top:
            result[a["id"]] = top[node]
    return result
