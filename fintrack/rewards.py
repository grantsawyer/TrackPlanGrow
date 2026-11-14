"""Credit card rewards, estimated from reward rates you enter (user's request 2026-10-25; Plaid doesn't report rewards).

A card you've set up (`card_rewards`, migration 0039) earns points, miles or cash back at a base rate, with its own rates
(`card_reward_rates`, 0040 / 0041) for a category, a merchant, or a category at one merchant (user's request 2026-10-26: airfare
from United can earn more than other airfare and other United spending). The most specific rate wins (`matching_rate`):

    category at the merchant  >  its parent category at the merchant  >  the merchant  >  the category
    >  its parent category  >  the card's base rate

A merchant is text you type ("United"), matched against each transaction's description (transactions_effective
.description: yours > merchant > bank text), ignoring case (user's request 2026-10-26, migration 0041). When several
texts match at the same step, the longest wins ("Acme Air" over "Acme"). For each line on a card:

    rewards = spending (cents) x rate (hundredths) / 10,000, rounded half-up

which is points (or miles) for 3x on dollars, and cents of cash back for 3% on cents: one formula for both. Spending is
counted as on the Credit cards page (spending.card_spending: refunds reduce it, so they take rewards back). Rounded per
line, so a card's rows add up to its total. An estimate: caps, rotating categories, bonuses and how your bank codes
each merchant aren't known.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

UNITS = {"points": "Points", "miles": "Miles", "cash": "Cash back"}
MAX_RATE_X100 = 10000           # 100x / 100%: anything above is a typo
MAX_TEXT = 100                  # merchant text
MAX_BONUS = 100_000_000         # a bonus: points / miles, or cents of cash back ($1M)
# Spending that never earns rewards, ahead of every rate (user's request 2026-10-26: "credit card membership fees should
# be excluded"): Plaid's Bank fees category (yours is named "Credit Card Membership Fees") and its subcategories; it also
# holds ATM, foreign transaction, late and interest fees, which don't earn either. Still spending; listed at 0x.
NO_REWARDS = frozenset({"BANK_FEES"})


@dataclass
class RewardRate:
    """One of a card's rates: for a category, merchant text, or the category at that merchant."""
    id: int
    category: str | None
    merchant: str | None            # text a description must contain (any case)
    rate_x100: int


@dataclass
class CardRewards:
    account_id: int
    unit: str
    base_rate_x100: int
    rates: list[RewardRate] = field(default_factory=list)  # highest rate first


@dataclass
class RewardLine:
    """A row of a card's table: a category's spending at one rate (at one merchant when a merchant rate applies)."""
    category: str
    label: str
    merchant: str | None            # the merchant rate's text
    amount_cents: int = 0
    count: int = 0
    earned: int = 0
    # the Transactions search that lists exactly this line's transactions with ?category=…&only=1 (user's request
    # 2026-10-26): its merchant text, less the card's other merchant texts that could take a transaction in its category
    search: str = ""


@dataclass
class RateGroup:
    """A card's lines that earn the same rate (the Credit cards table groups by rate, user's request 2026-10-25)."""
    rate_x100: int
    spending_cents: int = 0
    earned: int = 0
    lines: list[RewardLine] = field(default_factory=list)  # largest first
    # what earns this rate, shown after it ("Travel · Acme", "Everything else" for the base rate), user's request
    # 2026-10-26: a rate nothing matched is still listed by its heading, with no lines
    definitions: list[str] = field(default_factory=list)


@dataclass
class Bonus:
    """A reward bonus you added to one transaction (user's request 2026-10-26: one-offs, not a rate), or bonus points
    of their own (`transaction_id` None; migration 0061)."""
    transaction_id: int | None
    date: str
    description: str
    amount: int                     # in the card's unit: points / miles, or cents of cash back


@dataclass
class Estimate:
    unit: str
    total: int                                               # points / miles, or cents of cash back; bonuses included
    by_category: dict[str, int] = field(default_factory=dict)
    groups: list[RateGroup] = field(default_factory=list)    # highest rate first
    bonuses: list[Bonus] = field(default_factory=list)       # newest first
    bonus_total: int = 0


