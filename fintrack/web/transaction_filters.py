"""The Transactions list's search, filters, date range and sort, as SQL, and the summary and chart data built from
the same filtered list (also used by an account's page, web/accounts.py)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import Request

from fintrack import account_types, authorized_users, categories, spending, tags
from fintrack.money import to_cents

# The page-wide toggle above the filters (user's request 2026-10-30; replaced the Kind dropdown). Transfers includes
# loan account entries (kind excluded); investment activity is in the list with the rest (investment_activity.
# as_transactions), so the old "Investment activity" kind is gone.
KIND_FILTERS = {"": "All", "income": "Income", "expense": "Spending", "transfer": "Transfers"}
# also taken from a link, not on the toggle: just the loan account entries (the Spending page's "N loan entries")
LOAN_KIND = "excluded"
# Sortable columns (milestone 14): heading -> ORDER BY. Only these strings ever reach SQL.
SORTS = {
    "date": "date {d}, id {d}",
    "description": "LOWER(description) {d}, date DESC, id DESC",
    "category": "LOWER(category_label) {d}, date DESC, id DESC",
    "account": "LOWER(account_name) {d}, date DESC, id DESC",
    "amount": "amount_cents {d}, date DESC, id DESC",     # Plaid sign: biggest money out first when desc
    # an account page's Card column (authorized users, 2026-10-29): the card's last 4 (no card = the account's own)
    "card": "COALESCE(account_owner, account_mask, '') {d}, date DESC, id DESC",
}
# Date range in the ribbon (user's requests 2026-10-08): key -> label, in dropdown order. "custom"
# is listed only once you've entered a date yourself. Default: 365 days ("All" was removed; a Custom
# range with both dates blank still shows everything).
# The date range choices and their dates are shared with the Spending page (spending.py).
RANGES = spending.RANGES
range_bounds = spending.range_bounds
DEFAULT_RANGE = "365"


# An amount term in the search box: "42.50", "$1,200", ">100", "<=20" (matches money in or out).
AMOUNT_TERM = re.compile(r"^(<=|>=|<|>)?\$?(\d[\d,]*(?:\.\d{1,2})?)$")


# A search term: "a quoted phrase" (one term, spaces and all) or a run of non-spaces; either may start with "-".
SEARCH_TERM = re.compile(r'-?"[^"]*"|\S+')


def parse_search(q: str) -> tuple[list[str], list[tuple[str, int]]]:
    """Split the search box into text words and amount conditions ((operator, cents)).
    Every text word must appear in the description, merchant, category or account name. A "quoted phrase" is one
    word; "-word" terms (excluded_words) are left out here."""
    words, amounts, _excluded, phrases = parse_search_terms(q)
    return words + phrases, amounts


def parse_search_terms(q: str) -> tuple[list[str], list[tuple[str, int]], list[str], list[str]]:
    """parse_search plus phrases and the words to leave out (user's request 2026-10-26, so the Credit cards rows can
    link to exactly their transactions; both look at the description you see, as reward rates do): "fake air"
    in quotes must appear in it, "-acme" or -"fake rail" must not. Returns (words, amounts, excluded, phrases);
    parse_search's words also hold the phrases, for callers that only search text."""
    words, amounts, excluded, phrases = [], [], [], []
    for term in SEARCH_TERM.findall(q):
        if term.startswith("-") and len(term) > 1:
            text = term[1:].strip('"').strip()
            if text:
                excluded.append(text)
            continue
        if term.startswith('"'):
            if term.strip('"').strip():
                phrases.append(term.strip('"').strip())
            continue
        m = AMOUNT_TERM.match(term)
        if m:
            try:
                cents = to_cents(Decimal(m.group(2).replace(",", "")))
            except InvalidOperation:
                words.append(term)
                continue
            amounts.append((m.group(1) or "=", cents))
        else:
            words.append(term)
    return words, amounts, excluded, phrases


_iso_or_blank = spending.iso_or_blank


def _filters(request: Request) -> dict[str, str]:
    return _filters_from(request.query_params)


