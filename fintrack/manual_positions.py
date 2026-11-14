"""Positions in manual investment accounts (user's request 2026-10-07, migration 0019).

A manual Investment / retirement account can hold lines instead of a typed value:

* a ticker line: N shares, valued at the ticker's price;
* an asset line: a dollar amount (cash, a fund without a ticker, ...).

Each line's quantity or amount is dated ("from Oct 1: 120 shares"), so changes over time are
kept; the amount on a day is the latest change on or before it. Typing "+10" or "-5" changes
the amount in effect on that date.

Dollar events (migration 0025, user's request 2026-10-12): a change can also be entered in dollars,
for funds without a ticker of their own tracked through an equivalent ticker (e.g. a State Street
index fund through SPY): a contribution (+$X) or withdrawal (-$X) buys or sells X / price shares
on that date; an opening value ($V) sets the line to V / price shares. The dollars are kept
(`cash_cents`, `entry`) and the shares re-derived whenever that day's price improves (`rederive`),
so the value on that date stays what you typed. Since 2026-11-14 (docs/parity-plan.md) a manual account's
contributions, withdrawals and fees are activity (ledger.py), which rebuilds these changes; what you type here is a
balance; the Savings page counts contributions from the activity (contributions.py).

Prices (ticker_prices, one per ticker per day), best first: a price you set > Alpaca (live
quote or daily history, price_history.py) > Plaid's latest price for the same ticker in a
connected account. A day is valued at
the ticker's latest price on or before it, else its earliest known price (history starts when
recording starts).

Revaluing writes the account's value into its normal value history (manual_valuations, note
"from positions"), so net worth, snapshots, charts and projection need no changes. It runs
when you change a line, at every sync, and on page loads that fetch live quotes (Overview,
Investments, the account's page; the user's choice). Page loads must not write when nothing
changed (every write bumps the page revision, which makes other open tabs reload), so every
write here is skipped when the stored value is already the same.

The lines are also mirrored into `holdings` (with prices in `security_prices`), so the
Investments page, Overview movers and the performance cards include them. A ticker you also
hold in a connected account shares that holding's security id, so they combine.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fintrack import manual
from fintrack.money import to_cents

# kind -> (label, holdings.security_type)
KINDS = {"stock": ("Stock", "equity"), "etf": ("ETF", "etf"), "fund": ("Fund", "mutual fund"),
         "bond": ("Bond", "fixed income"), "cash": ("Cash", "cash"), "other": ("Other", "other")}
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
VALUATION_NOTE = "from positions"
# Which price source wins when two disagree on the same day (higher wins).
SOURCE_RANK_SQL = "CASE {col} WHEN 'you' THEN 3 WHEN 'alpaca' THEN 2 WHEN 'finnhub' THEN 2 ELSE 1 END"


class PositionError(ValueError):
    """A position change was refused; the message is shown to you."""


# How an amount you type is meant (change_amount's `mode`). "amount" = shares for a ticker line, dollars for a line
# without one (the original behaviour, '+N' / '-N' relative); the others are dollar events (see above).
MODES = {"amount": "Amount", "contribution": "Contribution", "withdrawal": "Withdrawal", "opening": "Opening value"}

# The account page's Add / edit form (user's request 2026-10-12): an event is a type and a unit.
#   Contribution / Withdrawal: money (or shares) in / out on that date, on top of what was held;
#   Fee: taken out like a withdrawal, but a cost rather than money you took out (fee = 1; not on the Savings page);
#   Balance: what the line held on that date (a starting balance or a correction; never counted as a contribution).
#   Quantity = shares (ticker lines only); Value = dollars, turned into shares at that day's price.
EVENT_TYPES = {"contribution": "Contribution", "withdrawal": "Withdrawal", "fee": "Fee", "balance": "Balance"}
EVENT_UNITS = {"quantity": "Quantity", "value": "Value"}


# ---------------------------------------------------------------- parsing

def parse_ticker(text: str) -> str:
    ticker = (text or "").strip().upper()
    if not TICKER_RE.match(ticker):
        raise PositionError(f"“{text}” doesn't look like a ticker (letters, digits, . or -).")
    return ticker


def _split_sign(text: str) -> tuple[str | None, str]:
    """'+10' -> ('+', '10'); '-5' / '−5' -> ('-', '5'); '12' -> (None, '12')."""
    t = (text or "").strip().replace("−", "-")
    if t[:1] in ("+", "-"):
        return t[0], t[1:].strip()
    return None, t


def parse_quantity(text: str, current: float | None) -> float:
    """Shares: '120', '12.5', or '+10' / '-5' relative to `current`. Never below 0."""
    sign, number = _split_sign(text)
    try:
        amount = Decimal(number.replace(",", ""))
    except InvalidOperation:
        raise PositionError(f"“{text}” isn't a quantity.") from None
    if amount < 0 or not amount.is_finite():
        raise PositionError(f"“{text}” isn't a quantity.")
    if sign:
        amount = Decimal(str(current or 0)) + (amount if sign == "+" else -amount)
    if amount < 0:
        raise PositionError("That would leave fewer than 0 shares.")
    return float(amount)


def parse_value(text: str, current_cents: int | None) -> int:
    """Dollars: '2,500', or '+500' / '-200' relative to `current_cents`. Never below 0."""
    sign, number = _split_sign(text)
    try:
        cents = manual.parse_money(number)
    except manual.ManualError as exc:
        raise PositionError(str(exc)) from None
    if sign:
        cents = (current_cents or 0) + (cents if sign == "+" else -cents)
    if cents < 0:
        raise PositionError("That would leave less than $0.")
    return cents


def parse_cash(text: str) -> int:
    """A dollar event's amount: '500', '$1,250.50' (no sign: the mode says in or out). More than $0."""
    _sign, number = _split_sign(text)
    try:
        cents = manual.parse_money(number)
    except manual.ManualError as exc:
        raise PositionError(str(exc)) from None
    if cents <= 0:
        raise PositionError("Enter an amount more than $0.")
    return cents


