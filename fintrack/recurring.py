"""Recurring income and bills: local detection, schedules, and your review.

Plaid's Recurring Transactions product isn't on the Trial plan, so recurring
items are detected from your own transaction history:

1. Group posted transactions from the last ~26 months by (merchant, direction,
   account). "Merchant" is Plaid's merchant name, or the description with
   digits and noise words (POS, ACH, DEBIT, ...) removed.
2. Find the schedule from the gaps between dates: weekly, biweekly,
   semimonthly (same two days each month, e.g. 1st & 15th), monthly,
   quarterly, or annual.
3. Keep the group only if:
     - it has enough occurrences (MIN_OCCURRENCES),
     - >= 75% of the gaps fit the schedule,
     - >= 75% of the amounts are within +/-20% of the typical (median) amount,
     - it isn't stale (the last one was within 1.5 schedule-lengths of today).

A description that fails as a whole is tried again split by amount (user's report 2026-10-16: rent paid by bank
transfer carries the same text as other transfers to that bank and is told apart only by its amount). Each amount
band that passes becomes its own item, keyed "<description key> @<typical cents>", and only claims transactions
within BAND_MATCH_TOLERANCE of its own amount. A band's amount must be fixed (BAND_AMOUNT_TOLERANCE), and everyday spending
(EVERYDAY_CATEGORIES, by the band's top-level category) needs more occurrences (EVERYDAY_MIN_OCCURRENCES), because one
price picked out of many purchases can line up by chance (a restaurant meal at the same price a year apart); bills,
subscriptions and everything else need the usual MIN_OCCURRENCES.

Your review decides what's used: only CONFIRMED items feed the forecasts.
Re-detection (after every sync) updates detected items in place, never brings
back DISMISSED ones, and for CONFIRMED ones only advances last-seen/next date
(your edits to name, amount, and schedule are kept).
"""

from __future__ import annotations

import calendar
import re
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

# Long enough to see two yearly occurrences (Plaid provides up to 730 days of history).
LOOKBACK_DAYS = 800
REGULAR_SHARE = 0.75
AMOUNT_TOLERANCE = 0.20
VARIES_THRESHOLD = 0.02   # amounts differing by more than 2% are flagged "amount varies"

FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual")
FREQUENCY_LABELS = {
    "weekly": "Weekly", "biweekly": "Every 2 weeks", "semimonthly": "Twice a month",
    "monthly": "Monthly", "quarterly": "Quarterly", "annual": "Yearly",
}
# Typical gap (days) and the range of gaps accepted for each schedule.
GAPS = {
    "weekly": (7, 6, 8),
    "biweekly": (14, 13, 15),
    "semimonthly": (15, 11, 19),
    "monthly": (30, 26, 35),
    "quarterly": (91, 84, 98),
    "annual": (365, 350, 380),
}
MIN_OCCURRENCES = {"weekly": 4, "biweekly": 4, "semimonthly": 4, "monthly": 3, "quarterly": 3, "annual": 2}
# "<description key> @<cents>": an item split out of a shared description by its amount (keys are only a-z and
# spaces, so the mark can't occur in a description key)
AMOUNT_MARK = " @"
BAND_AMOUNT_TOLERANCE = VARIES_THRESHOLD   # a band's amounts must be fixed: within 2% of the typical amount
# A band's item claims transactions within 10% of its amount: enough for a rent increase, not for a different purchase
# at a nearby price (seen on Production 2026-10-16: a small monthly charge's 20% took an unrelated purchase).
BAND_MATCH_TOLERANCE = 0.10
# Amount bands in everyday spending need more occurrences on the frequent and the long schedules (user's requests
# 2026-10-16: food more than utilities or subscriptions; but 3 months of a fixed monthly amount is enough to suggest,
# you can dismiss it); keyed by top-level category, so subcategories follow their parent.
EVERYDAY_CATEGORIES = {"FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "ENTERTAINMENT", "TRAVEL", "PERSONAL_CARE",
                       "TRANSPORTATION", "CASH_WITHDRAWAL"}
EVERYDAY_MIN_OCCURRENCES = {"weekly": 8, "biweekly": 6, "semimonthly": 6, "monthly": 3, "quarterly": 4, "annual": 3}

