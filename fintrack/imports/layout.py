"""Layout detection: which row is the header, what each column holds, how dates and amounts are written, and which way
round money in and out are. Then the table's rows -> `Row`s in Plaid's terms (positive = money out).

Every bank's file differs (docs/import-plan.md "What varies"), so each guess is made from the column names **and** the
values, says how it was made (`Detection.notes`), and anything that can't be settled from the file is a **question**
(`Detection.questions`) you answer before importing. Your answers are kept per account in its profile (engine.py), so
a file with the same header never asks again.

- **Header:** the first row of labels (no dates or amounts) followed by a row of data (a date and an amount). Lines
  above it (account name, date range) are ignored; a file whose first row is already data has no header.
- **Dates:** every format that reads at least 80% of the column. If month-first and day-first both read all of it (no
  day over 12), the one that keeps the rows in date order wins; if both do, it's a question.
- **Amounts:** one signed column, Debit and Credit columns (either may hold negative numbers), or an amount with a
  DR / CR type column. `(12.34)`, `12.34-`, `12.34 CR`, `$1,234.56` and `1.234,56` (decimal comma) all read.
- **Sign** of a single signed column, best first: a running Balance column (each balance must equal the one before
  plus or minus the amount; that proves it), then telling words ("PAYROLL", a card's "PAYMENT"), then whichever sign
  most rows have is taken as money out. Only the balance proves it; otherwise it's a question.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from fintrack.imports.readers import Table
from fintrack.money import to_cents


class DetectError(ValueError):
    """A file whose layout couldn't be worked out; the message is shown to you."""


# ---------------------------------------------------------------- dates

# format -> the order its numbers are in ('text' = a month name, never ambiguous)
DATE_FORMATS = {
    "%Y-%m-%d": "ymd", "%Y/%m/%d": "ymd", "%Y.%m.%d": "ymd", "%Y%m%d": "ymd",
    "%m/%d/%Y": "mdy", "%m/%d/%y": "mdy", "%m-%d-%Y": "mdy", "%m-%d-%y": "mdy",
    "%d/%m/%Y": "dmy", "%d/%m/%y": "dmy", "%d-%m-%Y": "dmy", "%d-%m-%y": "dmy", "%d.%m.%Y": "dmy", "%d.%m.%y": "dmy",
    "%d %b %Y": "text", "%d-%b-%Y": "text", "%d-%b-%y": "text", "%d %b %y": "text", "%b %d %Y": "text",
    "%d %B %Y": "text", "%B %d %Y": "text",
}
# the same format with day and month swapped (answering the month-first / day-first question)
SWAPPED = {"%m/%d/%Y": "%d/%m/%Y", "%m/%d/%y": "%d/%m/%y", "%m-%d-%Y": "%d-%m-%Y", "%m-%d-%y": "%d-%m-%y"}
SWAPPED.update({v: k for k, v in SWAPPED.items()})


def _date_text(cell: str) -> str:
    """'03/04/2026 10:15:00' -> '03/04/2026'; 'Mar 4, 2026' -> 'Mar 4 2026'."""
    text = re.split(r"(?:T|\s)\d{1,2}:\d{2}", cell.strip())[0]
    return " ".join(text.replace(",", " ").split())


def parse_date(cell: str, fmt: str) -> str | None:
    """A cell in this format -> 'YYYY-MM-DD', or None. Years outside 1970-2100 don't count (stray numbers)."""
    try:
        d = datetime.strptime(_date_text(cell), fmt).date()
    except ValueError:
        return None
    return d.isoformat() if 1970 <= d.year <= 2100 else None


def is_date(cell: str) -> bool:
    return bool(cell) and any(parse_date(cell, f) for f in DATE_FORMATS)


def _in_order(dates: list[str]) -> bool:
    return dates == sorted(dates) or dates == sorted(dates, reverse=True)