def parse_shares(text: str, allow_zero: bool = False) -> float:
    """A number of shares (no sign: the event type says in or out)."""
    _sign, number = _split_sign(text)
    try:
        amount = Decimal(number.replace(",", ""))
    except InvalidOperation:
        raise PositionError(f"“{text}” isn't a quantity.") from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise PositionError("Enter a quantity more than 0.")
    return float(amount)


def parse_price(text: str) -> float:
    try:
        price = Decimal((text or "").strip().replace("$", "").replace(",", ""))
    except InvalidOperation:
        raise PositionError(f"“{text}” isn't a price.") from None
    if not price.is_finite() or price <= 0:
        raise PositionError("A price must be more than $0.")
    return float(price)


# ---------------------------------------------------------------- reads

@dataclass
class Line:
    id: int
    ticker: str | None
    name: str
    kind: str
    quantity: float | None = None          # ticker lines
    value_cents: int | None = None         # None: a ticker line with no price yet
    price: float | None = None
    price_date: str | None = None
    price_source: str | None = None
    changes: list[sqlite3.Row] = field(default_factory=list)   # newest first

    @property
    def kind_label(self) -> str:
        return KINDS[self.kind][0]


def _amount_on(conn: sqlite3.Connection, position_id: int, day: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT date, quantity, value_cents FROM manual_position_changes"
        " WHERE position_id = ? AND date <= ? ORDER BY date DESC LIMIT 1", (position_id, day)).fetchone()


def price_on(conn: sqlite3.Connection, ticker: str, day: str) -> sqlite3.Row | None:
    """(price, price_date, source) on or before `day`, else the earliest known one."""
    return (conn.execute("SELECT price, price_date, source FROM ticker_prices WHERE ticker = ? AND price_date <= ?"
                         " ORDER BY price_date DESC LIMIT 1", (ticker, day)).fetchone()
            or conn.execute("SELECT price, price_date, source FROM ticker_prices WHERE ticker = ?"
                            " ORDER BY price_date LIMIT 1", (ticker,)).fetchone())


def _derive(conn: sqlite3.Connection, ticker: str | None, rows: list[sqlite3.Row]) -> list[float | int]:
    """The amount in effect from each change (oldest first): shares for a ticker line, cents otherwise. Plain rows
    keep their stored amount; dollar events are turned into shares at that day's price (price_on)."""
    amounts: list[float | int] = []
    previous: float | int = 0
    for r in rows:
        if r["entry"] is None:
            amount = r["quantity"] if ticker else r["value_cents"]
        elif r["delta_quantity"] is not None:                       # shares typed in or out
            amount = previous + r["delta_quantity"]
        elif ticker:
            price = price_on(conn, ticker, r["date"])
            if price is None:
                raise PositionError(f"There's no price for {ticker} yet, so dollars can't be turned into shares."
                                    " Set today's price for the line first.")
            shares = r["cash_cents"] / 100 / price["price"]
            amount = shares if r["entry"] == "opening" else previous + shares
        else:
            amount = r["cash_cents"] if r["entry"] == "opening" else previous + r["cash_cents"]
        if amount < -1e-9:
            raise PositionError("That would leave less than nothing in the line on "
                                f"{date.fromisoformat(r['date']):%b %-d, %Y}.")
        amount = round(max(amount, 0.0), 9) if ticker else max(int(amount), 0)
        amounts.append(amount)
        previous = amount
    return amounts


