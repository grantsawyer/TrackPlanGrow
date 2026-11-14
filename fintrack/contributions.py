"""Money moved into and out of investment and retirement accounts (user's request 2026-10-09).

Two sources, combined so nothing is counted twice:

* **Bank side.** Plaid labels a transfer between a bank/card account and an investment or retirement account with its
  own category, "Transfers to and from investment and retirement funds":

      TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS   leaves a bank account, goes into an investment/retirement account
      TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS    arrives in a bank account from an investment/retirement account

  This is all there is for accounts we can't see inside (manual accounts, institutions that don't report activity).
* **Investment side** (migration 0023; synced only when Settings.sync_investment_transactions): activity inside the
  connected investment accounts. `cash` transactions with subtype `contribution` or `deposit` are money in
  (payroll deferrals, employer matches, transfers from the bank); `withdrawal` or `distribution` are money out.
  Buys, sells, dividends, interest and fees are not new money and are ignored. Seen in the Plaid Sandbox 2026-10-09:
  `cash`/`contribution` rows with negative amounts, e.g. "EMPLOYER PAYROLL CONTRIBUTION" into a 401(k).

A transfer from checking into a connected brokerage appears on BOTH sides. A bank-side row and an investment-side row
with the same amount within MATCH_DAYS days are one event, counted once and dated by the investment-side row. Whatever
doesn't pair up still counts: payroll and employer contributions (investment side only), and transfers into accounts we
can't see inside (bank side only). Transactions on investment-type accounts in the regular transactions table are
ignored, hidden accounts are left out, and a bank-side transaction you recategorized away from the transfer categories
is left out. Paycheck deductions into an account that doesn't report them (e.g. a manual 401(k)) still can't be seen.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from fintrack.spending import month_bounds, shift_month

INTO_CODE = "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS"
OUT_CODE = "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS"


MATCH_DAYS = 5                                  # a bank transfer and the deposit it makes arrive this close together
INTO_SUBTYPES = ("contribution", "deposit")      # investment `cash` transactions that are money in
OUT_SUBTYPES = ("withdrawal", "distribution")    # ... and money out
# Cash moved between an account's settlement fund and its holdings ("Sweep in" / "Sweep out") comes back as a deposit
# or withdrawal but never enters or leaves the account: seen in Production 2026-10-11 (a large "Sweep out" beside the
# real "Withdrawal"), so these are ignored. "Funds Received" / "Withdrawal" rows are the real movements.
SWEEP_WORDS = re.compile(r"\bsweep\b", re.IGNORECASE)


GROUPS = ("retirement", "investment")           # the two kinds of account money moves between
# A bank-side transfer doesn't say which account it went to, so its description decides: these words mean retirement.
RETIREMENT_WORDS = re.compile(
    r"\b(ira|roth|401\s*\(?k\)?|403\s*\(?b\)?|457\s*\(?b\)?|hsa|pension|retirement|rollover)(?![a-z0-9])", re.IGNORECASE)


def group_of_text(*texts: str | None) -> str:
    """'retirement' if the transfer's description names a retirement account type, else 'investment'."""
    return "retirement" if any(t and RETIREMENT_WORDS.search(t) for t in texts) else "investment"


@dataclass
class MonthFlow:
    month: str
    into: dict[str, int] = field(default_factory=lambda: {g: 0 for g in GROUPS})   # moved into accounts, by group
    out: dict[str, int] = field(default_factory=lambda: {g: 0 for g in GROUPS})    # moved out of them, by group
    # For the Savings page's columns (user's request 2026-10-11):
    bank_net: int = 0         # into - out of the money that went through a bank account (checking -> brokerage etc.)
    outside_into: int = 0     # contributions seen only inside an investment account (e.g. payroll / employer money)
    # Bank transfers (not Plaid's investment codes) that paired with an investment row, by THEIR date, Plaid sign
    # (+ = left the bank): savings.py takes them out of "other transfers" so they aren't counted twice.
    paired_transfers: int = 0
    # Net flows by group, then by investment account id (None = a bank-side transfer with no account), for the Savings
    # page's retirement and investment charts by account type (user's requests 2026-10-12).
    by_account: dict = field(default_factory=lambda: {g: {} for g in GROUPS})

    @property
    def retirement_by_account(self) -> dict:
        return self.by_account["retirement"]

    @property
    def into_cents(self) -> int:
        return sum(self.into.values())

    @property
    def out_cents(self) -> int:
        return sum(self.out.values())

    @property
    def net_cents(self) -> int:
        return self.into_cents - self.out_cents


