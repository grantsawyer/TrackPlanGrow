"""Importing a bank file into an account: prepare a plan (read -> detect -> rows -> ids, overlap, duplicates,
categories), show it, then apply it as one batch that can be undone. Nothing is written until `apply`.

**Ids.** Each row gets a made-up transaction id `csv-<hash>` from its own fields and its place among identical rows
(the formula of the first importer, 2026-10-29, so rows it imported count as already imported). Importing the same
file, or an overlapping download, twice adds nothing; Plaid never sends those ids, so a sync never changes them.

**Overlap** (user's decision 2026-11-13): the file's dates are compared with the account's own transactions (Plaid's,
earlier imports from other files, yours). If they overlap, you choose:
- `outside`: only rows outside the dates the account has (for a connected account: before its first transaction,
  since Plaid keeps adding the rest);
- `skip`: every row except likely duplicates (an existing transaction of the exact amount within 3 days, each existing
  one matched once);
- `review`: like skip, but you pick likely duplicates to take anyway (`take`).

**Categories**, the first that applies: a card payment (money in on a card with "PAYMENT" / "PYMT") -> credit card
payment; a merchant Plaid already knows (`merchant_key`) -> Plaid's category, name and logo; your mapping of the bank's
category, then the built-in one (CATEGORY_MAP) -> Uncategorized. Your rules then run as for any transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from fintrack import manual
from fintrack.imports import layout as lay
from fintrack.imports.layout import Detection, Layout, Row
from fintrack.imports.readers import read_table

ID_PREFIX = "csv-"
OVERLAP_CHOICES = ("outside", "skip", "review")
DUPLICATE_DAYS = 3

# A bank's own category -> Plaid's (primary, detailed), for a merchant Plaid hasn't seen. The first entries came from
# one card issuer's file (matched against Plaid's rows, 2026-10-29), each the most general fit; the rest are common
# names other banks use. Matched ignoring case. Your choices for an account's bank (its profile) come first.
CATEGORY_MAP = {
    "Merchandise": ("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE"),
    "Dining": ("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    "Gas/Automotive": ("TRANSPORTATION", "TRANSPORTATION_GAS"),
    "Airfare": ("TRAVEL", "TRAVEL_FLIGHTS"),
    "Lodging": ("TRAVEL", "TRAVEL_LODGING"),
    "Car Rental": ("TRAVEL", "TRAVEL_RENTAL_CARS"),
    "Other Travel": ("TRAVEL", "TRAVEL_OTHER_TRAVEL"),
    "Entertainment": ("ENTERTAINMENT", "ENTERTAINMENT_OTHER_ENTERTAINMENT"),
    "Phone/Cable": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_TELEPHONE"),
    "Internet": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_INTERNET_AND_CABLE"),
    "Utilities": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_OTHER_UTILITIES"),
    "Health Care": ("MEDICAL", "MEDICAL_OTHER_MEDICAL"),
    "Insurance": ("GENERAL_SERVICES", "GENERAL_SERVICES_INSURANCE"),
    "Other Services": ("GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES"),
    "Fee/Interest Charge": ("BANK_FEES", "BANK_FEES_OTHER_BANK_FEES"),
    "Payment/Credit": ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
    "Groceries": ("FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES"),
    "Food & Drink": ("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    "Restaurants": ("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    "Gas": ("TRANSPORTATION", "TRANSPORTATION_GAS"),
    "Travel": ("TRAVEL", "TRAVEL_OTHER_TRAVEL"),
    "Shopping": ("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE"),
    "Bills & Utilities": ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_OTHER_UTILITIES"),
    "Health & Wellness": ("MEDICAL", "MEDICAL_OTHER_MEDICAL"),
    "Fees & Adjustments": ("BANK_FEES", "BANK_FEES_OTHER_BANK_FEES"),
    "Payment": ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
}
_CATEGORY_MAP_LOWER = {k.lower(): v for k, v in CATEGORY_MAP.items()}
UNKNOWN = ("OTHER", "OTHER_OTHER")          # blank or unlisted: Uncategorized, for you to sort
CARD_PAYMENT = ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")


class ImportFileError(ValueError):
    """A file or an account that can't be imported as asked; the message is shown to you."""