def parse_rate(text: str) -> int:
    """'3', '1.5', '2%', '3x' -> hundredths (300, 150, 200, 300). Raises ValueError for anything else or out of range."""
    cleaned = (text or "").strip().lower().rstrip("x%").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("Enter a rate like 3 or 1.5.") from None
    x100 = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not 0 <= x100 <= MAX_RATE_X100:
        raise ValueError("A rate must be between 0 and 100.")
    return x100


def rate_label(unit: str, x100: int) -> str:
    """150 -> '1.5x' (points / miles) or '1.5%' (cash back)."""
    number = f"{Decimal(x100) / 100:.2f}".rstrip("0").rstrip(".")        # "10.00" -> "10", "1.50" -> "1.5"
    return f"{number}%" if unit == "cash" else f"{number}x"


def _check_card(conn: sqlite3.Connection, account_id: int) -> None:
    if not conn.execute("SELECT 1 FROM accounts WHERE id = ? AND type = 'credit'", (account_id,)).fetchone():
        raise ValueError("That isn't a credit card account.")


def set_up(conn: sqlite3.Connection, account_id: int, unit: str, base_rate_x100: int) -> None:
    """Start estimating a card's rewards, or change what it earns and its base rate."""
    _check_card(conn, account_id)
    if unit not in UNITS:
        raise ValueError("Pick points, miles or cash back.")
    with conn:
        conn.execute(
            """INSERT INTO card_rewards (account_id, unit, base_rate_x100) VALUES (?, ?, ?)
               ON CONFLICT (account_id) DO UPDATE SET unit = excluded.unit, base_rate_x100 = excluded.base_rate_x100""",
            (account_id, unit, base_rate_x100),
        )


def remove(conn: sqlite3.Connection, account_id: int) -> None:
    """Stop estimating a card's rewards (its rates go too)."""
    with conn:
        conn.execute("DELETE FROM card_rewards WHERE account_id = ?", (account_id,))


def set_rate(conn: sqlite3.Connection, account_id: int, category_key: str | None, merchant_text: str | None,
             rate_x100: int) -> None:
    """A card's rate for a category, merchant text, or both (replaces the rate it had for the same pair; the text's
    case doesn't matter)."""
    if not conn.execute("SELECT 1 FROM card_rewards WHERE account_id = ?", (account_id,)).fetchone():
        raise ValueError("Set up the card's rewards first.")
    category_key, merchant_text = category_key or None, " ".join((merchant_text or "").split()) or None
    if category_key is None and merchant_text is None:
        raise ValueError("Pick a category, type a merchant, or both.")
    if category_key and not conn.execute("SELECT 1 FROM categories WHERE key = ?", (category_key,)).fetchone():
        raise ValueError("Pick a category.")
    if merchant_text and len(merchant_text) > MAX_TEXT:
        raise ValueError(f"Keep the merchant to {MAX_TEXT} characters.")
    with conn:
        conn.execute(
            """DELETE FROM card_reward_rates WHERE account_id = ?
               AND COALESCE(category_key, '') = COALESCE(?, '') AND LOWER(COALESCE(merchant_text, '')) = LOWER(COALESCE(?, ''))""",
            (account_id, category_key, merchant_text),
        )
        conn.execute(
            "INSERT INTO card_reward_rates (account_id, category_key, merchant_text, rate_x100) VALUES (?, ?, ?, ?)",
            (account_id, category_key, merchant_text, rate_x100),
        )


