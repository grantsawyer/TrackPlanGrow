"""Readers: a downloaded file -> rows of text cells (a `Table`), whatever the file type. Detection (layout.py) only ever
sees a Table, so it never cares where the cells came from.

CSV (comma, semicolon, tab or pipe), Excel `.xlsx` (xlsx.py) and OFX / QFX / QBO and QIF (money_files.py; phase 3,
docs/import-plan.md). Everything is read as text: nothing in a file is evaluated or followed (no formulas, macros or
links), and the file is never written anywhere.
"""

from __future__ import annotations

import csv
import io

MAX_BYTES = 10 * 1024 * 1024          # a bank's download is far smaller; anything bigger is refused
DELIMITERS = (",", ";", "\t", "|")

Table = list[list[str]]


class ReadError(ValueError):
    """A file that can't be read as a table; the message is shown to you."""


def decode(data: bytes) -> str:
    """Bytes -> text: UTF-8 (a byte-order mark dropped), else Windows-1252, which banks' older exports use and which
    decodes any byte."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _parse(text: str, delimiter: str) -> Table:
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]


def sniff_delimiter(text: str) -> str:
    """The delimiter that splits the most lines into the same number of cells (2 or more): a preamble line or a totals
    line doesn't throw it off, and a comma inside quotes doesn't count."""
    sample = "\n".join(text.splitlines()[:60])
    best, best_score = ",", -1
    for d in DELIMITERS:
        widths = [len(row) for row in _parse(sample, d) if len(row) > 1]
        if not widths:
            continue
        common = max(set(widths), key=widths.count)
        score = widths.count(common) * common          # many lines of the same, wide shape
        if score > best_score:
            best, best_score = d, score
    return best


def read_csv(data: bytes) -> Table:
    """A CSV (or semicolon / tab / pipe separated) file -> its rows, cells stripped; empty lines dropped."""
    text = decode(data)
    if "\x00" in text:
        raise ReadError("This looks like a binary file, not a CSV. Save it from your bank as CSV.")
    return [row for row in _parse(text, sniff_delimiter(text)) if any(row)]


def read_table(data: bytes, file_name: str) -> Table:
    """Any supported file -> its rows, the type told by its content (then its name): Excel (.xlsx), OFX / QFX / QBO,
    QIF, else CSV. Refuses what's too big, old Excel (.xls) and what isn't a table."""
    from fintrack.imports import money_files, xlsx

    if len(data) > MAX_BYTES:
        raise ReadError(f"{file_name} is over {MAX_BYTES // (1024 * 1024)} MB; a bank's download is much smaller.")
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if data[:4] == b"PK\x03\x04":
        table = xlsx.read_xlsx(data)
    elif data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or suffix == "xls":
        raise ReadError("Old Excel files (.xls) aren't read: open it in Excel or Numbers and save it as .xlsx or CSV.")
    elif money_files.is_ofx(data) or suffix in ("ofx", "qfx", "qbo"):
        table = money_files.read_ofx(data)
    elif money_files.is_qif(data) or suffix == "qif":
        table = money_files.read_qif(data)
    else:
        table = read_csv(data)
    if not table:
        raise ReadError(f"{file_name} is empty.")
    return table