def _filters_from(q) -> dict[str, str]:
    """The list's filters from query parameters (GET) or the group-edit form (POST).

    The date range: the ribbon dropdown (range; ui.js sets it to "custom" when you edit a date),
    else an old-style ?month=YYYY-MM link (Budgets, Spending) = Custom with that month's dates,
    else From/To dates given = Custom, else 365 days. Presets become From/To dates here."""
    picked = q.get("range") or ""
    start, end = _iso_or_blank(q.get("from", "")), _iso_or_blank(q.get("to", ""))
    month = q.get("month", "")
    if not picked and month:
        first, after = spending.month_bounds(spending.parse_month(month))
        picked, start = "custom", first
        end = (date.fromisoformat(after) - timedelta(days=1)).isoformat()
    if picked not in RANGES:
        picked = "custom" if (start or end) else DEFAULT_RANGE
    if picked != "custom":
        start, end = range_bounds(picked)
    return {
        "q": q.get("q", "").strip(),
        "range": picked,
        "account": _account_filter(q),
        "category": _category_filter(q),
        "kind": q.get("kind", "") if q.get("kind", "") in KIND_FILTERS or q.get("kind") == LOAN_KIND else "",
        "from": start,
        "to": end,
        "tag": _tag_filter(q),                         # your tags (tags.py, 2026-10-28), a checklist (2026-10-30)
        # a month (YYYY-MM) or day picked on the Money in and out chart, inside the date range (user's request 2026-10-26)
        "period": q.get("period", "") if PERIOD_PICK.fullmatch(q.get("period", "")) else "",
        # a flow picked on the Transfer flows chart (user's request 2026-10-31): "<from>><to>", each end an account
        # ("a<id>") or a category ("c<key>"), as visuals.transfer_flows keys its nodes
        "flow": q.get("flow", "") if FLOW_PICK.fullmatch(q.get("flow", "")) else "",
        # one description, exactly, picked on the list by description (user's request 2026-11-01)
        "description": q.get("description", "")[:200],
        # money in or out only, from a figure in the Summary (user's request 2026-11-01)
        "money": q.get("money", "") if q.get("money") in ("in", "out") else "",
        "sort": q.get("sort", "") if q.get("sort", "") in SORTS else "",
        "dir": "asc" if q.get("dir") == "asc" else "",
    }


PERIOD_PICK = re.compile(r"\d{4}-\d{2}(-\d{2})?")
FLOW_PICK = re.compile(r"(a\d+|c[\w-]+)>(a\d+|c[\w-]+)")


def _flow_where(value: str) -> tuple[str, list]:
    """SQL for a picked flow: the rows visuals.transfer_flows draws as it. Account → account = both halves of every
    matched pair between them (money out of the first, into the second; a half can be investment activity: its
    transfer_pair_id names a transaction, a transaction's names it negated). An account ↔ a category = that account's
    transfers with no match in that category, out of it (account → category) or into it (category → account)."""
    src, dst = value.split(">")
    if src[0] == "a" and dst[0] == "a":
        def paired_with(account_id: int) -> str:
            return ("(transfer_pair_id IN (SELECT id FROM transactions WHERE account_id = ?)"
                    " OR transfer_pair_id IN (SELECT -id FROM investment_transactions WHERE account_id = ?))")
        out_id, in_id = int(src[1:]), int(dst[1:])
        return (f"(is_transfer = 1 AND ((account_id = ? AND amount_cents > 0 AND {paired_with(in_id)})"
                f" OR (account_id = ? AND amount_cents < 0 AND {paired_with(out_id)})))",
                [out_id, in_id, in_id, in_id, out_id, out_id])
    unmatched = "(is_transfer = 0 OR transfer_pair_id IS NULL)"
    if src[0] == "a":                                  # money out, to its category
        return f"({unmatched} AND account_id = ? AND amount_cents > 0 AND category = ?)", [int(src[1:]), dst[1:]]
    if dst[0] == "a":                                  # money in, from its category
        return f"({unmatched} AND account_id = ? AND amount_cents < 0 AND category = ?)", [int(dst[1:]), src[1:]]
    return "0", []                                     # category to category: never drawn
# The account filter's values (a checklist, user's request 2026-10-29): "<id>" = the whole account; "<id>:main" = its
# primary card, "<id>:<last 4>" = an authorized user's card (authorized_users.py)
ACCOUNT_PICK = re.compile(r"\d+(:(main|[0-9A-Za-z]{2,4}))?")


def _account_filter(q) -> str:
    """The checked accounts and cards, comma-joined (repeated ?account= from the checklist, or one value holding
    several from a kept URL or group edit's hidden input); anything else is dropped."""
    raw = q.getlist("account") if hasattr(q, "getlist") else [q.get("account", "")]
    picked = {v.strip() for part in raw for v in str(part).split(",") if ACCOUNT_PICK.fullmatch(v.strip())}
    # a ticked account covers its cards' boxes, so the same choice is always the same value
    return ",".join(sorted(v for v in picked if ":" not in v or v.partition(":")[0] not in picked))


