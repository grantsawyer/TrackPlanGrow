"""OFX / QFX / QBO and QIF (the Quicken and QuickBooks downloads banks offer) -> rows of text cells (phase 3,
docs/import-plan.md).

Both formats are the same at every bank, so they become a table whose columns detection always reads the same way:
Posted Date, Transaction Date, Description, Debit, Credit (and QIF's Category). Their amounts are signed from your
side (negative = money out, for bank accounts and cards alike), so money out goes in Debit and money in in Credit and
the sign is never a question.

- **OFX** (1.x is SGML with unclosed leaf tags, 2.x is XML; QFX and QBO are OFX): each <STMTTRN> is a transaction:
  DTPOSTED (posted), DTUSER (bought), TRNAMT, NAME (else MEMO / PAYEE). A file with several accounts is refused.
  An **investment statement** (<INVSTMTRS>, phase 5) becomes an activity table (Date, Action, Symbol, Description,
  Quantity, Price, Amount: each BUY* / SELL* / REINVEST / INCOME / INVEXPENSE / TRANSFER / INVBANKTRAN ..., tickers from
  its SECLIST), or, when it lists no activity, a holdings table (its positions and cash, "As of" its date).
- **QIF**: records end with '^'; D date (3/4'26 = 3/4/26), T (or U) amount, P payee (else M memo), L category.
  `!Type:Invst` (phase 5) is refused, as is a file with several accounts.

Read as text with regular expressions: no XML parser, so nothing in the file is expanded or followed.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation

from fintrack.imports.readers import ReadError, Table, decode

HEADER = ["Posted Date", "Transaction Date", "Description", "Debit", "Credit"]


def is_ofx(data: bytes) -> bool:
    return b"<OFX>" in data[:65536].upper()


def is_qif(data: bytes) -> bool:
    return decode(data[:4096]).lstrip().upper().startswith("!TYPE:")


def _split(amount: str) -> tuple[str, str] | None:
    """A signed amount (negative = money out) -> (debit, credit) cells, or None if it isn't a number."""
    try:
        value = Decimal(amount.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None
    return (str(-value), "") if value < 0 else ("", str(value))


# ---------------------------------------------------------------- OFX

def _ofx_date(text: str) -> str:
    """'20260928120000.000[-5:EST]' -> '2026-09-28'."""
    digits = re.match(r"\s*(\d{4})(\d{2})(\d{2})", text or "")
    return f"{digits.group(1)}-{digits.group(2)}-{digits.group(3)}" if digits else ""


def read_ofx(data: bytes) -> Table:
    text = decode(data)
    upper = text.upper()
    accounts = {a.strip() for a in re.findall(r"<ACCTID>([^<\r\n]+)", text, re.I)}
    if len(accounts) > 1:
        raise ReadError(f"This file holds {len(accounts)} accounts: download one account at a time.")
    if "<INVSTMTRS>" in upper:
        return read_ofx_investments(text)
    blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.S | re.I)
    rows: Table = [HEADER]
    for block in blocks:
        fields: dict[str, str] = {}
        for tag, value in re.findall(r"<([A-Za-z0-9.]+)>([^<\r\n]*)", block):
            fields.setdefault(tag.upper(), html.unescape(value).strip())
        split = _split(fields.get("TRNAMT", ""))
        if split is None:
            continue
        description = fields.get("NAME") or fields.get("MEMO") or fields.get("PAYEE") or ""
        rows.append([_ofx_date(fields.get("DTPOSTED", "")), _ofx_date(fields.get("DTUSER", "")), description, *split])
    return rows if len(rows) > 1 else []


# ---------------------------------------------------------------- QIF

def _qif_date(text: str) -> str:
    """"3/4'26" / '3/ 4/26' / '03-04-2026' -> '3/4/26' etc.: the apostrophe before a 2-digit year becomes a slash
    (detection then reads the format, month or day first)."""
    return text.replace(" ", "").replace("'", "/")


def read_qif(data: bytes) -> Table:
    rows: Table = [HEADER + ["Category"]]
    kind = None
    record: dict[str, str] = {}
    for line in decode(data).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("!TYPE:"):
            if kind is not None and len(rows) > 1:
                raise ReadError("This file holds several accounts: download one account at a time.")
            kind = line[6:].strip().lower()
            if kind.startswith("invst"):
                raise ReadError("This is an investment file: those can't be imported yet.")
            continue
        if line.startswith("!"):
            continue                                     # !Account, !Option: headers of no use here
        if line == "^":
            amount = record.get("T") or record.get("U") or ""
            split = _split(amount) if record.get("D") else None
            if split is not None:
                rows.append([_qif_date(record["D"]), "", record.get("P") or record.get("M") or "", *split,
                             record.get("L", "")])
            record = {}
            continue
        record.setdefault(line[0], line[1:].strip())
    return rows if len(rows) > 1 else []


