"""Excel `.xlsx` -> rows of text cells, with the standard library only (user's decision 2026-11-13: no openpyxl).

An `.xlsx` is a zip of XML files: `xl/workbook.xml` lists the sheets, `xl/_rels/workbook.xml.rels` says which file
each is, `xl/sharedStrings.xml` holds the text the cells point to, and `xl/styles.xml` says which cells are dates
(Excel stores a date as a day count; its number format makes it a date). The first sheet that holds transactions is
read (a row with a date and an amount), else the first sheet.

Read only, never run: formulas aren't evaluated (a formula cell's saved value is read), macros and links are ignored.
Guarded: the zip's members must unpack to at most MAX_UNPACKED (zip bombs), and XML declaring a DOCTYPE or ENTITY is
refused (entity expansion), before anything is parsed.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

from fintrack.imports.readers import ReadError, Table

MAX_UNPACKED = 60 * 1024 * 1024     # a bank's sheet unpacks to far less
# Excel's built-in date (and date-time) number formats
DATE_FORMAT_IDS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}


def _local(tag: str) -> str:
    """'{namespace}row' -> 'row' (both the usual and the 'Strict' Excel namespaces)."""
    return tag.rsplit("}", 1)[-1]


def _children(element, name: str):
    return [c for c in element if _local(c.tag) == name]


def _find_all(element, name: str):
    return [e for e in element.iter() if _local(e.tag) == name]


def _attr(element, name: str) -> str | None:
    """An attribute by its local name (r:id is '{relationships namespace}id')."""
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _xml(zf: zipfile.ZipFile, name: str):
    raw = zf.read(name)
    head = raw.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ReadError("This spreadsheet has content that isn't read (a DOCTYPE): save it as CSV.")
    return ElementTree.fromstring(raw)


def _text(si) -> str:
    """A shared or inline string: its <t>, or its rich-text runs' <t>s together."""
    return "".join(t.text or "" for t in _find_all(si, "t"))


def _is_date_format(code: str) -> bool:
    """A custom number format that shows a date: it has d or y once quoted text and [colours] are left out."""
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code or "")
    return bool(re.search(r"[dy]", code, re.I))


def _number(text: str) -> str:
    """A stored number as the bank meant it: '84.120000000000005' -> '84.12', '350' -> '350'."""
    try:
        value = float(text)
    except ValueError:
        return text
    return str(int(value)) if value.is_integer() else repr(round(value, 10))


def _column(ref: str) -> int:
    """'C12' -> 2."""
    n = 0
    for ch in re.match(r"[A-Z]+", ref.upper()).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data: bytes) -> Table:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ReadError("This file isn't a spreadsheet Excel can open.") from None
    with zf:
        infos = zf.infolist()
        if any(i.flag_bits & 0x1 for i in infos):
            raise ReadError("This spreadsheet is password-protected: save an unprotected copy, or a CSV.")
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise ReadError("This spreadsheet is too big to read.")
        names = {i.filename for i in infos}
        if "xl/workbook.xml" not in names:
            raise ReadError("This isn't an Excel workbook (.xlsx): save it as .xlsx or CSV.")

        workbook = _xml(zf, "xl/workbook.xml")
        epoch = date(1904, 1, 1) if any(_attr(p, "date1904") in ("1", "true") for p in _find_all(workbook, "workbookPr")) \
            else date(1899, 12, 30)
        targets = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in _find_all(_xml(zf, "xl/_rels/workbook.xml.rels"), "Relationship"):
                target = rel.get("Target", "")
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                targets[rel.get("Id")] = path
        sheets = [targets.get(_attr(s, "id")) for s in _find_all(workbook, "sheet") if _attr(s, "state") != "hidden"]
        sheets = [s for s in sheets if s in names] or sorted(n for n in names if n.startswith("xl/worksheets/sheet"))

        shared = [_text(si) for si in _find_all(_xml(zf, "xl/sharedStrings.xml"), "si")] \
            if "xl/sharedStrings.xml" in names else []
        date_styles: set[int] = set()
        if "xl/styles.xml" in names:
            styles = _xml(zf, "xl/styles.xml")
            custom = {int(f.get("numFmtId", "-1")): f.get("formatCode", "") for f in _find_all(styles, "numFmt")}
            for xfs in _find_all(styles, "cellXfs"):
                for i, xf in enumerate(_children(xfs, "xf")):
                    fmt = int(xf.get("numFmtId", "0"))
                    if fmt in DATE_FORMAT_IDS or (fmt in custom and _is_date_format(custom[fmt])):
                        date_styles.add(i)

        def cell_text(c) -> str:
            kind = c.get("t", "n")
            v = next((e.text or "" for e in _children(c, "v")), "")
            if kind == "s":
                return shared[int(v)] if v.isdigit() and int(v) < len(shared) else ""
            if kind == "inlineStr":
                return "".join(_text(i) for i in _children(c, "is"))
            if kind == "b":
                return "TRUE" if v == "1" else "FALSE"
            if kind == "d":
                return v[:10]                            # an ISO date
            if kind in ("str", "e"):
                return v
            if v and int(c.get("s", "0") or 0) in date_styles:
                try:
                    return (epoch + timedelta(days=int(float(v)))).isoformat()
                except (ValueError, OverflowError):
                    return v
            return _number(v)

        def sheet_rows(path: str) -> Table:
            rows: Table = []
            for row in _find_all(_xml(zf, path), "row"):
                cells: dict[int, str] = {}
                for n, c in enumerate(_children(row, "c")):
                    ref = c.get("r")
                    cells[_column(ref) if ref else n] = cell_text(c).strip()
                if any(cells.values()):
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
            return rows

        from fintrack.imports import layout
        first: Table = []
        for path in sheets:
            rows = sheet_rows(path)
            first = first or rows
            try:
                layout.locate(rows)                      # a sheet of transactions
                return rows
            except ValueError:
                continue
        return first