NOISE_WORDS = {
    "pos", "ach", "debit", "credit", "purchase", "card", "online", "recurring", "autopay", "web",
    "id", "ppd", "ccd", "des", "ref", "txn", "trans", "checkcard", "visa", "mastercard", "the",
}


# ---------------------------------------------------------------- schedules

def _add_months(day: date, months: int, day_of_month: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day_of_month, calendar.monthrange(year, month)[1]))


def occurrences(frequency: str, anchor: date, start: date, end: date, days_of_month: str | None = None) -> list[date]:
    """Dates of a schedule within [start, end]. `anchor` is any date on the schedule
    (we use the next expected date). Month-based schedules keep the anchor's day of
    month, clamped to short months (31st -> Feb 28/29 -> Mar 31)."""
    if end < start:
        return []
    result: list[date] = []
    if frequency in ("weekly", "biweekly"):
        step = 7 if frequency == "weekly" else 14
        offset = (start - anchor).days
        first = anchor + timedelta(days=-(-offset // step) * step) if offset > 0 else \
            anchor - timedelta(days=(-offset // step) * step)
        day = first
        while day <= end:
            if day >= start:
                result.append(day)
            day += timedelta(days=step)
    elif frequency == "semimonthly":
        days = sorted(int(d) for d in (days_of_month or f"{anchor.day}").split(","))
        month = date(start.year, start.month, 1)
        while month <= end:
            for d in days:
                candidate = date(month.year, month.month, min(d, calendar.monthrange(month.year, month.month)[1]))
                if start <= candidate <= end:
                    result.append(candidate)
            month = _add_months(month, 1, 1)
    else:
        step = {"monthly": 1, "quarterly": 3, "annual": 12}[frequency]
        # The intended day of month (stored separately, so a clamped Feb 28
        # doesn't turn a 31st-of-month bill into a 28th-of-month one).
        dom = int(days_of_month) if days_of_month else anchor.day
        # Walk back from the anchor to before `start`, then forward.
        k = 0
        while _add_months(anchor, -k * step, dom) > start:
            k += 1
        day_index = -k
        while True:
            day = _add_months(anchor, day_index * step, dom)
            if day > end:
                break
            if day >= start:
                result.append(day)
            day_index += 1
    return sorted(set(result))


def next_after(frequency: str, anchor: date, after: date, days_of_month: str | None = None) -> date:
    """First scheduled date strictly after `after`."""
    span = timedelta(days=400)
    return occurrences(frequency, anchor, after + timedelta(days=1), after + span, days_of_month)[0]


def next_expected_after(frequency: str, last_seen: date, days_of_month: str | None = None) -> date:
    """The next occurrence after the last one seen, at least the schedule's
    minimum gap later (a monthly item last seen Sep 20 is never due Sep 29)."""
    earliest = last_seen + timedelta(days=GAPS[frequency][1] - 1)
    return next_after(frequency, last_seen, earliest, days_of_month)


# ---------------------------------------------------------------- merchant keys

def merchant_key(merchant_name: str | None, name: str | None) -> str:
    """'Netflix' -> 'netflix'; 'POS DEBIT SPOTIFY P1A2B3 NY' -> 'spotify ny'."""
    base = (merchant_name or name or "").lower()
    words = [w for w in re.sub(r"[^a-z]+", " ", base).split() if len(w) > 1]
    meaningful = [w for w in words if w not in NOISE_WORDS]
    return " ".join(meaningful or words)


# ---------------------------------------------------------------- detection

@dataclass
class Detected:
    merchant_key: str
    account_id: int
    name: str
    category_key: str | None
    amount_cents: int          # typical (median), Plaid sign
    amount_varies: bool
    frequency: str
    days_of_month: str | None
    last_seen: date
    next_expected: date
    occurrences: int
    kind: str                  # income / bill / transfer


def _semimonthly_days(dates: list[date]) -> str | None:
    """'1,15' if the dates cluster on two days of the month (within 3 days), else None."""
    days = sorted(d.day for d in dates)
    clusters: list[list[int]] = []
    for d in days:
        if clusters and d - clusters[-1][0] <= 3:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    if len(clusters) != 2 or min(len(c) for c in clusters) < 2:
        return None
    return ",".join(str(round(statistics.median(c))) for c in clusters)


def _schedule(dates: list[date]) -> tuple[str, str | None] | None:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not gaps:
        return None
    median_gap = statistics.median(gaps)

    def fits(frequency: str) -> bool:
        _, low, high = GAPS[frequency]
        return sum(low <= g <= high for g in gaps) / len(gaps) >= REGULAR_SHARE

    # Biweekly before semimonthly: biweekly gaps are steadily 14; twice-a-month
    # gaps wobble (14-17) but the days of the month repeat.
    for frequency in ("weekly", "biweekly"):
        if GAPS[frequency][1] <= median_gap <= GAPS[frequency][2] and fits(frequency):
            return frequency, None
    if 11 <= median_gap <= 19:
        days = _semimonthly_days(dates)
        if days and fits("semimonthly"):
            return "semimonthly", days
    for frequency in ("monthly", "quarterly", "annual"):
        if GAPS[frequency][1] <= median_gap <= GAPS[frequency][2] and fits(frequency):
            return frequency, str(_month_day(dates))
    return None


def _month_day(dates: list[date]) -> int:
    """The day of month a month-based schedule falls on.

    Calendar-anchored (rent on the 1st, a bill on the 31st clamped in short
    months): >= 75% of dates within 3 days of the most common day -> that day
    (ties go to the later day, so an end-of-month bill stays on the 31st).
    Drifting (billed every ~30 days, so the day moves through the month):
    the latest occurrence's day.
    """
    counts = {d.day: sum(x.day == d.day for x in dates) for d in dates}
    common = max(counts, key=lambda day: (counts[day], day))
    near = sum(abs(d.day - common) <= 3 or (common >= 28 and d.day >= 28) for d in dates)
    return common if near / len(dates) >= REGULAR_SHARE else dates[-1].day


def detect(conn: sqlite3.Connection, today: date | None = None) -> list[Detected]:
    """Find recurring patterns in posted transactions on visible, non-loan accounts."""
    today = today or date.today()
    rows = conn.execute(
        """
        SELECT id, account_id, date, amount_cents, name, merchant_name, category, kind
        FROM transactions_effective
        WHERE pending = 0 AND account_hidden = 0 AND account_type != 'loan'
          AND amount_cents != 0 AND date >= ? AND date <= ?
        ORDER BY date
        """,
        ((today - timedelta(days=LOOKBACK_DAYS)).isoformat(), today.isoformat()),
    ).fetchall()

    groups: dict[tuple[str, int, bool], list[sqlite3.Row]] = {}
    for r in rows:
        key = merchant_key(r["merchant_name"], r["name"])
        if key:
            groups.setdefault((key, r["account_id"], r["amount_cents"] > 0), []).append(r)

    found = []
    parents: dict[str, str] | None = None
    for (key, account_id, is_outflow), txns in groups.items():
        hit = _detect_group(key, account_id, is_outflow, txns, today)
        if hit:
            found.append(hit)
            continue
        # Not regular as a whole: maybe several regular payments share the description (rent and savings transfers
        # to one bank), each with its own amount. Try each amount band on its own.
        bands = _amount_bands(txns)
        if len(bands) < 2:
            continue
        if parents is None:                              # top-level category per key (subcategories -> parent)
            parents = dict(conn.execute("SELECT key, COALESCE(parent_key, key) FROM categories").fetchall())
        for band in bands:
            typical = round(statistics.median(abs(t["amount_cents"]) for t in band))
            hit = _detect_group(f"{key}{AMOUNT_MARK}{typical}", account_id, is_outflow, band, today, parents=parents)
            if hit:
                found.append(hit)
    return found


def split_key(key: str) -> tuple[str, int | None]:
    """'fake bank transfer @145000' -> ('fake bank transfer', 145000); a plain key -> (key, None)."""
    base, mark, cents = key.partition(AMOUNT_MARK)
    return (base, int(cents)) if mark and cents.isdigit() else (key, None)


def _amount_bands(txns: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    """A description's transactions grouped by amount: by size, a new band starts when an amount is more than
    AMOUNT_TOLERANCE above the band's smallest. Each band is returned in date order."""
    bands: list[list[sqlite3.Row]] = []
    for t in sorted(txns, key=lambda t: abs(t["amount_cents"])):
        if bands and abs(t["amount_cents"]) <= abs(bands[-1][0]["amount_cents"]) * (1 + AMOUNT_TOLERANCE):
            bands[-1].append(t)
        else:
            bands.append([t])
    return [sorted(band, key=lambda t: t["date"]) for band in bands]


def _detect_group(key: str, account_id: int, is_outflow: bool, txns: list[sqlite3.Row], today: date,
                  parents: dict[str, str] | None = None) -> Detected | None:
    """One group of transactions (date order) as a recurring item, or None if it isn't regular enough. `parents`
    (category key -> top-level key) = an amount band split out of a shared description: its amount must be fixed, and
    everyday spending needs EVERYDAY_MIN_OCCURRENCES."""
    # One per day: a same-day duplicate (e.g. two coffees) isn't a schedule.
    by_day: dict[date, sqlite3.Row] = {}
    for t in txns:
        by_day.setdefault(date.fromisoformat(t["date"]), t)
    dates = sorted(by_day)
    schedule = _schedule(dates)
    if schedule is None:
        return None
    frequency, days_of_month = schedule
    categories = [by_day[d]["category"] for d in dates]
    category = max(set(categories), key=categories.count)
    band = parents is not None
    everyday = band and parents.get(category, category) in EVERYDAY_CATEGORIES
    if len(dates) < (EVERYDAY_MIN_OCCURRENCES if everyday else MIN_OCCURRENCES)[frequency]:
        return None

    amounts = [abs(by_day[d]["amount_cents"]) for d in dates]
    typical = statistics.median(amounts)
    tolerance = BAND_AMOUNT_TOLERANCE if band else AMOUNT_TOLERANCE
    close = [a for a in amounts if abs(a - typical) <= tolerance * typical]
    if len(close) / len(amounts) < REGULAR_SHARE:
        return None
    # Expected amount from the most recent 3, so raises and price changes show up
    # right away (consistency above is still judged over the whole history).
    expected = statistics.median(amounts[-3:])

    last = dates[-1]
    if (today - last).days > 1.5 * GAPS[frequency][0] + 3:
        return None  # stale: it seems to have stopped

    latest = by_day[last]
    kinds = [by_day[d]["kind"] for d in dates]
    kind = "transfer" if kinds.count("transfer") > len(kinds) / 2 else ("bill" if is_outflow else "income")
    return Detected(
        merchant_key=key,
        account_id=account_id,
        name=latest["merchant_name"] or latest["name"],
        category_key=category,
        amount_cents=round(expected) * (1 if is_outflow else -1),
        amount_varies=(max(amounts[-3:]) - min(amounts[-3:])) > VARIES_THRESHOLD * expected,
        frequency=frequency,
        days_of_month=days_of_month,
        last_seen=last,
        next_expected=next_expected_after(frequency, last, days_of_month),
        occurrences=len(dates),
        kind=kind,
    )


# ---------------------------------------------------------------- storing detections

def refresh(conn: sqlite3.Connection, today: date | None = None) -> dict[str, int]:
    """Re-run detection and reconcile with stored items. Returns counts for messages."""
    today = today or date.today()
    detections = detect(conn, today)
    stored = conn.execute("SELECT * FROM recurring_items WHERE source = 'local'").fetchall()
    matched: set[int] = set()
    counts = {"new": 0, "updated": 0, "removed": 0}
    with conn:
        for d in detections:
            row = _stored_item(stored, d)
            if row is not None:
                matched.add(row["id"])
            if row is None:
                conn.execute(
                    """INSERT INTO recurring_items (name, merchant_key, account_id, category_key, amount_cents,
                           amount_varies, frequency, days_of_month, next_expected_date, type, status, source,
                           last_seen_date, occurrences)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'detected', 'local', ?, ?)""",
                    (d.name, d.merchant_key, d.account_id, d.category_key, d.amount_cents, int(d.amount_varies),
                     d.frequency, d.days_of_month, d.next_expected.isoformat(), d.kind,
                     d.last_seen.isoformat(), d.occurrences),
                )
                counts["new"] += 1
            elif row["status"] == "detected":
                conn.execute(
                    """UPDATE recurring_items SET name = ?, merchant_key = ?, category_key = ?, amount_cents = ?,
                           amount_varies = ?, frequency = ?, days_of_month = ?, next_expected_date = ?, type = ?,
                           last_seen_date = ?, occurrences = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (d.name, d.merchant_key, d.category_key, d.amount_cents, int(d.amount_varies), d.frequency,
                     d.days_of_month, d.next_expected.isoformat(), d.kind, d.last_seen.isoformat(), d.occurrences,
                     row["id"]),
                )
                counts["updated"] += 1
            # dismissed: left alone, never resurrected

        # Confirmed items: keep your settings, advance last-seen and next date from history (an item you added by hand
        # advances through the transactions you linked to it).
        for row in conn.execute("SELECT * FROM recurring_items WHERE status = 'confirmed'").fetchall():
            _advance(conn, row)

        # Unreviewed detections that no longer match are removed.
        for row in stored:
            if row["status"] == "detected" and row["id"] not in matched:
                conn.execute("DELETE FROM recurring_items WHERE id = ?", (row["id"],))
                counts["removed"] += 1
    return counts


def _stored_item(stored: list[sqlite3.Row], d: Detected) -> sqlite3.Row | None:
    """The stored local item a detection is (same account and direction): the same key, or for an amount band the same
    description with an amount within AMOUNT_TOLERANCE (a rent increase updates the item instead of adding one)."""
    same = [r for r in stored if r["account_id"] == d.account_id and (r["amount_cents"] > 0) == (d.amount_cents > 0)]
    exact = next((r for r in same if r["merchant_key"] == d.merchant_key), None)
    base, amount = split_key(d.merchant_key)
    if exact is not None or amount is None:
        return exact
    for r in same:
        r_base, r_amount = split_key(r["merchant_key"] or "")
        if r_amount is not None and r_base == base and abs(abs(r["amount_cents"]) - amount) <= AMOUNT_TOLERANCE * amount:
            return r
    return None


# ---------------------------------------------------------------- matching history

def matching_transaction_ids(conn: sqlite3.Connection, item: sqlite3.Row, since: str | None = None) -> set[int]:
    """Transactions that belong to a local item (same merchant key, account, direction; an amount band's item also
    only those within BAND_MATCH_TOLERANCE of its amount)."""
    if not item["merchant_key"] or item["account_id"] is None:
        return set()
    params: list = [item["account_id"]]
    where = "account_id = ? AND pending = 0"
    if since:
        where += " AND date >= ?"
        params.append(since)
    outflow = item["amount_cents"] > 0
    base, _banded = split_key(item["merchant_key"])
    return {
        r["id"]
        for r in conn.execute(f"SELECT id, merchant_name, name, amount_cents FROM transactions WHERE {where}", params)
        if (r["amount_cents"] > 0) == outflow and merchant_key(r["merchant_name"], r["name"]) == base
        and _amount_fits(item, r["amount_cents"])
    }


def _amount_fits(item: sqlite3.Row, cents: int) -> bool:
    """An amount band's item only takes amounts within BAND_MATCH_TOLERANCE of its own; a plain item takes any."""
    if split_key(item["merchant_key"] or "")[1] is None:
        return True
    expected = abs(item["amount_cents"])
    return abs(abs(cents) - expected) <= BAND_MATCH_TOLERANCE * expected


def transaction_ids(conn: sqlite3.Connection, item: sqlite3.Row) -> set[int]:
    """An item's transactions: what matching finds (detected items) plus the ones you linked to it, without the ones
    you linked elsewhere or marked "not part of any recurring item" (recurring_links, migration 0036)."""
    links = dict(conn.execute("SELECT transaction_id, item_id FROM recurring_links").fetchall())
    found = matching_transaction_ids(conn, item) if item["source"] == "local" else set()
    return {t for t in found if t not in links} | {t for t, i in links.items() if i == item["id"]}


def last_matching_date(conn: sqlite3.Connection, item: sqlite3.Row) -> str | None:
    ids = transaction_ids(conn, item)
    if not ids:
        return None
    return conn.execute(
        f"SELECT MAX(date) FROM transactions WHERE id IN ({','.join('?' * len(ids))})", list(ids)
    ).fetchone()[0]


def _advance(conn: sqlite3.Connection, item: sqlite3.Row) -> None:
    """Move an item's last-seen and next date up to its newest transaction (no write when nothing is newer)."""
    seen = last_matching_date(conn, item)
    if seen and (item["last_seen_date"] is None or seen > item["last_seen_date"]):
        nxt = next_expected_after(item["frequency"], date.fromisoformat(seen), item["days_of_month"])
        conn.execute("UPDATE recurring_items SET last_seen_date = ?, next_expected_date = ?, updated_at = CURRENT_TIMESTAMP"
                     " WHERE id = ?", (seen, nxt.isoformat(), item["id"]))


# ---------------------------------------------------------------- transactions and items (user's request 2026-10-16)

def items_of(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict[int, sqlite3.Row]:
    """For transaction rows (transactions_effective) on screen: the CONFIRMED item each one belongs to (the recurring
    icon). Your link decides when there is one (an item, or none); otherwise matching by description, account,
    direction (and amount, for an amount band), as matching_transaction_ids does for one item."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    items = {r["id"]: r for r in conn.execute("SELECT * FROM recurring_items WHERE status = 'confirmed'")}
    links = dict(conn.execute(f"SELECT transaction_id, item_id FROM recurring_links WHERE transaction_id IN"
                              f" ({','.join('?' * len(ids))})", ids).fetchall())
    by_key: dict[tuple[str, int, bool], list[sqlite3.Row]] = {}
    # amount bands first: the more specific item wins
    for item in sorted(items.values(), key=lambda i: split_key(i["merchant_key"] or "")[1] is None):
        if item["source"] == "local" and item["merchant_key"] and item["account_id"] is not None:
            by_key.setdefault((split_key(item["merchant_key"])[0], item["account_id"], item["amount_cents"] > 0),
                              []).append(item)
    result = {}
    for r in rows:
        if r["id"] in links:
            if links[r["id"]] in items:
                result[r["id"]] = items[links[r["id"]]]
            continue
        if r["pending"]:
            continue
        key = (merchant_key(r["merchant_name"], r["name"]), r["account_id"], r["amount_cents"] > 0)
        match = next((i for i in by_key.get(key, []) if _amount_fits(i, r["amount_cents"])), None)
        if match is not None:
            result[r["id"]] = match
    return result


def link_of(conn: sqlite3.Connection, transaction_id: int) -> tuple[bool, int | None]:
    """(has a link of yours, its item id or None = "not part of any recurring item")."""
    row = conn.execute("SELECT item_id FROM recurring_links WHERE transaction_id = ?", (transaction_id,)).fetchone()
    return (row is not None, row[0] if row else None)


def set_link(conn: sqlite3.Connection, transaction_id: int, item_id: int | None) -> None:
    """Your choice for one transaction: part of this confirmed item, or None = not part of any recurring item."""
    if item_id is not None:
        item = conn.execute("SELECT * FROM recurring_items WHERE id = ? AND status = 'confirmed'", (item_id,)).fetchone()
        if item is None:
            raise RecurringError("Choose one of your confirmed recurring items.")
    with conn:
        conn.execute("INSERT INTO recurring_links (transaction_id, item_id) VALUES (?, ?)"
                     " ON CONFLICT (transaction_id) DO UPDATE SET item_id = excluded.item_id", (transaction_id, item_id))
        if item_id is not None:
            _advance(conn, item)


def clear_link(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Back to automatic: matching by description decides again."""
    with conn:
        conn.execute("DELETE FROM recurring_links WHERE transaction_id = ?", (transaction_id,))


def _schedule_days(frequency: str, day: date) -> str | None:
    """The days of the month a schedule starting on `day` keeps (as detection stores them)."""
    if frequency in ("weekly", "biweekly"):
        return None
    if frequency == "semimonthly":
        other = day.day + 15 if day.day <= 15 else day.day - 15
        return ",".join(str(d) for d in sorted((day.day, other)))
    return str(day.day)


def create_from_transaction(conn: sqlite3.Connection, transaction_id: int, frequency: str) -> int:
    """"Make it recurring" from a transaction's edit panel: a confirmed item with its name, amount, account and
    category, on `frequency` from its date. Keyed to this amount (an amount band, "<description> @<cents>"), so later
    transactions with the same text and a similar amount join it, but other payments sharing the text don't (rent vs
    savings transfers). An item already stored for this description and amount (detected or dismissed) is reused and
    confirmed. The transaction is linked to it. Returns the item's id."""
    if frequency not in FREQUENCIES:
        raise RecurringError("Choose how often it repeats.")
    t = conn.execute("SELECT * FROM transactions_effective WHERE id = ?", (transaction_id,)).fetchone()
    if t is None:
        raise RecurringError("That transaction no longer exists.")
    day = date.fromisoformat(t["date"])
    kind = "transfer" if t["kind"] == "transfer" else ("bill" if t["amount_cents"] > 0 else "income")
    base = merchant_key(t["merchant_name"], t["name"])
    key = f"{base}{AMOUNT_MARK}{abs(t['amount_cents'])}" if base else None
    days = _schedule_days(frequency, day)
    fields = (t["merchant_name"] or t["name"] or "Recurring item", t["category"], t["amount_cents"], frequency, days,
              next_expected_after(frequency, day, days).isoformat(), kind, t["date"])
    with conn:
        existing = None
        if key:
            stored = conn.execute("SELECT * FROM recurring_items WHERE source = 'local'").fetchall()
            probe = Detected(key, t["account_id"], "", None, t["amount_cents"], False, frequency, days, day, day, 1, kind)
            existing = _stored_item(stored, probe)
        if existing is not None:
            conn.execute("""UPDATE recurring_items SET name = ?, category_key = ?, amount_cents = ?, frequency = ?,
                                days_of_month = ?, next_expected_date = ?, type = ?, last_seen_date = ?, status = 'confirmed',
                                updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (*fields, existing["id"]))
            item_id = existing["id"]
        else:
            item_id = conn.execute(
                """INSERT INTO recurring_items (name, category_key, amount_cents, frequency, days_of_month,
                       next_expected_date, type, last_seen_date, merchant_key, account_id, status, source, occurrences)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, 1)""",
                (*fields, key, t["account_id"], "local" if key else "manual")).lastrowid
        conn.execute("INSERT INTO recurring_links (transaction_id, item_id) VALUES (?, ?)"
                     " ON CONFLICT (transaction_id) DO UPDATE SET item_id = excluded.item_id", (transaction_id, item_id))
        _advance(conn, conn.execute("SELECT * FROM recurring_items WHERE id = ?", (item_id,)).fetchone())
    return item_id


# ---------------------------------------------------------------- your review

class RecurringError(ValueError):
    """A change was refused; the message is shown to you."""


def list_items(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, a.name AS account_name, a.mask AS account_mask, c.label AS category_label
        FROM recurring_items r
        LEFT JOIN accounts a ON a.id = r.account_id
        LEFT JOIN categories c ON c.key = r.category_key
        WHERE r.status = ?
        ORDER BY r.type = 'income' DESC, ABS(r.amount_cents) DESC
        """,
        (status,),
    ).fetchall()


def upcoming(conn: sqlite3.Connection, today: date | None = None, days: int = 7) -> list[dict]:
    """Confirmed bills and income due from today through today + `days`, soonest first.

    Items whose expected date has already passed come first, flagged `overdue`
    (the forecast assumes an overdue bill is paid today). Dates before an item's
    next expected date are skipped: that cycle was already seen.
    """
    today = today or date.today()
    end = today + timedelta(days=days)
    result = []
    for item in list_items(conn, "confirmed"):
        if not item["next_expected_date"]:
            continue
        expected = date.fromisoformat(item["next_expected_date"])
        if expected < today:
            dates, overdue = [expected], True
        else:
            dates = occurrences(item["frequency"], expected, expected, end, item["days_of_month"])
            overdue = False
        for day in dates:
            result.append({"date": day.isoformat(), "overdue": overdue, "name": item["name"],
                           "amount_cents": item["amount_cents"], "type": item["type"],
                           "account_name": item["account_name"], "account_mask": item["account_mask"]})
    result.sort(key=lambda r: (not r["overdue"], r["date"], r["type"] != "income"))
    return result


def set_status(conn: sqlite3.Connection, item_id: int, status: str) -> None:
    if status not in ("detected", "confirmed", "dismissed"):
        raise RecurringError("Unknown status.")
    if conn.execute(
        "UPDATE recurring_items SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, item_id)
    ).rowcount == 0:
        raise LookupError(f"No recurring item {item_id}")
    conn.commit()


# ---------------------------------------------------------------- actions (user's request 2026-11-03; migration 0060)

# A confirmed item's "Then do this", applied to each of its transactions by rules.apply_all (before any rule). The rule
# builder's actions, apart from "Recurring" (it's this item already).
ACTION_KINDS = ("set_category", "set_description", "add_tag", "purpose", "not_transfer", "accept")


def actions_of(conn: sqlite3.Connection, item_id: int) -> list:
    """The item's actions, in the order you added them (rules.Action)."""
    from fintrack.rules import Action

    return [Action(r[0], r[1]) for r in
            conn.execute("SELECT kind, value FROM recurring_actions WHERE item_id = ? ORDER BY id", (item_id,))]


def form_actions(conn: sqlite3.Connection, item: sqlite3.Row | None) -> list:
    """What the window shows under "Then do this": the item's actions, or for an item without any yet a "Set category"
    for the category it has (so saving it applies that category to its transactions)."""
    from fintrack.rules import Action

    if item is None:
        return []
    found = actions_of(conn, item["id"])
    if not found and item["category_key"]:
        found = [Action("set_category", item["category_key"])]
    return found


def save_item(
    conn: sqlite3.Connection, item_id: int | None, *, name: str, amount_text: str, direction: str,
    frequency: str, next_date: str, account_id: int | None, category_key: str | None = None,
    days_of_month: str = "", actions: list | None = None,
) -> int:
    """Create a manual item (item_id None) or edit an existing one. Saved items are confirmed. `actions` (rules.Action,
    from the window's "Then do this"; None leaves them as they are) replace the item's; its category is then the one
    its "Set category" sets (the one Budgets and the forecast use). Run rules.run afterwards to apply them."""
    from fintrack.money import parse_amount

    if actions is not None:
        if any(a.kind not in ACTION_KINDS for a in actions):
            raise RecurringError("Choose what each action does.")
        category_key = next((a.value for a in actions if a.kind == "set_category"), category_key)

    name = (name or "").strip()
    if not name:
        raise RecurringError("Give it a name.")
    try:
        amount = parse_amount(amount_text)
    except ValueError as exc:
        raise RecurringError(str(exc)) from None
    if direction not in ("in", "out"):
        raise RecurringError("Choose money in or money out.")
    if frequency not in FREQUENCIES:
        raise RecurringError("Choose how often it happens.")
    try:
        next_day = date.fromisoformat(next_date)
    except ValueError:
        raise RecurringError("Next date must be a date (YYYY-MM-DD).") from None
    dom = None
    if frequency == "semimonthly":
        try:
            days = sorted({int(d) for d in (days_of_month or "").replace(" ", "").split(",") if d})
        except ValueError:
            days = []
        if len(days) != 2 or not all(1 <= d <= 31 for d in days):
            raise RecurringError("Twice a month needs two days of the month, e.g. 1,15.")
        dom = ",".join(str(d) for d in days)
    elif frequency in ("monthly", "quarterly", "annual"):
        dom = str(next_day.day)
    signed = amount if direction == "out" else -amount
    kind = "income" if direction == "in" else "bill"
    category_row = conn.execute("SELECT kind FROM categories WHERE key = ?", (category_key,)).fetchone() if category_key else None
    if category_row and category_row[0] == "transfer":
        kind = "transfer"

    if item_id is None:
        item_id = conn.execute(
            """INSERT INTO recurring_items (name, account_id, category_key, amount_cents, frequency, days_of_month,
                   next_expected_date, type, status, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 'manual')""",
            (name, account_id, category_key or None, signed, frequency, dom, next_day.isoformat(), kind),
        ).lastrowid
    else:
        if conn.execute(
            """UPDATE recurring_items SET name = ?, account_id = ?, category_key = ?, amount_cents = ?,
                   frequency = ?, days_of_month = ?, next_expected_date = ?, type = ?, status = 'confirmed',
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (name, account_id, category_key or None, signed, frequency, dom, next_day.isoformat(), kind, item_id),
        ).rowcount == 0:
            raise LookupError(f"No recurring item {item_id}")
    if actions is not None:
        conn.execute("DELETE FROM recurring_actions WHERE item_id = ?", (item_id,))
        conn.executemany("INSERT INTO recurring_actions (item_id, kind, value) VALUES (?, ?, ?)",
                         [(item_id, a.kind, a.value) for a in actions])
    conn.commit()
    return item_id


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    """Manual items are deleted; detected ones are dismissed so they don't come back."""
    row = conn.execute("SELECT source FROM recurring_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise LookupError(f"No recurring item {item_id}")
    if row["source"] == "manual":
        conn.execute("DELETE FROM recurring_items WHERE id = ?", (item_id,))
        conn.commit()
    else:
        set_status(conn, item_id, "dismissed")