# The category filter's values (a checklist like the accounts', user's request 2026-10-29): "<key>" = the category with
# its subcategories; "<parent>:self" = the parent without them; "<parent>:<sub key>" = one of its subcategories
CATEGORY_PICK = re.compile(r"[0-9A-Za-z_]+(:[0-9A-Za-z_]+)?")


def _category_filter(q) -> str:
    """The checked categories, comma-joined, as `_account_filter`. An older link's ?uncat=1 is Uncategorized (the box
    it came from was removed, user's request 2026-10-30: the checklist has the category). Its ?only=1 (Credit cards,
    Tags) asks for its one category without the subcategories ("<key>:self"). A ticked category's own boxes below it
    are dropped (it covers them), so the same choice is always the same value."""
    raw = q.getlist("category") if hasattr(q, "getlist") else [q.get("category", "")]
    picked = {v.strip() for part in raw for v in str(part).split(",") if CATEGORY_PICK.fullmatch(v.strip())}
    if q.get("uncat") == "1":
        picked.add("UNCATEGORIZED")                 # an older link's Uncategorized box (removed 2026-10-30)
    if q.get("only") == "1" and len(picked) == 1 and ":" not in next(iter(picked)):
        picked = {next(iter(picked)) + ":self"}
    return ",".join(sorted(v for v in picked if ":" not in v or v.partition(":")[0] not in picked))


def _category_where(value: str) -> tuple[str, list]:
    """SQL for the category filter: any of the checked categories (a category includes its subcategories, as its count
    on Categories and its budget line do, 2026-10-25)."""
    ors, params = [], []
    for v in value.split(","):
        key, _, part = v.partition(":")
        if not part:
            ors.append("(category = ? OR category IN (SELECT key FROM categories WHERE parent_key = ?))")
            params += [key, key]
        else:
            ors.append("category = ?")
            params.append(key if part == "self" else part)
    return "(" + " OR ".join(ors) + ")", params


def _tag_filter(q) -> str:
    """The checked tags, comma-joined, as `_account_filter`: tag ids, and "none" = transactions without a tag (user's
    request 2026-10-30)."""
    raw = q.getlist("tag") if hasattr(q, "getlist") else [q.get("tag", "")]
    picked = {v.strip() for part in raw for v in str(part).split(",") if v.strip().isdigit() or v.strip() == "none"}
    return ",".join(sorted(picked))


def _tag_where(value: str) -> tuple[str, list]:
    """SQL for the tag filter: any of the checked tags, or no tag at all."""
    ids = [int(v) for v in value.split(",") if v.isdigit()]
    ors = []
    if ids:
        ors.append(f"id IN (SELECT transaction_id FROM {tags.LINKS_SQL} WHERE tag_id IN ({','.join('?' * len(ids))}))")
    if "none" in value.split(","):
        ors.append(f"id NOT IN (SELECT transaction_id FROM {tags.LINKS_SQL})")
    return "(" + " OR ".join(ors) + ")", ids


def _category_lit(picked: set[str], key: str, subs: list[str], parent: str | None = None) -> bool:
    """Whether the pie's slice for `key` (a category with `subs`, or a subcategory of `parent`) is in the category
    filter, so it keeps its colour while the rest dim."""
    if parent:
        return bool({parent, f"{parent}:{key}", key} & picked)
    return key in picked or any(v.startswith(key + ":") for v in picked) or bool(set(subs) & picked)


def _account_where(value: str) -> tuple[str, list]:
    """SQL for the account filter: any of the checked accounts or cards."""
    ors, params = [], []
    for v in value.split(","):
        account_id, _, card = v.partition(":")
        if not card:
            ors.append("account_id = ?")
            params.append(int(account_id))
        elif card == "main":
            ors.append(f"(account_id = ? AND NOT {authorized_users.AU_SQL})")
            params.append(int(account_id))
        else:
            ors.append("(account_id = ? AND account_owner = ?)")
            params += [int(account_id), card]
    return "(" + " OR ".join(ors) + ")", params