# ---------------------------------------------------------------- merchants Plaid knows

def merchant_key(text: str | None) -> str:
    """Bank text without what changes per purchase: 'FAKE MART 1234' and 'FAKE MART 5678' -> 'FAKE MART';
    'AMAZON MKTPL*E40KT5NN3' -> 'AMAZON MKTPL*'. Order codes after a '*' go, then words with digits, '#' and spaces."""
    text = re.sub(r"\*\S*", "*", (text or "").upper())
    words = [w for w in text.replace("#", " ").split() if not any(ch.isdigit() for ch in w)]
    return " ".join(words)


@dataclass
class Known:
    """What Plaid says about a merchant, from its latest transaction with that bank text."""
    merchant_name: str | None
    primary: str
    detailed: str
    logo_url: str | None
    merchant_entity_id: str | None


def known_merchants(conn: sqlite3.Connection) -> dict[str, Known]:
    """{merchant_key: Plaid's category, name and logo} from Plaid's own transactions (never imported ones), the latest
    winning. Keyed by the raw bank text when there is one, else the description."""
    found: dict[str, Known] = {}
    for r in conn.execute(
            """SELECT COALESCE(original_description, name), merchant_name, plaid_category_primary,
                      plaid_category_detailed, logo_url, merchant_entity_id
               FROM transactions
               WHERE plaid_transaction_id NOT LIKE 'csv-%' AND plaid_category_detailed IS NOT NULL
                 AND removed_at IS NULL
               ORDER BY date, id"""):
        key = merchant_key(r[0])
        if key:
            found[key] = Known(r[1], r[2], r[3], r[4], r[5])
    return found


def import_id(account_id: int, row: Row, occurrence: int) -> str:
    """The made-up transaction id: the same row in the same place among identical rows -> the same id, every time."""
    raw = "|".join((str(account_id), row.posted, row.purchased, row.card, str(row.amount_cents), row.description,
                    str(occurrence)))
    return ID_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _row_key(row: Row) -> tuple:
    """What makes two rows identical for the id (not their place in the file, nor the balance after them)."""
    return (row.posted, row.purchased, row.card, row.amount_cents, row.description)


# ---------------------------------------------------------------- accounts and profiles

@dataclass(frozen=True)
class Account:
    id: int
    name: str
    mask: str | None
    type: str
    classification: str
    is_manual: bool
    live: bool                   # connected and not removed: Plaid keeps adding its transactions


def account_info(conn: sqlite3.Connection, account_id: int) -> Account:
    r = conn.execute("""SELECT a.id, a.name, a.mask, a.type, a.classification, a.is_manual, i.status
                        FROM accounts a LEFT JOIN items i ON i.id = a.item_id WHERE a.id = ?""", (account_id,)).fetchone()
    if not r:
        raise ImportFileError(f"No account {account_id}.")
    return Account(r[0], r[1], r[2], r[3], r[4], bool(r[5]), not r[5] and r[6] != "removed")


def load_profile(conn: sqlite3.Connection, account_id: int, fp: str) -> tuple[Layout | None, dict[str, str]]:
    """Your saved layout for this account's files with this header, and your mapping of its bank's categories
    {bank's category: one of your category keys}."""
    r = conn.execute("SELECT layout, categories FROM import_profiles WHERE account_id = ? AND fingerprint = ?",
                     (account_id, fp)).fetchone()
    if not r:
        return None, {}
    return Layout.from_json(json.loads(r[0])), {k: v for k, v in json.loads(r[1]).items() if isinstance(v, str)}


def save_profile(conn: sqlite3.Connection, account_id: int, detection: Detection, categories: dict[str, str]) -> None:
    conn.execute(
        """INSERT INTO import_profiles (account_id, fingerprint, header, layout, categories) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (account_id, fingerprint) DO UPDATE SET header = excluded.header, layout = excluded.layout,
               categories = excluded.categories, updated_at = CURRENT_TIMESTAMP""",
        (account_id, detection.fingerprint, json.dumps(detection.header), json.dumps(detection.layout.to_json()),
         json.dumps(categories)))


