"""Credit card statements and their payments in the cash-flow forecast.

Plaid Liabilities reports each card's CURRENT statement (balance, statement date,
due date, last payment). From that:

    still owed = statement balance - payments made to the card since the statement date

Payments are the card's own transactions in the "Credit card payment" category
(money in to the card) dated on/after the statement date; Plaid's last_payment is
used instead when it's larger (it can know about a payment before its transaction
arrives). What's still owed leaves the card's PAYING account on the due date. A
past-due, unpaid statement is assumed to be paid today (a cash forecast should err
toward warning you).

Statements issued later aren't known yet, so they're ESTIMATED (user's request 2026-10-23): each card's average
monthly spend (`monthly_spend`: its charges, refunds, fees and interest, not payments to it, over the last
AVERAGE_MONTHS complete months) leaves the paying account once a month on the card's usual payment day (`pay_day`: the
due date's day of the month, else the usual day of your payments to it), starting the month after the known statement's
due date (`estimates`, `estimated_payment_events`).

Paying account per card (`card_payment_accounts`): your choice, or "don't forecast";
with no choice, the only visible checking/savings account (if there is exactly one).
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from fintrack.money import to_cents
from fintrack.spending import month_bounds, shift_month

CARD_PAYMENT_CATEGORY = "CREDIT_CARD_PAYMENT"
AVERAGE_MONTHS = 3          # complete months averaged for a card's monthly spend (fewer when there's less history)


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def store_statement(conn: sqlite3.Connection, account_id: int, card: dict) -> None:
    """Save one card's current statement from /liabilities/get (inside the caller's transaction)."""
    conn.execute(
        """
        INSERT INTO card_statements (account_id, statement_balance_cents, statement_date, due_date,
                                     minimum_payment_cents, last_payment_cents, last_payment_date, is_overdue)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (account_id) DO UPDATE SET
            statement_balance_cents = excluded.statement_balance_cents,
            statement_date = excluded.statement_date, due_date = excluded.due_date,
            minimum_payment_cents = excluded.minimum_payment_cents,
            last_payment_cents = excluded.last_payment_cents, last_payment_date = excluded.last_payment_date,
            is_overdue = excluded.is_overdue, updated_at = CURRENT_TIMESTAMP
        """,
        (account_id, to_cents(card.get("last_statement_balance")), _iso(card.get("last_statement_issue_date")),
         _iso(card.get("next_payment_due_date")), to_cents(card.get("minimum_payment_amount")),
         to_cents(card.get("last_payment_amount")), _iso(card.get("last_payment_date")),
         None if card.get("is_overdue") is None else int(bool(card["is_overdue"]))),
    )


# ---------------------------------------------------------------- paying accounts

def payer_options(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Accounts that can pay a card: anything visible except cards and loans."""
    return conn.execute(
        """SELECT id, name, mask, type, is_manual FROM accounts
           WHERE is_hidden = 0 AND type NOT IN ('credit', 'loan') ORDER BY type = 'depository' DESC, name"""
    ).fetchall()