def _account_groups(conn, accounts, cards: dict, picked: set[str]) -> tuple[list[dict], str]:
    """The account checklist's groups (user's request 2026-10-30): visible connected accounts under their top-level
    account type (account_types.top_types; your type table's order, "Other" last), so one box ticks every credit card.
    Also the button's words: each fully ticked group, account or card by name (topmost only), joined; none = "All
    accounts" (ui.js writes the same once the page runs)."""
    top = account_types.top_types(conn)
    groups: dict[str, dict] = {}
    for a in accounts:
        position, name = top.get(a["id"], (10 ** 6, "Other"))
        groups.setdefault(name, {"name": name, "position": position, "key": f"g{len(groups)}", "accounts": []})
        groups[name]["accounts"].append(a)
    ordered = sorted(groups.values(), key=lambda g: (g["position"], g["name"].lower()))
    labels = []
    for g in ordered:
        g["picked"] = all(str(a["id"]) in picked for a in g["accounts"])
        if g["picked"]:
            labels.append(g["name"])
            continue
        for a in g["accounts"]:
            if str(a["id"]) in picked:
                labels.append(a["name"])
            else:
                labels += [a["name"] + (" (AU)" if c.au else "") for c in cards.get(a["id"], []) if c.value in picked]
    return ordered, ", ".join(labels) or "All accounts"


def _category_groups(rows, picked: set[str]) -> tuple[list[dict], str]:
    """The category checklist (user's requests 2026-10-29/30): the main groups (what categories count as, KIND_ORDER:
    Spending, Transfers, Income), each category under its group and its subcategories under it, in list_categories'
    order. Also the button's words: the topmost ticked boxes by name, joined; none = "All categories" (ui.js writes the
    same once the page runs). A group's box has no value of its own: it ticks its categories."""
    groups = {kind: {"kind": kind, "label": categories.KIND_LABELS[kind], "tree": []} for kind in categories.KIND_ORDER}
    subs_of = {}
    for c in rows:
        if c["parent_key"] and c["parent_key"] in subs_of:
            subs_of[c["parent_key"]].append(c)
        else:
            subs_of[c["key"]] = []
            groups.get(c["kind"], groups["expense"])["tree"].append((c, subs_of[c["key"]]))
    labels = []
    for g in groups.values():
        g["picked"] = bool(g["tree"]) and all(c["key"] in picked for c, _ in g["tree"])
        if g["picked"]:
            labels.append(g["label"])
            continue
        for c, subs in g["tree"]:
            if c["key"] in picked or f'{c["key"]}:self' in picked:
                labels.append(c["label"])
            if c["key"] not in picked:
                labels += [x["label"] for x in subs if f'{c["key"]}:{x["key"]}' in picked or x["key"] in picked]
    return [g for g in groups.values() if g["tree"]], ", ".join(labels) or "All categories"


def _range_days(conn, filters: dict[str, str]) -> int | None:
    """Days in the list's date range up to today (an open side = the first / last transaction's date); None for a
    range that hasn't started. The averages divide by this, not by the bars: a day or month without transactions
    counts too, and a clipped or unfinished month isn't a whole one."""
    start, end = filters["from"], filters["to"]
    if not start or not end:
        first, last = conn.execute(
            "SELECT MIN(date), MAX(date) FROM transactions_effective WHERE account_hidden = 0").fetchone()
        start, end = start or first, end or last
    if not start or not end:
        return None
    days = (date.fromisoformat(min(end, date.today().isoformat())) - date.fromisoformat(start)).days + 1
    return days if days >= 1 else None


def _per(by_day: bool) -> tuple[float, str]:
    """An average per day when the bars are days (This month, Last month, a short custom range), else per month."""
    return (1, "day") if by_day else (spending.AVG_DAYS_PER_MONTH, "month")


def _flow_average(conn, filters: dict[str, str], period: dict, by_day: bool) -> dict | None:
    """Money in and out's average for its card's corner (user's request 2026-10-30), over `_range_days`."""
    days = _range_days(conn, filters)
    if days is None:
        return None
    per, unit = _per(by_day)
    return {"in_cents": round(period["in_cents"] * per / days), "out_cents": round(period["out_cents"] * per / days),
            "per": unit}