# ---------------------------------------------------------------- the plan

def _category_exists(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("SELECT 1 FROM categories WHERE key = ?", (key,)).fetchone() is not None


def last_overlap_choice(conn: sqlite3.Connection, account_id: int) -> str | None:
    """What you chose the last time a file for this account overlapped (the Import page selects it again)."""
    r = conn.execute("""SELECT overlap_choice FROM import_batches WHERE account_id = ? AND overlap_choice IS NOT NULL
                        ORDER BY created_at DESC, id DESC LIMIT 1""", (account_id,)).fetchone()
    return r[0] if r else None


def importable_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Accounts a file can go into: connected ones (not removed) and manual bank accounts, cards and loans (phase 4),
    not hidden; by institution (a manual account's own, else "Manual") and name."""
    return conn.execute(
        f"""SELECT a.id, a.name, a.mask, a.type, a.is_manual,
                   COALESCE(i.institution_name, NULLIF(trim(a.manual_institution), ''), 'Manual') AS institution_name
            FROM accounts a LEFT JOIN items i ON i.id = a.item_id
            WHERE a.is_hidden = 0 AND ({manual.takes_transactions_sql('a')} OR a.type = 'investment')
              AND (a.is_manual = 1 OR i.status <> 'removed')
            ORDER BY a.is_manual, lower(institution_name), lower(a.name), a.id""").fetchall()

def bank_category(name: str) -> tuple[str, str] | None:
    """The built-in mapping of a bank's category to Plaid's, or None."""
    return _CATEGORY_MAP_LOWER.get(name.lower()) if name else None


@dataclass(frozen=True)
class Choice:
    """A row's category: Plaid's terms (stored as what the bank / Plaid said), and your category when you mapped the
    bank's (it becomes the row's category ahead of Plaid's; rules still come first)."""
    primary: str
    detailed: str
    known: Known | None = None
    key: str | None = None
    source: str = "none"             # payment / plaid (merchant known) / yours / bank (built-in map) / none


def choose_category(account: Account, row: Row, known: Known | None, mine: dict[str, str]) -> Choice:
    text = row.description.upper()
    paying = "PAYMENT" in text or "PYMT" in text
    builtin = bank_category(row.category)
    if paying and ((account.type == "credit" and row.amount_cents < 0) or builtin == CARD_PAYMENT):
        return Choice(*CARD_PAYMENT, source="payment")   # paying the card: a transfer, whatever Plaid called others
    if known:
        return Choice(known.primary, known.detailed, known, source="plaid")   # Plaid knows the merchant
    if row.category and mine.get(row.category):
        return Choice(*UNKNOWN, key=mine[row.category], source="yours")
    if builtin and builtin != CARD_PAYMENT:
        return Choice(*builtin, source="bank")
    return Choice(*UNKNOWN)                              # some other credit, or a category not mapped: for you to sort


@dataclass
class Existing:
    """One of the account's transactions, for the overlap and duplicate checks."""
    id: int
    date: str
    authorized: str | None
    amount_cents: int
    name: str


@dataclass
class Planned:
    row: Row
    transaction_id: str
    primary: str
    detailed: str
    known: Known | None              # Plaid knew the merchant (its category, name, logo are used)
    category_key: str | None = None  # your mapping of the bank's category (Choice.key)
    category_source: str = "none"
    status: str = "new"              # new / already (imported before) / covered (outside the overlap choice) / duplicate
    duplicate_of: Existing | None = None
    take: bool = True


@dataclass
class Overlap:
    """The file's dates against the account's: shown with what each choice would add."""
    file_from: str
    file_to: str
    have: list[tuple[str, str | None]]   # the dates the account has (covered_spans); end None: Plaid keeps adding
    outside: int                     # rows each choice adds (outside: neither on those dates nor likely duplicates)
    skip: int
    duplicates: int                  # likely duplicates (review: skip + the ones you take)


@dataclass
class Plan:
    account: Account
    detection: Detection
    file_name: str
    file_sha256: str
    rows: list[Planned] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)      # (row in the file, why it couldn't be read)
    overlap: Overlap | None = None
    choice: str | None = None
    cards_missing: list[tuple[str, str]] = field(default_factory=list)  # (id, card) imported before without the card
    categories: dict[str, str] = field(default_factory=dict)       # your mapping {bank's category: key} (profile)
    bank_categories: Counter = field(default_factory=Counter)      # every bank category in the file, with its rows
    unmapped: Counter = field(default_factory=Counter)             # the bank's categories with no mapping
    cards: Counter = field(default_factory=Counter)                # the cards used (last 4) in the file, with their rows
    primary_card: str | None = None      # which of them is the account's own card (the rest: authorized users')
    primary_asked: bool = False          # more than one card and none is the account's: you say which
    kind: str = "transactions"           # layout.KINDS: transactions / activity / positions
    action_keys: dict = field(default_factory=dict)   # activity: action key -> [kind, an example, rows]
    problems: list[str] = field(default_factory=list) # what stops an investment import (investments.check_lines)
    as_of: str | None = None             # a holdings statement's date
    zeroed: list[str] = field(default_factory=list)   # holdings: lines the statement doesn't list (set to 0)
    holding_keys: dict = field(default_factory=dict)  # a manual account: the file's symbols -> their target and rows
    unpaired: int = 0                                 # reinvestments the file has no dividend for
    add_dividends: bool = True                        # ...and whether one is added for each (investments.py)
    lines: list | None = None                         # that account's lines (investments.account_lines)

    @property
    def new(self) -> list[Planned]:
        return [p for p in self.rows if p.take]

    def count(self, status: str) -> int:
        return sum(1 for p in self.rows if p.status == status)

    @property
    def known_count(self) -> int:
        return sum(1 for p in self.new if getattr(p, "known", None))

    @property
    def needs(self) -> list[str]:
        """What you must settle before importing: 'date_order', 'sign', 'overlap', 'primary_card'."""
        return (self.detection.questions + (["overlap"] if self.overlap and not self.choice else [])
                + (["primary_card"] if self.primary_asked else [])
                + (["as_of"] if self.kind == "positions" and not self.as_of else [])
                + (["problems"] if self.problems else []))

    @property
    def primary_change(self) -> bool:
        """Importing would change which card is the account's own: a manual account takes the primary's last 4 as its
        number; on a connected one (whose number is Plaid's), the primary card's imported rows lose their card."""
        if not self.primary_card or self.primary_card == (self.account.mask or ""):
            return False
        return self.account.is_manual or any(p.row.card == self.primary_card for p in self.rows)


SPAN_GAP_DAYS = 35      # imported / your rows further apart than this are separate spans (two imports, a gap between)


def covered_spans(dated: list[tuple[str, bool]], live: bool, before: str | None = None) -> list[tuple[str, str | None]]:
    """The dates an account already has, as spans (start, end; end None = open-ended), from its transactions'
    (date, is Plaid's) in date order. Plaid's rows cover from the first on while the connection is live (Plaid keeps
    adding), else first to last; imported and your own rows cover their dates, split at gaps over SPAN_GAP_DAYS.
    `before` (a date you gave): everything from it on counts as covered, instead."""
    if before:
        return [(before, None)]
    plaid = [d for d, is_plaid in dated if is_plaid]
    spans: list[tuple[str, str | None]] = [(plaid[0], None if live else plaid[-1])] if plaid else []
    run: tuple[str, str] | None = None
    for d in sorted(d for d, is_plaid in dated if not is_plaid):
        if run and _days(run[1], d) <= SPAN_GAP_DAYS:
            run = (run[0], d)
        else:
            if run:
                spans.append(run)
            run = (d, d)
    if run:
        spans.append(run)
    # merge what touches or overlaps (an import inside Plaid's span adds nothing)
    merged: list[tuple[str, str | None]] = []
    for start, end in sorted(spans):
        if merged and (merged[-1][1] is None or start <= merged[-1][1]):
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, None if last_end is None or end is None else max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def match_duplicates(rows: list[Planned], existing: list[Existing]) -> None:
    """Mark each row with its likely duplicate: an existing transaction of the same amount within DUPLICATE_DAYS (by
    its date or purchase date), the closest one, each existing one used once."""
    by_amount: dict[int, list[Existing]] = {}
    for e in existing:
        by_amount.setdefault(e.amount_cents, []).append(e)
    for p in sorted(rows, key=lambda p: p.row.posted):
        pool = by_amount.get(p.row.amount_cents, [])
        best, best_days = None, DUPLICATE_DAYS + 1
        for e in pool:
            days = min(_days(p.row.posted, e.date), _days(p.row.posted, e.authorized) if e.authorized else 99)
            if days < best_days:
                best, best_days = e, days
        if best is not None:
            p.duplicate_of = best
            pool.remove(best)


def prepare(conn: sqlite3.Connection, account_id: int, data: bytes, file_name: str, *,
            columns: dict[str, int] | None = None, date_order: str | None = None, sign: int | None = None,
            overlap: str | None = None, take: set[int] | frozenset[int] = frozenset(),
            before: str | None = None, exact_columns: bool = False,
            categories: dict[str, str] | None = None, primary_card: str | None = None, kind: str | None = None,
            actions: dict[str, str] | None = None, as_of: str | None = None,
            holdings: dict[str, str] | None = None, add_dividends: bool | None = None) -> Plan:
    """What importing this file into the account would do; writes nothing. Your answers: `columns` {role: index},
    `date_order` 'mdy' / 'dmy', `sign` (+1: positive amounts are money out), `overlap` (OVERLAP_CHOICES), `take` (rows
    in the file of likely duplicates to import anyway, with 'review'), `before` (only rows before this date),
    `exact_columns` (`columns` is every column's role), `categories` (your mapping of the bank's categories to your
    category keys; a blank key removes one), `primary_card` (which card in the file is the account's own: its last 4,
    or '' for none of them), `kind` (what the file holds: layout.KINDS), `actions` (an activity file's action words ->
    investments.ACTION_KINDS), `as_of` (a holdings statement's date, when the file doesn't say), `holdings` (a manual
    account: each fund's symbol -> the line it goes to: 'new', a line id or a line's ticker), `add_dividends`
    (a dividend added for each reinvestment the file has none for; default: as last time, else yes)."""
    from fintrack.imports import investments

    if overlap is not None and overlap not in OVERLAP_CHOICES:
        raise ImportFileError(f"Overlap choice must be one of {', '.join(OVERLAP_CHOICES)}.")
    account = account_info(conn, account_id)
    try:
        table = read_table(data, file_name)
        where = lay.locate(table)
        saved, mine = load_profile(conn, account_id, where.fingerprint)
        detection = lay.detect(table, account.classification, saved, columns=columns, date_order=date_order, sign=sign,
                               exact_columns=exact_columns, kind=kind)
    except ValueError as exc:                            # ReadError / DetectError: shown as they are
        raise ImportFileError(f"{file_name}: {exc}") from None
    file_kind = detection.layout.kind
    _check_account(account, file_kind)
    sha = hashlib.sha256(data).hexdigest()
    if file_kind != "transactions":
        # investment files (phase 5): investments.py
        detection.layout.actions = {k: v for k, v in (detection.layout.actions | (actions or {})).items()
                                    if v in investments.ACTION_KINDS or (k == "(add dividends)" and v in ("yes", "no"))}
        detection.layout.holdings = {k: v for k, v in (detection.layout.holdings | (holdings or {})).items() if v}
        if file_kind == "activity":
            inv_rows, skipped = lay.read_activity(table, detection)
            plan = Plan(account, detection, file_name, sha, skipped=skipped, categories=mine, kind=file_kind)
            if add_dividends is not None:
                detection.layout.actions["(add dividends)"] = "yes" if add_dividends else "no"
            plan.add_dividends = detection.layout.actions.get("(add dividends)", "yes") == "yes"
            existing = investments.plan_activity(conn, plan, inv_rows, detection.layout.actions)
            _resolve_overlap(plan, existing, account.live, before, overlap, take)
            if account.is_manual:
                investments.check_lines(conn, plan)
        else:
            held, skipped = lay.read_holdings(table, detection)
            plan = Plan(account, detection, file_name, sha, skipped=skipped, categories=mine, kind=file_kind,
                        as_of=as_of or lay.statement_date(table, detection))
            investments.plan_positions(conn, plan, held)
        return plan
    rows, skipped = lay.read_rows(table, detection)
    if categories is not None:
        mine = {k: v for k, v in (mine | categories).items() if v and _category_exists(conn, v)}
    plan = Plan(account, detection, file_name, sha, skipped=skipped, categories=mine)

    # Ids, and rows imported before
    seen: Counter = Counter()
    known = known_merchants(conn)
    for row in sorted(rows, key=lambda r: (r.posted, r.purchased, r.line)):
        occurrence = seen[_row_key(row)]
        seen[_row_key(row)] += 1
        tid = import_id(account_id, row, occurrence)
        choice = choose_category(account, row, known.get(merchant_key(row.description)), mine)
        p = Planned(row, tid, choice.primary, choice.detailed, choice.known, choice.key, choice.source)
        if row.category:
            plan.bank_categories[row.category] += 1
        found = conn.execute("SELECT account_owner FROM transactions WHERE plaid_transaction_id = ?", (tid,)).fetchone()
        if found:
            p.status, p.take = "already", False
            if found[0] is None and row.card:
                plan.cards_missing.append((tid, row.card))
        elif row.category and choice.source == "none":
            plan.unmapped[row.category] += 1
        plan.rows.append(p)

    # Whose card: the account's own number when the file has it; else, on a manual account with no number, the file's
    # only card; else you say which (user's report 2026-11-13: a manual card has no number, so every row read as an
    # authorized user's).
    plan.cards = Counter(r.card for r in rows if r.card)
    if plan.cards:
        if primary_card is not None and (primary_card == "" or primary_card in plan.cards):
            plan.primary_card = primary_card or None
        elif account.mask and account.mask in plan.cards:
            plan.primary_card = account.mask
        elif len(plan.cards) == 1 and not account.mask:
            plan.primary_card = next(iter(plan.cards))
        else:
            plan.primary_asked = True

    # Overlap with the account's other transactions (not this file's own rows imported before)
    ids = {p.transaction_id for p in plan.rows}
    existing = [(Existing(r[0], r[1], r[2], r[3], r[4]), not r[5].startswith((ID_PREFIX, "manual-"))) for r in conn.execute(
        """SELECT id, date, authorized_date, amount_cents, name, plaid_transaction_id FROM transactions
           WHERE account_id = ? AND removed_at IS NULL ORDER BY date""", (account_id,)) if r[5] not in ids]
    _resolve_overlap(plan, existing, account.live, before, overlap, take)
    return plan


def _check_account(account: Account, kind: str) -> None:
    """Which accounts take which kind of file."""
    if kind == "transactions":
        if account.is_manual and account.type not in manual.TRANSACTION_TYPES:
            # its value comes from positions, linked transfers or what you type (manual.py): no transactions of its own
            raise ImportFileError(f"{account.name} can't take a file of transactions: only bank accounts, cards and "
                                  "loans hold them (an investment account takes activity or holdings).")
    elif account.type != "investment":
        raise ImportFileError(f"This file holds investment {'activity' if kind == 'activity' else 'holdings'}: choose "
                              "an investment account (or, if it's transactions, say so above its columns).")
    elif kind == "positions" and not account.is_manual:
        raise ImportFileError("A holdings statement goes into a manual investment account: a connected account's "
                              "holdings come from Plaid.")


def _resolve_overlap(plan: Plan, existing: list[tuple[Existing, bool]], live: bool, before: str | None,
                     overlap: str | None, take) -> None:
    """The overlap question and likely duplicates (see the module's docstring), for a plan's rows: `existing` are the
    account's other rows, each with whether it's Plaid's."""
    spans = covered_spans([(e.date, is_plaid) for e, is_plaid in existing], live, before)
    fresh = [p for p in plan.rows if p.status not in ("already", "deleted")]

    def covered(p: Planned) -> bool:
        return any(start <= p.row.posted and (end is None or p.row.posted <= end) for start, end in spans)

    # A likely duplicate counts as overlap even just outside those dates: Plaid often dates a row a day or two after
    # the bank's file does, so the row before Plaid's first can be Plaid's first.
    match_duplicates(fresh, [e for e, _ in existing])

    def overlapping(p: Planned) -> bool:
        return covered(p) or p.duplicate_of is not None

    if any(overlapping(p) for p in fresh):
        dupes = sum(1 for p in fresh if p.duplicate_of)
        plan.overlap = Overlap(min(p.row.posted for p in fresh), max(p.row.posted for p in fresh), spans,
                               sum(1 for p in fresh if not overlapping(p)), len(fresh) - dupes, dupes)
        plan.choice = overlap or ("outside" if before else None)
        for p in fresh:
            if p.duplicate_of and plan.choice != "outside":
                p.status = "duplicate"
                p.take = plan.choice == "review" and p.row.line in take
            elif overlapping(p) and plan.choice in ("outside", None):
                p.status, p.take = ("covered" if covered(p) else "duplicate"), False
            if plan.choice is None:
                p.take = False                           # nothing is taken until you choose
    else:
        for p in fresh:
            p.duplicate_of = None


# ---------------------------------------------------------------- writing it, and undoing it

def closing_balances(rows: list[Row], classification: str) -> dict[str, int]:
    """{day: the file's running balance after that day's last row}, as a manual account's value: what you have, or for
    a card or loan what you owe (a file showing what's owed as negative numbers is turned round)."""
    ordered = sorted((r for r in rows if r.balance_cents is not None), key=lambda r: r.line)
    if not ordered:
        return {}
    if ordered[0].posted > ordered[-1].posted:
        ordered.reverse()                                # newest first in the file: oldest first now
    days: dict[str, int] = {}
    for r in ordered:
        days[r.posted] = r.balance_cents                 # the day's last row wins
    values = sorted(days.values())
    if classification == "liability" and values[len(values) // 2] < 0:
        days = {d: -v for d, v in days.items()}
    return days


def apply(conn: sqlite3.Connection, plan: Plan) -> tuple[int | None, int]:
    """Write a plan's rows as one batch (one database transaction), keep the layout in the account's profile, then redo
    what a sync redoes: rules, transfer matching, recurring detection. Returns (batch id or None, rows added)."""
    from fintrack import categories, recurring, rules, sync, transfers

    if plan.needs:
        raise ImportFileError("Settle these before importing: " + ", ".join(plan.needs) + ".")
    if plan.kind != "transactions":
        from fintrack.imports import investments
        return investments.apply(conn, plan)
    new = plan.new
    split, remaps = categories.split_set(conn), categories.remaps(conn)
    batch_id = None
    # a connected account's number is Plaid's: the primary card's rows carry no card (so they're the main card's)
    own_card = plan.primary_card if plan.primary_change and not plan.account.is_manual else None
    with conn:
        if plan.primary_change:
            if plan.account.is_manual:
                conn.execute("UPDATE accounts SET mask = ? WHERE id = ?", (plan.primary_card, plan.account.id))
            else:
                conn.execute(f"""UPDATE transactions SET account_owner = NULL WHERE account_id = ? AND account_owner = ?
                                 AND plaid_transaction_id LIKE '{ID_PREFIX}%'""", (plan.account.id, plan.primary_card))
        if new:
            batch_id = conn.execute(
                """INSERT INTO import_batches (account_id, file_name, file_sha256, rows_added, date_from, date_to,
                                               overlap_choice) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (plan.account.id, plan.file_name, plan.file_sha256, len(new), min(p.row.posted for p in new),
                 max(p.row.posted for p in new), plan.choice)).lastrowid
        for item in new:
            row, k = item.row, item.known
            sync._upsert_transaction(conn, plan.account.id, {
                "transaction_id": item.transaction_id,
                "date": row.posted,
                "authorized_date": row.purchased or None,
                "amount": row.amount_cents / 100,        # to_cents goes through str(): exact for whole cents
                "iso_currency_code": "USD",
                "name": row.description,
                "original_description": row.description,
                "merchant_name": k.merchant_name if k else None,
                "personal_finance_category": {"primary": item.primary, "detailed": item.detailed},
                "logo_url": k.logo_url if k else None,
                "merchant_entity_id": k.merchant_entity_id if k else None,
                "pending": False,
                # the card used (its last 4), where a card issuer's Plaid data puts it too (migration 0051)
                "account_owner": None if row.card == own_card else (row.card or None),
            }, split, remaps)
            # your mapping of the bank's category: the row's category ahead of Plaid's (rules still come first)
            conn.execute("UPDATE transactions SET import_batch_id = ?, plaid_category_key = COALESCE(?, plaid_category_key)"
                         " WHERE plaid_transaction_id = ?", (batch_id, item.category_key, item.transaction_id))
        for tid, card in plan.cards_missing:
            if card == own_card:
                continue
            conn.execute("UPDATE transactions SET account_owner = ? WHERE plaid_transaction_id = ? AND account_owner IS NULL",
                         (card, tid))
        save_profile(conn, plan.account.id, plan.detection, plan.categories)
        if plan.account.is_manual and batch_id is not None:
            # the file's closing balance on each day becomes the account's value then (yours on a day are kept;
            # a negative one, a card paid past zero, is left to the transactions to work out)
            for day, cents in closing_balances([p.row for p in plan.rows], plan.account.classification).items():
                if cents >= 0:
                    conn.execute("""INSERT OR IGNORE INTO manual_valuations (account_id, date, value_cents, note,
                                    import_batch_id) VALUES (?, ?, ?, 'from an import', ?)""",
                                 (plan.account.id, day, cents, batch_id))
    if plan.account.is_manual:
        manual.refresh(conn, plan.account.id)           # its balance follows its transactions (manual.value_on)
    if new:
        rules.apply_all(conn)
        transfers.detect_transfers(conn)
        recurring.refresh(conn)
    conn.commit()
    return batch_id, len(new)


def batches(conn: sqlite3.Connection, account_id: int | None = None) -> list[sqlite3.Row]:
    """Past imports, newest first, with their account's name."""
    return conn.execute(
        """SELECT b.*, a.name AS account_name, a.mask AS account_mask FROM import_batches b
           JOIN accounts a ON a.id = b.account_id WHERE ? IS NULL OR b.account_id = ?
           ORDER BY b.created_at DESC, b.id DESC""", (account_id, account_id)).fetchall()


def undo(conn: sqlite3.Connection, batch_id: int) -> int:
    """Delete everything one import added (your edits to those rows go with them), then redo rules, transfers and
    recurring. Returns how many transactions were deleted."""
    from fintrack import recurring, rules, transfers

    batch = conn.execute("SELECT undone_at, account_id, kind, date_from FROM import_batches WHERE id = ?",
                         (batch_id,)).fetchone()
    if batch is None:
        raise ImportFileError(f"No import {batch_id}.")
    if batch[0]:
        raise ImportFileError(f"Import {batch_id} was already undone.")
    if batch[2] != "transactions":
        from fintrack.imports import investments
        return investments.undo(conn, batch_id, batch[1], batch[3])
    with conn:
        deleted = conn.execute("DELETE FROM transactions WHERE import_batch_id = ?", (batch_id,)).rowcount
        conn.execute("DELETE FROM manual_valuations WHERE import_batch_id = ?", (batch_id,))
        conn.execute("UPDATE import_batches SET undone_at = CURRENT_TIMESTAMP WHERE id = ?", (batch_id,))
    if account_info(conn, batch[1]).is_manual:
        manual.refresh(conn, batch[1])
    rules.apply_all(conn)
    transfers.detect_transfers(conn)
    recurring.refresh(conn)
    conn.commit()
    return deleted


def peek(data: bytes, file_name: str) -> tuple[list[list[str]], lay.Located | None]:
    """The file's rows and, if it can be told, where its header and data are: the Import page shows them with column
    pickers even when the columns couldn't be worked out."""
    try:
        table = read_table(data, file_name)
    except ValueError:
        return [], None
    try:
        return table, lay.locate(table)
    except ValueError:
        return table, None