def rederive(conn: sqlite3.Connection, position_id: int) -> bool:
    """Bring the line's dollar events' amounts up to date with the prices (no commit; raises PositionError if an
    event can't be priced or would go below zero). True if a stored amount changed."""
    position = _position(conn, position_id)
    rows = conn.execute("SELECT rowid, date, quantity, value_cents, cash_cents, entry, delta_quantity"
                        " FROM manual_position_changes WHERE position_id = ? ORDER BY date", (position_id,)).fetchall()
    changed = False
    column = "quantity" if position["ticker"] else "value_cents"
    for r, amount in zip(rows, _derive(conn, position["ticker"], rows)):
        stored = r[column]
        if stored is None or (abs(stored - amount) > 1e-9 if position["ticker"] else stored != amount):
            conn.execute(f"UPDATE manual_position_changes SET {column} = ? WHERE rowid = ?", (amount, r["rowid"]))
            changed = True
    return changed


def has_positions(conn: sqlite3.Connection, account_id: int) -> bool:
    return conn.execute("SELECT 1 FROM manual_positions WHERE account_id = ?", (account_id,)).fetchone() is not None


def position_tickers(conn: sqlite3.Connection, account_id: int | None = None) -> list[str]:
    """Tickers of position lines (one account, or every visible manual account)."""
    if account_id is not None:
        rows = conn.execute("SELECT DISTINCT ticker FROM manual_positions WHERE account_id = ? AND ticker IS NOT NULL",
                            (account_id,))
    else:
        rows = conn.execute("SELECT DISTINCT p.ticker FROM manual_positions p JOIN accounts a ON a.id = p.account_id"
                            " WHERE a.is_hidden = 0 AND p.ticker IS NOT NULL")
    return [r[0] for r in rows]


def lines(conn: sqlite3.Connection, account_id: int, today: date | None = None) -> list[Line]:
    """The account's lines as of today (amount, price, value), largest first."""
    day = (today or date.today()).isoformat()
    result = []
    for p in conn.execute("SELECT * FROM manual_positions WHERE account_id = ? ORDER BY name", (account_id,)):
        line = Line(p["id"], p["ticker"], p["name"], p["kind"], changes=conn.execute(
            "SELECT date, quantity, value_cents, cash_cents, entry FROM manual_position_changes WHERE position_id = ?"
            " ORDER BY date DESC", (p["id"],)).fetchall())
        amount = _amount_on(conn, p["id"], day)
        if p["ticker"]:
            line.quantity = amount["quantity"] if amount else 0.0
            price = price_on(conn, p["ticker"], day)
            if price:
                line.price, line.price_date, line.price_source = price["price"], price["price_date"], price["source"]
                line.value_cents = to_cents(line.quantity * line.price)
        else:
            line.value_cents = amount["value_cents"] if amount else 0
        result.append(line)
    return sorted(result, key=lambda ln: -(ln.value_cents or 0))


def account_value(conn: sqlite3.Connection, account_id: int, day: str) -> int | None:
    """The lines' total on `day`; None before any line has an amount. A ticker with no price
    at all counts as $0 (the account page flags it)."""
    total, any_amount = 0, False
    for p in conn.execute("SELECT id, ticker FROM manual_positions WHERE account_id = ?", (account_id,)):
        amount = _amount_on(conn, p["id"], day)
        if amount is None:
            continue
        any_amount = True
        if p["ticker"]:
            price = price_on(conn, p["ticker"], day)
            total += to_cents(amount["quantity"] * price["price"]) if price else 0
        else:
            total += amount["value_cents"]
    return total if any_amount else None


# ---------------------------------------------------------------- prices

def _save_price(conn: sqlite3.Connection, ticker: str, day: str, price: float, source: str) -> None:
    """Upsert one price; a weaker source never replaces a stronger one; unchanged = no write."""
    conn.execute(
        f"""
        INSERT INTO ticker_prices (ticker, price_date, price, source) VALUES (?, ?, ?, ?)
        ON CONFLICT (ticker, price_date) DO UPDATE SET price = excluded.price, source = excluded.source
        WHERE {SOURCE_RANK_SQL.format(col='ticker_prices.source')} <= {SOURCE_RANK_SQL.format(col='excluded.source')}
          AND (ticker_prices.price != excluded.price OR ticker_prices.source != excluded.source)
        """,
        (ticker, day, price, source),
    )


