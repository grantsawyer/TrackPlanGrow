"""Converting Plaid's decimal amounts to integer cents, and back for display."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_cents(amount: float | int | str | None) -> int | None:
    """12.345 -> 1235. Goes through str() so float artifacts (1.005 stored as
    1.00499999...) round the way a person would expect."""
    if amount is None:
        return None
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_amount(text: str) -> int:
    """'$18,500' / '18500' / '18,500.00' -> cents. Raises ValueError if it isn't
    a number or is negative."""
    cleaned = (text or "").strip().replace("$", "").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{text!r} isn't an amount.") from None
    if not value.is_finite():
        raise ValueError(f"{text!r} isn't an amount.")
    if value < 0:
        raise ValueError("Enter a positive amount.")
    return to_cents(value)


# ---------------------------------------------------------------- formulas (budget amounts)

FORMULA_MAX_LENGTH = 200
_TOKEN = re.compile(r"\s*(?:(\$?\d[\d,]*(?:\.\d*)?|\$?\.\d+)|(.))")


def parse_amount_formula(text: str) -> int:
    """An amount or a calculation -> cents (user's request 2026-10-06).

    '795', '$1,200', '=795/12', '=(1200+300)*12%', '-(5)+10'. The leading '=' is optional.
    Operators: + - * / ( ) and % as a percent (spreadsheet style: '15%' = 0.15). Exact
    decimal math, rounded half-up to cents at the end. Never uses eval(): a tiny parser.
    Raises ValueError (shown to you) for anything else, division by zero, or a negative result.
    """
    raw = (text or "").strip()
    try:
        return parse_amount(raw)            # the common case: a plain amount
    except ValueError:
        if not raw:
            raise
    expression = raw[1:] if raw.startswith("=") else raw
    if len(expression) > FORMULA_MAX_LENGTH:
        raise ValueError("That formula is too long.")
    value = _Formula(expression, raw).parse()
    if value < 0:
        raise ValueError("Enter a positive amount.")
    return to_cents(value)


class _Formula:
    """Recursive-descent parser over Decimals:
        expr    := term (('+' | '-') term)*
        term    := unary (('*' | '/') unary)*
        unary   := ('+' | '-') unary | postfix
        postfix := primary '%'*
        primary := number | '(' expr ')'
    """

    def __init__(self, expression: str, original: str):
        self.original = original
        self.tokens: list[tuple[str, object]] = []
        for number, op in _TOKEN.findall(expression):
            if number:
                self.tokens.append(("num", Decimal(number.replace("$", "").replace(",", ""))))
            elif op.strip():
                if op not in "+-*/%()":
                    raise self._error()
                self.tokens.append(("op", op))
        self.pos = 0

    def _error(self) -> ValueError:
        return ValueError(f"{self.original!r} isn't an amount or a calculation like =795/12.")

    def _peek(self) -> object:
        return self.tokens[self.pos][1] if self.pos < len(self.tokens) and self.tokens[self.pos][0] == "op" else None

    def parse(self) -> Decimal:
        if not self.tokens:
            raise self._error()
        value = self._expr()
        if self.pos != len(self.tokens):
            raise self._error()
        return value

    def _expr(self) -> Decimal:
        value = self._term()
        while self._peek() in ("+", "-"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            value = value + self._term() if op == "+" else value - self._term()
        return value

    def _term(self) -> Decimal:
        value = self._unary()
        while self._peek() in ("*", "/"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            right = self._unary()
            if op == "*":
                value *= right
            elif right == 0:
                raise ValueError("Can't divide by zero.")
            else:
                value /= right
        return value

    def _unary(self) -> Decimal:
        if self._peek() in ("+", "-"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            value = self._unary()
            return -value if op == "-" else value
        return self._postfix()

    def _postfix(self) -> Decimal:
        value = self._primary()
        while self._peek() == "%":
            self.pos += 1
            value /= 100
        return value

    def _primary(self) -> Decimal:
        if self.pos >= len(self.tokens):
            raise self._error()
        kind, value = self.tokens[self.pos]
        if kind == "num":
            self.pos += 1
            return value
        if value == "(":
            self.pos += 1
            inner = self._expr()
            if self._peek() != ")":
                raise self._error()
            self.pos += 1
            return inner
        raise self._error()


def format_cents(cents: int | None) -> str:
    """1234567 -> '$12,345.67'; -500 -> '-$5.00'; None -> '—'."""
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"
