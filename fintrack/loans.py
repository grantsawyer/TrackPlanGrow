"""Loan terms (interest rate + monthly payment) for payoff projections.

Plaid's Liabilities product returns terms for credit cards, mortgages, and
student loans only. Auto loans, HELOCs, and manual liabilities get terms only if
you enter them. Rows you enter (source 'you') are never overwritten by a sync.

Credit cards from Plaid: the purchase APR and minimum payment are stored for
reference, but the projection holds card balances constant (it assumes cards are
paid in full each month). Enter terms yourself for a card you carry a balance on.
"""

from __future__ import annotations

import sqlite3

from fintrack import cards
from fintrack.money import parse_amount, to_cents


class LoanError(ValueError):
    pass


def _upsert_plaid(conn: sqlite3.Connection, account_id: int, apr, payment, detail: str) -> None:
    """Store Plaid's terms unless you've entered your own for this account."""
    payment_cents = to_cents(payment)
    # Plaid can report a negative minimum payment (seen in Production on a new
    # credit card with a $0 statement). It isn't a real payment, so
    # store it as unknown instead of failing the whole sync on the CHECK.
    if payment_cents is not None and payment_cents < 0:
        payment_cents = None
    conn.execute(
        """
        INSERT INTO liability_terms (account_id, apr_percent, monthly_payment_cents, source, detail)
        VALUES (?, ?, ?, 'plaid', ?)
        ON CONFLICT (account_id) DO UPDATE SET
            apr_percent = excluded.apr_percent,
            monthly_payment_cents = excluded.monthly_payment_cents,
            detail = excluded.detail,
            updated_at = CURRENT_TIMESTAMP
        WHERE liability_terms.source = 'plaid'
        """,
        (account_id, apr, payment_cents, detail),
    )


def store_plaid_liabilities(conn: sqlite3.Connection, item_id: int, data: dict) -> int:
    """Save terms from /liabilities/get for this Item's accounts. Returns rows touched."""
    ids = {
        r["plaid_account_id"]: r["id"]
        for r in conn.execute("SELECT id, plaid_account_id FROM accounts WHERE item_id = ?", (item_id,))
    }
    count = 0
    with conn:
        for card in data.get("credit", []):
            if card["account_id"] not in ids:
                continue
            aprs = {a.get("apr_type"): a.get("apr_percentage") for a in card.get("aprs") or []}
            apr = aprs.get("purchase_apr", next(iter(aprs.values()), None))
            _upsert_plaid(conn, ids[card["account_id"]], apr, card.get("minimum_payment_amount"),
                          "credit card (minimum payment)")
            # Statement balance + due date for the cash-flow forecast (cards.py).
            cards.store_statement(conn, ids[card["account_id"]], card)
            count += 1
        for mortgage in data.get("mortgage", []):
            if mortgage["account_id"] not in ids:
                continue
            rate = (mortgage.get("interest_rate") or {}).get("percentage")
            maturity = mortgage.get("maturity_date")
            _upsert_plaid(conn, ids[mortgage["account_id"]], rate, mortgage.get("next_monthly_payment"),
                          f"mortgage{f', matures {maturity}' if maturity else ''}")
            count += 1
        for loan in data.get("student", []):
            if loan["account_id"] not in ids:
                continue
            payoff = loan.get("expected_payoff_date")
            _upsert_plaid(conn, ids[loan["account_id"]], loan.get("interest_rate_percentage"),
                          loan.get("minimum_payment_amount"),
                          f"student loan (minimum payment){f', expected payoff {payoff}' if payoff else ''}")
            count += 1
    return count


def set_terms(conn: sqlite3.Connection, account_id: int, apr_text: str, payment_text: str) -> None:
    """Enter your own terms (kept across syncs). Both blank = clear your entry."""
    account = conn.execute(
        "SELECT * FROM accounts WHERE id = ? AND classification = 'liability'", (account_id,)
    ).fetchone()
    if account is None:
        raise LoanError("That isn't a liability account.")
    apr_text, payment_text = (apr_text or "").strip().rstrip("%"), (payment_text or "").strip()
    if not apr_text and not payment_text:
        conn.execute("DELETE FROM liability_terms WHERE account_id = ? AND source = 'you'", (account_id,))
        conn.commit()
        return
    try:
        apr = float(apr_text) if apr_text else None
    except ValueError:
        raise LoanError("Interest rate must be a number, e.g. 6.5.") from None
    if apr is not None and not 0 <= apr < 100:
        raise LoanError("Interest rate must be between 0 and 100%.")
    try:
        payment = parse_amount(payment_text) if payment_text else None
    except ValueError as exc:
        raise LoanError(f"Monthly payment: {exc}") from None
    conn.execute(
        """
        INSERT INTO liability_terms (account_id, apr_percent, monthly_payment_cents, source, detail)
        VALUES (?, ?, ?, 'you', 'entered by you')
        ON CONFLICT (account_id) DO UPDATE SET
            apr_percent = excluded.apr_percent, monthly_payment_cents = excluded.monthly_payment_cents,
            source = 'you', detail = 'entered by you', updated_at = CURRENT_TIMESTAMP
        """,
        (account_id, apr, payment),
    )
    conn.commit()


def liabilities_with_terms(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every visible liability account with its terms (if any)."""
    return conn.execute(
        """
        SELECT a.id, a.name, a.mask, a.type, a.subtype, a.is_manual, a.current_balance_cents,
               t.apr_percent, t.monthly_payment_cents, t.source, t.detail
        FROM accounts a LEFT JOIN liability_terms t ON t.account_id = a.id
        WHERE a.classification = 'liability' AND a.is_hidden = 0 AND a.current_balance_cents IS NOT NULL
        ORDER BY a.type, a.name
        """
    ).fetchall()