def _record_prices(conn: sqlite3.Connection, tickers: list[str], live: dict, today: date) -> None:
    """Today's prices for these tickers: Alpaca's live quote, else Plaid's latest price for the
    same ticker in a connected account (security_prices, not our own mirrored rows)."""
    for ticker in tickers:
        quote = live.get(ticker)
        if quote is not None:
            day = datetime.fromtimestamp(quote.as_of).date() if quote.as_of else today
            _save_price(conn, ticker, day.isoformat(), quote.price, "alpaca")
            continue
        plaid = conn.execute(
            "SELECT price_date, price FROM security_prices WHERE ticker = ? AND security_id NOT LIKE 'manual:%'"
            " ORDER BY price_date DESC LIMIT 1", (ticker,)).fetchone()
        if plaid and plaid["price"] and plaid["price"] > 0:
            _save_price(conn, ticker, plaid["price_date"], plaid["price"], "plaid")


# ---------------------------------------------------------------- holdings mirror

def _mirror_holdings(conn: sqlite3.Connection, account_id: int, today: date) -> None:
    """Make this account's `holdings` rows match its lines today (rewritten only if different)."""
    wanted, prices_wanted = [], []
    for line in lines(conn, account_id, today):
        if not line.value_cents and not line.quantity:
            continue                                   # sold / emptied lines aren't holdings
        security_type, is_cash = KINDS[line.kind][1], 1 if line.kind == "cash" else 0
        if line.ticker:
            plaid = conn.execute(
                "SELECT h.security_id FROM holdings h JOIN accounts a ON a.id = h.account_id"
                " WHERE a.is_manual = 0 AND h.ticker = ? LIMIT 1", (line.ticker,)).fetchone()
            security_id = plaid[0] if plaid else f"manual:{line.ticker}"
            wanted.append((security_id, line.name, line.ticker, security_type, is_cash, line.quantity,
                           line.price, line.value_cents, line.price_date))
            if line.price:
                prices_wanted.append((security_id, line.price_date, line.ticker, line.name, line.price))
        else:
            wanted.append((f"manual-line:{line.id}", line.name, None, security_type, is_cash, None, None,
                           line.value_cents, None))
    wanted.sort()
    current = [tuple(r) for r in conn.execute(
        "SELECT security_id, name, ticker, security_type, is_cash, quantity, price, value_cents, price_as_of"
        " FROM holdings WHERE account_id = ? ORDER BY security_id", (account_id,))]
    if current != wanted:
        conn.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
        conn.executemany(
            "INSERT INTO holdings (account_id, security_id, name, ticker, security_type, is_cash, quantity, price,"
            " value_cents, price_as_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(account_id, *row) for row in wanted])
    # Price history for movers / performance, same table Plaid holdings use (no-op if unchanged).
    conn.executemany(
        """
        INSERT INTO security_prices (security_id, price_date, ticker, name, price) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (security_id, price_date) DO UPDATE SET price = excluded.price
        WHERE security_prices.price != excluded.price
        """,
        prices_wanted)


# ---------------------------------------------------------------- revalue

def revalue(conn: sqlite3.Connection, account_id: int, live: dict | None = None,
            since: str | None = None, today: date | None = None) -> bool:
    """Price the account's tickers, write its value for today (and for every valued day from
    `since` on, after a backdated change), mirror holdings. True if its value history changed."""
    today = today or date.today()
    account = manual.get_account(conn, account_id)
    if account["type"] != "investment" or not has_positions(conn, account_id):
        if conn.execute("SELECT 1 FROM holdings WHERE account_id = ?", (account_id,)).fetchone():
            conn.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
            conn.commit()
        return False
    _record_prices(conn, position_tickers(conn, account_id), live or {}, today)
    # Dollar events follow the prices just recorded; a line that can't be derived keeps its stored amounts.
    since_derived = None
    for (position_id,) in conn.execute("SELECT id FROM manual_positions WHERE account_id = ?", (account_id,)).fetchall():
        try:
            if rederive(conn, position_id):
                first_event = conn.execute("SELECT MIN(date) FROM manual_position_changes WHERE position_id = ?"
                                           " AND entry IS NOT NULL", (position_id,)).fetchone()[0]
                since_derived = min(filter(None, (since_derived, first_event)))
        except PositionError:
            conn.rollback()
    if since_derived:
        since = min(filter(None, (since, since_derived)))

    first = conn.execute("SELECT MIN(c.date) FROM manual_position_changes c JOIN manual_positions p"
                         " ON p.id = c.position_id WHERE p.account_id = ?", (account_id,)).fetchone()[0]
    start = min(since or today.isoformat(), today.isoformat())
    # Today, the change date, and every day from it that has a value or a net-worth snapshot,
    # so a backdated change is priced day by day where prices exist.
    days = {start, today.isoformat()} | {r[0] for r in conn.execute(
        "SELECT date FROM manual_valuations WHERE account_id = ? AND date >= ?"
        " UNION SELECT DISTINCT date FROM balance_snapshots WHERE date >= ?", (account_id, start, start))}
    changed = False
    for day in sorted(d for d in days if first and d >= first):
        value = account_value(conn, account_id, day)
        if value is None:
            continue
        stored = conn.execute("SELECT value_cents FROM manual_valuations WHERE account_id = ? AND date = ?",
                              (account_id, day)).fetchone()
        if stored is None or stored[0] != value:
            conn.execute(
                """INSERT INTO manual_valuations (account_id, date, value_cents, note) VALUES (?, ?, ?, ?)
                   ON CONFLICT (account_id, date) DO UPDATE SET value_cents = excluded.value_cents,
                                                               note = excluded.note""",
                (account_id, day, value, VALUATION_NOTE))
            changed = True
    _mirror_holdings(conn, account_id, today)
    conn.commit()
    if changed:
        manual._refresh(conn, account_id)          # balance, snapshot history, today's snapshot
    return changed


def revalue_all(conn: sqlite3.Connection, live: dict | None = None, today: date | None = None) -> bool:
    """Revalue every visible manual account that has lines."""
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT a.id FROM accounts a JOIN manual_positions p ON p.account_id = a.id"
        " WHERE a.is_manual = 1 AND a.is_hidden = 0")]
    return any([revalue(conn, account_id, live, today=today) for account_id in ids])