def _widest(conn, source: tuple[str, list], where: str, params: list) -> dict | None:
    """The longest values in the whole filtered list, every page: the table's hidden sizing row reserves their width, so
    sorting or paging never moves the columns (user's request 2026-11-01). Only other filters can."""
    rows_sql, rows_params = source
    every = rows_params + params
    longest = lambda column: conn.execute(    # noqa: E731
        f"SELECT {column} FROM ({rows_sql}) WHERE {where} ORDER BY length({column}) DESC LIMIT 1", every).fetchone()
    account, category = longest("account_name"), longest("category_label")
    if account is None:
        return None
    first, largest = conn.execute(f"SELECT MIN(date), MAX(ABS(amount_cents)) FROM ({rows_sql}) WHERE {where}", every).fetchone()
    # a date in another year shows its year; November 30 stands for the widest month and day
    year = first[:4] if first[:4] != str(date.today().year) else str(date.today().year)
    has_au = conn.execute(f"SELECT 1 FROM ({rows_sql}) WHERE {where} AND {authorized_users.AU_SQL} LIMIT 1", every).fetchone()
    return {"account": account[0], "category": category[0], "date": f"{year}-11-30", "amount_cents": largest,
            "au": has_au is not None}


def _flow_label(conn, value: str) -> str:
    """A picked flow's ends by name, "From → To" ("" without one)."""
    if not value:
        return ""
    def name(end: str) -> str:
        if end[0] == "a":
            row = conn.execute("SELECT name FROM accounts WHERE id = ?", (int(end[1:]),)).fetchone()
        else:
            row = conn.execute("SELECT label FROM categories WHERE key = ?", (end[1:],)).fetchone()
        return row[0] if row else "?"
    return " → ".join(name(end) for end in value.split(">"))


def _summary(conn, source: tuple[str, list], where: str, params: list, kept: dict) -> list[dict]:
    """The All view's Summary card (user's requests 2026-10-31): the filtered rows by group (the page's toggle; loan
    entries with Transfers, as in that view), each with its count, its money in and its money out (both as sizes, in
    cents), its net (in - out) and a link to that view; then All, the sum of the three. Money in is e.g. income, refunds,
    transfers received; money out purchases, transfers sent. Each figure is a link (user's request 2026-11-01): In / Out =
    that group's money in / out (`money`; again on All's clears it), Net = the group's whole view; `where` leaves out
    the card's own `money` pick, so its figures stay put, like the charts'."""
    rows_sql, rows_params = source
    found = {k: {"count": 0, "in_cents": 0, "out_cents": 0} for k in ("income", "expense", "transfer")}
    for kind, n, money_in, money_out in conn.execute(
            f"""SELECT CASE WHEN kind = 'excluded' THEN 'transfer' ELSE kind END AS g, COUNT(*),
                       COALESCE(SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END), 0),   -- Plaid's sign:
                       COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0)     -- + = money out
                FROM ({rows_sql}) WHERE {where} GROUP BY g""", rows_params + params):
        if kind in found:
            found[kind] = {"count": n, "in_cents": money_in, "out_cents": money_out}
    base = {k: v for k, v in kept.items() if k not in ("kind", "page", "money")}       # the other filters stay
    picked = kept.get("money", "")

    def link(kind: str = "", money: str = "") -> str:
        return "/transactions?" + urlencode({**base, **({"kind": kind} if kind else {}), **({"money": money} if money else {})})
    groups = [{"kind": k, "label": KIND_FILTERS[k], "url": link(k), "in_url": link(k, "in"), "out_url": link(k, "out"),
               "net_url": link(k), **found[k]} for k in ("income", "expense", "transfer")]
    every = {key: sum(g[key] for g in groups) for key in ("count", "in_cents", "out_cents")}
    # All stays on this view: its In / Out toggles the pick, its Net clears one (nothing to do without one)
    everything = {"label": "All", "url": None, "in_url": link(money="" if picked == "in" else "in"),
                  "out_url": link(money="" if picked == "out" else "out"), "net_url": link() if picked else None,
                  "in_picked": picked == "in", "out_picked": picked == "out", **every}
    return [{**g, "net_cents": g["in_cents"] - g["out_cents"]} for g in (everything, *groups)]