def delete_rate(conn: sqlite3.Connection, account_id: int, rate_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM card_reward_rates WHERE account_id = ? AND id = ?", (account_id, rate_id))


def all_rewards(conn: sqlite3.Connection) -> dict[int, CardRewards]:
    """{account_id: CardRewards} for every card you've set up."""
    cards = {r["account_id"]: CardRewards(r["account_id"], r["unit"], r["base_rate_x100"])
             for r in conn.execute("SELECT account_id, unit, base_rate_x100 FROM card_rewards")}
    for r in conn.execute(
        "SELECT id, account_id, category_key, merchant_text, rate_x100 FROM card_reward_rates ORDER BY rate_x100 DESC, id"
    ):
        cards[r["account_id"]].rates.append(RewardRate(r["id"], r["category_key"], r["merchant_text"], r["rate_x100"]))
    return cards


def matching_rate(card: CardRewards, category: str, description: str | None,
                  parents: dict[str, str | None]) -> RewardRate | None:
    """The card's most specific rate for a transaction in `category` whose description is `description`, or None for
    the base rate. Steps, most specific first: the category at a merchant, its parent category at a merchant, a
    merchant, the category, its parent. Within a step the longest matching merchant text wins."""
    text = (description or "").casefold()
    parent = parents.get(category)

    def at(cat: str | None, with_merchant: bool) -> RewardRate | None:
        if cat is None and not with_merchant:
            return None
        hits = [r for r in card.rates if r.category == cat and (
            r.merchant is not None and r.merchant.casefold() in text if with_merchant else r.merchant is None)]
        return max(hits, key=lambda r: (len(r.merchant or ""), -r.id), default=None)

    for cat, with_merchant in ((category, True), (parent, True), (None, True), (category, False), (parent, False)):
        if hit := at(cat, with_merchant):
            return hit
    return None


def rate_for(card: CardRewards, category: str, parents: dict[str, str | None], description: str | None = None) -> int:
    rate = matching_rate(card, category, description, parents)
    return rate.rate_x100 if rate else card.base_rate_x100


def _earned(cents: int, rate_x100: int) -> int:
    return int((Decimal(cents) * rate_x100 / 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _phrase(text: str) -> str:
    """A merchant text as one search term, always quoted ("acme", "fake rail"): a quoted phrase searches only the
    description, as reward rates match (a plain word would also match an account or category named "Acme…")."""
    return '"' + text.replace('"', "") + '"'


def _search_for(card: CardRewards, category: str, merchant: str | None, parents: dict[str, str | None]) -> str:
    """A line's Transactions search: its own merchant text, and "-text" for every other merchant rate on the card that
    could apply in this category (its category, its parent, or any category), so the link lists the line's
    transactions only. Not exact when one description holds two merchant texts."""
    scope = (category, parents.get(category), None)
    others = dict.fromkeys(r.merchant for r in card.rates if r.merchant and r.category in scope
                           and r.merchant.casefold() != (merchant or "").casefold())
    return " ".join(([_phrase(merchant)] if merchant else []) + ["-" + _phrase(t) for t in others])


def _no_rewards_key(category: str, parents: dict[str, str | None]) -> str | None:
    """The NO_REWARDS category this one is (or is under), else None."""
    for key in (category, parents.get(category)):
        if key in NO_REWARDS:
            return key
    return None


def estimates(conn: sqlite3.Connection, cards, start: str, end: str) -> dict[int, Estimate]:
    """{account_id: Estimate} for the cards (spending.CardSpending) you've set up, from `start` to `end` (inclusive);
    others are left out. Transactions are read with card_spending's conditions, so the lines add up to the card's
    total. Every rate has a group even when nothing matched it (user's request 2026-10-26), the base rate's included,
    and each group names the rates that earn it (`RateGroup.definitions`). NO_REWARDS spending (card fees) earns 0x
    whatever the rates say. Bonuses on transactions in the range add to the total (`Estimate.bonuses`)."""
    rewards = all_rewards(conn)
    parents, labels = {}, {}
    for r in conn.execute("SELECT key, parent_key, label FROM categories"):
        parents[r["key"]], labels[r["key"]] = r["parent_key"], r["label"]
    wanted = {c.account_id for c in cards} & rewards.keys()
    lines: dict[int, dict[tuple, tuple[int, RewardLine]]] = {a: {} for a in wanted}   # (rate, category, merchant)
    fees: dict[int, dict[str, None]] = {a: {} for a in wanted}                        # NO_REWARDS keys seen, in order
    for r in conn.execute(
        """SELECT id, account_id, category, category_label, description, amount_cents
           FROM transactions_effective
           WHERE account_type = 'credit' AND account_hidden = 0 AND kind = 'expense' AND date >= ? AND date <= ?""",
        (start, end),
    ):
        if r["account_id"] not in wanted:
            continue
        card = rewards[r["account_id"]]
        fee = _no_rewards_key(r["category"], parents)
        rate = None if fee else matching_rate(card, r["category"], r["description"], parents)
        x100 = 0 if fee else rate.rate_x100 if rate else card.base_rate_x100
        merchant = rate.merchant if rate else None                  # a merchant rate gets its own row
        if fee:
            fees[r["account_id"]][fee] = None
        key = (x100, r["category"], merchant)
        if key not in lines[r["account_id"]]:
            lines[r["account_id"]][key] = (x100, RewardLine(
                r["category"], r["category_label"], merchant,
                search="" if fee else _search_for(card, r["category"], merchant, parents)))
        line = lines[r["account_id"]][key][1]
        line.amount_cents += r["amount_cents"]
        line.count += 1
    bonuses = card_bonuses(conn, wanted, start, end)
    out = {}
    for account_id in wanted:
        card = rewards[account_id]
        groups: dict[int, RateGroup] = {card.base_rate_x100: RateGroup(card.base_rate_x100)}
        by_category: dict[str, int] = {}
        for x100, line in sorted(lines[account_id].values(), key=lambda item: item[1].amount_cents, reverse=True):
            line.earned = _earned(line.amount_cents, x100)
            group = groups.setdefault(x100, RateGroup(x100))
            group.spending_cents += line.amount_cents
            group.earned += line.earned
            group.lines.append(line)
            by_category[line.category] = by_category.get(line.category, 0) + line.earned
        for rate in card.rates:                                     # highest first, then oldest
            label = labels.get(rate.category, rate.category) if rate.category else "Any category"
            groups.setdefault(rate.rate_x100, RateGroup(rate.rate_x100)).definitions.append(
                f"{label} · {rate.merchant}" if rate.merchant else label)
        if fees[account_id]:                                        # only when the card has some
            groups.setdefault(0, RateGroup(0)).definitions.extend(labels.get(k, k) for k in fees[account_id])
        groups[card.base_rate_x100].definitions.append(
            "everything else" if groups[card.base_rate_x100].definitions else "Everything else")
        own = bonuses.get(account_id, [])
        bonus_total = sum(b.amount for b in own)
        out[account_id] = Estimate(card.unit, sum(by_category.values()) + bonus_total, by_category,
                                   sorted(groups.values(), key=lambda g: g.rate_x100, reverse=True), own, bonus_total)
    return out


# ---------------------------------------------------------------- bonuses on single transactions (migration 0042)

def _parse_amount(unit: str, text: str, what: str) -> int:
    """'5,000' -> 5000 points / miles; '$25' or '25.00' -> 2500 cents of cash back. ValueError for anything else."""
    cleaned = (text or "").strip().replace(",", "").lstrip("+").lstrip("$").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Enter a {what} like 5,000" + (" or 25.00." if unit == "cash" else ".")) from None
    if unit == "cash":
        return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if value != value.to_integral_value():
        raise ValueError(f"Enter whole {unit}.")
    return int(value)


def parse_bonus(unit: str, text: str) -> int:
    """A bonus in its unit (points / miles, or cents of cash back); more than 0."""
    amount = _parse_amount(unit, text, "bonus")
    if not 0 < amount <= MAX_BONUS:
        raise ValueError("A bonus must be more than 0.")
    return amount


def unit_for_transaction(conn: sqlite3.Connection, transaction_id: int) -> str | None:
    """What a transaction's card earns ("points", "miles", "cash"), or None if it isn't on a card you've set up."""
    row = conn.execute(
        """SELECT cr.unit FROM transactions t JOIN card_rewards cr ON cr.account_id = t.account_id
           WHERE t.id = ?""", (transaction_id,)).fetchone()
    return row["unit"] if row else None


def set_bonus(conn: sqlite3.Connection, transaction_id: int, amount: int) -> None:
    """Add a bonus to a transaction on a card you've set up, or replace the one it had."""
    if unit_for_transaction(conn, transaction_id) is None:
        raise ValueError("Set up the card's rewards first.")
    with conn:
        conn.execute(
            """INSERT INTO transaction_reward_bonuses (transaction_id, amount) VALUES (?, ?)
               ON CONFLICT (transaction_id) DO UPDATE SET amount = excluded.amount""", (transaction_id, amount))


def delete_bonus(conn: sqlite3.Connection, transaction_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM transaction_reward_bonuses WHERE transaction_id = ?", (transaction_id,))


def bonus_of(conn: sqlite3.Connection, transaction_id: int) -> int | None:
    row = conn.execute("SELECT amount FROM transaction_reward_bonuses WHERE transaction_id = ?", (transaction_id,)).fetchone()
    return row["amount"] if row else None


def bonus_label(unit: str, amount: int) -> str:
    """5000 -> '5,000 points'; 2500 (cash) -> '$25.00 cash back'."""
    if unit == "cash":
        return f"${amount / 100:,.2f} cash back"
    return f"{amount:,} {unit}"


def bonus_labels(conn: sqlite3.Connection, transaction_ids) -> dict[int, str]:
    """{transaction id: '5,000 points'} for those of `transaction_ids` with a bonus on a card you've set up (the
    Transactions rows' gift icon)."""
    ids = [int(i) for i in transaction_ids]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: bonus_label(r["unit"], r["amount"]) for r in conn.execute(
        f"""SELECT t.id, b.amount, cr.unit FROM transaction_reward_bonuses b
            JOIN transactions t ON t.id = b.transaction_id JOIN card_rewards cr ON cr.account_id = t.account_id
            WHERE t.id IN ({marks})""", ids)}


def card_bonuses(conn: sqlite3.Connection, account_ids, start: str, end: str) -> dict[int, list[Bonus]]:
    """{account_id: [Bonus, newest first]} for bonuses on transactions dated `start`..`end` on those cards."""
    ids = list(account_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[int, list[Bonus]] = {}
    for r in conn.execute(
        f"""SELECT t.account_id, t.id, t.date, t.description, b.amount FROM transaction_reward_bonuses b
            JOIN transactions_effective t ON t.id = b.transaction_id
            WHERE t.account_id IN ({marks}) AND t.account_hidden = 0 AND t.date >= ? AND t.date <= ?
            ORDER BY t.date DESC, t.id DESC""", (*ids, start, end)):
        out.setdefault(r["account_id"], []).append(Bonus(r["id"], r["date"], r["description"], r["amount"]))
    # ...and bonus points of their own (a sign-up bonus paid in points; user's request 2026-11-03), in the card's unit
    units = {r[0]: r[1] for r in conn.execute(f"SELECT account_id, unit FROM card_rewards WHERE account_id IN ({marks})", ids)}
    for b in bonus_points(conn, start=start, end=end):
        if b.account_id in units and b.unit == units[b.account_id]:
            out.setdefault(b.account_id, []).append(Bonus(None, b.date, b.note or "Bonus points", b.amount))
    for found in out.values():
        found.sort(key=lambda b: (b.date, b.transaction_id or 0), reverse=True)
    return out


# ---------------------------------------------------------------- rewards balances (account page)
# A card's balance as of a date, as its issuer shows it (user's request 2026-10-26; migration 0044). One per card per
# date; each keeps the unit it was entered in.

MAX_BALANCE = 10_000_000_000    # points / miles, or cents of cash back ($100M)


@dataclass
class Balance:
    id: int
    as_of: str
    unit: str
    amount: int


def parse_balance(unit: str, text: str) -> int:
    """A balance in its unit (points / miles, or cents of cash back); 0 or more."""
    amount = _parse_amount(unit, text, "balance")
    if not 0 <= amount <= MAX_BALANCE:
        raise ValueError("A balance can't be negative.")
    return amount


def balances(conn: sqlite3.Connection, account_id: int) -> list[Balance]:
    """A card's balances, newest first."""
    return [Balance(r["id"], r["as_of"], r["unit"], r["amount"]) for r in conn.execute(
        "SELECT id, as_of, unit, amount FROM reward_balances WHERE account_id = ? ORDER BY as_of DESC", (account_id,))]


def balance_unit(conn: sqlite3.Connection, account_id: int) -> str | None:
    """The unit a new balance is entered in: the card's rewards unit (Credit cards), else None (you pick)."""
    row = conn.execute("SELECT unit FROM card_rewards WHERE account_id = ?", (account_id,)).fetchone()
    return row["unit"] if row else None


def _check_date(as_of: str, today: date, what: str = "balance") -> str:
    try:
        day = date.fromisoformat((as_of or "").strip())
    except ValueError:
        raise ValueError(f"Enter the date of the {what}.") from None
    if day > today:
        raise ValueError(f"A {what} can't be dated in the future.")
    return day.isoformat()


def set_balance(conn: sqlite3.Connection, account_id: int, as_of: str, unit: str, amount: int,
                balance_id: int | None = None, today: date | None = None) -> None:
    """Add a balance (one already on that date is replaced), or change one (`balance_id`): its date and amount; a balance
    moved onto another one's date replaces it."""
    _check_card(conn, account_id)
    if unit not in UNITS:
        raise ValueError("Pick points, miles or cash back.")
    as_of = _check_date(as_of, today or date.today())
    with conn:
        if balance_id is not None:
            if conn.execute("SELECT 1 FROM reward_balances WHERE id = ? AND account_id = ?",
                            (balance_id, account_id)).fetchone() is None:
                raise ValueError("That balance is gone.")
            conn.execute("DELETE FROM reward_balances WHERE account_id = ? AND as_of = ? AND id <> ?",
                         (account_id, as_of, balance_id))
            conn.execute("UPDATE reward_balances SET as_of = ?, unit = ?, amount = ? WHERE id = ?",
                         (as_of, unit, amount, balance_id))
        else:
            conn.execute(
                """INSERT INTO reward_balances (account_id, as_of, unit, amount) VALUES (?, ?, ?, ?)
                   ON CONFLICT (account_id, as_of) DO UPDATE SET unit = excluded.unit, amount = excluded.amount""",
                (account_id, as_of, unit, amount))


def delete_balance(conn: sqlite3.Connection, account_id: int, balance_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM reward_balances WHERE id = ? AND account_id = ?", (balance_id, account_id))


# ---------------------------------------------------------------- rewards per transaction (a card's page)
# Each transaction's rewards on its own (user's request 2026-10-26): spending earns at its rate (the same matching as the
# estimates: card fees 0x, a refund takes rewards back), plus a bonus you added to it. Rounded per transaction, as
# issuers do, so the Credit cards estimate (rounded per category) can differ from the column's sum by a little.

def transaction_rewards(conn: sqlite3.Connection, account_id: int, ids=None, after: str | None = None) -> dict[int, int]:
    """{transaction id: rewards} on a card you've set up, for the transactions `ids` (all when None) dated after
    `after` (all when None). Only transactions that earn or carry a bonus are in it; {} for a card not set up."""
    card = all_rewards(conn).get(account_id)
    if card is None:
        return {}
    where, params = ["te.account_id = ?"], [account_id]
    if ids is not None:
        ids = [int(i) for i in ids]
        if not ids:
            return {}
        where.append(f"te.id IN ({','.join('?' * len(ids))})")
        params += ids
    if after:
        where.append("te.date > ?")
        params.append(after)
    parents = {r["key"]: r["parent_key"] for r in conn.execute("SELECT key, parent_key FROM categories")}
    out = {}
    for r in conn.execute(
        f"""SELECT te.id, te.kind, te.category, te.description, te.amount_cents, b.amount AS bonus
            FROM transactions_effective te LEFT JOIN transaction_reward_bonuses b ON b.transaction_id = te.id
            WHERE {' AND '.join(where)}""", params):
        earned = None
        if r["kind"] == "expense":
            x100 = 0 if _no_rewards_key(r["category"], parents) else rate_for(card, r["category"], parents,
                                                                               r["description"])
            earned = _earned(r["amount_cents"], x100)
        if r["bonus"]:
            earned = (earned or 0) + r["bonus"]
        if earned is not None:
            out[r["id"]] = earned
    return out


@dataclass
class CurrentBalance:
    latest: Balance           # the newest balance you entered
    earned_since: int         # rewards on the card's transactions dated after it, and bonus points (migration 0061)
    total: int
    redeemed_since: int = 0   # rewards you redeemed after it (migration 0061)


def current_balance(conn: sqlite3.Connection, account_id: int) -> CurrentBalance | None:
    """The latest balance you entered, plus the rewards of every transaction after its date and the bonus points
    since, less what you redeemed since (user's request 2026-11-03), in the card's unit. None without a balance, for a
    card not set up, or when the latest balance is in another unit than the card earns."""
    entered = balances(conn, account_id)
    card = all_rewards(conn).get(account_id)
    if not entered or card is None or entered[0].unit != card.unit:
        return None
    after = entered[0].as_of
    since = sum(transaction_rewards(conn, account_id, after=after).values())
    since += sum(b.amount for b in bonus_points(conn, account_id) if b.date > after and b.unit == card.unit)
    redeemed = conn.execute(
        """SELECT COALESCE(SUM(u.amount), 0) FROM transaction_rewards_used u JOIN transactions t ON t.id = u.transaction_id
           WHERE t.account_id = ? AND t.date > ? AND u.unit = ?""", (account_id, after, card.unit)).fetchone()[0]
    return CurrentBalance(entered[0], since, entered[0].amount + since - redeemed, redeemed)


# ---------------------------------------------------------------- rewards used and bonus points (migrations 0061, 0062)
# User's requests 2026-11-03 / 04. Rewards spent are transactions, so a trip costs the same whether cash or points paid:
#   a purchase paid with points  two manual transactions on its card (manual_transactions.add, ids 'manual-…'): the cost
#                                and a rebate of the same amount in the same category, carrying the points
#   a statement credit           a real credit on the card that points paid: it carries the points (its edit panel)
# Their value is the rebate's or credit's amount; value per point = value / points. Nothing about earning is special: a
# pair is ordinary spending in its category (its cost and rebate earn and take back the same), and points turned into
# cash are filed as rewards income, which earns nothing anyway (user's request 2026-11-04). Card credits (a travel
# credit against a purchase) aren't these: they offset the expense and its rewards, as refunds do. Bonus points (a sign-up
# bonus paid in points) only add to the balance; a cash sign-up bonus is income (it never came off the balance).


@dataclass
class BonusPoints:
    id: int
    account_id: int
    date: str
    amount: int
    unit: str
    note: str


def rewards_used(conn: sqlite3.Connection, transaction_ids) -> dict[int, int]:
    """{transaction id: rewards it used} for those of `transaction_ids` that used some."""
    ids = [int(i) for i in transaction_ids]
    if not ids:
        return {}
    return dict(conn.execute(f"SELECT transaction_id, amount FROM transaction_rewards_used WHERE transaction_id IN "
                             f"({','.join('?' * len(ids))})", ids).fetchall())


def parse_rewards_used(unit: str, text: str) -> int:
    """Rewards used, in the card's unit (points / miles, or cents of cash back); more than 0."""
    amount = _parse_amount(unit, text, "number of " + (unit if unit != "cash" else "dollars of cash back"))
    if not 0 < amount <= MAX_BALANCE:
        raise ValueError("Enter the rewards used.")
    return amount


def set_rewards_used(conn: sqlite3.Connection, transaction_id: int, amount: int, charge_id: int | None = None) -> None:
    """Mark money in on a card you've set up as paid by `amount` of its rewards (a statement credit, or a manual cost's
    rebate), or change how much."""
    unit = unit_for_transaction(conn, transaction_id)
    if unit is None:
        raise ValueError("Set up the card's rewards first (Credit cards → Estimate rewards).")
    row = conn.execute("SELECT amount_cents FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if row["amount_cents"] >= 0:
        raise ValueError("Only money in (a credit) can be paid with rewards.")
    with conn:
        conn.execute("""INSERT INTO transaction_rewards_used (transaction_id, amount, unit, charge_id) VALUES (?, ?, ?, ?)
                        ON CONFLICT (transaction_id) DO UPDATE SET amount = excluded.amount, unit = excluded.unit,
                            charge_id = COALESCE(excluded.charge_id, charge_id)""",
                     (transaction_id, amount, unit, charge_id))


def delete_rewards_used(conn: sqlite3.Connection, transaction_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM transaction_rewards_used WHERE transaction_id = ?", (transaction_id,))


def bonus_points(conn: sqlite3.Connection, account_id: int | None = None, start: str | None = None,
                 end: str | None = None) -> list[BonusPoints]:
    """Bonus points on one card (all when None) dated `start`..`end`, newest first."""
    where, params = ["1"], []
    for clause, value in (("account_id = ?", account_id), ("date >= ?", start), ("date <= ?", end)):
        if value is not None:
            where.append(clause)
            params.append(value)
    return [BonusPoints(r["id"], r["account_id"], r["date"], r["amount"], r["unit"], r["note"]) for r in conn.execute(
        f"SELECT * FROM reward_bonus_points WHERE {' AND '.join(where)} ORDER BY date DESC, id DESC", params)]


def _card_unit(conn: sqlite3.Connection, account_id: int) -> str:
    _check_card(conn, account_id)
    unit = balance_unit(conn, account_id)
    if unit is None:
        raise ValueError("Set up the card's rewards first (Credit cards → Estimate rewards).")
    return unit


def save_bonus_points(conn: sqlite3.Connection, account_id: int, bonus_id: int | None, day: str, amount_text: str,
                      note: str = "", today: date | None = None) -> None:
    """Add bonus points (in the card's unit) or change them (`bonus_id`)."""
    unit = _card_unit(conn, account_id)
    amount = parse_bonus(unit, amount_text)
    day = _check_date(day, today or date.today(), "bonus")
    note = " ".join((note or "").split())[:120]
    with conn:
        if bonus_id is None:
            conn.execute("INSERT INTO reward_bonus_points (account_id, date, amount, unit, note) VALUES (?, ?, ?, ?, ?)",
                         (account_id, day, amount, unit, note))
        elif conn.execute("UPDATE reward_bonus_points SET date = ?, amount = ?, unit = ?, note = ? WHERE id = ? AND account_id = ?",
                          (day, amount, unit, note, bonus_id, account_id)).rowcount == 0:
            raise ValueError("That bonus is gone.")


def delete_bonus_points(conn: sqlite3.Connection, account_id: int, bonus_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM reward_bonus_points WHERE id = ? AND account_id = ?", (bonus_id, account_id))


@dataclass
class RedeemedSummary:
    """A card's rewards used in a date range (the Credit cards page)."""
    unit: str
    amount: int = 0                 # rewards used
    value_cents: int = 0            # what they paid: the rebates' and credits' amounts
    count: int = 0
    cash_rewards_cents: int = 0     # money in filed as income on the card that rewards didn't pay (a cash sign-up bonus)

    @property
    def cents_per_point(self) -> float | None:
        return None if self.unit == "cash" or not self.amount else self.value_cents / self.amount

    def share_of(self, spending_cents: int) -> float | None:
        """Rewards' share of the card's cost: value / (spending + value). Spending is net of the rebates and credits,
        so adding the value back gives what everything cost."""
        cost = spending_cents + self.value_cents
        return self.value_cents / cost if self.count and cost > 0 else None


def redeemed_summaries(conn: sqlite3.Connection, account_ids, start: str, end: str) -> dict[int, RedeemedSummary]:
    """{card: RedeemedSummary} for those of `account_ids` that used rewards or got cash rewards from `start` to `end`."""
    ids = set(account_ids)
    out: dict[int, RedeemedSummary] = {}
    for r in conn.execute(
            """SELECT t.account_id, u.amount, u.unit, -t.amount_cents AS value FROM transaction_rewards_used u
               JOIN transactions_effective t ON t.id = u.transaction_id
               WHERE t.account_hidden = 0 AND t.date >= ? AND t.date <= ?""", (start, end)):
        if r["account_id"] not in ids:
            continue
        s = out.setdefault(r["account_id"], RedeemedSummary(r["unit"]))
        if r["unit"] == s.unit:
            s.amount += r["amount"]
        s.value_cents += r["value"]
        s.count += 1
    for r in conn.execute(
            """SELECT account_id, -SUM(amount_cents) FROM transactions_effective
               WHERE account_type = 'credit' AND account_hidden = 0 AND kind = 'income' AND date >= ? AND date <= ?
                 AND id NOT IN (SELECT transaction_id FROM transaction_rewards_used)
               GROUP BY account_id""", (start, end)):
        if r[0] in ids and r[1]:
            out.setdefault(r[0], RedeemedSummary(balance_unit(conn, r[0]) or "points")).cash_rewards_cents = r[1]
    return out