def automatic_payer(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The only visible checking/savings account, connected or manual (docs/parity-plan.md), if there is exactly one."""
    rows = conn.execute(
        "SELECT id, name, mask FROM accounts WHERE is_hidden = 0 AND type = 'depository'"
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def payers(conn: sqlite3.Connection) -> dict[int, dict]:
    """{card id: {"pay_from": account id | None, "mode": "auto" | "chosen" | "none"}} for visible cards."""
    chosen = {r["card_account_id"]: r["pay_from_account_id"]
              for r in conn.execute("SELECT * FROM card_payment_accounts")}
    valid = {r["id"] for r in payer_options(conn)}
    auto = automatic_payer(conn)
    result = {}
    for card in conn.execute("SELECT id FROM accounts WHERE type = 'credit' AND is_hidden = 0"):
        if card["id"] in chosen:
            target = chosen[card["id"]]
            if target is None:
                result[card["id"]] = {"pay_from": None, "mode": "none"}
            elif target in valid:
                result[card["id"]] = {"pay_from": target, "mode": "chosen"}
            else:   # the chosen account was hidden or removed: fall back to automatic
                result[card["id"]] = {"pay_from": auto["id"] if auto else None, "mode": "auto"}
        else:
            result[card["id"]] = {"pay_from": auto["id"] if auto else None, "mode": "auto"}
    return result


def set_payer(conn: sqlite3.Connection, card_id: int, choice: str) -> None:
    """choice: "auto" (remove your choice), "none" (don't forecast), or an account id."""
    card = conn.execute("SELECT id FROM accounts WHERE id = ? AND type = 'credit'", (card_id,)).fetchone()
    if card is None:
        raise ValueError("That isn't a credit card account.")
    with conn:
        if choice == "auto":
            conn.execute("DELETE FROM card_payment_accounts WHERE card_account_id = ?", (card_id,))
            return
        if choice == "none":
            target = None
        else:
            if not choice.isdigit() or int(choice) not in {r["id"] for r in payer_options(conn)}:
                raise ValueError("Choose one of the listed accounts.")
            target = int(choice)
        conn.execute(
            """INSERT INTO card_payment_accounts (card_account_id, pay_from_account_id) VALUES (?, ?)
               ON CONFLICT (card_account_id) DO UPDATE SET pay_from_account_id = excluded.pay_from_account_id,
                                                          updated_at = CURRENT_TIMESTAMP""",
            (card_id, target),
        )


# ---------------------------------------------------------------- what's owed and when

@dataclass
class StatementDue:
    card_id: int
    card_name: str
    card_mask: str | None
    pay_from: int | None
    statement_cents: int
    paid_cents: int
    due_date: date | None
    overdue: bool              # past due (or Plaid says overdue) and not paid

    @property
    def owed_cents(self) -> int:
        return max(0, self.statement_cents - self.paid_cents)


# ---------------------------------------------------------------- a manual card's statement (docs/parity-plan.md)

def closing_on_or_before(day: date, closing_day: int) -> date:
    """The last closing date on or before `day` for a card closing on `closing_day` of each month (the month's last day
    when it's shorter)."""
    def in_month(year: int, month: int) -> date:
        return date(year, month, min(closing_day, calendar.monthrange(year, month)[1]))
    this = in_month(day.year, day.month)
    if this <= day:
        return this
    previous = date(day.year, day.month, 1) - timedelta(days=1)
    return in_month(previous.year, previous.month)


def due_after(closing: date, due_day: int) -> date:
    """The first `due_day` of a month after the closing date."""
    for months in (0, 1):
        year, month = (closing.year, closing.month) if months == 0 else (
            (closing.year + 1, 1) if closing.month == 12 else (closing.year, closing.month + 1))
        candidate = date(year, month, min(due_day, calendar.monthrange(year, month)[1]))
        if candidate > closing:
            return candidate
    raise AssertionError("unreachable")


def statement_settings(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM card_statement_settings WHERE account_id = ?", (account_id,)).fetchone()


def current_closing(settings: sqlite3.Row, today: date) -> date:
    """The current statement's closing date: the one you set, while it's in the current period (after the closing
    before the scheduled one), else the scheduled one."""
    scheduled = closing_on_or_before(today, settings["closing_day"])
    previous = closing_on_or_before(scheduled - timedelta(days=1), settings["closing_day"])
    if settings["statement_date"]:
        chosen = date.fromisoformat(settings["statement_date"])
        if previous < chosen <= today:
            return chosen
    return scheduled


def refresh_manual_statements(conn: sqlite3.Connection, today: date | None = None) -> None:
    """Write each manual card's current statement into card_statements, as Plaid's are, from its settings: the balance
    it owed at the end of the closing date, due on the next due day. Writes only what changed (a write makes other open
    pages reload)."""
    from fintrack import manual

    today = today or date.today()
    for s in conn.execute("""SELECT s.* FROM card_statement_settings s JOIN accounts a ON a.id = s.account_id
                             WHERE a.is_manual = 1 AND a.type = 'credit'""").fetchall():
        closing = current_closing(s, today)
        balance = manual.balance_on(conn, s["account_id"], closing.isoformat())
        wanted = (balance or 0, closing.isoformat(), due_after(closing, s["due_day"]).isoformat(), s["minimum_cents"])
        have = conn.execute("""SELECT statement_balance_cents, statement_date, due_date, minimum_payment_cents
                               FROM card_statements WHERE account_id = ?""", (s["account_id"],)).fetchone()
        if have is None or tuple(have) != wanted:
            with conn:
                conn.execute(
                    """INSERT INTO card_statements (account_id, statement_balance_cents, statement_date, due_date,
                                                    minimum_payment_cents, is_overdue) VALUES (?, ?, ?, ?, ?, 0)
                       ON CONFLICT (account_id) DO UPDATE SET statement_balance_cents = excluded.statement_balance_cents,
                           statement_date = excluded.statement_date, due_date = excluded.due_date,
                           minimum_payment_cents = excluded.minimum_payment_cents, updated_at = CURRENT_TIMESTAMP""",
                    (s["account_id"], *wanted))


def set_statement_settings(conn: sqlite3.Connection, account_id: int, closing_text: str, due_text: str,
                           minimum_text: str = "") -> None:
    """A manual card's closing and due days (1-31) and, optionally, the current statement's minimum."""
    from fintrack.money import parse_amount

    card = conn.execute("SELECT is_manual, type FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if card is None or not card["is_manual"] or card["type"] != "credit":
        raise ValueError("Only a manual credit card's statement is set here; a connected card's comes from the bank.")
    days = []
    for text in (closing_text, due_text):
        if not (text or "").strip().isdigit() or not 1 <= int(text) <= 31:
            raise ValueError("Enter the closing and due days as days of the month, 1 to 31.")
        days.append(int(text))
    try:
        minimum = parse_amount(minimum_text) if (minimum_text or "").strip() else None
    except ValueError:
        raise ValueError("Enter the minimum as an amount, like 35.00.") from None
    with conn:
        conn.execute("""INSERT INTO card_statement_settings (account_id, closing_day, due_day, minimum_cents)
                        VALUES (?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE SET closing_day = excluded.closing_day,
                        due_day = excluded.due_day, minimum_cents = excluded.minimum_cents, updated_at = CURRENT_TIMESTAMP""",
                     (account_id, *days, minimum))
    refresh_manual_statements(conn)


def set_statement_date(conn: sqlite3.Connection, account_id: int, text: str, today: date | None = None) -> None:
    """Change the current statement's closing date (blank = the scheduled one); its balance follows."""
    today = today or date.today()
    settings = statement_settings(conn, account_id)
    if settings is None:
        raise ValueError("Set the card's closing and due days first.")
    chosen = None
    if (text or "").strip():
        try:
            chosen = date.fromisoformat(text.strip())
        except ValueError:
            raise ValueError("Enter the closing date.") from None
        scheduled = closing_on_or_before(today, settings["closing_day"])
        previous = closing_on_or_before(scheduled - timedelta(days=1), settings["closing_day"])
        if not previous < chosen <= today:
            raise ValueError(f"The current statement closes after {previous:%b %-d} and by today.")
    with conn:
        conn.execute("UPDATE card_statement_settings SET statement_date = ?, updated_at = CURRENT_TIMESTAMP"
                     " WHERE account_id = ?", (chosen.isoformat() if chosen else None, account_id))
    refresh_manual_statements(conn, today)


def statements(conn: sqlite3.Connection, today: date | None = None) -> list[StatementDue]:
    """Each visible card's current statement and how much of it is still owed (a manual card's worked out first)."""
    today = today or date.today()
    refresh_manual_statements(conn, today)
    pay = payers(conn)
    rows = conn.execute(
        """SELECT s.*, a.name, a.mask FROM card_statements s JOIN accounts a ON a.id = s.account_id
           WHERE a.is_hidden = 0 AND a.type = 'credit'"""
    ).fetchall()
    result = []
    for s in rows:
        statement = s["statement_balance_cents"] or 0
        paid = 0
        if s["statement_date"]:
            # Payments to the card (money in: negative amounts) since the statement date.
            paid = conn.execute(
                """SELECT COALESCE(SUM(-amount_cents), 0) FROM transactions_effective
                   WHERE account_id = ? AND category = ? AND amount_cents < 0 AND date >= ?""",
                (s["account_id"], CARD_PAYMENT_CATEGORY, s["statement_date"]),
            ).fetchone()[0]
            if s["last_payment_date"] and s["last_payment_date"] >= s["statement_date"]:
                paid = max(paid, s["last_payment_cents"] or 0)
        due = date.fromisoformat(s["due_date"]) if s["due_date"] else None
        result.append(StatementDue(s["account_id"], s["name"], s["mask"],
                                   pay.get(s["account_id"], {}).get("pay_from"),
                                   statement, paid, due, False))
    result.sort(key=lambda d: (d.due_date or date.max, d.card_name))
    _paid_from_bank(conn, result, {s["account_id"]: s["statement_date"] for s in rows})
    flagged = {s["account_id"] for s in rows if s["is_overdue"]}          # Plaid says overdue
    for d in result:
        d.overdue = d.owed_cents > 0 and ((d.due_date is not None and d.due_date < today) or d.card_id in flagged)
    return result


def _paid_from_bank(conn: sqlite3.Connection, dues: list[StatementDue], statement_dates: dict[int, str | None]) -> None:
    """A payment that has left the paying account but not yet reached the card (the card side often posts a day or
    two later; user's report 2026-11-03) pays a statement when its amount is exactly what's still owed: an unmatched
    card payment (not yet paired as a transfer) from that account since the statement date. One account may pay
    several cards, so the exact amount picks the card; each payment counts once, earliest due first. Once the card
    side posts, the pair is a transfer and the card-side sum above covers it."""
    used: set[int] = set()
    for d in dues:                                     # sorted by due date
        since = statement_dates.get(d.card_id)
        if d.owed_cents <= 0 or d.pay_from is None or not since:
            continue
        not_used = f"AND id NOT IN ({','.join('?' * len(used))})" if used else ""   # (NOT IN (NULL) matches nothing)
        hit = conn.execute(
            f"""SELECT id FROM transactions_effective
                WHERE account_id = ? AND category = ? AND amount_cents = ? AND is_transfer = 0 AND date >= ? {not_used}
                ORDER BY date, id LIMIT 1""",
            (d.pay_from, CARD_PAYMENT_CATEGORY, d.owed_cents, since, *used)).fetchone()
        if hit:
            used.add(hit[0])
            d.paid_cents += d.owed_cents


def payment_events(conn: sqlite3.Connection, today: date, end: date) -> list[tuple[int, date, str, int, bool]]:
    """(paying account id, day, name, balance change, overdue) for statements still owed that
    fall due by `end`. Overdue ones land today; paid or unassigned ones are skipped."""
    events = []
    for d in statements(conn, today):
        if d.owed_cents <= 0 or d.pay_from is None or d.due_date is None:
            continue
        day = today if d.overdue else d.due_date
        if day > end:
            continue
        label = f"{d.card_name} statement"            # no mask in cash flow (user's request 2026-10-23)
        events.append((d.pay_from, day, label, -d.owed_cents, d.overdue))
    return events


# ---------------------------------------------------------------- estimated future statements (2026-10-23)

# What a card's statement is made of: everything on the card except money paid to it (the card-payment category, or
# any transfer coming in). Charges are positive, refunds negative (Plaid's sign).
_SPEND_SQL = f"""
    SELECT COALESCE(SUM(amount_cents), 0) FROM transactions_effective
    WHERE account_id = ? AND date >= ? AND date < ?
      AND category <> '{CARD_PAYMENT_CATEGORY}' AND NOT (kind = 'transfer' AND amount_cents < 0)
"""


def monthly_spend(conn: sqlite3.Connection, card_id: int, today: date | None = None) -> tuple[int | None, int]:
    """(average monthly spend in cents, months averaged) over the last AVERAGE_MONTHS complete months, back no
    further than the card's first transaction. (None, 0) without a complete month of history. Never negative."""
    today = today or date.today()
    first = conn.execute("SELECT MIN(date) FROM transactions WHERE account_id = ?", (card_id,)).fetchone()[0]
    if first is None:
        return None, 0
    months, month = [], shift_month(f"{today:%Y-%m}", -1)
    while len(months) < AVERAGE_MONTHS and month >= first[:7]:
        months.append(month)
        month = shift_month(month, -1)
    if not months:
        return None, 0
    total = sum(conn.execute(_SPEND_SQL, (card_id, *month_bounds(m))).fetchone()[0] for m in months)
    return max(0, round(total / len(months))), len(months)


def recent_spend(conn: sqlite3.Connection, card_id: int, today: date | None = None, days: int = 30) -> int:
    """Spend on the card over the last `days` days, today included (Cash flow's "Last 30 days", user's request
    2026-10-27): the same rule as the monthly average (payments and transfers in left out). Never negative."""
    today = today or date.today()
    start, end = (today - timedelta(days=days - 1)).isoformat(), (today + timedelta(days=1)).isoformat()
    return max(0, conn.execute(_SPEND_SQL, (card_id, start, end)).fetchone()[0])


def pay_day(conn: sqlite3.Connection, card_id: int) -> int | None:
    """The card's usual payment day of the month: its statement due date's day, else the median day of its last six
    payments (money in, card-payment category)."""
    due = conn.execute("SELECT due_date FROM card_statements WHERE account_id = ?", (card_id,)).fetchone()
    if due and due[0]:
        return int(due[0][8:10])
    days = [int(r[0][8:10]) for r in conn.execute(
        "SELECT date FROM transactions_effective WHERE account_id = ? AND category = ? AND amount_cents < 0"
        " ORDER BY date DESC LIMIT 6", (card_id, CARD_PAYMENT_CATEGORY))]
    return round(median(days)) if days else None


def _on_day(month: str, day: int) -> date:
    """`day` of `month`, clamped to the month's length (the 31st -> Feb 28)."""
    year, mon = (int(p) for p in month.split("-"))
    return date(year, mon, min(day, calendar.monthrange(year, mon)[1]))


@dataclass
class CardEstimate:
    card_id: int
    card_name: str
    pay_from: int | None
    monthly_cents: int | None      # average monthly spend; None without a complete month of history
    months: int                    # months averaged
    pay_day: int | None
    first_due: date | None         # the first estimated payment: after the known statement's due date and today
    last_30_cents: int = 0         # spend over the last 30 days (recent_spend)


def estimates(conn: sqlite3.Connection, today: date | None = None) -> list[CardEstimate]:
    """Each visible card's average monthly spend and its first estimated (not yet issued) statement payment."""
    today = today or date.today()
    pay = payers(conn)
    result = []
    for card in conn.execute(
            """SELECT a.id, a.name, s.due_date FROM accounts a LEFT JOIN card_statements s ON s.account_id = a.id
               WHERE a.type = 'credit' AND a.is_hidden = 0 ORDER BY a.name"""):
        monthly, months = monthly_spend(conn, card["id"], today)
        day = pay_day(conn, card["id"])
        first = None
        if day is not None:
            # the month after the known statement is due (that one is forecast from the statement itself), else the
            # next payment day after today; never today or earlier
            month = shift_month(card["due_date"][:7], 1) if card["due_date"] else f"{today:%Y-%m}"
            first = _on_day(month, day)
            while first <= today:
                month = shift_month(month, 1)
                first = _on_day(month, day)
        result.append(CardEstimate(card["id"], card["name"], pay.get(card["id"], {}).get("pay_from"),
                                   monthly, months, day, first, recent_spend(conn, card["id"], today)))
    return result


def estimated_payment_events(conn: sqlite3.Connection, today: date, end: date) -> list[tuple[int, date, str, int, bool]]:
    """(paying account id, day, name, balance change, overdue=False) for each estimated statement payment due by `end`:
    one a month from each card's first estimated due date, of its average monthly spend. Cards without a paying
    account, a payment day or any spend are skipped."""
    events = []
    for e in estimates(conn, today):
        if e.pay_from is None or not e.monthly_cents or e.first_due is None:
            continue
        month = f"{e.first_due:%Y-%m}"
        day = e.first_due
        while day <= end:
            events.append((e.pay_from, day, f"{e.card_name} statement (estimated)", -e.monthly_cents, False))
            month = shift_month(month, 1)
            day = _on_day(month, e.pay_day)
    return events