@dataclass
class _Flow:
    day: date
    cents: int
    direction: str          # "into" or "out"
    source: str             # "bank" or "investment"
    group: str              # "retirement" or "investment"
    matched: bool = False
    account_id: int | None = None   # investment rows: the account, so moves between two accounts can be found
    partner: str = ""               # investment rows: "bank" (paired with a bank transaction), "internal" (paired with
                                    # another investment account's row), "" (seen only here)
    text: str = ""                  # bank rows: the raw and cleaned bank text, for destination rules
    transaction_id: int | None = None   # bank rows: the transaction (transfer_accounts, for its logo)
    paired_account: int | None = None   # bank rows: the investment account whose row it paired with (logos and
                                        # transfer matching only; totals never read it: a paired bank row
                                        # is counted on the investment side)
    investment_id: int | None = None    # investment rows: their investment_transactions id; bank rows: the one they
                                        # paired with (investment_links)
    rule: str = ""                      # bank rows: the destination rule's text that named their account


def _bank_flows(conn: sqlite3.Connection, start: date, end: date) -> list[_Flow]:
    flows = []
    for r in conn.execute(
        """
        SELECT e.id, e.date, e.plaid_category_detailed AS code, e.amount_cents, e.name, e.merchant_name,
               t.original_description
        FROM transactions_effective e JOIN transactions t ON t.id = e.id
        WHERE account_hidden = 0 AND account_type <> 'investment'
          AND e.plaid_category_detailed IN (?, ?)
          -- left alone unless you moved it out of the transfer categories
          AND (category_source = 'plaid' OR category LIKE 'TRANSFER%')
          AND e.date >= ? AND e.date < ?
        """,
        (INTO_CODE, OUT_CODE, start.isoformat(), end.isoformat()),
    ):
        group = group_of_text(r["name"], r["merchant_name"])
        text = " ".join(filter(None, (r["original_description"], r["name"], r["merchant_name"])))
        # Plaid sign on the bank account: positive = left it (into investments); negative = arrived (out of them).
        if r["code"] == INTO_CODE:
            flows.append(_Flow(date.fromisoformat(r["date"]), r["amount_cents"], "into", "bank", group, text=text,
                               transaction_id=r["id"]))
        else:
            flows.append(_Flow(date.fromisoformat(r["date"]), -r["amount_cents"], "out", "bank", group, text=text,
                               transaction_id=r["id"]))
    return flows


# ---------------------------------------------------------------- destination rules (migration 0030)

def _destination_rules(conn: sqlite3.Connection) -> list[tuple[int, str, int, str, str]]:
    """(rule id, pattern lower-cased, account id, group, pattern as you wrote it) for every rule on a visible account,
    oldest first."""
    from fintrack import visuals        # imported here: visuals imports this module

    return [(r["id"], r["pattern"].strip().lower(), r["account_id"],
             "retirement" if visuals.is_retirement_account(r) else "investment", r["pattern"].strip())
            for r in conn.execute("SELECT d.id, d.pattern, d.account_id, a.subtype, a.type_kind, a.type_label"
                                  " FROM destination_rules d"
                                  " JOIN accounts a ON a.id = d.account_id WHERE a.is_hidden = 0 ORDER BY d.id")]


def _rule_for(flow: _Flow, rules: list[tuple[int, str, int, str, str]]) -> tuple[int, str, int, str, str] | None:
    """The first rule whose text the transfer's bank text contains (case-insensitive)."""
    text = flow.text.lower()
    return next((r for r in rules if r[1] and r[1] in text), None)