def _balance(summary: list[dict]) -> dict:
    """The Summary card as a balance (user's request 2026-11-11: net, not in and out; something to see at a glance): each
    group's net on the side it falls - what came in (a positive net, e.g. income) and what went out (spending, transfers
    sent away) - as two bars on one scale, the gap between them being the list's net. Each group's `share` is of that
    scale (its segment's width) and `of_side` of its own side's total."""
    every, groups = summary[0], summary[1:]
    came_in = [g for g in groups if g["net_cents"] > 0]
    went_out = [g for g in groups if g["net_cents"] < 0]
    in_cents = sum(g["net_cents"] for g in came_in)
    out_cents = -sum(g["net_cents"] for g in went_out)
    scale = max(in_cents, out_cents, 1)

    def placed(g: dict, side_cents: int) -> dict:
        size = abs(g["net_cents"])
        return {**g, "size_cents": size, "share": size / scale, "of_side": round(size * 100 / side_cents) if side_cents else 0}
    # the net, drawn where it is: a dashed piece completing the shorter bar to the longer one's length
    gap = abs(in_cents - out_cents) / scale
    return {"net_cents": every["net_cents"], "count": every["count"], "in_cents": in_cents, "out_cents": out_cents,
            # a bar's total opens what moved money that way (?money=in / out, again to clear it)
            "in_url": every.get("in_url"), "out_url": every.get("out_url"),
            "gap_share": gap, "gap_on": "in" if out_cents > in_cents else "out" if in_cents > out_cents else None,
            "came_in": [placed(g, in_cents) for g in came_in], "went_out": [placed(g, out_cents) for g in went_out],
            # the key: every group with something in it, in its usual order; a group netting to $0 says so
            "key": [{**g, **(placed(g, in_cents if g["net_cents"] > 0 else out_cents) if g["net_cents"] else {"of_side": None}),
                     # a group alone on its side: no "100% of in" (it says nothing)
                     **({"of_side": None} if len(came_in if g["net_cents"] > 0 else went_out) == 1 else {})}
                    for g in groups if g["count"]]}


def _url_filters(filters: dict[str, str]) -> dict[str, str]:
    """Filters worth keeping in a URL: the range key (unless the default), From/To only for Custom."""
    keep = {k: v for k, v in filters.items() if v and k not in ("range", "from", "to")}
    if filters["range"] != DEFAULT_RANGE:
        keep["range"] = filters["range"]
    if filters["range"] == "custom":
        keep.update({k: filters[k] for k in ("from", "to") if filters[k]})
    return keep


def _short_range(filters: dict[str, str]) -> bool:
    if filters["range"] in ("this-month", "last-month"):
        return True
    if filters["from"] and filters["to"]:
        return (date.fromisoformat(filters["to"]) - date.fromisoformat(filters["from"])).days <= 62
    return False


def _query(filters: dict[str, str]) -> tuple[str, list]:
    """WHERE clause + params for the filters. All values go in as parameters."""
    where, params = ["account_hidden = 0"], []
    words, amounts, excluded, phrases = parse_search_terms(filters["q"])
    for word in words:
        # A literal % or _ in the search isn't a wildcard.
        like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(description LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR merchant_name LIKE ? ESCAPE '\\'"
                     " OR category_label LIKE ? ESCAPE '\\' OR account_name LIKE ? ESCAPE '\\')")
        params += [like] * 5
    # "a phrase" / "-word": the description you see (yours > merchant > bank text) does / doesn't contain it, as reward
    # rates match (a plain word above also searches the category and account names)
    for phrase, negate in [(p, False) for p in phrases] + [(w, True) for w in excluded]:
        where.append(f"description {'NOT ' if negate else ''}LIKE ? ESCAPE '\\'")
        params.append("%" + phrase.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    for operator, cents in amounts:
        where.append(f"ABS(amount_cents) {operator} ?")      # operator from AMOUNT_TERM only
        params.append(cents)
    if filters["from"]:
        where.append("date >= ?")
        params.append(filters["from"])
    if filters["to"]:
        where.append("date <= ?")
        params.append(filters["to"])
    if filters.get("period"):
        where.append("substr(date, 1, ?) = ?")               # the picked month or day
        params += [len(filters["period"]), filters["period"]]
    if filters["account"]:
        clause, values = _account_where(filters["account"])
        where.append(clause)
        params += values
    if filters["category"]:
        clause, values = _category_where(filters["category"])
        where.append(clause)
        params += values
    if filters["kind"] == "transfer":
        where.append("kind IN ('transfer', 'excluded')")          # Transfers: loan account entries too
    elif filters["kind"]:
        where.append("kind = ?")
        params.append(filters["kind"])
    if filters.get("tag"):
        clause, values = _tag_where(filters["tag"])
        where.append(clause)
        params += values
    if filters.get("flow"):
        clause, values = _flow_where(filters["flow"])
        where.append(clause)
        params += values
    if filters.get("description"):
        where.append("description = ?")
        params.append(filters["description"])
    if filters.get("money"):
        where.append("amount_cents < 0" if filters["money"] == "in" else "amount_cents > 0")     # Plaid's sign
    return " AND ".join(where), params