# ---------------------------------------------------------------- OFX investment statements (phase 5)

INV_AGGREGATES = ("BUYSTOCK", "BUYMF", "BUYDEBT", "BUYOPT", "BUYOTHER", "SELLSTOCK", "SELLMF", "SELLDEBT", "SELLOPT",
                  "SELLOTHER", "REINVEST", "INCOME", "INVEXPENSE", "TRANSFER", "INVBANKTRAN", "MARGININTEREST",
                  "RETOFCAP", "JRNLFUND", "JRNLSEC", "SPLIT", "CLOSUREOPT")
INCOME_TYPES = {"DIV": "Dividend", "INTEREST": "Interest", "CGLONG": "Capital gain", "CGSHORT": "Capital gain",
                "MISC": "Income"}


def _leaves(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for tag, value in re.findall(r"<([A-Za-z0-9.]+)>([^<\r\n]*)", block):
        if value.strip():
            fields.setdefault(tag.upper(), html.unescape(value).strip())
    return fields


def _plain(number: str) -> str:
    try:
        return str(Decimal(number.replace(",", "")))
    except InvalidOperation:
        return ""


def read_ofx_investments(text: str) -> Table:
    securities = {}
    for block in re.findall(r"<SECINFO>(.*?)</SECINFO>", text, re.S | re.I):
        f = _leaves(block)
        if f.get("UNIQUEID"):
            securities[f["UNIQUEID"]] = (f.get("TICKER", ""), f.get("SECNAME", ""))
    rows: Table = [["Date", "Action", "Symbol", "Description", "Quantity", "Price", "Amount"]]
    pattern = re.compile(r"<(" + "|".join(INV_AGGREGATES) + r")>(.*?)</\1>", re.S | re.I)
    for name, block in pattern.findall(text):
        name, f = name.upper(), _leaves(block)
        ticker, secname = securities.get(f.get("UNIQUEID", ""), ("", ""))
        amount = f.get("TOTAL") or f.get("TRNAMT") or ""
        if name.startswith("BUY"):
            action = "Buy"
        elif name.startswith("SELL"):
            action = "Sell"
        elif name == "REINVEST":
            action = "Reinvest"
        elif name == "INCOME":
            action = INCOME_TYPES.get(f.get("INCOMETYPE", "").upper(), "Income")
        elif name in ("INVEXPENSE", "MARGININTEREST"):
            action = "Fee"
        elif name == "TRANSFER":
            action = "Transfer in" if f.get("TFERACTION", "").upper() == "IN" else "Transfer out"
        elif name == "INVBANKTRAN":
            action = "Deposit" if _plain(amount) and Decimal(_plain(amount)) > 0 else "Withdrawal"
        elif name == "RETOFCAP":
            action = "Return of capital"
        else:
            action = name.title()
        rows.append([_ofx_date(f.get("DTTRADE") or f.get("DTPOSTED", "")), action, ticker,
                     secname or f.get("MEMO") or f.get("NAME") or action, _plain(f.get("UNITS", "")),
                     _plain(f.get("UNITPRICE", "")), _plain(amount)])
    if len(rows) > 1:
        return rows
    # No activity: the statement's holdings, as of its date.
    as_of = _ofx_date((re.search(r"<DTASOF>([^<\r\n]+)", text, re.I) or [None, ""])[1])
    holdings: Table = [["As of", as_of], ["Symbol", "Description", "Quantity", "Price", "Market Value"]]
    for block in re.findall(r"<INVPOS>(.*?)</INVPOS>", text, re.S | re.I):
        f = _leaves(block)
        ticker, secname = securities.get(f.get("UNIQUEID", ""), ("", ""))
        holdings.append([ticker or f.get("UNIQUEID", ""), secname or ticker, _plain(f.get("UNITS", "")),
                         _plain(f.get("UNITPRICE", "")), _plain(f.get("MKTVAL", ""))])
    cash = re.search(r"<AVAILCASH>([^<\r\n]+)", text, re.I)
    if cash and _plain(cash.group(1)):
        holdings.append(["", "Cash", "", "", _plain(cash.group(1))])
    return holdings if len(holdings) > 2 else []
