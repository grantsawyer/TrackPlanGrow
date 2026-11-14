"""Data for the charts (milestone 13).

Each function returns plain dicts/lists ready to embed as JSON for static/charts.js.
Money stays integer cents in here; `dollars()` converts once, at the edge, because
the charts plot dollars. Every chart reuses the module that owns its numbers
(budgets, recurring, savings, networth, forecast), so a chart can never disagree
with the table next to it.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from fintrack import contributions, networth, recurring
from fintrack.budgets import MonthBudget
from fintrack.spending import average_per_month_cents, month_bounds, shift_month


def dollars(cents: int | float | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


# ---------------------------------------------------------------- net worth

def networth_trend(conn: sqlite3.Connection, days: int = 365) -> list[dict]:
    """Daily net worth for up to the last `days` days (the page's 90/180/365 buttons
    slice this client-side). Carried days (no sync) are flagged for dashing."""
    points = networth.history(conn)[-days:]
    return [{"d": p.date, "v": dollars(p.net_cents), "assets": dollars(p.assets_cents),
             "liabilities": dollars(p.liabilities_cents), "carried": p.carried} for p in points]


def networth_trend_range(conn: sqlite3.Connection, start: str, end: str, show: str = "value",
                         today: date | None = None) -> list[dict]:
    """The Net worth page's "Over time" chart for the ribbon's date range (user's request 2026-10-13: it follows the
    range instead of its own 90 / 180 / 365 buttons): one point per day from `start` to `end` (no later than today),
    so the axis spans the whole range; days before the first snapshot are empty (v None). `show` = the ribbon's
    $ / % toggle: "value" = net worth in dollars; "pct" = its % change since the day before the range (or since the
    first snapshot, when the range starts before it), `net` keeps the dollars for the tooltip. Carried days (no
    sync) are flagged for dashing, as in networth_trend."""
    last = min(end, (today or date.today()).isoformat())
    history = networth.history(conn)
    by_day = {p.date: p for p in history}
    before = [p for p in history if p.date < start]
    inside = [p for p in history if start <= p.date <= last]
    base = (before[-1] if before else inside[0]).net_cents if (before or inside) else 0
    points = []
    day = date.fromisoformat(start)
    while day.isoformat() <= last:
        p = by_day.get(day.isoformat())
        if p is None:
            points.append({"d": day.isoformat(), "v": None, "carried": False})
        else:
            v = dollars(p.net_cents) if show == "value" else (
                round((p.net_cents - base) / abs(base) * 100, 2) if base else None)
            points.append({"d": p.date, "v": v, "net": dollars(p.net_cents), "assets": dollars(p.assets_cents),
                           "liabilities": dollars(p.liabilities_cents), "carried": p.carried})
        day += timedelta(days=1)
    return points


# Readable names for Plaid's investment account subtypes (anything else: first letter capitalised).
SUBTYPE_LABELS = {"401k": "401(k)", "401a": "401(a)", "403b": "403(b)", "457b": "457(b)", "roth 401k": "Roth 401(k)",
                  "ira": "IRA", "roth": "Roth IRA", "sep ira": "SEP IRA", "simple ira": "SIMPLE IRA", "hsa": "HSA",
                  "brokerage": "Brokerage", "non-taxable brokerage account": "Brokerage", "529": "529 plan"}
OTHER_INVESTMENTS = "Other investments"
# Account types that count as retirement on the Portfolio page (user's choice 2026-10-07: 401(k),
# IRA / Roth and HSA; brokerage and anything else = "Investment accounts"). Compared after
# _type_key, so a category named "401K" or "Roth IRA" matches too.
RETIREMENT_TYPES = {"401k", "401a", "403b", "457b", "roth401k", "ira", "rothira", "sepira", "simpleira",
                    "hsa", "sarsep", "keogh", "pension", "retirement", "profitsharingplan", "rollover",
                    "rolloverira", "traditionalira"}


def _type_key(label: str) -> str:
    """'401(k)' and a category named '401K' are the same type."""
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _own(account, column: str):
    """An account column that some queries don't select (account_types' columns, migration 0032): None then."""
    try:
        return account[column]
    except (IndexError, KeyError):
        return None


def investment_type_label(account: sqlite3.Row) -> str:
    """An investment account's type for grouping: the type you gave it (account_types.py) if any, else Plaid's subtype
    in readable form, else "Other investments" (e.g. a manual account). (Account categories, which used to come
    first, were removed 2026-10-13: user's request.)"""
    if _own(account, "type_label"):
        return account["type_label"]
    subtype = (account["subtype"] or "").strip().lower()
    if not subtype or subtype == "other":
        return OTHER_INVESTMENTS
    return SUBTYPE_LABELS.get(subtype, subtype[:1].upper() + subtype[1:])


def is_retirement_account(account: sqlite3.Row) -> bool:
    """Is this investment account a retirement account (401(k), IRA, Roth, HSA and similar)? The type you gave it decides
    (account_types.py: its kind); else Plaid's subtype (RETIREMENT_TYPES). The one rule behind Portfolio's Retirement
    tile, the Savings and Balances cards and the transfers chart. `account` needs `subtype` (and `type_kind`)."""
    if _own(account, "type_kind"):
        return account["type_kind"] == "retirement"
    return _type_key(investment_type_label(account)) in RETIREMENT_TYPES


def investments_by_type(conn: sqlite3.Connection, days: int = 366) -> dict:
    """Investment accounts' value per day, stacked by account type (Portfolio page; user's request
    2026-10-07). From the daily snapshots, like net worth: days with no sync repeat the previous
    day (whole days, flagged in `carried`), a sync day without investments is 0. Types whose names
    match ('401(k)' / '401K') merge, your type's spelling first. The largest types (on the
    last day) keep their own series; past MAX_SERIES the rest fold into "Other"."""
    labels: dict[str, str] = {}                      # type key -> display label
    group_of: dict[int, str] = {}                    # account id -> type key
    for a in conn.execute("SELECT id, subtype, type_kind, type_label FROM accounts"):
        label = investment_type_label(a)
        key = _type_key(label)
        if key not in labels or a["type_label"]:
            labels[key] = label
        group_of[a["id"]] = key
    by_day: dict[str, dict[str, int]] = {d: defaultdict(int) for (d,) in
                                         conn.execute("SELECT DISTINCT date FROM balance_snapshots ORDER BY date")}
    for r in conn.execute("SELECT date, account_id, balance_cents FROM balance_snapshots"
                          " WHERE account_type = 'investment' AND classification = 'asset'"):
        by_day[r["date"]][group_of.get(r["account_id"], _type_key(OTHER_INVESTMENTS))] += r["balance_cents"]
    if not by_day:
        return {"labels": [], "series": [], "carried": []}
    # Every day from the first snapshot to the last; a missing day repeats the one before.
    dates, carried, totals = [], [], []
    day, last = date.fromisoformat(min(by_day)), date.fromisoformat(max(by_day))
    previous: dict[str, int] = {}
    while day <= last:
        iso = day.isoformat()
        if iso in by_day:
            previous = by_day[iso]
        dates.append(iso)
        carried.append(iso not in by_day)
        totals.append(previous)
        day += timedelta(days=1)
    dates, carried, totals = dates[-days:], carried[-days:], totals[-days:]
    keys = [k for k in labels if any(t.get(k) for t in totals)]
    keys.sort(key=lambda k: -totals[-1].get(k, 0))
    series = [(k, labels[k]) for k in keys]
    if len(series) > MAX_SERIES:
        folded = [k for k, _ in series[MAX_SERIES - 1:]]
        series = series[:MAX_SERIES - 1] + [("_other", "Other")]
        totals = [{**t, "_other": sum(t.get(k, 0) for k in folded)} for t in totals]
    return {"labels": dates, "carried": carried,
            "series": [{"label": label, "values": [dollars(t.get(k, 0)) for t in totals]} for k, label in series]}


# The chart palette has five fixed slots (never cycled); more series fold into one.
MAX_SERIES = 5


# Readable kinds of holding (Plaid's security type), used by the Portfolio holdings table's Kind
# filter. Cash-equivalent positions count as "Cash"; anything else is "Other".
SECURITY_GROUPS = {"equity": "Stocks", "etf": "ETFs", "mutual fund": "Funds",
                   "fixed income": "Bonds", "cash": "Cash"}


# ---------------------------------------------------------------- spending

def _daily_spending(conn: sqlite3.Connection, start: str, end: str, categories: list[str] | None = None) -> dict[str, int]:
    """{date: spending cents} for dates in [start, end), optionally only some categories."""
    sql = ("SELECT date, SUM(amount_cents) AS total FROM transactions_effective"
           " WHERE account_hidden = 0 AND kind = 'expense' AND date >= ? AND date < ?")
    params: list = [start, end]
    if categories is not None:
        if not categories:
            return {}
        sql += f" AND category IN ({','.join('?' * len(categories))})"
        params += categories
    return {r["date"]: r["total"] for r in conn.execute(sql + " GROUP BY date", params)}


def _cumulative(daily: dict[str, int], month: str, through_day: int) -> list[float]:
    running, values = 0, []
    for day in range(1, through_day + 1):
        running += daily.get(f"{month}-{day:02d}", 0)
        values.append(dollars(running))
    return values


def cumulative_spending(conn: sqlite3.Connection, today: date | None = None, month: str | None = None) -> dict:
    """Spending so far in `month` (default: this month), day by day, against all of the month
    before it (cumulative). A month that has finished is drawn in full."""
    today = today or date.today()
    this_month = month or f"{today:%Y-%m}"
    last_month = shift_month(this_month, -1)
    last_len = calendar.monthrange(int(last_month[:4]), int(last_month[5:]))[1]
    this_len = calendar.monthrange(int(this_month[:4]), int(this_month[5:]))[1]
    through = today.day if this_month == f"{today:%Y-%m}" else this_len
    return {
        "days": list(range(1, max(this_len, last_len) + 1)),
        "this": _cumulative(_daily_spending(conn, *month_bounds(this_month)), this_month, through),
        "last": _cumulative(_daily_spending(conn, *month_bounds(last_month)), last_month, last_len),
        "this_month": this_month,
        "last_month": last_month,
    }


def category_shares(lines, tail_share: float = 0.05, max_slots: int = 8) -> dict:
    """The month's spending as pie slices (user's rule 2026-10-08): every category gets its own
    colour, except the smallest ones that together make up the last `tail_share` (5%) of the
    spending, which are combined into a gray "Other". The palette has `max_slots` validated colours
    (dataviz rule: never generate a 9th hue), so if more categories than that remain, the smallest
    of them join "Other" too. A lone leftover category keeps its own colour instead of being
    called "Other". `slots` maps every category key to its colour class (slot-1..8 or slot-other),
    so the table's swatches and the pie agree. `lines` = spending.CategoryLine, largest first;
    categories netting to <= 0 (refund-only) are not slices."""
    positive = [line for line in lines if line.amount_cents > 0]
    total = sum(line.amount_cents for line in positive)
    keep = len(positive)
    folded = 0
    while keep > 0 and (folded + positive[keep - 1].amount_cents) <= total * tail_share:
        folded += positive[keep - 1].amount_cents
        keep -= 1
    if len(positive) - keep == 1 and keep < max_slots:      # nothing to combine: keep its own colour
        keep += 1
    keep = min(keep, max_slots)
    leaders, rest = positive[:keep], positive[keep:]
    slices = [{"label": l.label, "key": l.category, "value": dollars(l.amount_cents)} for l in leaders]
    if rest:
        slices.append({"label": "Other", "key": "_other", "value": dollars(sum(l.amount_cents for l in rest))})
    slots = {l.category: f"slot-{i + 1}" for i, l in enumerate(leaders)}
    slots.update({l.category: "slot-other" for l in lines if l.category not in slots})
    return {"slices": slices, "slots": slots}


def card_category_charts(cards) -> dict:
    """Chart data per credit card (Analysis → Credit cards, user's request 2026-10-25; bars since 2026-10-26):
    {account_id: category_shares of that card}, so each card is coloured like Spending's treemap, by size within the card, its smallest 5% gray.
    (One colour per category across all the cards was tried first: with 8 colours for many more categories, up to a
    third of a card came out gray "Other".) `cards` = spending.CardSpending, lines largest first."""
    return {card.account_id: category_shares(card.lines) for card in cards}


def rate_key(rate_x100: int) -> str:
    """A rate group's key in a card's treemap and table ("rate-300" for 3x)."""
    return f"rate-{rate_x100}"


def card_rate_charts(estimates: dict, rate_label, max_slots: int = 8) -> dict:
    """Bar chart data for the cards with reward rates (user's requests 2026-10-26): one bar per rate on ANY card, lowest
    rate on the left (the table's order reversed; user's request), so every card has the same bars ($0 where a card
    lacks a rate or has no spending at it), each a rate's share of the card's spending. A rate has ONE colour on
    every card: all the rates on all the cards, highest first, take the palette's slots in order (5x is the same
    colour wherever it appears; 3x points and 3% cash back share one), any past the 8th go gray, and 0x (card fees) is
    always gray. {account_id: {"slices":
    [{label, key, value, detail}], "slots": {key: slot class}}}, keys from rate_key, detail = what earns the rate.
    `estimates` = rewards.estimates; `rate_label(unit, x100)` names a bar ("3x", "2%")."""
    every_rate = sorted({g.rate_x100 for est in estimates.values() for g in est.groups if g.rate_x100}, reverse=True)
    colours = {rate_key(x100): f"slot-{i + 1}" if i < max_slots else "slot-other" for i, x100 in enumerate(every_rate)}
    colours[rate_key(0)] = "slot-other"           # 0x (card fees, rewards.NO_REWARDS) earns nothing: gray, no slot used
    # every card's x axis has the same bars (user's request 2026-10-26): all the rates on all the cards, lowest first;
    # a rate the card doesn't have is a $0 bar, named in the card's unit
    axis = sorted({g.rate_x100 for est in estimates.values() for g in est.groups})
    charts = {}
    for account_id, est in estimates.items():
        own = {g.rate_x100: g for g in est.groups}
        slices = [{"label": rate_label(est.unit, x100), "key": rate_key(x100),
                   "value": dollars(own[x100].spending_cents) if x100 in own else 0.0,
                   "detail": ", ".join(own[x100].definitions) if x100 in own else ""} for x100 in axis]
        charts[account_id] = {"slices": slices, "slots": {p["key"]: colours[p["key"]] for p in slices}}
    return charts


def grouped_category_lines(lines, shares: dict) -> list:
    """The By category table's rows: the categories coloured gray in the treemap (`shares["slots"]`) are
    combined into one "Other" row at the end (amount, count and last month's amount added up), so the rows
    still add up to the month's spending. A single leftover category keeps its own name. The "Other" row's key
    is "_other", like the treemap tile, and picks all of them at once."""
    from fintrack.spending import CategoryLine

    # categories with prior-period spending only (nothing now; user's request 2026-11-04) keep their own rows, after
    # Other: folded into it, they'd hide the drop to nothing
    gone = [l for l in lines if l.amount_cents == 0 and l.count == 0 and l.previous_cents]
    lines = [l for l in lines if l not in gone]
    kept = [l for l in lines if shares["slots"].get(l.category) != "slot-other"]
    folded = [l for l in lines if shares["slots"].get(l.category) == "slot-other"]
    if len(folded) < 2:
        return kept + folded + gone
    combined = CategoryLine("_other", "Other", sum(l.amount_cents for l in folded), sum(l.count for l in folded),
                            sum(l.previous_cents for l in folded))
    return kept + [combined] + gone


def monthly_spending(conn: sqlite3.Connection, start: str, end: str, with_average: bool = False) -> dict:
    """Spending per calendar month between two inclusive dates, oldest first, every month in between
    included (zero when nothing was spent). The first and last months count only the days inside the
    range. {"months": ["2026-07", ...], "values": [dollars, ...], "average": dollars | None}.

    The average (`with_average`) is the monthly spending over the whole range: total spent divided by the
    range's length in months (days / 30.44). It is NOT the mean of the bars: a clipped first month or a
    month still in progress would count as a whole month and pull it down."""
    totals = {r["month"]: r["total"] for r in conn.execute(
        """
        SELECT substr(date, 1, 7) AS month, SUM(amount_cents) AS total
        FROM transactions_effective
        WHERE account_hidden = 0 AND kind = 'expense' AND date >= ? AND date <= ?
        GROUP BY month
        """,
        (start, end),
    )}
    months, month = [], start[:7]
    while month <= end[:7]:
        months.append(month)
        month = shift_month(month, 1)
    average = None
    if with_average:
        cents = average_per_month_cents(sum(totals.values()), start, end)
        average = None if cents is None else dollars(cents)
    return {"months": months, "values": [dollars(totals.get(m, 0)) for m in months], "average": average}


def period_totals(conn: sqlite3.Connection, where: str, params: list, by_day: bool,
                  source: tuple[str, list] | None = None) -> dict:
    """Money in and money out per day (a month is filtered) or per month, for the
    transaction list's current filters, and their totals. Plaid sign: positive = out. `source` (SQL, params): the rows
    to read instead of transactions_effective (the Transactions page's, with investment activity)."""
    period = "date" if by_day else "substr(date, 1, 7)"
    rows_sql, rows_params = source or ("SELECT * FROM transactions_effective", [])
    rows = conn.execute(
        f"""
        SELECT {period} AS p,
               COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents END), 0) AS money_out,
               COALESCE(SUM(CASE WHEN amount_cents < 0 THEN -amount_cents END), 0) AS money_in
        FROM ({rows_sql}) WHERE {where}
        GROUP BY p ORDER BY p
        """,
        rows_params + params,
    ).fetchall()
    return {"labels": [r["p"] for r in rows],
            "in": [dollars(r["money_in"]) for r in rows],
            "out": [dollars(r["money_out"]) for r in rows],
            # the totals, in cents, for the card's average (web/transaction_filters.py `_flow_average`)
            "in_cents": sum(r["money_in"] for r in rows), "out_cents": sum(r["money_out"] for r in rows)}


def period_sums(conn: sqlite3.Connection, where: str, params: list, by_day: bool, sign: int = 1,
                source: tuple[str, list] | None = None) -> dict:
    """One total per day or month (the Transactions page's Spending / Income views, user's request 2026-10-30: totals,
    not money in vs out): Σ amount × `sign` (1 = spending, refunds lower it; -1 = income). {labels, values (dollars),
    total_cents}. `source`: as period_totals'."""
    period = "date" if by_day else "substr(date, 1, 7)"
    rows_sql, rows_params = source or ("SELECT * FROM transactions_effective", [])
    rows = conn.execute(f"SELECT {period} AS p, SUM(amount_cents) * ? AS total FROM ({rows_sql}) WHERE {where}"
                        f" GROUP BY p ORDER BY p", [sign] + rows_params + params).fetchall()
    return {"labels": [r["p"] for r in rows], "values": [dollars(r["total"]) for r in rows],
            "total_cents": sum(r["total"] for r in rows)}


def transfer_flows(conn: sqlite3.Connection, where: str, params: list, source: tuple[str, list] | None = None,
                   max_slots: int = 8) -> dict:
    """The Transactions page's Transfers view (user's request 2026-10-30): where the filtered transfers went, for a flow
    (Sankey) chart. A matched pair (transfers.py) is one flow from the account money left to the account it reached,
    counted once even when both halves are listed; a half can be investment activity (a bank transfer matched with an
    investment account's own deposit / withdrawal, user's request 2026-10-31: its negative id, found in `source`). A
    transfer with no match flows between its account and its category (money out: account → "Credit Card Payment";
    money in: "Transfer in" → account), so every listed row shows.
    Left nodes are where money came from, right nodes where it went ("L:" / "R:" keys: "a<account id>", or
    "c<category key>", labelled with the category's name), so an account on both sides makes no loop; the Transactions
    page's flow filter names a flow by them (web/transaction_filters._flow_where). Accounts take the palette's slots by how much moved through them (past the 8th gray, never cycled);
    categories are gray. {flows: [{from, to, flow (dollars), count}], labels: {key: label}, slots: {key: slot},
    total_cents, count (transfers drawn, a matched pair once)}, largest flow first."""
    rows_sql, rows_params = source or ("SELECT * FROM transactions_effective", [])
    rows = conn.execute(f"""SELECT id, account_id, account_name, amount_cents, is_transfer, transfer_pair_id,
                                   category, category_label FROM ({rows_sql}) WHERE {where}""", rows_params + params).fetchall()
    partner_ids = [r["transfer_pair_id"] for r in rows if r["is_transfer"] and r["transfer_pair_id"]]
    partners = {}
    for chunk in range(0, len(partner_ids), 500):
        ids = partner_ids[chunk:chunk + 500]
        partners.update({r["id"]: r for r in conn.execute(
            f"SELECT id, account_id, account_name FROM ({rows_sql}) WHERE id IN ({','.join('?' * len(ids))})",
            rows_params + ids)})
    flows: dict[tuple[str, str], list[int]] = {}
    labels: dict[str, str] = {}
    through: dict[int, int] = {}                     # account id -> cents moved through it
    seen_pairs: set[tuple[int, int]] = set()

    def add(src: str, dst: str, cents: int, *accounts: int) -> None:
        f = flows.setdefault((src, dst), [0, 0])
        f[0] += cents
        f[1] += 1
        for a in accounts:
            through[a] = through.get(a, 0) + cents

    for r in rows:
        cents = abs(r["amount_cents"])
        if not cents:
            continue
        other = partners.get(r["transfer_pair_id"]) if r["is_transfer"] else None
        if other is not None:
            pair = tuple(sorted((r["id"], other["id"])))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            out, into = (r, other) if r["amount_cents"] > 0 else (other, r)
            src, dst = f"L:a{out['account_id']}", f"R:a{into['account_id']}"
            labels[src], labels[dst] = out["account_name"], into["account_name"]
            add(src, dst, cents, out["account_id"], into["account_id"])
        elif r["amount_cents"] > 0:
            src, dst = f"L:a{r['account_id']}", f"R:c{r['category']}"
            labels[src], labels[dst] = r["account_name"], r["category_label"]
            add(src, dst, cents, r["account_id"])
        else:
            src, dst = f"L:c{r['category']}", f"R:a{r['account_id']}"
            labels[src], labels[dst] = r["category_label"], r["account_name"]
            add(src, dst, cents, r["account_id"])
    ranked = sorted(through, key=lambda a: (-through[a], a))
    account_slot = {a: f"slot-{i + 1}" if i < max_slots else "slot-other" for i, a in enumerate(ranked)}
    slots = {key: account_slot[int(key[3:])] if key[2] == "a" else "slot-other" for key in labels}
    ordered = sorted(flows.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return {"flows": [{"from": src, "to": dst, "flow": dollars(cents), "count": n} for (src, dst), (cents, n) in ordered],
            "labels": labels, "slots": slots, "total_cents": sum(cents for cents, _ in flows.values()),
            "count": sum(n for _, n in flows.values())}


OTHER_MAX_PERCENT = 5        # the pie's "Other" holds at most this share of the total (user's request 2026-10-29)


def category_rings(conn: sqlite3.Connection, where: str, params: list, kind: str = "expense",
                   max_slots: int = 8, slots: dict | None = None, source: tuple[str, list] | None = None) -> dict:
    """The Transactions page's two-ring pie (user's request 2026-10-26): the filtered transactions' spending (or income,
    `kind`) by category, every page of the list. Inner ring = the categories (a subcategory counted in its parent),
    largest first, coloured slot 1..8 like Spending's treemap. The smallest fold into one gray "Other", but only while
    together they're at most OTHER_MAX_PERCENT of the total (user's request 2026-10-29; was everything past the 8th),
    and a single leftover keeps its own name. A category past the 8th that doesn't fold is its own gray slice (the
    palette has 8 validated colours; dataviz: never cycle them). Outer ring = each one's subcategories, largest first,
    then a blank for what's in the category itself. A category whose refunds outweigh its spending can't be a slice and
    is left out.
    {"total", "total_cents", "parents": [{key, label, value, slot, subs: [{key, label, value}], direct}]}, in dollars
    but total_cents; key "_other" for the folded ones (no subs; `members`: [{key, label, subs: [keys]}], largest first).
    `slots` ({key: slot}, from these rings without a picked period) keeps each category's colour when a pick reorders
    them: a colour follows its category, not its rank (a key it lacks is gray). `source`: as period_totals'."""
    sign = -1 if kind == "income" else 1
    rows_sql, rows_params = source or ("SELECT * FROM transactions_effective", [])                     # income is money in: negative amounts
    rows = conn.execute(
        f"""
        SELECT s.category, s.label, s.total, c.parent_key, COALESCE(p.label, c.parent_key) AS parent_label
        FROM (SELECT category, MAX(category_label) AS label, SUM(amount_cents) AS total
              FROM ({rows_sql}) WHERE {where} AND kind = ? GROUP BY category) s
        LEFT JOIN categories c ON c.key = s.category
        LEFT JOIN categories p ON p.key = c.parent_key
        """,
        rows_params + params + [kind],
    ).fetchall()
    parents: dict[str, dict] = {}
    for r in rows:
        cents = sign * r["total"]
        if cents <= 0:
            continue
        key, label = (r["parent_key"], r["parent_label"]) if r["parent_key"] else (r["category"], r["label"])
        group = parents.setdefault(key, {"key": key, "label": label, "cents": 0, "subs": [], "direct_cents": 0})
        group["cents"] += cents
        if r["parent_key"]:
            group["subs"].append({"key": r["category"], "label": r["label"], "cents": cents})
        else:
            group["direct_cents"] += cents
    ordered = sorted(parents.values(), key=lambda g: (-g["cents"], g["label"].lower()))
    total_cents = sum(g["cents"] for g in ordered)
    # fold from the smallest up while the folded total stays within the cap (whole cents: no float rounding)
    folded, folded_cents = [], 0
    for g in reversed(ordered):
        if (folded_cents + g["cents"]) * 100 > total_cents * OTHER_MAX_PERCENT:
            break
        folded.insert(0, g)
        folded_cents += g["cents"]
    if len(folded) > 1:
        ordered = ordered[:len(ordered) - len(folded)] + [
            {"key": "_other", "label": "Other", "cents": folded_cents, "subs": [], "direct_cents": folded_cents,
             "members": [{"key": g["key"], "label": g["label"], "subs": [x["key"] for x in g["subs"]]} for g in folded]}]
    out = []
    for i, g in enumerate(ordered):
        subs = sorted(g["subs"], key=lambda x: (-x["cents"], x["label"].lower()))
        out.append({"key": g["key"], "label": g["label"], "value": dollars(g["cents"]),
                    "slot": "slot-other" if g["key"] == "_other" else (
                        slots.get(g["key"], "slot-other") if slots is not None else
                        f"slot-{i + 1}" if i < max_slots else "slot-other"),
                    "subs": [{"key": x["key"], "label": x["label"], "value": dollars(x["cents"])} for x in subs],
                    "direct": dollars(g["direct_cents"]),
                    **({"members": g["members"]} if "members" in g else {})})
    return {"total": dollars(total_cents), "total_cents": total_cents, "parents": out}


def top_descriptions(conn: sqlite3.Connection, where: str, params: list, kind: str, limit: int = 10,
                     source: tuple[str, list] | None = None) -> dict:
    """The Transactions page's list by description (user's request 2026-11-01): the `limit` largest, each with its share
    of the net total (refunds lower it, so the total is the real spending or income). A description whose refunds
    outweigh its spending isn't listed. {"total_cents", "rows": [{key, label, cents, share (0..1), percent}]}, in cents;
    `source`: as period_totals'."""
    rows_sql, rows_params = source or ("SELECT * FROM transactions_effective", [])
    sign = -1 if kind == "income" else 1
    found = [(r[0], sign * r[1]) for r in conn.execute(
        f"SELECT description, SUM(amount_cents) FROM ({rows_sql}) WHERE {where} AND kind = ? GROUP BY description",
        rows_params + params + [kind])]
    total_cents = sum(c for _, c in found)
    top = sorted(((d, c) for d, c in found if c > 0), key=lambda g: (-g[1], str(g[0]).lower()))[:limit]
    return {"total_cents": total_cents,
            "rows": [{"key": d, "label": d, "cents": c, "share": min(c / total_cents, 1) if total_cents > 0 else 0,
                      "percent": round(100 * c / total_cents) if total_cents > 0 else 0} for d, c in top]}


# ---------------------------------------------------------------- budgets

def budget_bars(view: MonthBudget, with_other: bool = False) -> list[dict]:
    """Spent vs available per budgeted category, largest budget first. `with_other` adds one
    "Other" bar for all spending in categories without a budget (no budget marker)."""
    lines = sorted(view.lines, key=lambda line: -line.available_cents)
    bars = [{"label": line.label, "spent": dollars(line.spent_cents), "budget": dollars(line.available_cents),
             "status": line.status} for line in lines]
    if with_other and view.unbudgeted_cents:
        bars.append({"label": "Other", "spent": dollars(view.unbudgeted_cents), "budget": None, "status": "other"})
    return bars


def recurring_by_day(conn: sqlite3.Connection, kind: str = "bill", today: date | None = None) -> dict:
    """Confirmed recurring items of one kind ("income" or "bill") on each day of the current month: the Recurring page's
    two bar charts (user's request 2026-10-16). Each item lands on its scheduled dates in the month (recurring.occurrences
    from its next expected date, so days already past count too); annual items are left out, and so are recurring
    transfers (neither kind). `total_cents` = the month's total (the card's measure)."""
    today = today or date.today()
    start = today.replace(day=1)
    length = calendar.monthrange(today.year, today.month)[1]
    end = today.replace(day=length)
    cents = [0] * length
    items: list[list[dict]] = [[] for _ in range(length)]
    for item in recurring.list_items(conn, "confirmed"):
        if item["type"] != kind or item["frequency"] == "annual" or not item["next_expected_date"]:
            continue
        anchor = date.fromisoformat(item["next_expected_date"])
        amount = abs(item["amount_cents"])
        for day in recurring.occurrences(item["frequency"], anchor, start, end, item["days_of_month"]):
            cents[day.day - 1] += amount
            items[day.day - 1].append({"name": item["name"], "amount": dollars(amount)})
    return {"days": [start.replace(day=d).isoformat() for d in range(1, length + 1)],
            "values": [dollars(c) for c in cents], "items": items, "total_cents": sum(cents)}


def investment_transfers(conn: sqlite3.Connection, months: int = 12, today: date | None = None,
                         start: str | None = None, end: str | None = None) -> dict:
    """Money moved into and out of retirement accounts and of other investment accounts, by month (contributions.py has
    what counts and how each transfer is assigned), for the `transfers` chart on Portfolio and Savings. `into` and
    `out` each hold one list of dollars per group, drawn as two charts (user's request 2026-10-11: big withdrawals
    flattened the monthly contributions on one shared axis). Empty when nothing moved in the whole window."""
    # Savings passes its date range (inclusive ISO dates); Portfolio shows the last `months` months
    series = (contributions.between(conn, date.fromisoformat(start), date.fromisoformat(end)) if start and end
              else contributions.monthly(conn, months, today))
    if not any(m.into_cents or m.out_cents for m in series):
        return {"labels": [], "into": {}, "out": {}, "has_into": False, "has_out": False}
    return {"labels": [m.month for m in series],
            "into": {g: [dollars(m.into[g]) for m in series] for g in contributions.GROUPS},
            "out": {g: [dollars(m.out[g]) for m in series] for g in contributions.GROUPS},
            # the page draws money in and money out as two charts, each on its own axis; an empty one shows a note
            "has_into": any(m.into_cents for m in series), "has_out": any(m.out_cents for m in series)}


# One colour per kind of savings, the same on every Savings / Balances card (user's request 2026-10-12): a kind's own
# card draws its segments in shades of it; the Net savings card draws each part in it. CSS variables (app.css).
KIND_COLORS = {"retirement": "--series-1", "investment": "--series-2", "net_cash": "--series-3"}

# Balances page (balances.GROUPS): one colour per kind of account; credit cards violet (slot 7), not red, so a balance
# owed doesn't read as "money out"
BALANCE_COLORS = {"retirement": "--series-1", "investment": "--series-2", "purposes": "--series-5", "cash": "--series-3",
                  "credit_cards": "--series-7", "property_loans": "--series-4"}

# Balances groups that are money owed: their % change follows the balance owed and its colours are reversed
LIABILITY_GROUPS = {"credit_cards"}

# The segment for bank-side transfers with no account to name (Plaid doesn't say where they went), per group.
UNKNOWN_ACCOUNT = {"retirement": "Other retirement", "investment": "Account unknown"}


def savings_mix(conn: sqlite3.Connection, series, start: str, end: str, group: str = "retirement",
                by_day: bool = False) -> dict:
    """One kind of savings by account type as % of each month's total income (post-tax + pre-tax), for the Savings
    page's stacked bar cards (user's requests 2026-10-12): group "retirement" or "investment". Per month and type:
    net money moved into that kind of account (contributions.py: connected accounts, by account; bank-side transfers
    with no account are UNKNOWN_ACCOUNT[group]) and, for retirement, the contributions minus withdrawals you recorded on
    manual retirement accounts (manual_positions). So each stack's top = the table's Retirement / Investment % inc.
    Retirement segments are account types: your account category's name, else Plaid's subtype (investment_type_label;
    "401(k)" and "401K" are one type); investment segments are institutions (a manual account: its name). Fixed
    alphabetical order so colours don't move when the range changes. A month with no income has no
    column (None). `average` = the period's total / total income (like savings.combined_rate, not a mean of monthly %).
    `series` = savings.MonthSavings (oldest first). `by_day` (This / Last month; user's request 2026-10-12): `series` is one
    row per day (savings.range_series(by_day=True)) and each bar is a share of the period's income (_share)."""
    from fintrack import manual_positions                 # imported here: manual_positions imports this module lazily

    unknown = UNKNOWN_ACCOUNT[group]
    labels: dict[int | None, str] = {None: unknown}
    # Retirement: by account type (401(k), HSA, Roth IRA...). Investment: by institution (user's request 2026-10-12:
    # the institution's name, not the account category): a manual account's own institution alike (2026-11-14,
    # docs/parity-plan.md), else its name.
    for a in conn.execute("SELECT a.id, a.subtype, a.type_kind, a.type_label, a.name, a.manual_institution,"
                          " i.institution_name FROM accounts a LEFT JOIN items i ON i.id = a.item_id"):
        if group == "investment":
            labels[a["id"]] = a["institution_name"] or (a["manual_institution"] or "").strip() or a["name"]
        else:
            labels[a["id"]] = investment_type_label(a)
    # the same flows as the page's table (savings.range_series): Savings leaves investments -> bank out
    flows = {f.month: f for f in contributions.between(conn, date.fromisoformat(start), date.fromisoformat(end),
                                                       by_day, to_bank=False)}
    by_month: list[dict[str, int]] = []
    shown: dict[str, str] = {}                            # type key -> label as first seen (your category's spelling)
    for m in series:
        totals: dict[str, int] = {}
        flow = flows.get(m.month)
        for account_id, cents in (flow.by_account[group] if flow else {}).items():
            label = labels.get(account_id, unknown)
            key = _type_key(label)
            shown.setdefault(key, label)
            totals[key] = totals.get(key, 0) + cents
        by_month.append(totals)
    keys = sorted(k for k in shown if any(t.get(k) for t in by_month))
    saved = sum(sum(t.values()) for t in by_month)
    income = sum(m.total_income_cents for m in series)
    return {
        "average": round(saved / income * 100, 1) if income > 0 else None,
        "saved_cents": saved,
        "income_cents": income,
        "hue": KIND_COLORS[group],
        "labels": [m.month for m in series],
        "partial": [m.partial for m in series],
        "series": [{"label": shown[k],
                    "values": [_share(t.get(k, 0), m.total_income_cents, income, by_day) for t, m in zip(by_month, series)],
                    "dollars": [dollars(t.get(k, 0)) for t in by_month]} for k in keys],
    }


def _share(cents: int, own_income: int, period_income: int, by_day: bool) -> float | None:
    """A bar's % for the Savings cards: of its own month's income, or (by_day) of the whole period's income, because a
    single day's income is mostly zero (paydays) and wouldn't mean anything; daily bars then add up to the Average."""
    base = period_income if by_day else own_income
    return round(cents / base * 100, 1) if base > 0 else None


def net_cash_mix(series, by_day: bool = False) -> dict:
    """Net cash as % of each month's total income, for the Savings page's Net cash card (user's request 2026-10-12): the
    same shape as savings_mix with one series, straight from savings.MonthSavings.net_cash_cents (so the card's Total =
    the table's Net cash column). One series: net cash can't be split by account (spending is on cards, income in
    checking). A month with no income has no column (None)."""
    saved = sum(m.net_cash_cents for m in series)
    income = sum(m.total_income_cents for m in series)
    return {
        "average": round(saved / income * 100, 1) if income > 0 else None,
        "saved_cents": saved,
        "income_cents": income,
        "legend": False,                                  # one series, named by the card's title
        "hue": KIND_COLORS["net_cash"],
        "labels": [m.month for m in series],
        "partial": [m.partial for m in series],
        "series": [{"label": "Net cash",
                    "values": [_share(m.net_cash_cents, m.total_income_cents, income, by_day) for m in series],
                    "dollars": [dollars(m.net_cash_cents) for m in series]}] if series else [],
    }


def net_savings_mix(series, by_day: bool = False) -> dict:
    """The Savings page's Net savings card (user's request 2026-10-12): what was saved, by part (Retirement,
    Investment, Net cash: the pieces of savings.MonthSavings.saved_cents), each a share of the
    month's income (or of the period's, `by_day`; _share). A stack's top = the table's Rate; Total = saved over the period
    and Average = savings.combined_rate. Spending is in Net cash (post-tax income - spending - transfers). Parts with no
    amounts in the period are left out (Net cash always shows)."""
    parts = [("Retirement", "retirement_cents", "retirement"), ("Investment", "investment_cents", "investment"),
             ("Net cash", "net_cash_cents", "net_cash")]
    parts = [p for p in parts if p[0] == "Net cash" or any(getattr(m, p[1]) for m in series)]
    saved = sum(m.saved_cents for m in series)
    income = sum(m.total_income_cents for m in series)
    return {
        "average": round(saved / income * 100, 1) if income > 0 else None,
        "saved_cents": saved,
        "income_cents": income,
        "labels": [m.month for m in series],
        "partial": [m.partial for m in series],
        "colors": [KIND_COLORS[kind] for _label, _attr, kind in parts],      # each part in its kind's colour
        "series": [{"label": label,
                    "values": [_share(getattr(m, attr), m.total_income_cents, income, by_day) for m in series],
                    "dollars": [dollars(getattr(m, attr)) for m in series]}
                   for label, attr, _kind in parts],
    }


# ---------------------------------------------------------------- Balances page (balances.py)

# The Balances page's $ / % toggle in the ribbon (user's request 2026-10-12): what the cards' bars show
BALANCE_SHOWS = ("pct", "value")    # % change per month (or day) | total value at the end of each month (or day)


def _bar_pct(change: int, base: int, sign: int = 1) -> float | None:
    """One bar segment in % mode: its change ÷ the size of the card's value just before the period (so a stack's top is
    the card's % change for that period); None (no bar) when that value was 0."""
    return round(sign * change / abs(base) * 100, 2) if base else None


def balance_change_mix(periods, group: str, whole, show: str = "pct") -> dict:
    """One kind of account per month (or day), stacked by segment (account type, institution or account): the Balances
    page's cards (user's requests 2026-10-12). `show` (the ribbon's $ / % toggle): "pct" = each period's % change
    (stacked bars, sharebars), "value" = the total value at the end of each period, in dollars (stacked areas,
    stackedarea). `whole` = balances.summary() over the range for the
    card's measures: `value_cents` = the group's total value at the end of the range, `total_cents` = its change, `pct` =
    change / the starting value's size (None when it started at 0).
    Credit cards (LIABILITY_GROUPS) use the opposite colour logic to assets: their % follows the amount OWED (owing more
    = ▲ in red, paying down = ▼ in green; `invert`), in the measure and the % bars alike.
    Segments in fixed alphabetical order so colours don't move between ranges; drawn in shades of the kind's colour."""
    invert = group in LIABILITY_GROUPS
    sign = -1 if invert else 1
    changes = [p.segment_changes(group) for p in periods]
    values = [{s: v for (g, s), v in p.after.items() if g == group} for p in periods]
    keys = sorted({k for c in changes for k, v in c.items() if v} | {k for v in values for k, cents in v.items() if cents}
                  | ({k for (g, k), v in whole.after.items() if g == group and v} if whole else set()), key=str.lower)
    pct = whole.pct(group) if whole else None
    if invert and pct is not None:
        pct = -pct
    if show == "value":
        series = [{"label": k, "values": [dollars(v.get(k, 0)) for v in values],
                   "dollars": [dollars(v.get(k, 0)) for v in values]} for k in keys]
    else:
        series = [{"label": k, "values": [_bar_pct(c.get(k, 0), p.value_before(group), sign) for c, p in zip(changes, periods)],
                   "dollars": [dollars(c.get(k, 0)) for c in changes]} for k in keys]
    return {
        "money": show == "value",
        "total_footer": True,                             # $ mode is a stacked area (stackedarea): Total in the tooltip
        "decimals": 1,                                    # % bars: 0.8%, not 1%
        "invert": invert,
        "value_cents": whole.value_after(group) if whole else 0,
        "total_cents": whole.change(group) if whole else 0,
        "pct": round(pct * 100, 1) if pct is not None else None,
        "hue": BALANCE_COLORS[group],
        "labels": [p.month for p in periods],
        "partial": [p.partial for p in periods],
        "series": series,
    }


def networth_change_mix(periods, groups: list[str], whole, show: str = "pct") -> dict:
    """The Balances page's Net worth card, stacked by kind of account (`groups`, keys of balances.GROUPS), each in its
    kind's colour: per month (or day) each part's share of net worth's % change ("pct"; the stack's top = net worth's %
    change) or its value at the end ("value"; owed kinds stack below zero, the parts add up to net worth)."""
    from fintrack import balances

    pct = whole.pct() if whole else None
    if show == "value":
        series = [{"label": balances.LABELS[g], "values": [dollars(p.value_after(g)) for p in periods],
                   "dollars": [dollars(p.value_after(g)) for p in periods]} for g in groups]
    else:
        series = [{"label": balances.LABELS[g], "values": [_bar_pct(p.change(g), p.value_before()) for p in periods],
                   "dollars": [dollars(p.change(g)) for p in periods]} for g in groups]
    return {
        "money": show == "value",
        "total_footer": True,
        "decimals": 1,
        "value_cents": whole.value_after() if whole else 0,          # net worth at the end of the range
        "total_cents": whole.change() if whole else 0,
        "pct": round(pct * 100, 1) if pct is not None else None,
        "labels": [p.month for p in periods],
        "partial": [p.partial for p in periods],
        "colors": [BALANCE_COLORS[g] for g in groups],
        "series": series,
    }


def balance_forecast(flow, days: int = 30, prefer: int | None = None) -> dict:
    """Projected daily balance for each forecast account (today + `days`), with each day's
    events, from forecast.cash_flow (so the chart agrees with the Forecast page). The
    `prefer` account is listed first (the chart shows the first by default). Names without masks (user's request
    2026-10-23)."""
    accounts = []
    for a in flow.accounts:
        by_day: dict[str, list[str]] = {}
        for e in a.events:
            sign = "+" if e.change_cents > 0 else "−"
            by_day.setdefault(e.day.isoformat(), []).append(
                f"{e.name}{' (overdue)' if e.overdue else ''} {sign}${abs(e.change_cents) / 100:,.2f}")
        points = a.points[: days + 1]
        accounts.append({
            "id": a.account_id, "label": a.name,
            "labels": [d.isoformat() for d, _ in points],
            "balance": [dollars(c) for _, c in points],
            "events": [by_day.get(d.isoformat(), []) for d, _ in points],
        })
    accounts.sort(key=lambda acc: acc["id"] != prefer)
    return {"accounts": accounts, "threshold": dollars(flow.threshold_cents)}