def pick_date_format(values: list[str], order: str | None = None) -> tuple[str, str, bool] | None:
    """The format for a column of dates: (format, how it was chosen, still ambiguous). `order` ('mdy' / 'dmy') is your
    answer to month-first or day-first. None if no format reads 80% of the values."""
    values = [v for v in values if v]
    if not values:
        return None
    counts = {f: sum(parse_date(v, f) is not None for v in values) for f in DATE_FORMATS}
    best = max(counts.values())
    if best < max(1, 0.8 * len(values)):
        return None
    top = [f for f in DATE_FORMATS if counts[f] == best]
    families = {DATE_FORMATS[f] for f in top}
    if order in ("mdy", "dmy") and order in families:
        return next(f for f in top if DATE_FORMATS[f] == order), "as you chose", False
    if not {"mdy", "dmy"} <= families:
        return top[0], "", False
    # Every day is 12 or under: either order reads them all. The right one keeps the rows in date order.
    ordered = {DATE_FORMATS[f] for f in top
               if DATE_FORMATS[f] in ("mdy", "dmy") and _in_order([d for v in values if (d := parse_date(v, f))])}
    if len(ordered) == 1:
        fam = ordered.pop()
        return next(f for f in top if DATE_FORMATS[f] == fam), "settled by the rows' order", False
    return next(f for f in top if DATE_FORMATS[f] == "mdy"), "month first or day first can't be told apart", True


# ---------------------------------------------------------------- money

_CURRENCY = re.compile(r"[$€£¥]|\b(?:USD|EUR|GBP|CAD|AUD)\b", re.I)
_COMMA_DECIMAL = re.compile(r"^[(+-]?[$€£]?\s?\d{1,3}(?:[.\s]\d{3})*,\d{2}\)?-?$")
_DOT_DECIMAL = re.compile(r"\d\.\d{2}\)?-?$")


def parse_money(cell: str, decimal_comma: bool = False) -> Decimal | None:
    """A money cell -> a signed Decimal in the file's own sign, or None. Reads '$1,234.56', '(12.34)', '12.34-',
    '−12.34', '12.34 CR' (negative) / '12.34 DR', and with `decimal_comma` '1.234,56'."""
    text = cell.strip()
    if not text:
        return None
    negative = False
    upper = text.upper()
    if upper.endswith("CR"):
        negative, text = True, text[:-2]
    elif upper.endswith("DR"):
        text = text[:-2]
    text = _CURRENCY.sub("", text).replace("−", "-")
    text = "".join(text.split()).replace("'", "")
    if text.startswith("(") and text.endswith(")"):
        negative, text = not negative, text[1:-1]
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    elif text.endswith("-"):
        negative, text = not negative, text[:-1]
    elif text.startswith("+"):
        text = text[1:]
    text = text.replace(".", "").replace(",", ".") if decimal_comma else text.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", text):
        return None
    value = Decimal(text)
    return -value if negative else value


def uses_decimal_comma(table: Table) -> bool:
    """Whether the file writes money as 1.234,56: more cells look like that than like 1,234.56 (dates left out)."""
    comma = dot = 0
    for row in table:
        for cell in row:
            if not cell or is_date(cell):
                continue
            if _COMMA_DECIMAL.match(cell.strip()):
                comma += 1
            elif _DOT_DECIMAL.search(cell.strip()):
                dot += 1
    return comma > dot


# ---------------------------------------------------------------- the header row

def _has_date_and_money(row: list[str], dc: bool) -> bool:
    dates = {i for i, c in enumerate(row) if is_date(c)}
    return bool(dates) and any(i not in dates and c and parse_money(c, dc) is not None for i, c in enumerate(row))


_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.\-/][A-Z0-9]{1,2})?\**$")


def _holding(row: list[str], dc: bool) -> bool:
    """A holdings statement's row: a ticker and at least two numbers (quantity, price, value), no date needed."""
    return (any(_TICKER.match(c) for c in row)
            and sum(1 for c in row if c and not _TICKER.match(c) and parse_money(c, dc) is not None) >= 2)


def _labels(row: list[str], dc: bool) -> bool:
    cells = [c for c in row if c]
    return len(cells) >= 2 and not any(is_date(c) or parse_money(c, dc) is not None for c in cells)


def find_header(table: Table, dc: bool) -> tuple[int | None, int]:
    """(the header row's index or None when there's no header, the first data row's index)."""
    for i, row in enumerate(table[:40]):
        if _has_date_and_money(row, dc):
            return None, i                               # data before any labels: the file has no header
        if _labels(row, dc):
            # a header is followed straight away by data ("Date range, 01/01 - 01/31" above it isn't)
            if i + 1 < len(table) and (_has_date_and_money(table[i + 1], dc) or _holding(table[i + 1], dc)):
                return i, i + 1
    raise DetectError("No transactions found: no row has both a date and an amount.")