def _apply_destinations(conn: sqlite3.Connection, bank: list[_Flow]) -> None:
    """Bank transfers that paired with nothing: the first destination rule whose text they contain (raw bank text,
    name or merchant) names the account they went to, and that account decides retirement or investment (user's
    request 2026-10-12). Unmatched by any rule: account unknown, grouped by their description."""
    rules = _destination_rules(conn)
    for flow in bank:
        rule = None if flow.matched or not rules else _rule_for(flow, rules)
        if rule:
            flow.account_id, flow.group, flow.rule = rule[2], rule[3], rule[4]


def add_destination_rule(conn: sqlite3.Connection, account_id: int, pattern: str) -> None:
    pattern = (pattern or "").strip()
    if len(pattern) < 3:
        raise ValueError("Enter at least 3 characters of the bank's text (e.g. a reference number).")
    if conn.execute("SELECT 1 FROM accounts WHERE id = ? AND 'investment' IN (type, COALESCE(plaid_type, ''))",
                    (account_id,)).fetchone() is None:
        raise ValueError("Destination rules are for investment and retirement accounts.")
    with conn:
        conn.execute("INSERT INTO destination_rules (account_id, pattern) VALUES (?, ?)", (account_id, pattern[:80]))


def delete_destination_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM destination_rules WHERE id = ?", (rule_id,))


def destination_rules(conn: sqlite3.Connection, account_id: int, today: date | None = None) -> list[dict]:
    """The account's rules for its page, each with how many unpaired transfers of the last 24 months it covers (a
    transfer counts for the first rule it matches, as when applied)."""
    today = today or date.today()
    start, after = today - timedelta(days=731), today + timedelta(days=1)
    bank = _bank_flows(conn, start, after)
    _pair_up(bank, _investment_flows(conn, start, after))
    rules = _destination_rules(conn)
    counts: dict[int, int] = {}
    for flow in bank:
        rule = None if flow.matched or not rules else _rule_for(flow, rules)
        if rule:
            counts[rule[0]] = counts.get(rule[0], 0) + 1
    return [{"id": r["id"], "pattern": r["pattern"], "count": counts.get(r["id"], 0)}
            for r in conn.execute("SELECT id, pattern FROM destination_rules WHERE account_id = ? ORDER BY id",
                                  (account_id,))]


def _bank_transfer_candidates(conn: sqlite3.Connection, start: date, end: date) -> list[_Flow]:
    """Bank transactions that count as transfers (kind 'transfer': your category or Plaid's) WITHOUT Plaid's
    investment-transfer codes. They count only as the other half of an investment-account row (same amount, within
    MATCH_DAYS days); unpaired ones are ignored. Seen in Production 2026-10-11: brokerage withdrawals reached checking
    as "… PAYMENTS" filed by Plaid as INCOME_SALARY, which you had recategorized as Transfer in."""
    flows = []
    for r in conn.execute(
        """
        SELECT id, date, amount_cents FROM transactions_effective
        WHERE account_hidden = 0 AND account_type <> 'investment' AND kind = 'transfer'
          AND COALESCE(plaid_category_detailed, '') NOT IN (?, ?) AND date >= ? AND date < ?
          AND id NOT IN (SELECT transaction_id FROM purpose_links WHERE account_id IS NOT NULL)   -- money lent etc.
        """,
        (INTO_CODE, OUT_CODE, start.isoformat(), end.isoformat()),
    ):
        # Plaid sign on the bank account: positive = left it (into investments); negative = arrived (out of them).
        direction = "into" if r["amount_cents"] > 0 else "out"
        flows.append(_Flow(date.fromisoformat(r["date"]), abs(r["amount_cents"]), direction, "bank", "",
                           transaction_id=r["id"]))
    return flows