# ---------------------------------------------------------------- changes

def _investment_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    account = manual.get_account(conn, account_id)
    if account["type"] != "investment":
        raise PositionError("Positions are for Investment / retirement accounts.")
    return account


def _position(conn: sqlite3.Connection, position_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM manual_positions WHERE id = ?", (position_id,)).fetchone()
    if row is None:
        raise PositionError("That line no longer exists.")
    return row


def add_line(conn: sqlite3.Connection, account_id: int, ticker_text: str, name: str, kind: str,
             amount_text: str, date_text: str, live: dict | None = None, today: date | None = None,
             mode: str = "amount") -> int:
    """A new line: a ticker + quantity, or (no ticker) a named asset + dollar amount. mode="opening" on a ticker
    line: the amount is its dollar value that day, turned into shares at that day's price (a fund tracked through
    an equivalent ticker)."""
    _investment_account(conn, account_id)
    day = manual.parse_date(date_text, today)
    name = (name or "").strip()
    cash, entry = None, None
    if (ticker_text or "").strip():
        ticker = parse_ticker(ticker_text)
        kind = kind if kind in KINDS else "stock"
        if conn.execute("SELECT 1 FROM manual_positions WHERE account_id = ? AND ticker = ?",
                        (account_id, ticker)).fetchone():
            raise PositionError(f"There's already a line for {ticker}. Add an event to it instead.")
        if mode == "opening":
            quantity, value, cash, entry = 0.0, None, parse_cash(amount_text), "opening"
        else:
            quantity, value = parse_quantity(amount_text, None), None
        name = name or ticker
    else:
        if not name:
            raise PositionError("Give the line a name (e.g. Cash), or enter a ticker.")
        ticker, kind = None, kind if kind in KINDS else "other"
        quantity, value = None, parse_value(amount_text, None)
    if ticker and entry:
        _record_prices(conn, [ticker], live or {}, today or date.today())   # a price to turn the dollars into shares
    with conn:
        position_id = conn.execute("INSERT INTO manual_positions (account_id, ticker, name, kind) VALUES (?, ?, ?, ?)",
                                   (account_id, ticker, name[:60], kind)).lastrowid
        conn.execute("INSERT INTO manual_position_changes (position_id, date, quantity, value_cents, cash_cents, entry)"
                     " VALUES (?, ?, ?, ?, ?, ?)", (position_id, day, quantity, value, cash, entry))
        rederive(conn, position_id)
    revalue(conn, account_id, live, since=day, today=today)
    return position_id


def _event_values(conn: sqlite3.Connection, position: sqlite3.Row, day: str, amount_text: str,
                  mode: str) -> tuple[float | None, int | None, int | None, str | None]:
    """(quantity, value_cents, cash_cents, entry) for a change typed in `mode` (see MODES). For dollar events the
    amount column is a placeholder that rederive() fills in."""
    if mode in ("contribution", "withdrawal", "opening"):
        cash = parse_cash(amount_text)
        cash, entry = (-cash if mode == "withdrawal" else cash), ("opening" if mode == "opening" else "contribution")
        return (0.0, None, cash, entry) if position["ticker"] else (None, 0, cash, entry)
    current = _amount_on(conn, position["id"], day)
    if position["ticker"]:
        return parse_quantity(amount_text, current["quantity"] if current else None), None, None, None
    return None, parse_value(amount_text, current["value_cents"] if current else None), None, None


def _write_event(conn: sqlite3.Connection, position: sqlite3.Row, day: str, values: tuple, live: dict | None,
                 today: date | None) -> None:
    """Insert or replace the change on `day`, re-derive the line (rolled back if that fails)."""
    if values[3] and position["ticker"]:
        _record_prices(conn, [position["ticker"]], live or {}, today or date.today())
    with conn:
        _store_event(conn, position["id"], day, (*values, None, 0))


def change_amount(conn: sqlite3.Connection, position_id: int, amount_text: str, date_text: str,
                  live: dict | None = None, today: date | None = None, mode: str = "amount") -> int:
    """An event on `date`: mode "amount" = from that date the line holds this amount ('+N' / '-N' = relative to
    that date's amount); "contribution" / "withdrawal" / "opening" = dollars (see MODES)."""
    position = _position(conn, position_id)
    day = manual.parse_date(date_text, today)
    _write_event(conn, position, day, _event_values(conn, position, day, amount_text, mode), live, today)
    revalue(conn, position["account_id"], live, since=day, today=today)
    return position["account_id"]


def _event_row(position: sqlite3.Row, kind: str, unit: str, amount_text: str) -> tuple:
    """(quantity, value_cents, cash_cents, entry, delta_quantity, fee) for an event typed on the Add / edit form.
    Amounts for derived rows are placeholders that rederive() fills in."""
    if kind not in EVENT_TYPES or unit not in EVENT_UNITS:
        raise PositionError("Choose the event type and whether you entered a quantity or a value.")
    return (*_event_amounts(position, kind, unit, amount_text), 1 if kind == "fee" else 0)


def _event_amounts(position: sqlite3.Row, kind: str, unit: str, amount_text: str) -> tuple:
    """(quantity, value_cents, cash_cents, entry, delta_quantity): see _event_row."""
    sign = -1 if kind in ("withdrawal", "fee") else 1
    if not position["ticker"]:
        if unit == "quantity":
            raise PositionError(f"{position['name']} has no ticker, so enter a value.")
        if kind == "balance":
            return None, parse_value(amount_text, None), None, None, None
        return None, 0, sign * parse_cash(amount_text), "contribution", None
    if unit == "quantity":
        if kind == "balance":
            return parse_shares(amount_text, allow_zero=True), None, None, None, None
        return 0.0, None, None, "contribution", sign * parse_shares(amount_text)
    if kind == "balance":
        cents = parse_value(amount_text, None)
        return (0.0, None, cents, "opening", None) if cents else (0.0, None, None, None, None)   # $0 = sold all
    return 0.0, None, sign * parse_cash(amount_text), "contribution", None


def _store_event(conn: sqlite3.Connection, position_id: int, day: str, row: tuple) -> None:
    """Insert or replace the line's event on `day` and re-derive the line (no commit; the caller's transaction)."""
    conn.execute(
        """INSERT INTO manual_position_changes (position_id, date, quantity, value_cents, cash_cents, entry, delta_quantity,
                                                fee)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (position_id, date) DO UPDATE SET quantity = excluded.quantity, value_cents = excluded.value_cents,
               cash_cents = excluded.cash_cents, entry = excluded.entry, delta_quantity = excluded.delta_quantity,
               fee = excluded.fee""",
        (position_id, day, *row))
    rederive(conn, position_id)


def _find_line(conn: sqlite3.Connection, account_id: int, text: str) -> sqlite3.Row | None:
    """The account's line for a ticker, or for a line's name (lines without a ticker, e.g. Cash)."""
    text = (text or "").strip()
    return conn.execute("SELECT * FROM manual_positions WHERE account_id = ? AND (ticker = ? OR lower(name) = lower(?))"
                        " ORDER BY ticker IS NULL LIMIT 1", (account_id, text.upper(), text)).fetchone()


def record_event(conn: sqlite3.Connection, account_id: int, line_text: str, date_text: str, kind: str, unit: str,
                 amount_text: str, live: dict | None = None, today: date | None = None) -> int:
    """The account page's Add form: an event on a ticker (a new ticker becomes a new line). Refused, with nothing
    written, if it can't be priced or would leave less than nothing. Returns the line's id."""
    _investment_account(conn, account_id)
    day = manual.parse_date(date_text, today)
    position = _find_line(conn, account_id, line_text)
    ticker = position["ticker"] if position else parse_ticker(line_text)
    if position is None:                                    # a new line: shaped like a row, for _event_row
        position = {"id": None, "ticker": ticker, "name": ticker}
    row = _event_row(position, kind, unit, amount_text)
    if ticker:
        _record_prices(conn, [ticker], live or {}, today or date.today())   # a price to turn dollars into shares
    if position["id"] is not None and conn.execute(
            "SELECT 1 FROM manual_position_changes WHERE position_id = ? AND date = ?", (position["id"], day)).fetchone():
        raise PositionError(f"{position['name']} already has an event on {date.fromisoformat(day):%b %-d, %Y}."
                            " Edit that one instead.")
    with conn:
        position_id = position["id"] or conn.execute(
            "INSERT INTO manual_positions (account_id, ticker, name, kind) VALUES (?, ?, ?, 'etf')",
            (account_id, ticker, ticker)).lastrowid
        _store_event(conn, position_id, day, row)
    revalue(conn, account_id, live, since=day, today=today)
    return position_id


def edit_event(conn: sqlite3.Connection, position_id: int, old_date: str, date_text: str, kind: str, unit: str,
               amount_text: str, live: dict | None = None, today: date | None = None) -> int:
    """Edit one History event in place (date, type, unit, amount); nothing changes if the new version is refused."""
    position = _position(conn, position_id)
    day = manual.parse_date(date_text, today)
    if day != old_date and conn.execute("SELECT 1 FROM manual_position_changes WHERE position_id = ? AND date = ?",
                                        (position_id, day)).fetchone():
        raise PositionError(f"{position['name']} already has an event on that date. Edit that one instead.")
    row = _event_row(position, kind, unit, amount_text)
    if position["ticker"]:
        _record_prices(conn, [position["ticker"]], live or {}, today or date.today())
    with conn:
        conn.execute("DELETE FROM manual_position_changes WHERE position_id = ? AND date = ?", (position_id, old_date))
        _store_event(conn, position_id, day, row)
    revalue(conn, position["account_id"], live, since=min(day, old_date), today=today)
    return position["account_id"]


def delete_change(conn: sqlite3.Connection, position_id: int, day: str, today: date | None = None) -> int:
    position = _position(conn, position_id)
    count = conn.execute("SELECT COUNT(*) FROM manual_position_changes WHERE position_id = ?", (position_id,)).fetchone()[0]
    if count <= 1:
        return remove_line(conn, position_id, today)          # its last event: the ticker leaves the account
    with conn:
        conn.execute("DELETE FROM manual_position_changes WHERE position_id = ? AND date = ?", (position_id, day))
        rederive(conn, position_id)                  # later events build on what's left
    revalue(conn, position["account_id"], since=day, today=today)
    return position["account_id"]


def remove_line(conn: sqlite3.Connection, position_id: int, today: date | None = None) -> int:
    """Delete a line and its history (a mistake). Selling is a change to 0 instead."""
    position = _position(conn, position_id)
    first = conn.execute("SELECT MIN(date) FROM manual_position_changes WHERE position_id = ?",
                         (position_id,)).fetchone()[0]
    with conn:
        conn.execute("DELETE FROM manual_positions WHERE id = ?", (position_id,))
    account_id = position["account_id"]
    if has_positions(conn, account_id):
        revalue(conn, account_id, since=first, today=today)
    else:
        revalue(conn, account_id, today=today)      # clears the mirrored holdings
    return account_id


def set_price(conn: sqlite3.Connection, position_id: int, price_text: str, today: date | None = None) -> int:
    """Your own price for today (for tickers Alpaca and Plaid can't price, e.g. mutual funds)."""
    position = _position(conn, position_id)
    if not position["ticker"]:
        raise PositionError("Only ticker lines have a price.")
    today = today or date.today()
    with conn:
        _save_price(conn, position["ticker"], today.isoformat(), parse_price(price_text), "you")
    for (account_id,) in conn.execute("SELECT DISTINCT account_id FROM manual_positions WHERE ticker = ?",
                                      (position["ticker"],)).fetchall():
        revalue(conn, account_id, today=today)
    return position["account_id"]


# ---------------------------------------------------------------- events (the account page's History)

@dataclass
class Event:
    position_id: int
    line: str
    ticker: str | None
    date: str
    entry: str | None            # None = a plain amount; "contribution" (cash_cents < 0 = withdrawal) / "opening"
    cash_cents: int | None
    quantity: float | None       # shares held from this date (ticker lines)
    value_cents: int | None      # dollars held from this date (lines without a ticker)
    delta_quantity: float | None = None
    fee: int = 0
    contributed_cents: int | None = None   # an imported change: what was paid in (migration 0070)
    import_batch_id: int | None = None     # the import that wrote it (shown read-only; Undo is on the Import page)
    from_activity: int = 0                 # worked out from the account's activity (ledger.py, migration 0073)
    price: float | None = None   # the ticker's price that day

    @property
    def imported(self) -> bool:
        """Not typed here: from an import, or from the account's activity (shown read-only)."""
        return self.import_batch_id is not None or bool(self.from_activity)

    @property
    def label(self) -> str:
        """An imported change's type: what it did (it may be a buy, which the Add form has no type for)."""
        if self.fee:
            return "Fee"
        if self.entry != "contribution":
            return "Balance"
        if self.contributed_cents:
            return "Contribution" if self.contributed_cents > 0 else "Withdrawal"
        moved = self.delta_quantity if self.delta_quantity is not None else (self.cash_cents or 0)
        if self.ticker:
            return "Buy" if moved > 0 else "Sell"
        return "Cash in" if moved > 0 else "Cash out"

    @property
    def kind(self) -> str:
        """contribution / withdrawal / fee / balance (EVENT_TYPES)."""
        if self.fee:
            return "fee"
        if self.entry == "contribution":
            return "withdrawal" if (self.cash_cents if self.cash_cents is not None else self.delta_quantity or 0) < 0 \
                else "contribution"
        return "balance"

    @property
    def unit(self) -> str:
        """quantity / value: how it was typed (EVENT_UNITS)."""
        if not self.ticker or self.cash_cents is not None:
            return "value"
        return "quantity" if self.delta_quantity is not None or self.entry is None else "value"

    @property
    def typed(self) -> str:
        """The amount as you typed it, for the edit box."""
        if self.cash_cents is not None:
            return f"{abs(self.cash_cents) / 100:.2f}"
        if self.delta_quantity is not None:
            return f"{abs(self.delta_quantity):.6f}".rstrip("0").rstrip(".")
        if self.ticker:
            return f"{self.quantity or 0:.6f}".rstrip("0").rstrip(".")
        return f"{(self.value_cents or 0) / 100:.2f}"

    @property
    def shares(self) -> float | None:
        """The event's shares: moved in / out (negative = out), or held for a Balance. None without a ticker."""
        if not self.ticker:
            return None
        if self.delta_quantity is not None:
            return self.delta_quantity
        if self.entry == "contribution":
            return self.cash_cents / 100 / self.price if self.price else None
        return self.quantity

    @property
    def dollars(self) -> int | None:
        """The event's dollars in cents: moved in / out, or held for a Balance."""
        if self.cash_cents is not None:
            return self.cash_cents
        if not self.ticker:
            return self.value_cents
        return to_cents(self.shares * self.price) if self.price and self.shares is not None else None


def events(conn: sqlite3.Connection, account_id: int) -> list[Event]:
    """Every change on the account's lines, newest first (the History table; the daily valuations they produce
    are not events)."""
    result = []
    for r in conn.execute("""
        SELECT c.position_id, p.name, p.ticker, c.date, c.entry, c.cash_cents, c.quantity, c.value_cents, c.delta_quantity,
               c.fee, c.contributed_cents, c.import_batch_id, c.from_activity
        FROM manual_position_changes c JOIN manual_positions p ON p.id = c.position_id
        WHERE p.account_id = ? ORDER BY c.date DESC, p.name""", (account_id,)):
        event = Event(*r)
        if event.ticker:
            price = price_on(conn, event.ticker, event.date)
            event.price = price["price"] if price else None
        result.append(event)
    return result