def _norm(name: str) -> str:
    """'Trans. Date' -> 'trans date'; 'Amount (USD)' -> 'amount usd'; 'DR/CR' -> 'dr cr'."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def fingerprint(header: list[str] | None, width: int) -> str:
    """What identifies a layout: its column names in order (or just its width when there's no header)."""
    key = "\x1f".join(_norm(h) for h in header) if header else "(no header)"
    return hashlib.sha256(f"{key}|{width}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------- what each column holds

ROLES = ("date", "authorized", "description", "amount", "debit", "credit", "direction", "balance", "card", "category",
         "symbol", "action", "quantity", "price", "value")
MONEY_ROLES = ("amount", "debit", "credit", "balance", "quantity", "price", "value")
# Roles that only mean something in one kind of file (KINDS): dropped from the others.
INVESTMENT_ROLES = {"symbol", "action", "quantity", "price", "value"}
BANK_ROLES = {"authorized", "debit", "credit", "direction", "balance", "card", "category"}
KINDS = {"transactions": "Transactions", "activity": "Investment activity", "positions": "Holdings"}

# Column names (as _norm writes them) -> role. 'posted' and 'purchased' become date / authorized below.
NAMES = {
    "posted": ("posted date", "post date", "posting date", "date posted", "booking date", "settlement date", "posted",
               "settled date", "clearing date"),
    "purchased": ("transaction date", "trans date", "purchase date", "authorized date", "authorisation date",
                  "date of transaction", "txn date", "tran date"),
    "date": ("date", "effective date", "value date", "activity date", "run date"),
    "description": ("description", "payee", "merchant", "merchant name", "name", "transaction description", "details",
                    "narrative", "transaction details", "transaction", "particulars", "payee name",
                    "original description", "description 1"),
    "memo": ("memo", "notes", "note", "reference", "additional info", "extended description", "memo description"),
    "amount": ("amount", "transaction amount", "amount usd", "value", "net amount", "amt"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "debit amount",
              "withdrawal amount", "charges", "charge", "outflow", "debit usd", "withdrawals usd", "purchases"),
    "credit": ("credit", "credits", "deposit", "deposits", "money in", "paid in", "credit amount", "deposit amount",
               "inflow", "credit usd", "deposits usd", "payments credits"),
    "direction": ("type", "transaction type", "dr cr", "cr dr", "debit credit", "credit debit", "debit credit indicator",
                  "credit debit indicator", "dc"),
    "balance": ("balance", "running balance", "running bal", "available balance", "ledger balance", "balance usd",
                "account balance", "closing balance"),
    "card": ("card", "card no", "card number", "card last 4", "last 4", "card ending", "card ending in", "card num",
             "last four"),
    # investment files (phase 5): activity and holdings
    "symbol": ("symbol", "ticker", "security symbol", "symbol cusip", "sym", "ticker symbol"),
    "action": ("action", "activity", "activity type", "transaction type", "type", "trans type", "action type",
               "transaction"),
    "quantity": ("quantity", "shares", "qty", "units", "share quantity", "quantity shares", "shares units"),
    "price": ("price", "share price", "price per share", "unit price", "nav", "price usd", "last price",
              "price share", "last price usd"),
    "value": ("current value", "market value", "total value", "value usd", "ending value", "market value usd",
              "current value usd", "ending market value"),
    "category": ("category", "transaction category", "merchant category", "categories", "spend category"),
}
# Then words inside a name, for names not listed above.
CONTAINS = (("symbol", "symbol"), ("ticker", "symbol"), ("quantity", "quantity"), ("shares", "quantity"),
            ("price", "price"), ("market value", "value"), ("current value", "value"),
            ("balance", "balance"), ("debit", "debit"), ("withdraw", "debit"), ("deposit", "credit"),
            ("credit", "credit"), ("amount", "amount"), ("post", "posted"), ("date", "date"),
            ("description", "description"), ("payee", "description"), ("merchant", "description"),
            ("category", "category"),
            ("action", "action"))           # last: "transaction date" has "action" in it

OUT_WORDS = {"dr", "debit", "d", "withdrawal", "out"}
IN_WORDS = {"cr", "credit", "c", "deposit", "in"}


def _share(values: list[str], test) -> float:
    filled = [v for v in values if v]
    return sum(1 for v in filled if test(v)) / len(filled) if filled else 0.0


def _fits(role: str, values: list[str], dc: bool) -> bool:
    """Whether a column's values can be that role (80% of its filled cells)."""
    if role in ("posted", "purchased", "date"):
        return _share(values, is_date) >= 0.8
    if role in MONEY_ROLES:
        return _share(values, lambda v: parse_money(v, dc) is not None) >= 0.8
    if role == "direction":
        return _share(values, lambda v: v.lower() in OUT_WORDS | IN_WORDS) >= 0.8
    if role == "card":
        return _share(values, lambda v: any(ch.isdigit() for ch in v)) >= 0.8
    if role == "symbol":
        # 60%: a statement's totals and notes rows sit in its symbol column too
        return _share(values, lambda v: bool(re.fullmatch(r"[A-Za-z0-9.\-/*]{1,12}", v))) >= 0.6
    return True


def guess_columns(header: list[str] | None, columns: list[list[str]], dc: bool) -> tuple[dict[str, int], list[str]]:
    """{role: column index} from the names, then from the values for what's still missing; and notes on any guess
    made from values alone."""
    found: dict[str, int] = {}
    notes: list[str] = []
    names = [_norm(h) for h in header] if header else [""] * len(columns)

    def take(role: str, i: int) -> bool:
        if role in found or i in found.values() or not _fits(role, columns[i], dc):
            return False
        found[role] = i
        return True

    for i, name in enumerate(names):                     # exact names
        for role, listed in NAMES.items():
            if name in listed and take(role, i):
                break
    for i, name in enumerate(names):                     # words inside names
        if name and i not in found.values():
            for word, role in CONTAINS:
                if word in name and take(role, i):
                    break

    # Two dates: the posted one is the row's date (Plaid's choice), the other the purchase date.
    dated = [found.pop(r) for r in ("posted", "date", "purchased") if r in found]
    if dated:
        found["date"] = dated[0]
        if len(dated) > 1:
            found["authorized"] = dated[1]

    # From the values alone (no header, or names not recognized).
    free = [i for i in range(len(columns)) if i not in found.values()]
    if "date" not in found:
        i = next((i for i in free if _fits("date", columns[i], dc) and any(columns[i])), None)
        if i is not None:
            found["date"] = i
            free.remove(i)
            notes.append(f"Date: column {i + 1}, from its values.")
    if not ({"amount", "debit", "credit"} & found.keys()):
        money = [i for i in free if any(columns[i]) and _fits("amount", columns[i], dc)
                 and i != found.get("balance")]
        if money:
            found["amount"] = money[0]
            free.remove(money[0])
            notes.append(f"Amount: column {money[0] + 1}, from its values.")
    if "description" not in found:
        if "memo" in found:
            found["description"] = found.pop("memo")
        else:
            text = [i for i in free if any(columns[i]) and not _fits("amount", columns[i], dc)
                    and not _fits("date", columns[i], dc)]
            if text:
                i = max(text, key=lambda i: sum(len(v) for v in columns[i]))
                found["description"] = i
                notes.append(f"Description: column {i + 1}, from its values.")
    found.pop("memo", None)
    if "direction" in found and "amount" not in found:
        found.pop("direction")                           # a type column only means something beside one amount
    return found, notes


# ---------------------------------------------------------------- the layout, and reading rows with it

@dataclass
class Layout:
    """How to read one bank's file: kept in the account's profile."""
    columns: dict[str, int]          # role -> column index (0-based)
    date_format: str
    decimal_comma: bool = False
    sign: int | None = None          # one signed amount column: +1 = its positive amounts are money out, -1 = money in
    kind: str = "transactions"       # KINDS: transactions / activity (investment) / positions (holdings)
    actions: dict[str, str] = field(default_factory=dict)   # activity: your action words -> engine.ACTION_KINDS
    holdings: dict[str, str] = field(default_factory=dict)  # a fund's symbol -> the line it goes to (investments.resolve)

    def to_json(self) -> dict:
        return {"columns": self.columns, "date_format": self.date_format, "decimal_comma": self.decimal_comma,
                "sign": self.sign, "kind": self.kind, "actions": self.actions, "holdings": self.holdings}

    @classmethod
    def from_json(cls, data: dict) -> "Layout":
        return cls({k: int(v) for k, v in data["columns"].items()}, data["date_format"],
                   bool(data.get("decimal_comma")), data.get("sign"), data.get("kind") or "transactions",
                   dict(data.get("actions") or {}), dict(data.get("holdings") or {}))


@dataclass(frozen=True)
class Row:
    """One transaction from the file, in Plaid's terms."""
    line: int                        # its row in the file (1 = the first non-empty line)
    posted: str                      # YYYY-MM-DD: the row's date
    purchased: str                   # YYYY-MM-DD when the file has a second date, else ''
    description: str
    amount_cents: int                # positive = money out
    card: str = ""                   # the card used: last 4 digits only
    category: str = ""               # the bank's own
    balance_cents: int | None = None # the running balance after it, when the file has one


@dataclass
class Detection:
    layout: Layout
    header: list[str] | None
    data_start: int
    width: int
    fingerprint: str
    source: str = "detected"         # 'detected' or 'profile' (your saved choices for this header)
    notes: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)   # 'date_order', 'sign': to settle before importing
    sign_source: str = ""            # how money out was told: columns / type / balance / words / majority / profile / you


def _cell(row: list[str], i: int | None) -> str:
    return row[i] if i is not None and i < len(row) else ""


def _raw_amount(row: list[str], layout: Layout) -> Decimal | None | str:
    """The amount in money-out-positive terms before the sign of a single signed column is applied (debit / credit
    and type columns are already right). None when empty, an error text when unreadable."""
    c, dc = layout.columns, layout.decimal_comma
    if "amount" in c:
        text = _cell(row, c["amount"])
        value = parse_money(text, dc)
        if value is None:
            return f"amount '{text}' isn't a number" if text else None
        if "direction" in c:
            word = _cell(row, c["direction"]).lower()
            if word in OUT_WORDS:
                return abs(value)
            if word in IN_WORDS:
                return -abs(value)
            return f"type '{_cell(row, c['direction'])}' isn't debit or credit"
        return value
    debit, credit = _cell(row, c.get("debit")), _cell(row, c.get("credit"))
    d, cr = parse_money(debit, dc), parse_money(credit, dc)
    if (debit and d is None) or (credit and cr is None):
        return f"amount '{debit or credit}' isn't a number"
    if d is None and cr is None:
        return None
    return abs(d or 0) - abs(cr or 0)


def read_rows(table: Table, detection: Detection) -> tuple[list[Row], list[tuple[int, str]]]:
    """The file's transactions, in file order, and the rows that couldn't be read (row number, why)."""
    layout, c = detection.layout, detection.layout.columns
    rows: list[Row] = []
    skipped: list[tuple[int, str]] = []
    sign = layout.sign if ("amount" in c and "direction" not in c) else 1
    for index in range(detection.data_start, len(table)):
        cells, line = table[index], index + 1
        posted = parse_date(_cell(cells, c.get("date")), layout.date_format)
        purchased = parse_date(_cell(cells, c.get("authorized")), layout.date_format) or ""
        if not posted and purchased:                     # not posted yet: its purchase date
            posted, purchased = purchased, ""
        if not posted:
            skipped.append((line, "no date" if not _cell(cells, c.get("date")) else
                            f"date '{_cell(cells, c.get('date'))}' isn't a date"))
            continue
        raw = _raw_amount(cells, layout)
        if raw is None or isinstance(raw, str):
            skipped.append((line, raw or "no amount"))
            continue
        digits = re.sub(r"\D", "", _cell(cells, c.get("card")))
        balance = parse_money(_cell(cells, c.get("balance")), layout.decimal_comma)
        description = " ".join(_cell(cells, c.get("description")).split()) or "(no description)"
        rows.append(Row(line, posted, purchased, description, to_cents(raw * (sign or 1)), digits[-4:],
                        _cell(cells, c.get("category")), to_cents(balance) if balance is not None else None))
    return rows, skipped


# ---------------------------------------------------------------- which way round money goes

def _balance_proof(dates: list[str], amounts: list[Decimal], balances: list[Decimal]) -> tuple[int, float]:
    """(k, share of rows that fit) where each balance = the previous one + k x the amount, in date order. k=+1: the
    file's positive amounts raise the Balance column."""
    orders = [list(range(len(dates)))]
    if dates != sorted(dates):
        orders = [orders[0][::-1]] if dates == sorted(dates, reverse=True) else [orders[0], orders[0][::-1]]
    best = (1, 0.0)
    for order in orders:
        for k in (1, -1):
            fits = sum(1 for a, b in zip(order, order[1:]) if balances[b] - balances[a] == k * amounts[b])
            share = fits / (len(order) - 1) if len(order) > 1 else 0.0
            if fits >= 2 and share > best[1]:
                best = (k, share)
    return best


IN_HINTS = {"asset": ("PAYROLL", "DIRECT DEP", "DEPOSIT", "SALARY", "REFUND", "INTEREST PAID", "DIVIDEND"),
            "liability": ("PAYMENT", "PYMT", "REFUND", "RETURN", "CREDIT")}


def detect_sign(table: Table, detection: Detection, classification: str) -> tuple[int, str, str]:
    """For one signed amount column: (sign, how it was told, a note for the preview)."""
    c, dc = detection.layout.columns, detection.layout.decimal_comma
    probe = Layout(detection.layout.columns, detection.layout.date_format, dc, 1)
    data = []
    for cells in table[detection.data_start:]:
        when = parse_date(_cell(cells, c.get("date")), probe.date_format)
        raw = _raw_amount(cells, probe)
        if when and isinstance(raw, Decimal):
            data.append((when, raw, parse_money(_cell(cells, c.get("balance")), dc),
                         _cell(cells, c.get("description")).upper()))
    if not data:
        raise DetectError("No row has both a date and an amount.")
    if "balance" in c and all(d[2] is not None for d in data):
        k, share = _balance_proof([d[0] for d in data], [d[1] for d in data], [d[2] for d in data])
        if share >= 0.6:
            balances = sorted(d[2] for d in data)
            owed = classification == "liability" and balances[len(balances) // 2] > 0
            # Money out lowers what you have, and raises what you owe.
            sign = k if owed else -k
            return sign, "balance", (f"Money out: {'positive' if sign > 0 else 'negative'} amounts, proved by the "
                                     f"Balance column ({share:.0%} of rows add up).")
    hints = IN_HINTS["liability" if classification == "liability" else "asset"]
    signs = [1 if d[1] > 0 else -1 for d in data if d[1] and any(h in d[3] for h in hints)]
    if len(signs) >= 2 and max(signs.count(1), signs.count(-1)) >= 0.75 * len(signs):
        incoming = 1 if signs.count(1) > signs.count(-1) else -1
        sign = -incoming
        return sign, "words", (f"Money out: {'positive' if sign > 0 else 'negative'} amounts, a guess from rows like "
                               f"payments and deposits. Check the preview.")
    positive = sum(1 for d in data if d[1] > 0)
    negative = len(data) - positive
    if positive != negative:
        sign = 1 if positive > negative else -1
    else:                                # a tie: what's usual, cards' purchases positive, bank accounts' spending negative
        sign = 1 if classification == "liability" else -1
    return sign, "majority", (f"Money out: {'positive' if sign > 0 else 'negative'} amounts, a guess (most rows are "
                              f"spending). Check the preview.")


# ---------------------------------------------------------------- putting it together

@dataclass(frozen=True)
class Located:
    """Where a file's header and data are, before anything about its columns is guessed."""
    decimal_comma: bool
    header: list[str] | None
    data_start: int
    width: int
    fingerprint: str


def locate(table: Table) -> Located:
    dc = uses_decimal_comma(table)
    header_index, data_start = find_header(table, dc)
    header = table[header_index] if header_index is not None else None
    width = max(len(r) for r in table[header_index if header_index is not None else data_start:])
    return Located(dc, header, data_start, width, fingerprint(header, width))


def file_kind(columns: dict[str, int], values: list[list[str]]) -> str:
    """What a file holds, from its columns: a symbol with quantities / prices / actions is investment activity when its
    rows have different dates, else a holdings statement; anything else is transactions."""
    if "symbol" not in columns or not ((INVESTMENT_ROLES - {"symbol"}) & columns.keys()):
        return "transactions"
    if "date" not in columns or len({v for v in values[columns["date"]] if v}) <= 1:
        return "positions"
    return "activity"


def _for_kind(columns: dict[str, int], kind: str) -> dict[str, int]:
    """Only the roles that kind of file uses (a bank's Type column isn't an action; a holding has no card)."""
    drop = INVESTMENT_ROLES if kind == "transactions" else BANK_ROLES | ({"amount"} if "value" in columns else set())
    return {r: i for r, i in columns.items() if r not in drop}


def _check(columns: dict[str, int], kind: str) -> None:
    if kind == "positions":
        if "symbol" not in columns or not ({"quantity", "value", "amount"} & columns.keys()):
            raise DetectError("Choose the symbol column, and the quantity or value column, above their columns.")
    elif kind == "activity":
        if "date" not in columns:
            raise DetectError("Couldn't tell which column is the date: choose it above its column.")
        if "action" not in columns:
            raise DetectError("Choose the action column (Buy, Sell, Dividend...) above its column.")
        if not ({"amount", "quantity"} & columns.keys()):
            raise DetectError("Choose the amount or quantity column above its column.")
    else:
        if "date" not in columns:
            raise DetectError("Couldn't tell which column is the date: choose it above its column.")
        if not ({"amount", "debit", "credit"} & columns.keys()):
            raise DetectError("Couldn't tell which column is the amount: choose it (or Debit / Credit) above its column.")


def detect(table: Table, classification: str, saved: Layout | None = None, *,
           columns: dict[str, int] | None = None, date_order: str | None = None, sign: int | None = None,
           exact_columns: bool = False, kind: str | None = None) -> Detection:
    """Work out a file's layout. `saved`: your saved layout for this file's header (used when it still reads the file);
    `columns` / `date_order` / `sign` / `kind`: your answers, which win over both. `exact_columns`: `columns` is every
    column's role (the Import page's pickers), not corrections to the guesses."""
    where = locate(table)
    dc, header, data_start, width, fp = where.decimal_comma, where.header, where.data_start, where.width, where.fingerprint
    padded = [r + [""] * (width - len(r)) for r in table[data_start:]]
    values = [[r[i] for r in padded] for i in range(width)]
    notes: list[str] = []
    questions: list[str] = []
    kind = kind if kind in KINDS else None

    layout: Layout | None = None
    source = "detected"
    if saved is not None and all(i < width for i in saved.columns.values()):
        dates = [v for v in values[saved.columns["date"]] if v] if "date" in saved.columns else []
        if ("date" not in saved.columns and saved.kind == "positions") or (
                dates and _share(dates, lambda v: parse_date(v, saved.date_format) is not None) >= 0.8):
            layout, source = Layout(dict(saved.columns), saved.date_format, saved.decimal_comma, saved.sign,
                                    saved.kind, dict(saved.actions), dict(saved.holdings)), "profile"
            notes.append("Using the layout you confirmed for this account before.")
        else:
            notes.append("Your saved layout for this account no longer reads this file: detected again.")
    if layout is None:
        if columns is not None and exact_columns:
            found = {r: i for r, i in columns.items() if i < width}
        else:
            found, notes_found = guess_columns(header, values, dc)
            notes += notes_found
            if columns:
                found = {r: i for r, i in found.items() if r not in columns and i not in columns.values()} | columns
        chosen = kind or file_kind(found, values)
        layout = Layout(_for_kind(found, chosen), "", dc, kind=chosen)
    elif (columns and not (exact_columns and columns == layout.columns)) or date_order or (kind and kind != layout.kind):
        if columns:
            layout.columns = dict(columns) if exact_columns else (
                {r: i for r, i in layout.columns.items() if r not in columns and i not in columns.values()} | columns)
        layout.kind = kind or layout.kind
        layout.columns = _for_kind(layout.columns, layout.kind)
        source = "detected"
    _check(layout.columns, layout.kind)

    # The date format: kept while it reads the column (your saved one), else worked out.
    c = layout.columns
    if "date" in c:
        filled = [v for v in values[c["date"]] if v]
        if not (layout.date_format and not date_order
                and _share(filled, lambda v: parse_date(v, layout.date_format) is not None) >= 0.8):
            picked = pick_date_format(values[c["date"]], date_order)
            if picked is None:
                if layout.kind != "positions":
                    raise DetectError(f"Column {c['date'] + 1} doesn't read as dates.")
                c.pop("date")                            # a holdings file's "date" that isn't one: no date
            else:
                layout.date_format, how, ambiguous = picked
                if how:
                    notes.append(f"Dates: {how}.")
                if ambiguous:
                    questions.append("date_order")

    detection = Detection(layout, header, data_start, width, fp, source, notes, questions)
    if layout.kind != "transactions":
        detection.sign_source, layout.sign = "actions", None   # an investment file's actions say which way
    elif "amount" not in c:
        detection.sign_source, layout.sign = "columns", None
        notes.append("Money out: the Debit column; money in: the Credit column.")
    elif "direction" in c:
        detection.sign_source, layout.sign = "type", None
        notes.append("Money out / in: from the type column (debit / credit).")
    elif sign in (1, -1):
        layout.sign, detection.sign_source = sign, "you"
        notes.append(f"Money out: {'positive' if sign > 0 else 'negative'} amounts, as you chose.")
    elif source == "profile" and layout.sign in (1, -1):
        detection.sign_source = "profile"
    else:
        layout.sign, detection.sign_source, note = detect_sign(table, detection, classification)
        notes.append(note)
        if detection.sign_source != "balance":
            questions.append("sign")
    return detection


# ---------------------------------------------------------------- investment files' rows (phase 5)

@dataclass(frozen=True)
class InvRow:
    """One row of an investment activity file, as the broker wrote it."""
    line: int
    posted: str                      # YYYY-MM-DD (trade date)
    action: str                      # the broker's words ("YOU BOUGHT", "Reinvest Shares", "DIVIDEND")
    symbol: str                      # '' for a cash move
    description: str
    quantity: Decimal | None
    price: Decimal | None
    cash_cents: int | None           # the amount as written: + = cash into the account (brokers' usual sign)
    amount_cents: int = 0            # Plaid's investment sign (+ = cash out), set by the engine from the action


@dataclass(frozen=True)
class Holding:
    """One line of a holdings statement."""
    line: int
    symbol: str                      # '' for cash without a symbol
    description: str
    quantity: Decimal | None
    price: Decimal | None
    value_cents: int | None


def clean_symbol(text: str) -> str:
    """'SPAXX**' -> 'SPAXX'; ' vti ' -> 'VTI'."""
    return re.sub(r"[^A-Za-z0-9.\-/]", "", text or "").upper()


def _money(cells: list[str], i: int | None, dc: bool) -> Decimal | None:
    return parse_money(_cell(cells, i), dc)


def read_activity(table: Table, detection: Detection) -> tuple[list[InvRow], list[tuple[int, str]]]:
    layout, c = detection.layout, detection.layout.columns
    dc = layout.decimal_comma
    rows: list[InvRow] = []
    skipped: list[tuple[int, str]] = []
    for index in range(detection.data_start, len(table)):
        cells, line = table[index], index + 1
        posted = parse_date(_cell(cells, c.get("date")), layout.date_format)
        if not posted:
            skipped.append((line, "no date" if not _cell(cells, c.get("date")) else
                            f"date '{_cell(cells, c.get('date'))}' isn't a date"))
            continue
        action = " ".join(_cell(cells, c.get("action")).split())
        if not action:
            skipped.append((line, "no action"))
            continue
        quantity, price, amount = _money(cells, c.get("quantity"), dc), _money(cells, c.get("price"), dc), \
            _money(cells, c.get("amount"), dc)
        if quantity is None and amount is None:
            skipped.append((line, "no amount or quantity"))
            continue
        description = " ".join(_cell(cells, c.get("description")).split())
        rows.append(InvRow(line, posted, action, clean_symbol(_cell(cells, c.get("symbol"))), description or action,
                           quantity, price, to_cents(amount) if amount is not None else None))
    return rows, skipped


def read_holdings(table: Table, detection: Detection) -> tuple[list[Holding], list[tuple[int, str]]]:
    layout, c = detection.layout, detection.layout.columns
    dc = layout.decimal_comma
    holdings: list[Holding] = []
    skipped: list[tuple[int, str]] = []
    value_column = c.get("value", c.get("amount"))
    for index in range(detection.data_start, len(table)):
        cells, line = table[index], index + 1
        symbol = clean_symbol(_cell(cells, c.get("symbol")))
        description = " ".join(_cell(cells, c.get("description")).split())
        if "TOTAL" in f"{symbol} {description}".upper() or not (symbol or description):
            skipped.append((line, "a total" if "TOTAL" in f"{symbol} {description}".upper() else "no symbol"))
            continue
        quantity, price, value = _money(cells, c.get("quantity"), dc), _money(cells, c.get("price"), dc), \
            _money(cells, value_column, dc)
        if quantity is None and value is None:
            skipped.append((line, "no quantity or value"))
            continue
        holdings.append(Holding(line, symbol, description or symbol, quantity, price,
                                to_cents(value) if value is not None else None))
    return holdings, skipped


_DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}")


def statement_date(table: Table, detection: Detection) -> str | None:
    """A holdings statement's date: its date column when every row has the same one, else the first date in the lines
    above its header ("Positions as of 09/30/2026"); None if neither."""
    c, layout = detection.layout.columns, detection.layout
    if "date" in c:
        days = {parse_date(_cell(r, c["date"]), layout.date_format) for r in table[detection.data_start:]} - {None}
        if len(days) == 1:
            return days.pop()
    for row in table[:max(detection.data_start - 1, 0)]:
        for cell in row:
            for found in _DATE_IN_TEXT.findall(cell):
                for fmt in DATE_FORMATS:
                    if (day := parse_date(found, fmt)):
                        return day
    return None
