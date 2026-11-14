"""Investment files (docs/import-plan.md phase 5, user's decision 2026-11-13): activity and holdings statements.

**Activity** (a broker's history: date, action, symbol, quantity, price, amount). Each action's words are sorted into
ACTION_KINDS (by keywords, `classify`; your choice per account's file is kept in its profile, `Layout.actions`), which
says what the row did (`effect`): shares in or out of a holding, cash in or out of the account's cash, and how much of
it was money paid in from outside (a contribution) or taken out. Amounts and quantities are read as sizes (the action
says which way), except an "other cash move", which keeps the file's sign (+ = cash in, as brokers write it).
- into a **connected** investment account: rows of `investment_transactions` (ids 'imp-<hash>', Plaid's types and
  investment sign: + = cash out), shown and edited like Plaid's (investment_activity.py); for history before Plaid's.
- into a **manual** investment account: changes to its position lines (manual_positions.py), one per line and day:
  shares (`delta_quantity`) on the symbol's line, cash on a "Cash" line; `contributed_cents` says what was paid in
  (Savings' retirement contributions), so a buy contributes 0. Refused while a line would go below 0 (the file doesn't
  say what was held before it: import a holdings statement from before it first).
Re-importing adds nothing (ids, as for bank files); overlap and likely duplicates as for bank files (engine.py).

**Which line a fund goes to** (manual accounts; user's request 2026-11-13): each symbol in the file has a target
(`resolve`): your choice for this account's files (`Layout.holdings`: a line, or a new one), else the line with the same
ticker, else a line whose name is the fund's name, else a new line. A target with a **different ticker** (a proxy: a
fund without its own price tracked through, say, SPY) or **no ticker** (a line kept in dollars) is tracked **by
dollars**: the file's units and prices belong to the real fund, so only its dollar amounts are used (activity: dollar
changes, turned into the proxy's shares at its price that day, as your dollar events are; holdings: the fund's value as
the line's balance in dollars on the statement date), and its prices are never saved for the proxy ticker. Several funds
tracked through one line add up.

**Holdings** (a statement: symbol, description, quantity, price, value, as of one date) into a manual investment
account: each line's balance on that date (shares; a line without a symbol, cash, in dollars); lines the statement
doesn't list go to 0. Its prices are kept at the lowest rank (source 'plaid': a broker's reported price; Alpaca's and
yours win). A day you entered yourself is kept and listed.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal

from fintrack import ledger
from fintrack.imports.layout import Holding, InvRow

ACTION_KINDS = {"buy": "Buy", "reinvest": "Reinvestment (buy)", "sell": "Sell", "income": "Dividend / interest",
                "contribution": "Contribution", "withdrawal": "Withdrawal", "fee": "Fee", "cash": "Other cash move",
                "ignore": "Ignore"}
# Keywords in the broker's words, first match wins. A reinvestment is a buy paid by a dividend (user's decision
# 2026-11-13: the dividend is income, the reinvestment a buy, as for tax): each is paired with its dividend row
# (`pair_reinvestments`); one without is offered a dividend added for it.
ACTION_WORDS = [
    ("reinvest", ("REINVEST",)),
    ("buy", ("BUY", "BOUGHT", "PURCHASE")),
    ("sell", ("SELL", "SOLD", "REDEMPTION", "REDEEM")),
    ("income", ("DIVIDEND", "INTEREST", "CAP GAIN", "CAPITAL GAIN", "INCOME")),
    ("fee", ("FEE", "COMMISSION", "EXPENSE")),
    ("contribution", ("CONTRIBUTION", "DEPOSIT", "TRANSFER IN", "ROLLOVER", "EMPLOYER", "MATCH", "RECEIVED")),
    ("withdrawal", ("WITHDRAWAL", "DISTRIBUTION", "TRANSFER OUT", "DISBURSEMENT")),
]
CASH_WORDS = ("CASH", "MONEY MARKET", "SWEEP", "CORE")
ID_PREFIX = "imp-"
PRICE_SOURCE = "plaid"               # the lowest rank in ticker_prices: a broker's reported price


def action_key(text: str) -> str:
    """What a mapping is kept under: the first keyword in the words, else their first three words ('YOU BOUGHT FAKE
    500 INDEX' and 'YOU BOUGHT FAKE FUND' share 'BOUGHT')."""
    upper = (text or "").upper()
    for _kind, words in ACTION_WORDS:
        for word in words:
            if word in upper:
                return word
    return " ".join(re.sub(r"\([^)]*\)|[^A-Z ]", " ", upper).split()[:3]) or upper[:30]


def classify(text: str) -> str:
    upper = (text or "").upper()
    for kind, words in ACTION_WORDS:
        if any(w in upper for w in words):
            return kind
    return "cash"


@dataclass(frozen=True)
class Effect:
    shares: Decimal | None           # + in / - out of the symbol's line (None: no shares move)
    cash: int                        # cents into (+) / out of (-) the account's cash
    contributed: int = 0             # cents paid in from outside (+) / taken out (-)
    fee: bool = False


def effect(row: InvRow, kind: str) -> Effect | None:
    """What a row did, by its kind; None if it does nothing (ignored, or nothing to go on)."""
    q = abs(row.quantity) if row.quantity is not None else None
    a = abs(row.cash_cents) if row.cash_cents is not None else 0
    if q is None and row.symbol and row.price and a and kind in ("buy", "sell"):
        q = (Decimal(a) / 100 / abs(row.price)).quantize(Decimal("0.000001"))
    shares_ok = bool(row.symbol) and q is not None and q > 0
    if kind in ("buy", "reinvest"):
        return Effect(q if shares_ok else None, -a) if (shares_ok or a) else None
    if kind == "sell":
        return Effect(-q if shares_ok else None, a) if (shares_ok or a) else None
    if kind == "income":
        return Effect(None, a) if a else None
    if kind == "contribution":
        return Effect(q, 0, a) if shares_ok else (Effect(None, a, a) if a else None)
    if kind == "withdrawal":
        return Effect(-q, 0, -a) if shares_ok else (Effect(None, -a, -a) if a else None)
    if kind == "fee":
        # taken as shares (a 401(k)'s fees) when it names shares, else from the cash
        return Effect(-q, 0, 0, True) if shares_ok else (Effect(None, -a, 0, True) if a else None)
    if kind == "cash":
        return Effect(None, row.cash_cents or 0) if row.cash_cents else None
    return None


@dataclass
class InvPlanned:
    """An activity row in the plan (shaped like engine.Planned for the overlap and the page)."""
    row: InvRow
    transaction_id: str
    kind: str
    effect: Effect
    status: str = "new"
    duplicate_of: object = None
    take: bool = True
    target: object = None            # Target (manual accounts, rows with a symbol)
    paired: bool = False             # a reinvestment and its dividend, found together
    added: bool = False              # a dividend added for a reinvestment the file has none for


@dataclass
class HoldPlanned:
    row: Holding
    status: str = "new"              # new / already (the same balance is there) / yours (a day you entered: kept)
    take: bool = True
    line: str = ""                   # the file's symbol ('' = cash)
    target: object = None            # Target (not for cash)


@dataclass(frozen=True)
class Target:
    """Where one of the file's funds goes in a manual account."""
    line_id: int | None              # an existing line; None = a new line for the file's symbol
    ticker: str | None               # that line's ticker (None: a line kept in dollars)
    by_dollars: bool                 # a proxy (another ticker) or a dollars line: the file's dollars only
    name: str

    @property
    def key(self) -> tuple:
        return ("line", self.line_id) if self.line_id is not None else ("new", self.ticker)


def _norm(text: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def account_lines(conn: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    """A manual account's position lines a fund can go to (its Cash line aside), tickers first."""
    return conn.execute("""SELECT * FROM manual_positions WHERE account_id = ?
                           AND NOT (ticker IS NULL AND lower(name) = 'cash') ORDER BY ticker IS NULL, ticker, name""",
                        (account_id,)).fetchall()


def resolve(lines: list[sqlite3.Row], symbol: str, description: str, mapping: dict[str, str]) -> Target:
    """A file symbol's target: `mapping[symbol]` ('new', a line id, or a line's ticker), else the same ticker's line,
    else a line named like the fund, else a new line."""
    choice = mapping.get(symbol, "")
    line = None
    if choice != "new":
        line = (next((l for l in lines if str(l["id"]) == choice or (l["ticker"] and l["ticker"] == choice)), None)
                if choice else None)
        if line is None:
            name = _norm(description)
            line = (next((l for l in lines if l["ticker"] == symbol), None)
                    or next((l for l in lines if name and _norm(l["name"]) == name), None))
    if line is None:
        return Target(None, symbol, False, description or symbol)
    return Target(line["id"], line["ticker"], line["ticker"] != symbol, line["name"])


def plaid_amount(e: Effect, row: InvRow) -> int:
    """Plaid's investment sign for the row (+ = cash out): the cash it moved, else the dollars of the shares."""
    if e.cash:
        return -e.cash
    dollars = abs(row.cash_cents or 0)
    return dollars if (e.shares or 0) > 0 else -dollars


def activity_id(account_id: int, row: InvRow, kind: str, occurrence: int) -> str:
    raw = "|".join((str(account_id), row.posted, kind, row.symbol, str(row.quantity), str(row.cash_cents),
                    row.description, str(occurrence)))
    return ID_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------- activity: the plan

def plan_activity(conn: sqlite3.Connection, plan, rows: list[InvRow], mine: dict[str, str]) -> list:
    """Fill plan.rows with the activity; returns the existing rows (engine.Existing, is Plaid's) for the overlap."""
    from fintrack.imports import engine

    seen: Counter = Counter()
    for row in sorted(rows, key=lambda r: (r.posted, r.line)):
        key = action_key(row.action)
        kind = mine.get(key) or classify(row.action)
        plan.action_keys.setdefault(key, [kind, row.action, 0])[2] += 1
        e = effect(row, kind)
        if e is None:
            if kind != "ignore":
                plan.skipped.append((row.line, f"nothing to import for '{row.action}'"))
            continue
        row = replace(row, amount_cents=plaid_amount(e, row))
        ident = (row.posted, kind, row.symbol, row.quantity, row.cash_cents, row.description)
        tid = activity_id(plan.account.id, row, kind, seen[ident])
        seen[ident] += 1
        p = InvPlanned(row, tid, kind, e)
        if plan.account.is_manual and row.symbol and e.shares is not None:
            p.target = _target(conn, plan, row.symbol, row.description)
        found = conn.execute("SELECT 1 FROM investment_transactions WHERE plaid_investment_transaction_id = ?",
                             (tid,)).fetchone()
        if found:
            p.status, p.take = "already", False
        plan.rows.append(p)
    pair_reinvestments(conn, plan)
    # the account's other activity, alike for manual and connected accounts (the ledger; yours and imported aren't
    # Plaid's); a two-row activity's second row ('<id>/2') is the same activity as its first
    ids = {p.transaction_id for p in plan.rows}
    return [(engine.Existing(r[0], r[1], None, r[2], r[3]), not r[4].startswith((ID_PREFIX, "act-")))
            for r in conn.execute("""SELECT id, date, amount_cents, name, plaid_investment_transaction_id
                                     FROM investment_transactions WHERE account_id = ? ORDER BY date""",
                                  (plan.account.id,))
            if r[4].split("/", 1)[0] not in ids and "/" not in r[4]]


def _target(conn: sqlite3.Connection, plan, symbol: str, description: str) -> Target:
    """A symbol's target, noted in plan.holding_keys for the page's Holding pickers."""
    if plan.lines is None:
        plan.lines = account_lines(conn, plan.account.id)
    t = resolve(plan.lines, symbol, description, plan.detection.layout.holdings)
    entry = plan.holding_keys.setdefault(symbol, {"target": t, "description": description, "count": 0})
    entry["count"] += 1
    return t


CASH_KEY = ("cash",)


def _position_of(conn: sqlite3.Connection, account_id: int, key: tuple) -> sqlite3.Row | None:
    if key == CASH_KEY:
        return _line(conn, account_id, "")
    if key[0] == "line":
        return conn.execute("SELECT * FROM manual_positions WHERE id = ?", (key[1],)).fetchone()
    return _line(conn, account_id, key[1])


def is_reinvestment(p: InvPlanned) -> bool:
    """A reinvestment: its kind, or a buy whose words say so (a profile saved before 'reinvest' existed maps it to buy)."""
    return p.kind == "reinvest" or (p.kind == "buy" and "REINVEST" in p.row.action.upper())


PAIR_DAYS = 3                        # a dividend and its reinvestment can be dated a day or two apart


def pair_reinvestments(conn: sqlite3.Connection, plan) -> None:
    """Pair each reinvestment with a dividend row (same fund or none named, the same dollars, within PAIR_DAYS); the two
    import as they are: the dividend as income, the reinvestment as a buy. One without a dividend is counted
    (`plan.unpaired`) and, unless you said no (`plan.add_dividends`), gets one added: a dividend of its dollars on its
    date (id from the reinvestment's, so re-importing finds it again), so the income side is there."""
    from datetime import date as _date

    def days(a: str, b: str) -> int:
        return abs((_date.fromisoformat(a) - _date.fromisoformat(b)).days)

    incomes = [p for p in plan.rows if p.kind == "income"]
    used: set[int] = set()
    for r in [p for p in plan.rows if is_reinvestment(p)]:
        amount = abs(r.row.cash_cents or 0)
        match = next((d for d in incomes if id(d) not in used and amount and abs(d.row.cash_cents or 0) == amount
                      and d.row.symbol in ("", r.row.symbol) and days(d.row.posted, r.row.posted) <= PAIR_DAYS), None)
        if match is not None:
            used.add(id(match))
            r.paired = match.paired = True
            continue
        if not amount:
            continue
        plan.unpaired += 1
        if not plan.add_dividends:
            continue
        row = InvRow(r.row.line, r.row.posted, "Dividend (added for a reinvestment)", r.row.symbol, r.row.description,
                     None, None, amount)
        e = Effect(None, amount)
        row = replace(row, amount_cents=plaid_amount(e, row))
        tid = ID_PREFIX + hashlib.sha256(f"{r.transaction_id}|dividend".encode()).hexdigest()[:24]
        added = InvPlanned(row, tid, "income", e, added=True, paired=True)
        r.paired = True
        if conn.execute("SELECT 1 FROM investment_transactions WHERE plaid_investment_transaction_id = ?",
                        (tid,)).fetchone():
            added.status, added.take = "already", False
        plan.rows.append(added)


def _held_before(conn: sqlite3.Connection, account_id: int, key: tuple, day: str) -> Decimal:
    """What a line held before `day`: shares, or dollars (the Cash line); 0 if it doesn't exist yet."""
    position = _position_of(conn, account_id, key)
    before = conn.execute("""SELECT quantity, value_cents FROM manual_position_changes WHERE position_id = ? AND date < ?
                             ORDER BY date DESC LIMIT 1""", (position["id"], day)).fetchone() if position else None
    if before is None:
        return Decimal(0)
    return Decimal(before["value_cents"] or 0) / 100 if key == CASH_KEY else Decimal(str(before["quantity"] or 0))


def _dollars(p: InvPlanned) -> int:
    """A by-dollars row's change in its line: the file's dollars, signed like its shares (in +, out -)."""
    return abs(p.row.cash_cents or 0) * (1 if (p.effect.shares or 0) > 0 else -1)


def _moves(p: InvPlanned) -> list[tuple[tuple, str, Decimal]]:
    """(line key, 'shares' / 'dollars', change) a row makes: on its fund's line (shares, or dollars when tracked by
    dollars), and dollars on the Cash line."""
    moves = []
    if p.effect.shares is not None:
        t = p.target or Target(None, p.row.symbol, False, p.row.symbol)
        if t.by_dollars:
            moves.append((t.key, "dollars", Decimal(_dollars(p)) / 100))
        else:
            moves.append((t.key, "shares", p.effect.shares))
    if p.effect.cash:
        moves.append((CASH_KEY, "dollars", Decimal(p.effect.cash) / 100))
    return moves


def check_lines(conn: sqlite3.Connection, plan) -> None:
    """A manual account: what stops the import (plan.problems): a fund tracked by dollars without a dollar amount, a
    line going below 0 (what was held before the file isn't in it), or a day a line already has an entry on."""
    taken = sorted((p for p in plan.rows if p.take), key=lambda p: (p.row.posted, p.row.line))
    for p in taken:
        if p.target and p.target.by_dollars and not p.row.cash_cents:
            plan.problems.append(f"{p.row.symbol} on {p.row.posted} has no dollar amount, so it can't be tracked "
                                 f"through {p.target.ticker or p.target.name}: choose its own line, or leave it out.")
            return
    # Each line's amount at the end of each day (a day's rows in any order: a reinvestment can be listed above the
    # dividend that paid for it, as a newest-first file does; 2026-11-13).
    running: dict[tuple, Decimal] = {}
    by_day: dict[str, list[InvPlanned]] = defaultdict(list)
    for p in taken:
        by_day[p.row.posted].append(p)
    for day in sorted(by_day):
        touched: dict[tuple, str] = {}
        for p in by_day[day]:
            for key, unit, change in _moves(p):
                if unit == "dollars" and key != CASH_KEY:
                    continue                             # a proxy's dollars become shares at the price: can't run out here
                if key not in running:
                    running[key] = _held_before(conn, plan.account.id, key, day)
                running[key] += change
                touched[key] = p.row.symbol
        for key, symbol in touched.items():
            if running[key] < Decimal("-0.000001"):
                what = f"{symbol} shares" if key != CASH_KEY else "the account's cash"
                plan.problems.append(f"On {day}, {what} would go below 0: the file doesn't say what was held before "
                                     "it. Import a holdings statement from before its first date first."
                                     + (" A reinvestment without its dividend does this: add the dividends (above)."
                                        if key == CASH_KEY and plan.unpaired and not plan.add_dividends else ""))
                return
    units: dict[tuple, set] = defaultdict(set)
    for p in taken:
        for key, unit, _change in _moves(p):
            units[(key, p.row.posted)].add(unit)
    for (key, day), kinds in sorted(units.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))):
        position = _position_of(conn, plan.account.id, key)
        name = position["name"] if position else ("Cash" if key == CASH_KEY else str(key[1]))
        if len(kinds) > 1:
            plan.problems.append(f"On {day}, {name} gets both shares and dollars from the file: track those funds "
                                 "through different lines.")
            return
        if position and conn.execute("""SELECT 1 FROM manual_position_changes WHERE position_id = ? AND date = ?
                                        AND from_activity = 0""", (position["id"], day)).fetchone():
            plan.problems.append(f"{position['name']} already has an entry of yours on {day}: change or delete it on "
                                 "the account's page first.")
            return


def _line(conn: sqlite3.Connection, account_id: int, symbol: str) -> sqlite3.Row | None:
    """The account's line for a symbol, or ('') its Cash line (no ticker)."""
    if symbol:
        return conn.execute("SELECT * FROM manual_positions WHERE account_id = ? AND ticker = ?",
                            (account_id, symbol)).fetchone()
    return conn.execute("""SELECT * FROM manual_positions WHERE account_id = ? AND ticker IS NULL
                           ORDER BY lower(name) <> 'cash', id LIMIT 1""", (account_id,)).fetchone()


def _make_line(conn: sqlite3.Connection, account_id: int, symbol: str, name: str, batch_id: int) -> int:
    kind = "cash" if not symbol else ("fund" if len(symbol) == 5 and symbol.endswith("X") else "stock")
    return conn.execute("INSERT INTO manual_positions (account_id, ticker, name, kind, import_batch_id) VALUES (?, ?, ?, ?, ?)",
                        (account_id, symbol or None, (name or symbol or "Cash")[:60], kind, batch_id)).lastrowid


# ---------------------------------------------------------------- holdings: the plan

def is_cash(h: Holding) -> bool:
    return not h.symbol or (h.quantity is None and any(w in h.description.upper() for w in CASH_WORDS))


def plan_positions(conn: sqlite3.Connection, plan, holdings: list[Holding]) -> None:
    from fintrack import manual_positions

    for h in holdings:
        cash = is_cash(h)
        p = HoldPlanned(h, line="" if cash else h.symbol)
        if not cash and h.quantity is None and h.value_cents is None:
            continue
        if not cash:
            p.target = _target(conn, plan, h.symbol, h.description)
            if p.target.by_dollars and h.value_cents is None:
                plan.problems.append(f"{h.symbol} has no value on the statement, so it can't be tracked through "
                                     f"{p.target.ticker or p.target.name}: choose its own line.")
        plan.rows.append(p)
    if not plan.as_of:
        return
    # What each line would hold (funds tracked through one line add up), against what it holds that day already.
    wanted: dict[tuple, list] = {}
    for p in plan.rows:
        key = p.target.key if p.target else CASH_KEY
        w = wanted.setdefault(key, [0, Decimal(0), p.target.by_dollars if p.target else True, []])
        w[0] += p.row.value_cents or 0
        w[1] += p.row.quantity or 0
        w[3].append(p)
    for key, (cents, quantity, by_dollars, rows) in wanted.items():
        position = _position_of(conn, plan.account.id, key)
        on_day = conn.execute("""SELECT * FROM manual_position_changes WHERE position_id = ? AND date = ?
                                 AND from_activity = 0""", (position["id"], plan.as_of)).fetchone() if position else None
        if on_day is None:
            continue
        if key == CASH_KEY or (by_dollars and not position["ticker"]):
            same = on_day["entry"] is None and on_day["value_cents"] == cents
        elif by_dollars:
            same = on_day["entry"] == "opening" and on_day["cash_cents"] == cents
        else:
            same = on_day["entry"] is None and abs((on_day["quantity"] or 0) - float(quantity)) < 1e-9
        for p in rows:
            p.status, p.take = ("already" if same else "yours"), False
    listed = {p.target.line_id for p in plan.rows if p.target and p.target.line_id is not None}
    plan.zeroed = [r["ticker"] for r in conn.execute(
        "SELECT * FROM manual_positions WHERE account_id = ? AND ticker IS NOT NULL", (plan.account.id,))
        if r["id"] not in listed and r["ticker"] not in {p.line for p in plan.rows}
        and (a := manual_positions._amount_on(conn, r["id"], plan.as_of)) and (a["quantity"] or 0) > 0]


# ---------------------------------------------------------------- writing

def apply(conn: sqlite3.Connection, plan) -> tuple[int | None, int]:
    from fintrack import manual_positions
    from fintrack.imports import engine

    new = plan.new
    if not new and not getattr(plan, "zeroed", None):
        return None, 0
    if plan.kind == "positions":
        date_from = date_to = plan.as_of
    else:
        date_from, date_to = min(p.row.posted for p in new), max(p.row.posted for p in new)
    touched: set[int] = set()
    try:
        with conn:
            batch_id = conn.execute(
                """INSERT INTO import_batches (account_id, file_name, file_sha256, rows_added, date_from, date_to,
                                               overlap_choice, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan.account.id, plan.file_name, plan.file_sha256, len(new), date_from, date_to, plan.choice,
                 plan.kind)).lastrowid
            if plan.kind == "positions":
                _write_positions(conn, plan, new, batch_id, touched)
            else:
                _write_activity(conn, plan, new, batch_id)
            engine.save_profile(conn, plan.account.id, plan.detection, plan.categories)
            for position_id in touched:
                manual_positions.rederive(conn, position_id)
    except manual_positions.PositionError as exc:
        raise engine.ImportFileError(f"Not imported: {exc}") from None
    if plan.account.is_manual:
        # its holdings follow its activity (ledger.py); a problem there takes the whole import back
        try:
            clashes = ledger.rebuild_positions(conn, plan.account.id)
        except manual_positions.PositionError as exc:
            undo(conn, batch_id, plan.account.id, date_from)
            raise engine.ImportFileError(f"Not imported: {exc}") from None
        if clashes:
            undo(conn, batch_id, plan.account.id, date_from)
            raise engine.ImportFileError(f"Not imported: {clashes[0]} already has an entry of yours.")
        manual_positions.revalue(conn, plan.account.id, since=date_from)
    conn.commit()
    return batch_id, len(new)


def _save_price(conn, symbol: str, day: str, price) -> None:
    from fintrack import manual_positions
    if symbol and price and price > 0:
        manual_positions._save_price(conn, symbol, day, float(price), PRICE_SOURCE)


def _write_positions(conn, plan, new: list[HoldPlanned], batch_id: int, touched: set[int]) -> None:
    cash_cents = sum(p.row.value_cents or 0 for p in new if not p.line)
    by_line: dict[tuple, list[HoldPlanned]] = defaultdict(list)
    for p in new:
        if p.line:
            by_line[p.target.key].append(p)
    for key, rows in by_line.items():
        t, h = rows[0].target, rows[0].row
        position = _position_of(conn, plan.account.id, key)
        position_id = position["id"] if position else _make_line(conn, plan.account.id, h.symbol, h.description, batch_id)
        cents = sum(p.row.value_cents or 0 for p in rows)
        if t.by_dollars and not t.ticker:                # a line kept in dollars: the funds' value
            row = (None, cents, None, None)
        elif t.by_dollars:                               # a proxy: the funds' value, as the proxy's shares that day
            row = (0.0, None, cents, "opening")
        else:
            price = h.price or (Decimal(h.value_cents) / 100 / h.quantity if h.value_cents and h.quantity else None)
            _save_price(conn, h.symbol, plan.as_of, price)
            row = (float(abs(h.quantity)), None, None, None) if h.quantity is not None else (0.0, None, cents, "opening")
        conn.execute("DELETE FROM manual_position_changes WHERE position_id = ? AND date = ? AND from_activity = 1",
                     (position_id, plan.as_of))         # the statement is that day's balance (rebuilt around it)
        conn.execute("""INSERT INTO manual_position_changes (position_id, date, quantity, value_cents, cash_cents, entry,
                        import_batch_id) VALUES (?, ?, ?, ?, ?, ?, ?)""", (position_id, plan.as_of, *row, batch_id))
        touched.add(position_id)
    if any(not p.line for p in new):
        position = _line(conn, plan.account.id, "")
        position_id = position["id"] if position else _make_line(conn, plan.account.id, "", "Cash", batch_id)
        conn.execute("DELETE FROM manual_position_changes WHERE position_id = ? AND date = ? AND from_activity = 1",
                     (position_id, plan.as_of))
        conn.execute("""INSERT INTO manual_position_changes (position_id, date, value_cents, import_batch_id)
                        VALUES (?, ?, ?, ?)""", (position_id, plan.as_of, max(cash_cents, 0), batch_id))
        touched.add(position_id)
    for symbol in getattr(plan, "zeroed", []):           # held before, not on the statement: sold
        position = _line(conn, plan.account.id, symbol)
        conn.execute("DELETE FROM manual_position_changes WHERE position_id = ? AND date = ? AND from_activity = 1",
                     (position["id"], plan.as_of))
        conn.execute("""INSERT OR IGNORE INTO manual_position_changes (position_id, date, quantity, import_batch_id)
                        VALUES (?, ?, 0, ?)""", (position["id"], plan.as_of, batch_id))
        touched.add(position["id"])


def _write_activity(conn, plan, new: list[InvPlanned], batch_id: int) -> None:
    """The file's activity into the ledger (ledger.py), alike for manual and connected accounts: a manual account's rows
    point at its lines (made for new symbols), a connected one's at Plaid's security for the ticker."""
    made: dict[str, int] = {}                            # new lines made in this import, by symbol
    for p in new:
        r, e = p.row, p.effect
        security, by_dollars = None, False
        if e.shares is not None and r.symbol:
            if plan.account.is_manual:
                t = p.target or Target(None, r.symbol, False, r.symbol)
                line_id = t.line_id
                if line_id is None:
                    line_id = made.get(r.symbol) or _make_line(
                        conn, plan.account.id, r.symbol, r.description if r.description != r.action else r.symbol,
                        batch_id)
                    made[r.symbol] = line_id
                security, by_dollars = ledger.line_security(line_id), t.by_dollars
            else:
                found = conn.execute("""SELECT security_id FROM security_prices WHERE ticker = ? ORDER BY price_date DESC
                                        LIMIT 1""", (r.symbol,)).fetchone()
                # Plaid's security for the ticker, else the ticker itself, so the row still shows it (as a manual
                # account's does; docs/parity-plan.md)
                security = found[0] if found else f"{ledger.TICKER_PREFIX}{r.symbol}"
        if plan.account.is_manual and not by_dollars:   # a proxy's prices aren't the fund's
            _save_price(conn, r.symbol, r.posted, r.price)
        name = r.description if r.description != r.action or not r.symbol else f"{r.action} {r.symbol}"
        ledger.write(conn, plan.account.id, r.posted, name,
                     ledger.rows_for(p.transaction_id, p.kind, e.shares, e.cash, abs(r.cash_cents or 0), r.action,
                                     by_dollars),
                     security, r.price, batch_id)


def undo(conn: sqlite3.Connection, batch_id: int, account_id: int, since: str | None) -> int:
    """Remove what an investment import added. Returns how many of the file's rows it had added."""
    from fintrack import manual_positions
    from fintrack.imports import engine

    manual = engine.account_info(conn, account_id).is_manual
    try:
        with conn:
            removed = conn.execute("SELECT rows_added FROM import_batches WHERE id = ?", (batch_id,)).fetchone()[0]
            conn.execute("DELETE FROM investment_transactions WHERE import_batch_id = ?", (batch_id,))
            lines = [r[0] for r in conn.execute("""SELECT DISTINCT position_id FROM manual_position_changes
                                                    WHERE import_batch_id = ?""", (batch_id,))]
            conn.execute("DELETE FROM manual_position_changes WHERE import_batch_id = ?", (batch_id,))
            conn.execute("UPDATE import_batches SET undone_at = CURRENT_TIMESTAMP WHERE id = ?", (batch_id,))
        if manual:
            ledger.rebuild_positions(conn, account_id)  # the rest of its activity, without these rows
        with conn:
            # lines this import made that nothing uses now
            conn.execute("""DELETE FROM manual_positions WHERE import_batch_id = ?
                            AND NOT EXISTS (SELECT 1 FROM manual_position_changes c WHERE c.position_id = manual_positions.id)
                            AND NOT EXISTS (SELECT 1 FROM investment_transactions t
                                            WHERE t.security_id = 'mpos:' || manual_positions.id)""", (batch_id,))
            for position_id in lines:
                if conn.execute("SELECT 1 FROM manual_positions WHERE id = ?", (position_id,)).fetchone():
                    manual_positions.rederive(conn, position_id)
    except manual_positions.PositionError as exc:
        raise engine.ImportFileError(f"Not undone: {exc}") from None
    if manual:
        manual_positions.revalue(conn, account_id, since=since)
    conn.commit()
    return removed