def _investment_flows(conn: sqlite3.Connection, start: date, end: date) -> list[_Flow]:
    from fintrack import visuals        # imported here: visuals imports this module

    flows = []
    for r in conn.execute(
        """
        SELECT t.id, t.date, t.name, t.subtype, t.amount_cents, t.account_id, a.subtype AS account_subtype,
               a.type_kind, a.type_label
        FROM investment_transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.is_hidden = 0 AND t.type = 'cash' AND t.subtype IN (?, ?, ?, ?) AND t.date >= ? AND t.date < ?
        """,
        (*INTO_SUBTYPES, *OUT_SUBTYPES, start.isoformat(), end.isoformat()),
    ):
        if SWEEP_WORDS.search(r["name"] or ""):
            continue                                    # internal cash sweep, not money in or out
        # The account decides, by the same rule as Portfolio's Retirement tile (visuals.is_retirement_account).
        group = "retirement" if visuals.is_retirement_account({"subtype": r["account_subtype"],
                                                               "type_kind": r["type_kind"], "type_label": r["type_label"]}) \
            else "investment"
        # Plaid's investment sign: negative = cash credited to the account (a contribution); positive = debited.
        if r["subtype"] in INTO_SUBTYPES:
            flows.append(_Flow(date.fromisoformat(r["date"]), -r["amount_cents"], "into", "investment", group,
                               account_id=r["account_id"], investment_id=r["id"]))
        else:
            flows.append(_Flow(date.fromisoformat(r["date"]), r["amount_cents"], "out", "investment", group,
                               account_id=r["account_id"], investment_id=r["id"]))
    return flows


def _pair_up(bank: list[_Flow], investment: list[_Flow]) -> None:
    """Mark bank rows that are the other half of an investment row: same direction, same amount, within MATCH_DAYS
    days; each investment row pairs once, nearest in time first."""
    for b in sorted(bank, key=lambda f: f.day):
        if b.cents <= 0:
            continue                                   # reversals don't pair
        best = None
        for i in investment:
            if i.matched or i.direction != b.direction or i.cents != b.cents:
                continue
            gap = abs((i.day - b.day).days)
            if gap <= MATCH_DAYS and (best is None or gap < best[0]):
                best = (gap, i)
        if best:
            best[1].matched = b.matched = True
            best[1].partner = "bank"
            b.paired_account, b.investment_id = best[1].account_id, best[1].investment_id


def _pair_internal(investment: list[_Flow]) -> None:
    """Moves between two of your investment accounts (e.g. brokerage -> traditional IRA, or a Roth conversion): a
    withdrawal from one account and a deposit of the same amount into another within MATCH_DAYS days, neither already
    paired with a bank transaction. Both rows still count (out of one kind of account, into the other), but neither is
    new money nor money through the bank."""
    outs = sorted((f for f in investment if not f.matched and f.direction == "out"), key=lambda f: f.day)
    for o in outs:
        best = None
        for i in investment:
            if i.matched or i.direction != "into" or i.cents != o.cents or i.account_id == o.account_id:
                continue
            gap = abs((i.day - o.day).days)
            if gap <= MATCH_DAYS and (best is None or gap < best[0]):
                best = (gap, i)
        if best:
            best[1].matched = o.matched = True
            best[1].partner = o.partner = "internal"


@dataclass(frozen=True)
class InvestmentLink:
    """A bank transfer and the investment / retirement account it went to or came from."""
    transaction_id: int             # the bank transaction
    account_id: int                 # the investment / retirement account
    how: str                        # "activity": paired with that account's own deposit / withdrawal; "rule": a
                                    # destination rule's text is in its bank text (no such row, e.g. before Plaid's history)
    investment_id: int | None       # "activity": the investment_transactions row it paired with
    rule: str = ""                  # "rule": the rule's text


def investment_links(conn: sqlite3.Connection) -> dict[int, InvestmentLink]:
    """{bank transaction id: InvestmentLink} for every bank transfer whose account is known, over all history: it
    paired with that account's own activity (Plaid's investment-transfer codes, or any other transfer of the same
    amount within MATCH_DAYS days), or, with Plaid's codes and no pair, a destination rule names the account. The same
    matching as `between` (Portfolio, Savings). For logos (transfer_accounts), and transfer matching: the "activity"
    ones are matched transfers (transfers.detect_transfers, user's request 2026-10-31); the "rule" ones aren't (no
    other half)."""
    lo, hi = date(1900, 1, 1), date(9999, 1, 1)
    bank = _bank_flows(conn, lo, hi)
    investment = _investment_flows(conn, lo, hi)
    _pair_up(bank, investment)
    candidates = _bank_transfer_candidates(conn, lo, hi)
    _pair_up(candidates, investment)
    _apply_destinations(conn, bank)
    links = {f.transaction_id: InvestmentLink(f.transaction_id, f.paired_account, "activity", f.investment_id)
             for f in bank + candidates if f.paired_account is not None}
    links.update({f.transaction_id: InvestmentLink(f.transaction_id, f.account_id, "rule", None, f.rule)
                  for f in bank if not f.matched and f.account_id is not None})
    return links


def transfer_accounts(conn: sqlite3.Connection) -> dict[int, int]:
    """{bank transaction id: the investment / retirement account it went to or came from} (investment_links). Used for
    transfer logos (logos.py)."""
    return {tid: link.account_id for tid, link in investment_links(conn).items()}


def monthly(conn: sqlite3.Connection, months: int = 12, today: date | None = None) -> list[MonthFlow]:
    """The last `months` months ending with the current one, oldest first; empty months are zeros."""
    today = today or date.today()
    current = f"{today:%Y-%m}"
    first = date.fromisoformat(month_bounds(shift_month(current, -(months - 1)))[0])
    last = date.fromisoformat(month_bounds(current)[1]) - timedelta(days=1)
    return between(conn, first, last)


def between(conn: sqlite3.Connection, start: date, end: date, by_day: bool = False,
            to_bank: bool = True) -> list[MonthFlow]:
    """Flows dated `start`..`end` (inclusive), one MonthFlow per calendar month the range touches, oldest first; an end
    month counts only its days inside the range (the Savings page's date range, user's request 2026-10-11).
    `by_day`: one MonthFlow per day instead (its `month` is the date; the Savings cards for This / Last month).
    `to_bank=False` (the Savings page, user's request 2026-10-12): money moved out of investment / retirement accounts
    INTO a bank account is left out entirely (not counted as money out of investments, nor as money into the bank), so
    savings reflect what was saved from income; Portfolio's transfer charts keep it."""
    if by_day:
        wanted = [date.fromordinal(n).isoformat() for n in range(start.toordinal(), end.toordinal() + 1)]
    else:
        wanted = [f"{start:%Y-%m}"]
        while wanted[-1] < f"{end:%Y-%m}":
            wanted.append(shift_month(wanted[-1], 1))
    key = (lambda d: d.isoformat()) if by_day else (lambda d: f"{d:%Y-%m}")
    margin = timedelta(days=MATCH_DAYS)                 # pairs can straddle the range's edges
    lo, hi = start - margin, end + timedelta(days=1) + margin
    bank = _bank_flows(conn, lo, hi)
    investment = _investment_flows(conn, lo, hi)
    _pair_up(bank, investment)                                           # Plaid's investment-transfer codes first
    candidates = _bank_transfer_candidates(conn, lo, hi)
    _pair_up(candidates, investment)                                     # then other transfers that match exactly
    _pair_internal(investment)                                           # then moves between your own accounts
    _apply_destinations(conn, bank)                                      # unpaired bank transfers: your rules

    flows = {m: MonthFlow(m) for m in wanted}
    for c in candidates:
        if c.matched and start <= c.day <= end:
            flows[key(c.day)].paired_transfers += c.cents if c.direction == "into" else -c.cents
    for flow in [f for f in bank if not f.matched] + investment:       # a pair counts once, on the investment side
        if not to_bank and flow.direction == "out" and (flow.source == "bank" or flow.partner == "bank"):
            continue                                                     # investments -> bank: left out (Savings)
        if start <= flow.day <= end:
            month = flows[key(flow.day)]
            sign = 1 if flow.direction == "into" else -1
            (month.into if flow.direction == "into" else month.out)[flow.group] += flow.cents
            split = month.by_account[flow.group]
            split[flow.account_id] = split.get(flow.account_id, 0) + sign * flow.cents
            if flow.source == "bank" or flow.partner == "bank":
                month.bank_net += sign * flow.cents
            elif not flow.partner and flow.direction == "into":
                month.outside_into += flow.cents
    return list(flows.values())
