"""Importing bank files of any layout (fintrack/imports/, `fintrack import`; user's requests 2026-10-29 and 2026-11-13,
docs/import-plan.md). Fake data only: made-up banks, merchants and amounts."""

import hashlib
import re
from datetime import date
from decimal import Decimal

import pytest

from fintrack.imports import engine, layout, readers
from fintrack import items, rules

CARD, CHECKING, OLD = 1, 2, 3
# The first importer's bank layout (2026-10-29): two dates, Debit / Credit, the card used, the bank's category.
HEADER = "Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
ROWS = """2026-07-05,2026-07-07,0000,FAKE GROCER 1234,Merchandise,42.00,
2026-07-01,2026-07-02,9999,FAKE GROCER 5678,Merchandise,10.50,
2026-07-01,2026-07-02,9999,FAKE GROCER 5678,Merchandise,10.50,
2026-06-20,2026-06-21,0000,FAKE DINER #12,Dining,25.00,
2026-06-15,2026-06-15,0000,FAKE CARD AUTOPAY PYMT,Payment/Credit,,300.00
2026-06-10,2026-06-11,0000,FAKE GROCER 1234,Merchandise,,5.25

2026-06-01,2026-06-02,0000,FAKE MYSTERY,,7.00,
"""


@pytest.fixture
def db(conn):
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name) VALUES (1, 'i1', 'sandbox', 'Fake Bank')")
    conn.execute("INSERT INTO items (id, plaid_item_id, plaid_env, institution_name, status)"
                 " VALUES (2, 'i2', 'sandbox', 'Fake Old Bank', 'removed')")
    for acc_id, item, kind in ((CARD, 1, "credit"), (CHECKING, 1, "depository"), (OLD, 2, "depository")):
        conn.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification)"
                     " VALUES (?, ?, ?, ?, '0000', ?, ?)",
                     (acc_id, item, f"acc-{acc_id}", f"Fake account {acc_id}", kind, items.classify(kind)))
    # Plaid's history on the card starts 2026-07-07; it already knows the fake grocer
    conn.execute("""INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name,
                    original_description, merchant_name, plaid_category_primary, plaid_category_detailed,
                    plaid_category_key, logo_url)
                    VALUES ('p1', 1, '2026-07-07', 4200, 'Fake Grocer', 'FAKE GROCER 1234', 'Fake Grocer',
                            'FOOD_AND_DRINK', 'FOOD_AND_DRINK_GROCERIES', 'FOOD_AND_DRINK', 'https://example.invalid/g.png')""")
    conn.commit()
    return conn


def detect(text: str, classification: str = "asset", **answers):
    table = readers.read_csv(text.encode())
    d = layout.detect(table, classification, **answers)
    rows, skipped = layout.read_rows(table, d)
    return d, rows, skipped


def prepare(db, account, text, **answers):
    return engine.prepare(db, account, text.encode(), "fake.csv", **answers)


# ---------------------------------------------------------------- the first bank's layout, as before

def test_the_first_banks_layout_reads_as_before():
    d, rows, skipped = detect(HEADER + ROWS, "liability")
    assert skipped == [] and len(rows) == 7                                # the blank line skipped
    assert d.layout.columns == {"date": 1, "authorized": 0, "card": 2, "description": 3, "category": 4,
                                "debit": 5, "credit": 6}                   # posted date = the row's date
    assert rows[0] == layout.Row(2, "2026-07-07", "2026-07-05", "FAKE GROCER 1234", 4200, "0000", "Merchandise")
    assert rows[4].amount_cents == -30000 and rows[5].amount_cents == -525  # credits: money in
    assert d.questions == [] and d.sign_source == "columns"


def test_ids_keep_the_first_importers_formula():
    """Rows imported by `import-csv` before 2026-11-13 must count as already imported."""
    _, rows, _ = detect(HEADER + ROWS)
    r = rows[1]
    raw = "|".join(("1", r.posted, r.purchased, r.card, str(r.amount_cents), r.description, "0"))
    assert engine.import_id(1, r, 0) == "csv-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def test_merchant_key_drops_what_changes_per_purchase():
    assert engine.merchant_key("FAKE GROCER 1234") == engine.merchant_key("Fake Grocer 5678") == "FAKE GROCER"
    assert engine.merchant_key("FAKE MKTPL*E40KT5NN3") == engine.merchant_key("FAKE MKTPL*ZZ9") == "FAKE MKTPL*"
    assert engine.merchant_key("FAKE DINER #12") == "FAKE DINER"


def test_the_plan_asks_about_the_overlap_then_categorizes(db):
    p = prepare(db, CARD, HEADER + ROWS)
    assert p.needs == ["overlap"] and p.new == []                          # nothing taken until you choose
    o = p.overlap
    assert (o.file_from, o.file_to, o.have) == ("2026-06-02", "2026-07-07", [("2026-07-07", None)])
    assert (o.outside, o.skip, o.duplicates) == (6, 6, 1)                  # Jul 7's grocer is Plaid's p1
    with pytest.raises(engine.ImportFileError):
        engine.apply(db, p)

    p = prepare(db, CARD, HEADER + ROWS, overlap="outside")
    assert p.needs == [] and p.count("covered") == 1                       # posted Jul 7: Plaid has it
    assert len(p.new) == 6 and len({x.transaction_id for x in p.new}) == 6  # two identical rows: two ids
    grocer = [x for x in p.new if x.row.description.startswith("FAKE GROCER")]
    assert all(x.known and (x.primary, x.detailed) == ("FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES") for x in grocer)
    [diner] = [x for x in p.new if x.row.description == "FAKE DINER #12"]
    assert not diner.known and diner.detailed == "FOOD_AND_DRINK_RESTAURANT"   # the bank's category, mapped
    [pay] = [x for x in p.new if "AUTOPAY" in x.row.description]
    assert pay.detailed == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
    [mystery] = [x for x in p.new if x.row.description == "FAKE MYSTERY"]
    assert (mystery.primary, mystery.detailed) == engine.UNKNOWN


def test_apply_adds_them_like_synced_rows_as_one_batch_and_undo_removes_it(db):
    rules.add_simple(db, "FAKE DINER", "TRAVEL", "bank_text")
    # the payment's bank side, for transfer matching
    db.execute("""INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name, plaid_category_key)
                  VALUES ('p2', 2, '2026-06-15', 30000, 'FAKE CARD PAYMENT', 'CREDIT_CARD_PAYMENT')""")
    db.commit()
    batch, added = engine.apply(db, prepare(db, CARD, HEADER + ROWS, overlap="outside"))
    assert added == 6
    rows = {r["name"]: r for r in db.execute("SELECT * FROM transactions_effective WHERE account_id = 1")}
    plaid = db.execute("SELECT date, amount_cents FROM transactions WHERE plaid_transaction_id = 'p1'").fetchone()
    assert tuple(plaid) == ("2026-07-07", 4200)                                      # Plaid's row untouched
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE account_id = 1").fetchone()[0] == 7
    refund = db.execute("SELECT * FROM transactions WHERE plaid_transaction_id LIKE 'csv-%' AND amount_cents = -525").fetchone()
    assert refund["date"] == "2026-06-11" and refund["authorized_date"] == "2026-06-10"
    assert refund["merchant_name"] == "Fake Grocer" and refund["logo_url"] == "https://example.invalid/g.png"
    assert refund["import_batch_id"] == batch
    assert rows["FAKE DINER #12"]["category"] == "TRAVEL"                            # your rule applies
    assert rows["FAKE CARD AUTOPAY PYMT"]["kind"] == "transfer"                      # paired with the bank side
    assert refund["account_owner"] == "0000"                                         # the card used, as Plaid puts it
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE account_owner = '9999'").fetchone()[0] == 2
    [b] = engine.batches(db, CARD)
    assert (b["rows_added"], b["date_from"], b["date_to"], b["overlap_choice"]) == (6, "2026-06-02", "2026-07-02", "outside")

    again = prepare(db, CARD, HEADER + ROWS)                    # its own rows don't count as an overlap
    assert again.count("already") == 6 and again.cards_missing == [] and again.choice is None
    assert again.overlap and again.overlap.outside == 0         # only the Jul 7 row, which Plaid has, is left
    # imported before the card was kept: a re-import fills it in, and only where it's missing
    db.execute("UPDATE transactions SET account_owner = NULL WHERE plaid_transaction_id LIKE 'csv-%'")
    db.execute("UPDATE transactions SET account_owner = 'kept' WHERE plaid_transaction_id LIKE 'csv-%' AND amount_cents = 2500")
    db.commit()
    fill = prepare(db, CARD, HEADER + ROWS, overlap="outside")
    assert len(fill.cards_missing) == 5 and engine.apply(db, fill) == (None, 0)
    assert dict(db.execute("SELECT account_owner, COUNT(*) FROM transactions WHERE plaid_transaction_id LIKE 'csv-%'"
                           " GROUP BY 1").fetchall()) == {"0000": 3, "9999": 2, "kept": 1}

    assert engine.undo(db, batch) == 6                                               # the whole import goes
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE account_id = 1").fetchone()[0] == 1
    assert engine.batches(db)[0]["undone_at"]
    assert db.execute("SELECT is_transfer FROM transactions WHERE plaid_transaction_id = 'p2'").fetchone()[0] == 0
    with pytest.raises(engine.ImportFileError):
        engine.undo(db, batch)


# ---------------------------------------------------------------- other layouts (all made up)

def test_signed_amounts_newest_first_with_preamble_totals_and_a_balance_proving_the_sign():
    text = """Fake Credit Union
Account,Checking ****0000
Date range,01/01/2026 - 01/31/2026
Date,Description,Amount,Running Bal.
01/20/2026,FAKE PAYROLL,"1,000.00","1,883.50"
01/15/2026,FAKE COFFEE,-4.50,883.50
01/14/2026,FAKE RENT,-800.00,888.00
01/02/2026,FAKE GROCER,-12.00,"1,688.00"
Total,,183.50,
"""
    d, rows, skipped = detect(text)
    assert d.header == ["Date", "Description", "Amount", "Running Bal."]
    assert d.layout.columns == {"date": 0, "description": 1, "amount": 2, "balance": 3}
    assert d.sign_source == "balance" and d.layout.sign == -1 and d.questions == []
    assert [r.amount_cents for r in rows] == [-100000, 450, 80000, 1200]           # Plaid's sign: payroll is money in
    assert rows[0].balance_cents == 188350
    assert skipped == [(9, "date 'Total' isn't a date")]


def test_a_cards_balance_is_what_you_owe():
    text = """Date,Description,Amount,Balance
2026-02-01,FAKE SHOP,25.00,125.00
2026-02-03,FAKE CAFE,5.00,130.00
2026-02-05,FAKE PAYMENT THANK YOU,-100.00,30.00
"""
    d, rows, _ = detect(text, "liability")
    assert d.sign_source == "balance" and d.layout.sign == 1
    assert [r.amount_cents for r in rows] == [2500, 500, -10000]


def test_debit_and_credit_columns_with_negative_debits_and_dollar_signs():
    d, rows, _ = detect("""Post Date,Payee,Withdrawals,Deposits
02/03/2026,FAKE HARDWARE,"-$1,234.56",
02/04/2026,FAKE REFUND,,$20.00
""")
    assert d.sign_source == "columns"
    assert [(r.posted, r.amount_cents) for r in rows] == [("2026-02-03", 123456), ("2026-02-04", -2000)]


def test_an_amount_with_a_dr_cr_type_column():
    d, rows, _ = detect("""Date,Details,Amount,Type
2026-03-01,FAKE STORE,10.00,DR
2026-03-02,FAKE INTEREST,0.25,CR
""")
    assert d.sign_source == "type" and [r.amount_cents for r in rows] == [1000, -25]


def test_parentheses_and_a_guessed_sign_is_a_question_until_answered():
    text = """Date,Description,Amount
2026-04-01,FAKE BAKERY,(3.00)
2026-04-02,FAKE BOOKS,(15.00)
2026-04-03,FAKE PAYROLL,500.00
"""
    d, rows, _ = detect(text)
    assert d.sign_source in ("words", "majority") and "sign" in d.questions
    assert [r.amount_cents for r in rows] == [300, 1500, -50000]                  # most rows are spending: money out
    d, rows, _ = detect(text, sign=1)                                             # you say positive is money out
    assert d.questions == [] and [r.amount_cents for r in rows] == [-300, -1500, 50000]


def test_semicolons_decimal_commas_and_day_first_dates():
    d, rows, _ = detect("""Buchungstag;Verwendungszweck;Betrag
03.02.2026;FAKE SUPERMARKT;-1.234,56
25.02.2026;FAKE GEHALT;2.000,00
""")
    assert d.layout.decimal_comma and d.layout.date_format == "%d.%m.%Y"
    assert d.layout.columns["date"] == 0 and d.layout.columns["amount"] == 2
    assert [(r.posted, r.amount_cents) for r in rows] == [("2026-02-03", 123456), ("2026-02-25", -200000)]


def test_month_or_day_first_from_the_rows_order_else_a_question():
    in_order = "Date,Description,Amount\n01/02/2026,FAKE A,1.00\n03/02/2026,FAKE B,2.00\n04/02/2026,FAKE C,3.00\n"
    _, rows, _ = detect(in_order.replace("04/02", "02/03"))                       # Jan 2, Mar 2, Feb 3: only day-first is in order
    assert [r.posted for r in rows] == ["2026-02-01", "2026-02-03", "2026-03-02"]
    d, rows, _ = detect(in_order)                                                 # both orders are in order: ask
    assert "date_order" in d.questions and rows[1].posted == "2026-03-02"
    d, rows, _ = detect(in_order, date_order="dmy")
    assert "date_order" not in d.questions and rows[1].posted == "2026-02-03"


def test_a_file_with_no_header_is_read_from_its_values():
    d, rows, _ = detect('"01/05/2026","-12.34","*","","FAKE GAS STATION 42"\n"01/06/2026","-3.00","*","","FAKE CAFE"\n')
    assert d.header is None and d.layout.columns["date"] == 0 and d.layout.columns["amount"] == 1
    assert d.layout.columns["description"] == 4 and rows[0].description == "FAKE GAS STATION 42"


def test_only_the_last_4_of_a_card_number_is_kept():
    _, rows, _ = detect("Date,Description,Amount,Card Number\n2026-05-01,FAKE,1.00,4111-1111-1111-0000\n", sign=1)
    assert rows[0].card == "0000"


def test_readers_sniff_the_delimiter_decode_old_encodings_and_refuse_what_they_cant_read():
    assert readers.read_csv("a\tb\n1\t2\n".encode()) == [["a", "b"], ["1", "2"]]
    assert readers.read_csv("Date,Caf\xe9\n".encode("cp1252")) == [["Date", "Café"]]
    with pytest.raises(readers.ReadError):
        readers.read_table(b"x" * (readers.MAX_BYTES + 1), "big.csv")
    with pytest.raises(readers.ReadError):
        readers.read_table(b"PK\x03\x04", "fake.xlsx")
    with pytest.raises(layout.DetectError):
        detect("just,some,words\nand,more,words\n")


# ---------------------------------------------------------------- overlap choices and profiles

CHECKING_FILE = """Date,Description,Amount,Balance
2026-07-01,FAKE GROCER,-20.00,980.00
2026-07-05,FAKE CAFE,-4.00,976.00
2026-07-08,FAKE GROCER,-20.00,956.00
2026-07-09,FAKE BOOKS,-9.00,947.00
"""


def test_overlap_skip_and_review_on_a_connected_account(db):
    # Plaid has the account from Jul 9: its grocer of Jul 9 is likely the file's Jul 8 row
    db.execute("""INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name)
                  VALUES ('p3', 2, '2026-07-09', 2000, 'Fake Grocer')""")
    db.commit()
    p = prepare(db, CHECKING, CHECKING_FILE)
    assert p.needs == ["overlap"] and (p.overlap.outside, p.overlap.skip, p.overlap.duplicates) == (2, 3, 1)
    p = prepare(db, CHECKING, CHECKING_FILE, overlap="skip")
    assert sorted(x.row.posted for x in p.new) == ["2026-07-01", "2026-07-05", "2026-07-09"]
    [dupe] = [x for x in p.rows if x.status == "duplicate"]
    assert dupe.row.posted == "2026-07-08" and dupe.duplicate_of.name == "Fake Grocer" and not dupe.take
    p = prepare(db, CHECKING, CHECKING_FILE, overlap="review", take={dupe.row.line})
    assert len(p.new) == 4                                                       # you took it anyway
    assert engine.apply(db, p)[1] == 4


def test_no_overlap_asks_nothing_and_a_removed_connection_only_covers_its_own_dates(db):
    assert prepare(db, CHECKING, CHECKING_FILE).overlap is None                  # the account has nothing yet
    # A removed connection: Plaid adds nothing more, so dates after its last transaction are outside too
    db.execute("""INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name)
                  VALUES ('p4', 3, '2026-07-02', 700, 'Fake Old'), ('p5', 3, '2026-07-06', 800, 'Fake Old')""")
    db.commit()
    p = prepare(db, OLD, CHECKING_FILE, overlap="outside")
    assert sorted(x.row.posted for x in p.new) == ["2026-07-01", "2026-07-08", "2026-07-09"]


def test_your_answers_are_kept_for_the_next_file_with_that_header(db):
    text = "Date,Description,Amount\n2026-04-01,FAKE BAKERY,3.00\n2026-04-02,FAKE BOOKS,15.00\n"
    p = prepare(db, CHECKING, text)
    assert p.needs == ["sign"]
    engine.apply(db, prepare(db, CHECKING, text, sign=-1))                       # positive amounts are money in here
    later = prepare(db, CHECKING, "Date,Description,Amount\n2026-05-01,FAKE SHOP,8.00\n")
    assert later.detection.source == "profile" and later.needs == [] and later.new[0].row.amount_cents == -800
    assert prepare(db, CARD, text).needs == ["sign"]                             # profiles are per account


def test_covered_spans():
    plaid, mine = True, False
    assert engine.covered_spans([], True) == []
    assert engine.covered_spans([("2026-03-01", plaid), ("2026-04-01", plaid)], True) == [("2026-03-01", None)]
    assert engine.covered_spans([("2026-03-01", plaid), ("2026-04-01", plaid)], False) == [("2026-03-01", "2026-04-01")]
    # two imports months apart don't cover the months between; one inside Plaid's span adds nothing
    assert engine.covered_spans([("2026-01-05", mine), ("2026-01-30", mine), ("2026-05-02", mine),
                                 ("2026-06-01", plaid), ("2026-06-10", mine)], True) == [
        ("2026-01-05", "2026-01-30"), ("2026-05-02", "2026-05-02"), ("2026-06-01", None)]
    assert engine.covered_spans([("2026-01-05", mine)], True, before="2026-03-01") == [("2026-03-01", None)]


def test_the_command_previews_asks_imports_lists_and_undoes(db, tmp_path, capsys):
    """`fintrack import` on a throwaway Sandbox data folder (the `settings` fixture behind `db`)."""
    from fintrack import cli

    db.execute("""INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name)
                  VALUES ('p3', 2, '2026-07-09', 2000, 'Fake Grocer')""")
    db.commit()
    path = tmp_path / "fake-download.csv"
    path.write_text(CHECKING_FILE)
    cli.main(["import", str(path), "--account", str(CHECKING)])
    out = capsys.readouterr().out
    assert "Overlap: the file has 2026-07-01 to 2026-07-09; the account already has transactions 2026-07-09 on." in out
    assert "Preview only" in out
    with pytest.raises(SystemExit):                                              # the overlap isn't answered yet
        cli.main(["import", str(path), "--account", str(CHECKING), "--apply"])
    assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    cli.main(["import-csv", str(path), "--account", str(CHECKING), "--overlap", "skip", "--apply"])   # the old name
    out = capsys.readouterr().out
    assert "Added 3 transactions (import 1; undo with --undo 1)" in out and "Backed up database" in out
    cli.main(["import", "--history"])
    assert "fake-download.csv  3 rows 2026-07-01 to 2026-07-09" in capsys.readouterr().out
    cli.main(["import", "--undo", "1", "--apply"])
    assert "Removed 3 transactions." in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE account_id = 2").fetchone()[0] == 1


def test_a_likely_duplicate_just_before_plaids_first_row_still_asks(db):
    """Plaid's first row (Jul 9) is the file's Jul 8 grocer: not on Plaid's dates, but still an overlap."""
    db.execute("""INSERT INTO transactions (plaid_transaction_id, account_id, date, amount_cents, name)
                  VALUES ('p3', 2, '2026-07-09', 2000, 'Fake Grocer')""")
    db.commit()
    text = CHECKING_FILE.replace("2026-07-09,FAKE BOOKS,-9.00,947.00\n", "")      # the file ends on Jul 8
    p = prepare(db, CHECKING, text)
    assert p.needs == ["overlap"] and (p.overlap.outside, p.overlap.skip, p.overlap.duplicates) == (2, 2, 1)
    p = prepare(db, CHECKING, text, overlap="outside")
    assert sorted(x.row.posted for x in p.new) == ["2026-07-01", "2026-07-05"]
    engine.apply(db, prepare(db, CHECKING, text, overlap="skip"))
    again = prepare(db, CHECKING, text)                                           # asks again; adds nothing unasked
    assert again.needs == ["overlap"] and again.new == []


# ---------------------------------------------------------------- the Import page (web/imports.py)

ORIGIN = {"origin": "https://localhost:8443"}


@pytest.fixture
def client(settings, db, cipher, gateway):
    from fastapi.testclient import TestClient

    from fintrack.crypto import TokenCipher
    from fintrack.web.app import create_app

    return TestClient(create_app(settings, gateway_factory=lambda s: gateway, cipher_factory=TokenCipher),
                      base_url="https://localhost:8443", follow_redirects=False)


def form_fields(html: str) -> list[tuple[str, str]]:
    """What the browser would post from a preview: hidden inputs, each select's chosen option, checked radios and
    boxes."""
    from html.parser import HTMLParser

    class Fields(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields, self.select, self.first = [], None, None

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "input" and a.get("name") and (a.get("type") == "hidden" or
                                                     (a.get("type") in ("radio", "checkbox") and "checked" in a)):
                self.fields.append((a["name"], a.get("value", "")))
            elif tag == "select":
                self.select, self.first, self.chosen = a["name"], None, False
            elif tag == "option" and self.select:
                if self.first is None:
                    self.first = a.get("value", "")
                if "selected" in a:
                    self.fields.append((self.select, a.get("value", "")))
                    self.chosen = True

        def handle_endtag(self, tag):
            if tag == "select" and self.select:
                if not self.chosen:
                    self.fields.append((self.select, self.first or ""))
                self.select = None

    parser = Fields()
    parser.feed(html)
    return parser.fields


def post(client, path, text, fields):
    """A multipart post like the form's (the test client takes repeated names as lists)."""
    data: dict[str, list[str]] = {}
    for k, v in (fields.items() if isinstance(fields, dict) else fields):
        data.setdefault(k, []).append(v)
    return client.post(path, data=data, files={"file": ("fake-download.csv", text.encode(), "text/csv")},
                       headers=ORIGIN)


def test_the_import_page_previews_asks_imports_and_undoes(client, db):
    page = client.get("/import?account=1").text
    assert 'name="file"' in page and '<option value="1" selected>Fake Bank · Fake account 1 ····0000</option>' in page
    first = post(client, "/import/preview", HEADER + ROWS, {"account": "1"}).text
    assert 'name="col_0"' in first and "overlaps the account" in first
    assert '<button type="submit" disabled>Import</button>' in first                # the overlap isn't answered
    fields = [f for f in form_fields(first) if f[0] != "overlap"] + [("overlap", "outside"), ("account", "1")]
    second = post(client, "/import/preview", HEADER + ROWS, fields).text
    assert "<strong>6 to add</strong>" in second and '<button type="submit" >Import</button>' in second
    assert "Dates already there" in second                                          # Jul 7: Plaid has it
    done = post(client, "/import/apply", HEADER + ROWS, form_fields(second) + [("account", "1")])
    assert done.status_code == 303 and "Added+6+transactions" in done.headers["location"]
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE account_id = 1").fetchone()[0] == 7
    page = client.get("/import").text
    assert "fake-download.csv" in page and "Remove 6 transactions" in page
    [batch] = engine.batches(db)
    assert client.post(f"/import/{batch['id']}/undo", headers=ORIGIN).status_code == 303
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE account_id = 1").fetchone()[0] == 1
    assert "Undone" in client.get("/import").text


def test_the_page_answers_questions_maps_bank_categories_and_fixes_columns(client, db):
    text = "Date,Description,Amount,Category\n2026-04-01,FAKE BAKERY,3.00,Bakeries\n2026-04-02,FAKE BOOKS,15.00,Books\n"
    first = post(client, "/import/preview", text, {"account": "2"}).text
    assert "Choose…" in first and "disabled>Import" in first                        # which sign is money out?
    fields = [f for f in form_fields(first) if f[0] != "money_out"] + [("money_out", "-1"), ("account", "2")]
    fields = [(k, "TRAVEL" if k == "cat_0" else v) for k, v in fields]               # Bakeries -> Travel
    second = post(client, "/import/preview", text, fields).text
    assert '<button type="submit" >Import</button>' in second and "+$3.00" in second   # money in, as chosen
    assert post(client, "/import/apply", text, form_fields(second) + [("account", "2")]).status_code == 303
    bakery = db.execute("SELECT category FROM transactions_effective WHERE name = 'FAKE BAKERY'").fetchone()[0]
    assert bakery == "TRAVEL"
    later = post(client, "/import/preview", text.replace("2026-04", "2026-05"), {"account": "2"}).text
    assert "Choose…" not in later and '<option value="TRAVEL" selected>' in later     # kept for this bank's files

    # Columns: the same role twice, or no amount, is said; the file's rows still show with their pickers.
    cols = [(k, v) for k, v in form_fields(first) if k.startswith(("col_", "cols", "previewed"))] + [("account", "2")]
    twice = post(client, "/import/preview", text, [(k, "date" if k == "col_1" else v) for k, v in cols]).text
    assert "Date is chosen for two columns." in twice and 'name="col_0"' in twice
    none = post(client, "/import/preview", text, [(k, "" if k == "col_2" else v) for k, v in cols]).text
    assert "which column is the amount" in none and 'name="col_2"' in none


def test_answers_for_another_file_start_over(client, db):
    first = post(client, "/import/preview", CHECKING_FILE, {"account": "2"}).text
    fields = [(k, "" if k.startswith("col_") else v) for k, v in form_fields(first)] + [("account", "2")]
    other = post(client, "/import/preview", CHECKING_FILE.replace("FAKE CAFE", "FAKE TEA"), fields).text
    assert "Choose the date column" not in other and "4 to add" in other           # the blank pickers were ignored


# ---------------------------------------------------------------- manual accounts take files (phase 4)

def manual_card(db, value="500.00", day="2026-03-31"):
    from fintrack import manual
    return manual.create_account(db, "Fake manual card", "credit", value, day)


CARD_FILE = """Date,Description,Amount
2026-03-10,FAKE BOOKS,20.00
2026-03-20,FAKE PAYMENT THANK YOU,-100.00
2026-04-02,FAKE CAFE,5.00
2026-04-05,FAKE SHOP,45.00
"""


def test_a_manual_credit_card_takes_a_file_and_its_balance_follows(db):
    from fintrack import manual
    card = manual_card(db)                                       # owed $500 at the end of Mar 31
    acct = db.execute("SELECT * FROM accounts WHERE id = ?", (card,)).fetchone()
    assert (acct["type"], acct["classification"]) == ("credit", "liability") and manual.takes_transactions(acct)
    assert card in [a["id"] for a in engine.importable_accounts(db)]
    p = prepare(db, card, CARD_FILE, sign=1)
    assert p.overlap is None and len(p.new) == 4                 # a value isn't a transaction: nothing overlaps
    batch, added = engine.apply(db, p)
    assert added == 4
    balance = db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (card,)).fetchone()[0]
    assert balance == 50000 + 500 + 4500                         # $500 + purchases after Mar 31
    values = [(r["date"], r["value_cents"]) for r in manual.valuations(db, card)]
    days = ["2026-03-09", "2026-03-15", "2026-03-31", "2026-04-03"]
    sign_values = [manual.value_on(d, sorted(values), [("2026-03-10", 2000), ("2026-03-20", -10000),
                                                         ("2026-04-02", 500), ("2026-04-05", 4500)], 1) for d in days]
    assert sign_values == [None, 60000, 50000, 50500]            # worked back before Mar 31 (before the payment)
    # the purchases count as spending, and on the Credit cards page's card list
    assert db.execute("SELECT COUNT(*) FROM transactions_effective WHERE account_id = ? AND kind = 'expense'",
                      (card,)).fetchone()[0] == 3
    assert engine.undo(db, batch) == 4
    assert db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (card,)).fetchone()[0] == 50000


def test_a_running_balance_gives_a_manual_account_its_values_and_undo_takes_them_back(db):
    from fintrack import manual
    card = manual_card(db, "0", "2026-01-01")
    text = """Date,Description,Amount,Balance
2026-02-03,FAKE CAFE,5.00,140.00
2026-02-03,FAKE SHOP,10.00,135.00
2026-02-01,FAKE STORE,25.00,125.00
"""
    batch, _ = engine.apply(db, prepare(db, card, text))
    values = {r["date"]: r["value_cents"] for r in manual.valuations(db, card)}
    assert values == {"2026-01-01": 0, "2026-02-01": 12500, "2026-02-03": 14000}   # each day's closing balance
    assert db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (card,)).fetchone()[0] == 14000
    engine.undo(db, batch)
    assert {r["date"] for r in manual.valuations(db, card)} == {"2026-01-01"}


def test_closing_balances_turn_round_a_card_shown_as_negative():
    rows = [layout.Row(2, "2026-02-05", "", "B", 500, balance_cents=-3000),
            layout.Row(3, "2026-02-01", "", "A", 2500, balance_cents=-2500)]           # newest first
    assert engine.closing_balances(rows, "liability") == {"2026-02-01": 2500, "2026-02-05": 3000}
    assert engine.closing_balances(rows, "asset") == {"2026-02-01": -2500, "2026-02-05": -3000}


def test_manual_accounts_that_hold_no_transactions_cant_take_files(db):
    from fintrack import manual
    house = manual.create_account(db, "Fake house", "property", "100000", "2026-01-01")
    with pytest.raises(engine.ImportFileError, match="only bank accounts, cards and loans"):
        prepare(db, house, CARD_FILE)
    assert house not in [a["id"] for a in engine.importable_accounts(db)]


def test_a_transaction_you_add_moves_a_manual_cards_balance(db):
    from fintrack import manual_transactions
    card = manual_card(db, "100", "2026-01-01")
    tid = manual_transactions.add(db, account_id=card, day="2026-01-05", description="Fake lunch", amount_text="12.50",
                                  direction="out", category_key="FOOD_AND_DRINK")
    assert db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (card,)).fetchone()[0] == 11250
    manual_transactions.delete(db, tid)
    assert db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (card,)).fetchone()[0] == 10000


def test_a_manual_cards_page_lists_its_transactions_and_links_to_import(client, db):
    card = manual_card(db)
    engine.apply(db, prepare(db, card, CARD_FILE, sign=1))
    page = client.get(f"/accounts/{card}").text
    assert f'href="/import?account={card}">Import a file' in page and "FAKE SHOP" in page and 'id="transactions"' in page
    assert "Credit card" in client.get("/manual/new").text
    assert f'<option value="{card}" >Manual · Fake manual card</option>' in client.get("/import").text


# ---------------------------------------------------------------- whose card (authorized users)

TWO_CARDS = """Date,Description,Amount,Card
2026-03-02,FAKE BOOKS,20.00,4321
2026-03-03,FAKE CAFE,5.00,4321
2026-03-04,FAKE TOYS,9.00,8765
"""


def au_rows(db, account_id):
    from fintrack import authorized_users
    return db.execute(f"SELECT COUNT(*) FROM transactions_effective WHERE account_id = ? AND {authorized_users.AU_SQL}",
                      (account_id,)).fetchone()[0]


def test_a_manual_cards_only_card_in_the_file_is_its_own(db):
    """User's report 2026-11-13: a manual card has no number, so every row read as an authorized user's."""
    card = manual_card(db)
    p = prepare(db, card, TWO_CARDS.replace("8765", "4321"), sign=1)
    assert p.primary_card == "4321" and p.needs == [] and p.primary_change
    engine.apply(db, p)
    assert db.execute("SELECT mask FROM accounts WHERE id = ?", (card,)).fetchone()[0] == "4321"
    assert au_rows(db, card) == 0


def test_two_cards_ask_which_is_the_primary(db):
    card = manual_card(db)
    p = prepare(db, card, TWO_CARDS, sign=1)
    assert p.needs == ["primary_card"] and p.cards == {"4321": 2, "8765": 1}
    engine.apply(db, prepare(db, card, TWO_CARDS, sign=1, primary_card="4321"))
    assert au_rows(db, card) == 1                                                  # the toys, on the other card
    assert prepare(db, card, TWO_CARDS, sign=1).primary_card == "4321"             # its number now


def test_a_connected_cards_number_stays_plaids(db):
    p = prepare(db, CHECKING, TWO_CARDS, sign=1)                                   # its mask 0000 isn't in the file
    assert p.needs == ["primary_card"]
    engine.apply(db, prepare(db, CHECKING, TWO_CARDS, sign=1, primary_card="4321"))
    assert db.execute("SELECT mask FROM accounts WHERE id = ?", (CHECKING,)).fetchone()[0] == "0000"
    owners = dict(db.execute("SELECT name, account_owner FROM transactions WHERE account_id = ?", (CHECKING,)).fetchall())
    assert owners == {"FAKE BOOKS": None, "FAKE CAFE": None, "FAKE TOYS": "8765"} and au_rows(db, CHECKING) == 1


def test_a_manual_accounts_last_4_fixes_rows_already_imported(client, db):
    card = manual_card(db)
    engine.apply(db, prepare(db, card, TWO_CARDS, sign=1, primary_card=""))        # "none of these": all AU
    assert au_rows(db, card) == 3
    r = client.post(f"/accounts/{card}/mask", data={"mask": "4321"}, headers=ORIGIN)
    assert r.status_code == 303 and au_rows(db, card) == 1
    assert 'name="mask" value="4321"' in client.get(f"/accounts/{card}").text
    assert "Enter the last 4" in client.post(f"/accounts/{card}/mask", data={"mask": "12345"}, headers=ORIGIN,
                                             follow_redirects=True).text
    # re-importing the file can set it too, with nothing new to add
    client.post(f"/accounts/{card}/mask", data={"mask": ""}, headers=ORIGIN)
    again = post(client, "/import/preview", TWO_CARDS, {"account": str(card)}).text
    fields = [f for f in form_fields(again) if f[0] not in ("primary_card", "money_out")]
    redo = post(client, "/import/preview", TWO_CARDS, fields + [("account", str(card)), ("primary_card", "4321"),
                                                                ("money_out", "1")]).text
    assert "<strong>0 to add</strong>" in redo and '<button type="submit" >Import</button>' in redo


# ---------------------------------------------------------------- more file types (phase 3): Excel, OFX, QIF

def make_xlsx(sheets: dict[str, str], shared: list[str], *, styles: str | None = None, extra: dict | None = None) -> bytes:
    """A minimal .xlsx built here (no binary test files): sheets {name: <sheetData> rows xml}, shared strings."""
    import io
    import zipfile

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel_ns}><sheets>' + "".join(
            f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(sheets)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
                       f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
                   + "</Relationships>")
        for i, rows in enumerate(sheets.values()):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f"<worksheet {ns}><sheetData>{rows}</sheetData></worksheet>")
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(
            s if s.startswith("<si>") else f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/styles.xml", styles or (
            f'<styleSheet {ns}><numFmts><numFmt numFmtId="164" formatCode="mm/dd/yyyy;@"/></numFmts>'
            '<cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="4"/></cellXfs></styleSheet>'))
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return buf.getvalue()


SHARED = ["Date", "Description", "Amount", "FAKE GROCERY MART",
          "<si><r><t>FAKE </t></r><r><t>PAYMENT</t></r></si>", "Account summary"]
DATA_SHEET = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
              '<row r="2"><c r="A2" s="1"><v>46293</v></c><c r="B2" t="s"><v>3</v></c><c r="C2" s="3"><v>-84.120000000000005</v></c></row>'
              '<row r="4"><c r="A4" s="2"><v>46290</v></c><c r="B4" t="s"><v>4</v></c><c r="C4"><v>350</v></c></row>'
              '<row r="5"><c r="A5" s="2"><v>46280</v></c><c r="C5"><v>-5.5</v></c><c r="B5" t="inlineStr"><is><t>FAKE CAFE</t></is></c></row>')


def test_an_excel_file_reads_its_transactions_sheet():
    data = make_xlsx({"Summary": '<row r="1"><c r="A1" t="s"><v>5</v></c></row>', "Activity": DATA_SHEET}, SHARED)
    table = readers.read_table(data, "fake.xlsx")
    assert table == [["Date", "Description", "Amount"],
                     ["2026-09-28", "FAKE GROCERY MART", "-84.12"],             # a day count with a date format
                     ["2026-09-25", "FAKE PAYMENT", "350"],                     # rich text runs, a custom date format
                     ["2026-09-15", "FAKE CAFE", "-5.5"]]                       # cells out of order, inline text
    d, rows, _ = detect("\n".join(",".join(r) for r in table), "liability")
    assert [r.amount_cents for r in rows] == [8412, -35000, 550]               # a card: purchases are money out


def test_excel_refusals():
    bad = make_xlsx({"S": DATA_SHEET}, SHARED, extra={"xl/x.xml": "x"})
    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(bad)) as src, zipfile.ZipFile(buf, "w") as z:
        for item in src.infolist():
            body = src.read(item.filename)
            if item.filename == "xl/sharedStrings.xml":
                body = b'<!DOCTYPE x [<!ENTITY a "aaaa">]>' + body
            z.writestr(item.filename, body)
    with pytest.raises(readers.ReadError, match="DOCTYPE"):
        readers.read_table(buf.getvalue(), "fake.xlsx")
    bomb = make_xlsx({"S": DATA_SHEET}, SHARED, extra={"xl/media/big.bin": "0" * (61 * 1024 * 1024)})
    with pytest.raises(readers.ReadError, match="too big"):
        readers.read_table(bomb, "fake.xlsx")
    with pytest.raises(readers.ReadError, match="xls"):
        readers.read_table(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "fake.xls")


OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
CHARSET:1252

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS></SONRS></SIGNONMSGSRSV1>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CURDEF>USD<CCACCTFROM><ACCTID>FAKE0000</CCACCTFROM>
<BANKTRANLIST><DTSTART>20260901<DTEND>20260930
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260929120000.000[-5:EST]<DTUSER>20260928<TRNAMT>-84.12<FITID>1<NAME>FAKE GROCERY &amp; MORE</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260926<TRNAMT>350.00<FITID>2<NAME>FAKE PAYMENT THANK YOU</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260915<TRNAMT>-41.30<FITID>3<MEMO>FAKE GAS 0042</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>-1234.56<DTASOF>20260930</LEDGERBAL></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>
</OFX>
"""


def test_ofx_reads_with_the_sign_settled(db):
    table = readers.read_table(OFX_SGML.encode("cp1252"), "fake.qfx")
    assert table[1] == ["2026-09-29", "2026-09-28", "FAKE GROCERY & MORE", "84.12", ""]
    assert table[3] == ["2026-09-15", "", "FAKE GAS 0042", "41.30", ""]          # no NAME: the memo
    card = manual_card(db, "0", "2026-09-01")
    p = engine.prepare(db, card, OFX_SGML.encode(), "fake.qbo")
    assert p.needs == [] and p.detection.sign_source == "columns"
    assert sorted(x.row.amount_cents for x in p.new) == [-35000, 4130, 8412]
    xml = OFX_SGML.replace("<NAME>FAKE PAYMENT THANK YOU</STMTTRN>", "<NAME>FAKE PAYMENT THANK YOU</NAME></STMTTRN>")
    assert readers.read_table(xml.encode(), "fake.ofx")[2][2] == "FAKE PAYMENT THANK YOU"     # OFX 2 (closed tags)
    two = OFX_SGML.replace("</BANKTRANLIST>", "</BANKTRANLIST><CCACCTFROM><ACCTID>FAKE9999</CCACCTFROM>")
    with pytest.raises(readers.ReadError, match="2 accounts"):
        readers.read_table(two.encode(), "fake.ofx")
    with pytest.raises(readers.ReadError, match="empty"):                          # an investment statement, empty
        readers.read_table(b"<OFX><INVSTMTMSGSRSV1><INVSTMTRS></INVSTMTRS></INVSTMTMSGSRSV1></OFX>", "fake.ofx")


QIF = """!Type:CCard
D9/28'26
T-84.12
PFAKE GROCERY
LGroceries
^
D9/26'26
T350.00
PFAKE PAYMENT
^
D9/ 5'26
U-1,005.00
MFAKE FURNITURE
^
"""


def test_qif_reads_with_the_sign_settled(db):
    table = readers.read_table(QIF.encode(), "fake.qif")
    assert table[0] == ["Posted Date", "Transaction Date", "Description", "Debit", "Credit", "Category"]
    assert table[1] == ["9/28/26", "", "FAKE GROCERY", "84.12", "", "Groceries"]
    assert table[3] == ["9/5/26", "", "FAKE FURNITURE", "1005.00", "", ""]
    card = manual_card(db, "0", "2026-09-01")
    p = engine.prepare(db, card, QIF.encode(), "fake.qif")
    assert p.needs == [] and [x.row.posted for x in p.new] == ["2026-09-05", "2026-09-26", "2026-09-28"]
    assert [x.detailed for x in p.new if x.row.description == "FAKE GROCERY"] == ["FOOD_AND_DRINK_GROCERIES"]
    with pytest.raises(readers.ReadError, match="investment"):
        readers.read_table(b"!Type:Invst\nD9/28'26\n^\n", "fake.qif")


# ---------------------------------------------------------------- investment files (phase 5)

def pretax(db, start="2026-01-01", end="2026-12-31"):
    """{month: contributions seen only inside investment accounts}: the Savings page's pre-tax income (contributions.py)."""
    from fintrack import contributions
    return {f.month: f.outside_into for f in contributions.between(db, date.fromisoformat(start), date.fromisoformat(end),
                                                                   False, to_bank=False) if f.outside_into}


def manual_investment(db, name="Fake 401k", subtype="401k"):
    from fintrack import manual
    account = manual.create_account(db, name, "investment", "0", "2026-01-01")
    db.execute("UPDATE accounts SET subtype = ? WHERE id = ?", (subtype, account))
    db.commit()
    return account


RETIREMENT = """Date,Action,Symbol,Description,Quantity,Price,Amount
01/15/2026,Contribution,FAKEX,FAKE TARGET FUND,10,50.00,500.00
01/15/2026,Employer Match,FAKEX,FAKE TARGET FUND,2,50.00,100.00
02/13/2026,Contribution,FAKEX,FAKE TARGET FUND,9.8,51.02,500.00
03/02/2026,Dividend,FAKEX,FAKE TARGET FUND,,,12.00
03/02/2026,Reinvestment,FAKEX,FAKE TARGET FUND,0.2,60.00,-12.00
03/10/2026,Fee,FAKEX,ADMIN FEE,0.1,50.00,-5.00
"""


def quantity_on(db, account, ticker, day):
    from fintrack import manual_positions
    line = db.execute("SELECT id FROM manual_positions WHERE account_id = ? AND ticker = ?", (account, ticker)).fetchone()
    return manual_positions._amount_on(db, line[0], day)["quantity"] if line else None


def test_retirement_activity_into_a_manual_account(db):
    from fintrack import manual_positions
    account = manual_investment(db)
    p = prepare(db, account, RETIREMENT)
    assert p.kind == "activity" and p.needs == [] and len(p.new) == 6
    kinds = {k: v[0] for k, v in p.action_keys.items()}
    assert kinds == {"CONTRIBUTION": "contribution", "EMPLOYER": "contribution", "DIVIDEND": "income",
                     "REINVEST": "reinvest", "FEE": "fee"}
    assert p.unpaired == 0 and all(x.paired for x in p.new if x.kind in ("income", "reinvest"))
    batch, added = engine.apply(db, p)
    assert added == 6
    assert quantity_on(db, account, "FAKEX", "2026-03-10") == pytest.approx(21.9)      # 10 + 2 + 9.8 + 0.2 - 0.1
    assert not db.execute("SELECT 1 FROM manual_positions WHERE account_id = ? AND ticker IS NULL AND name = 'Cash'"
                          " AND import_batch_id IS NOT NULL", (account,)).fetchone()  # the dividend bought shares
    # money paid in counts on the Savings page (its contribution rows, as a connected 401(k)'s); the reinvested
    # dividend and the fee don't
    assert pretax(db) == {"2026-01": 60000, "2026-02": 50000}
    assert prepare(db, account, RETIREMENT).new == []                                  # the same rows again: nothing
    assert engine.undo(db, batch) == 6
    assert quantity_on(db, account, "FAKEX", "2026-03-10") is None                     # its line went with it
    assert pretax(db) == {}


def test_selling_what_the_file_never_bought_stops_the_import(db):
    account = manual_investment(db, "Fake brokerage", "brokerage")
    text = "Date,Action,Symbol,Quantity,Price,Amount\n2026-04-01,Sold,FAKEQ,5,10.00,50.00\n2026-04-02,Bought,FAKEQ,1,10,-10\n"
    p = prepare(db, account, text)
    assert p.needs == ["problems"] and "FAKEQ shares would go below 0" in p.problems[0]
    with pytest.raises(engine.ImportFileError):
        engine.apply(db, p)


HOLDINGS = """Fake Brokerage
Positions as of 09/30/2026
Symbol,Description,Quantity,Last Price,Current Value
FAKEX,FAKE TARGET FUND,21.9,61.00,"1,335.90"
FAKEY,FAKE BOND FUND,10,9.50,95.00
SPAXX**,HELD IN MONEY MARKET,,,250.00
Account Total,,,,"1,680.90"
"""


def test_a_holdings_statement_sets_a_manual_accounts_lines(db):
    account = manual_investment(db, "Fake IRA", "ira")
    p = prepare(db, account, HOLDINGS)
    assert p.kind == "positions" and p.as_of == "2026-09-30" and p.needs == []
    assert [(x.line, x.row.quantity) for x in p.new] == [("FAKEX", Decimal("21.9")), ("FAKEY", Decimal("10")), ("", None)]
    batch, _ = engine.apply(db, p)
    assert quantity_on(db, account, "FAKEY", "2026-09-30") == 10
    assert db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (account,)).fetchone()[0] == 168090
    assert prepare(db, account, HOLDINGS).new == []                                     # the same balances: nothing
    later = prepare(db, account, HOLDINGS.replace("09/30/2026", "10/31/2026").replace(
        "FAKEY,FAKE BOND FUND,10,9.50,95.00\n", ""))
    assert later.zeroed == ["FAKEY"]                                                    # sold since: set to 0
    engine.apply(db, later)
    assert quantity_on(db, account, "FAKEY", "2026-10-31") == 0
    engine.undo(db, batch + 1)
    assert quantity_on(db, account, "FAKEY", "2026-10-31") == 10


def test_a_statement_without_its_date_asks_for_it(db):
    account = manual_investment(db, "Fake IRA", "ira")
    text = HOLDINGS.replace("Positions as of 09/30/2026\n", "")
    assert prepare(db, account, text).needs == ["as_of"]
    assert prepare(db, account, text, as_of="2026-09-30").needs == []


def test_activity_into_a_connected_investment_account(db):
    db.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification)"
               " VALUES (4, 1, 'acc-4', 'Fake brokerage', '0000', 'investment', 'asset')")
    db.execute("""INSERT INTO investment_transactions (plaid_investment_transaction_id, account_id, date, name, type,
                  subtype, amount_cents) VALUES ('pi1', 4, '2026-03-02', 'DIVIDEND FAKEX', 'cash', 'dividend', -1200)""")
    db.commit()
    p = prepare(db, 4, RETIREMENT)
    assert p.needs == ["overlap"] and p.overlap.duplicates == 1                         # Plaid's dividend
    batch, added = engine.apply(db, prepare(db, 4, RETIREMENT, overlap="skip"))
    assert added == 5
    rows = db.execute("SELECT * FROM investment_transactions WHERE import_batch_id = ?", (batch,)).fetchall()
    reinvest = db.execute("SELECT * FROM investment_transactions WHERE import_batch_id = ? AND type = 'buy'"
                          " AND date = '2026-03-02'", (batch,)).fetchone()
    assert (reinvest["amount_cents"], reinvest["quantity"]) == (1200, 0.2)              # Plaid's sign: cash out
    assert {(r["type"], r["subtype"]) for r in rows} >= {("cash", "contribution"), ("buy", "buy"), ("fee", "fee")}
    with pytest.raises(engine.ImportFileError, match="manual investment account"):
        prepare(db, 4, HOLDINGS)
    with pytest.raises(engine.ImportFileError, match="investment account"):
        prepare(db, CHECKING, RETIREMENT)
    engine.undo(db, batch)
    assert db.execute("SELECT COUNT(*) FROM investment_transactions").fetchone()[0] == 1


OFX_INV = """<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS><DTASOF>20260930<CURDEF>USD
<INVACCTFROM><BROKERID>fake.example<ACCTID>FAKE0001</INVACCTFROM>
<INVTRANLIST><DTSTART>20260901<DTEND>20260930
<BUYMF><INVBUY><INVTRAN><FITID>1<DTTRADE>20260905</INVTRAN><SECID><UNIQUEID>000000001<UNIQUEIDTYPE>CUSIP</SECID>
<UNITS>2<UNITPRICE>50<TOTAL>-100.00<SUBACCTSEC>CASH<SUBACCTFUND>CASH</INVBUY><BUYTYPE>BUY</BUYMF>
<INCOME><INVTRAN><FITID>2<DTTRADE>20260915</INVTRAN><SECID><UNIQUEID>000000001<UNIQUEIDTYPE>CUSIP</SECID>
<INCOMETYPE>DIV<TOTAL>3.25<SUBACCTSEC>CASH<SUBACCTFUND>CASH</INCOME>
<INVBANKTRAN><STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260901<TRNAMT>500.00<FITID>3<NAME>FAKE DEPOSIT</STMTTRN>
<SUBACCTFUND>CASH</INVBANKTRAN>
</INVTRANLIST>
<INVPOSLIST><POSMF><INVPOS><SECID><UNIQUEID>000000001<UNIQUEIDTYPE>CUSIP</SECID><HELDINACCT>CASH<POSTYPE>LONG
<UNITS>2<UNITPRICE>51<MKTVAL>102<DTPRICEASOF>20260930</INVPOS></POSMF></INVPOSLIST>
<INVBAL><AVAILCASH>403.25</INVBAL></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1>
<SECLISTMSGSRSV1><SECLIST><MFINFO><SECINFO><SECID><UNIQUEID>000000001<UNIQUEIDTYPE>CUSIP</SECID>
<SECNAME>FAKE INDEX FUND<TICKER>FAKEX</SECINFO></MFINFO></SECLIST></SECLISTMSGSRSV1></OFX>
"""


def test_ofx_investment_statements(db):
    table = readers.read_table(OFX_INV.encode(), "fake.qfx")
    assert table[0] == ["Date", "Action", "Symbol", "Description", "Quantity", "Price", "Amount"]
    assert table[1] == ["2026-09-05", "Buy", "FAKEX", "FAKE INDEX FUND", "2", "50", "-100.00"]
    assert table[3] == ["2026-09-01", "Deposit", "", "FAKE DEPOSIT", "", "", "500.00"]
    account = manual_investment(db, "Fake brokerage", "brokerage")
    p = engine.prepare(db, account, OFX_INV.encode(), "fake.qfx")
    assert p.kind == "activity" and p.needs == []                                       # deposit, then the buy
    engine.apply(db, p)
    assert quantity_on(db, account, "FAKEX", "2026-09-30") == 2
    positions_only = re.sub(r"<INVTRANLIST>.*?</INVTRANLIST>", "", OFX_INV, flags=re.S)
    held = readers.read_table(positions_only.encode(), "fake.qfx")
    assert held[0] == ["As of", "2026-09-30"] and held[2] == ["FAKEX", "FAKE INDEX FUND", "2", "51", "102"]
    assert held[3] == ["", "Cash", "", "", "403.25"]


def test_the_page_imports_investment_activity_and_holdings(client, db):
    account = manual_investment(db)
    first = post(client, "/import/preview", RETIREMENT, {"account": str(account)}).text
    assert '<option value="activity" selected>Investment activity</option>' in first and "Employer Match" in first
    fields = form_fields(first) + [("account", str(account))]
    done = post(client, "/import/apply", RETIREMENT, fields)
    assert done.status_code == 303 and "Added+6" in done.headers["location"]
    assert "activity" in client.get("/import").text
    ira = manual_investment(db, "Fake IRA", "ira")
    held = post(client, "/import/preview", HOLDINGS.replace("Positions as of 09/30/2026\n", ""), {"account": str(ira)}).text
    assert 'name="as_of"' in held and "import-ask" in held and "disabled>Import" in held   # the date isn't in the file
    fields = [f for f in form_fields(held) if f[0] != "as_of"] + [("account", str(ira)), ("as_of", "2026-09-30")]
    ready = post(client, "/import/preview", HOLDINGS.replace("Positions as of 09/30/2026\n", ""), fields).text
    assert '<button type="submit" >Import</button>' in ready and "$1,680.90 as of Sep 30" in ready


def test_imported_changes_show_what_they_did_and_arent_edited_in_place(client, db):
    account = manual_investment(db)
    engine.apply(db, prepare(db, account, RETIREMENT))
    page = client.get(f"/accounts/{account}").text
    balances = page.split("<h2>Balances</h2>")[1].split("</section>")[0]
    assert "From activity" not in balances and "<td>Buy</td>" not in balances        # activity isn't repeated there
    assert "<h2>Transactions</h2>" in page and "Add activity" in page and "ADMIN FEE" in page   # its activity, listed
    assert 'name="mask"' not in page                                                   # no Last 4 on an investment account


# ---------------------------------------------------------------- funds tracked through a proxy ticker

PROXY_ACTIVITY = """Date,Action,Symbol,Description,Quantity,Price,Amount
01/15/2026,Contribution,SSSPX,STATE STREET S&P 500 INDEX,40,25.00,1000.00
02/13/2026,Contribution,SSSPX,STATE STREET S&P 500 INDEX,39.2,25.51,1000.00
"""


def proxy_account(db):
    from fintrack import manual_positions
    account = manual_investment(db)
    db.executemany("INSERT INTO ticker_prices (ticker, price_date, price, source) VALUES ('SPY', ?, ?, 'alpaca')",
                   [("2026-01-01", 500.0), ("2026-02-13", 510.0), ("2026-03-01", 520.0)])
    db.commit()
    manual_positions.add_line(db, account, "SPY", "State Street S&P 500 Index", "fund", "10", "2026-01-01")
    return account


def test_a_fund_tracked_through_a_proxy_moves_by_dollars(db):
    from fintrack import manual_positions
    account = proxy_account(db)
    p = prepare(db, account, PROXY_ACTIVITY)
    target = p.holding_keys["SSSPX"]["target"]
    assert (target.ticker, target.by_dollars) == ("SPY", True)                        # found by its name
    engine.apply(db, p)
    assert quantity_on(db, account, "SPY", "2026-01-15") == pytest.approx(12)          # 10 + $1,000 / $500
    assert quantity_on(db, account, "SPY", "2026-02-13") == pytest.approx(12 + 1000 / 510)
    assert not db.execute("SELECT 1 FROM ticker_prices WHERE price IN (25, 25.51)").fetchone()   # not SPY's prices
    assert not db.execute("SELECT 1 FROM manual_positions WHERE ticker = 'SSSPX'").fetchone()      # no second line
    assert pretax(db) == {"2026-01": 100000, "2026-02": 100000}


def test_a_statement_sets_a_proxy_line_from_the_funds_value(db):
    account = proxy_account(db)
    text = """Positions as of 03/01/2026
Symbol,Description,Quantity,Price,Value
SSSPX,STATE STREET S&P 500 INDEX,249.6,25.00,"6,240.00"
SSBND,STATE STREET SMALL CAP,40,26.00,"1,040.00"
"""
    p = prepare(db, account, text, holdings={"SSBND": "SPY"})                         # two funds through SPY
    assert {k: v["target"].ticker for k, v in p.holding_keys.items()} == {"SSSPX": "SPY", "SSBND": "SPY"}
    engine.apply(db, p)
    assert quantity_on(db, account, "SPY", "2026-03-01") == pytest.approx(7280 / 520)  # their value at SPY's price
    again = prepare(db, account, text, holdings={"SSBND": "SPY"})
    assert again.new == [] and again.count("already") == 2                             # kept for next time too


def test_a_fund_given_its_own_line_keeps_its_units_and_price(db):
    account = proxy_account(db)
    engine.apply(db, prepare(db, account, PROXY_ACTIVITY, holdings={"SSSPX": "new"}))
    assert quantity_on(db, account, "SSSPX", "2026-02-13") == pytest.approx(79.2)
    assert quantity_on(db, account, "SPY", "2026-02-13") == 10
    assert prepare(db, account, PROXY_ACTIVITY).holding_keys["SSSPX"]["target"].ticker == "SSSPX"   # remembered


def test_the_page_picks_each_funds_line(client, db):
    account = proxy_account(db)
    first = post(client, "/import/preview", PROXY_ACTIVITY, {"account": str(account)}).text
    spy = db.execute("SELECT id FROM manual_positions WHERE ticker = 'SPY'").fetchone()[0]
    assert 'name="holdname_0" value="SSSPX"' in first
    assert f'<option value="{spy}" selected>SPY · State Street S&amp;P 500 Ind · by dollars</option>' in first
    fields = [(k, "new" if k == "hold_0" else v) for k, v in form_fields(first)] + [("account", str(account))]
    assert post(client, "/import/apply", PROXY_ACTIVITY, fields).status_code == 303
    assert quantity_on(db, account, "SSSPX", "2026-02-13") == pytest.approx(79.2)       # its own line, as chosen


# ---------------------------------------------------------------- reinvested dividends (2026-11-13)

NEWEST_FIRST = """Date,Action,Symbol,Description,Quantity,Price,Amount
03/16/2026,Reinvest Shares,FAKEQ,FAKE EQUITY FUND,0.5,24.00,-12.00
03/16/2026,Qualified Dividend,FAKEQ,FAKE EQUITY FUND,,,12.00
03/15/2026,Bought,FAKEQ,FAKE EQUITY FUND,10,20.00,-200.00
03/15/2026,Deposit,,FAKE DEPOSIT,,,200.00
"""


def test_a_reinvestment_listed_above_its_dividend_is_fine(db):
    """Fix 1: each day's closing amounts are checked, not each row (a newest-first file lists the reinvestment first)."""
    account = manual_investment(db, "Fake brokerage", "brokerage")
    p = prepare(db, account, NEWEST_FIRST)
    assert p.problems == [] and p.unpaired == 0
    engine.apply(db, p)
    assert quantity_on(db, account, "FAKEQ", "2026-03-16") == pytest.approx(10.5)


def test_a_reinvestment_without_its_dividend_gets_one_added(db):
    """Fix 2: the income side is added (the user's choice: dividend = income, reinvestment = a buy)."""
    account = manual_investment(db, "Fake brokerage", "brokerage")
    alone = NEWEST_FIRST.replace("03/16/2026,Qualified Dividend,FAKEQ,FAKE EQUITY FUND,,,12.00\n", "")
    p = prepare(db, account, alone)
    assert p.unpaired == 1 and p.problems == []
    [added] = [x for x in p.new if x.added]
    assert (added.kind, added.row.posted, added.row.cash_cents, added.row.symbol) == ("income", "2026-03-16", 1200, "FAKEQ")
    refused = prepare(db, account, alone, add_dividends=False)                      # you said no: cash goes below 0
    assert refused.unpaired == 1 and "would go below 0" in refused.problems[0]
    batch, added_rows = engine.apply(db, prepare(db, account, alone, add_dividends=True))
    assert added_rows == 4                                                          # the file's 3 + the added dividend
    again = prepare(db, account, alone)
    assert again.new == []                                                          # found again by its id
    engine.undo(db, batch)
    assert quantity_on(db, account, "FAKEQ", "2026-03-16") is None


def test_on_a_connected_account_the_pair_is_a_dividend_and_a_buy(db):
    db.execute("INSERT INTO accounts (id, item_id, plaid_account_id, name, mask, type, classification)"
               " VALUES (4, 1, 'acc-4', 'Fake brokerage', '0000', 'investment', 'asset')")
    db.commit()
    alone = NEWEST_FIRST.replace("03/16/2026,Qualified Dividend,FAKEQ,FAKE EQUITY FUND,,,12.00\n", "")
    batch, _ = engine.apply(db, prepare(db, 4, alone))
    rows = {(r["type"], r["subtype"]): r for r in db.execute(
        "SELECT * FROM investment_transactions WHERE import_batch_id = ? AND date = '2026-03-16'", (batch,))}
    assert rows[("cash", "dividend")]["amount_cents"] == -1200                      # income: cash in
    assert rows[("buy", "buy")]["amount_cents"] == 1200                             # the reinvestment: a buy
    from fintrack import investment_activity
    keys = investment_activity.category_keys(db)
    sql, params = investment_activity.as_transactions(db)
    cats = sorted(r["category"] for r in db.execute(f"SELECT * FROM ({sql}) WHERE date = '2026-03-16'", params))
    assert cats == sorted([keys["dividend"], keys["buy"]])                          # Investment income / Investments


def test_the_page_offers_the_added_dividends(client, db):
    account = manual_investment(db, "Fake brokerage", "brokerage")
    alone = NEWEST_FIRST.replace("03/16/2026,Qualified Dividend,FAKEQ,FAKE EQUITY FUND,,,12.00\n", "")
    page = post(client, "/import/preview", alone, {"account": str(account)}).text
    assert 'name="add_dividends" value="1" checked' in page and "1 reinvestment without one" in page
    fields = [f for f in form_fields(page) if f[0] != "add_dividends"] + [("account", str(account))]   # untick it
    off = post(client, "/import/preview", alone, fields).text
    assert 'name="add_dividends" value="1" >' in off and "would go below 0" in off


# ---------------------------------------------------------------- manual and connected alike (docs/parity-plan.md, phase 1)

def test_an_imported_transaction_can_be_deleted_and_stays_deleted(client, db):
    card = manual_card(db)
    engine.apply(db, prepare(db, card, CARD_FILE, sign=1))
    shop = db.execute("SELECT id FROM transactions WHERE name = 'FAKE SHOP'").fetchone()[0]
    panel = client.get(f"/transactions/{shop}/edit").text
    assert "Imported from a file: FAKE SHOP" in panel and f'hx-post="/transactions/{shop}/delete"' in panel
    assert client.post(f"/transactions/{shop}/delete", headers=ORIGIN).status_code == 200
    assert not db.execute("SELECT 1 FROM transactions WHERE id = ?", (shop,)).fetchone()
    assert db.execute("SELECT current_balance_cents FROM accounts WHERE id = ?", (card,)).fetchone()[0] == 50000 + 500
    again = prepare(db, card, CARD_FILE, sign=1)
    assert [x.row.description for x in again.new] == ["FAKE SHOP"]                 # a re-import brings it back
    plaid = db.execute("SELECT id FROM transactions WHERE plaid_transaction_id = 'p1'").fetchone()[0]
    assert client.post(f"/transactions/{plaid}/delete", headers=ORIGIN).status_code == 400   # the bank's own: no


def test_manual_checking_counts_in_cash_flow_and_can_pay_cards(db):
    from fintrack import cards, forecast, manual
    db.execute("UPDATE accounts SET is_hidden = 1 WHERE type = 'depository'")      # only the manual one left
    db.commit()
    checking = manual.create_account(db, "Fake manual checking", "cash", "1000", "2026-01-01")
    assert checking in [f.account_id for f in forecast.cash_flow(db).accounts]
    assert cards.automatic_payer(db)["id"] == checking


# ---------------------------------------------------------------- a manual card's statement (user's decision 2026-11-14)

def test_closing_and_due_dates():
    from fintrack import cards
    assert cards.closing_on_or_before(date(2026, 4, 20), 15) == date(2026, 4, 15)
    assert cards.closing_on_or_before(date(2026, 4, 10), 15) == date(2026, 3, 15)
    assert cards.closing_on_or_before(date(2026, 3, 5), 31) == date(2026, 2, 28)       # a short month: its last day
    assert cards.due_after(date(2026, 4, 15), 10) == date(2026, 5, 10)
    assert cards.due_after(date(2026, 4, 15), 25) == date(2026, 4, 25)


def test_a_manual_cards_statement_follows_its_transactions_and_its_closing_date(db):
    from fintrack import cards
    card = manual_card(db)                                                          # owes $500 at the end of Mar 31
    engine.apply(db, prepare(db, card, CARD_FILE, sign=1))                          # +$5 Apr 2, +$45 Apr 5
    cards.set_statement_settings(db, card, "15", "10", "35")
    april = date(2026, 4, 20)
    [due] = [s for s in cards.statements(db, april) if s.card_id == card]
    raw = db.execute("SELECT * FROM card_statements WHERE account_id = ?", (card,)).fetchone()
    assert (raw["statement_date"], raw["due_date"], raw["minimum_payment_cents"]) == ("2026-04-15", "2026-05-10", 3500)
    assert due.statement_cents == 55000                                             # what it owed on Apr 15
    cards.set_statement_date(db, card, "2026-04-03", today=april)                   # it really closed on Apr 3
    assert db.execute("SELECT statement_balance_cents FROM card_statements WHERE account_id = ?",
                      (card,)).fetchone()[0] == 50500
    with pytest.raises(ValueError, match="closes after"):
        cards.set_statement_date(db, card, "2026-03-10", today=april)               # that's the last period's
    with pytest.raises(ValueError):
        cards.set_statement_settings(db, CARD, "15", "10")                          # a connected card's is the bank's


def test_the_card_page_sets_the_statement_and_a_manual_card_with_transactions_can_be_deleted(client, db):
    from fintrack import manual
    card = manual_card(db)
    engine.apply(db, prepare(db, card, CARD_FILE, sign=1))
    assert "Set the days it closes and is due" in client.get(f"/accounts/{card}").text
    client.post(f"/accounts/{card}/statement-settings", data={"closing_day": "15", "due_day": "10"}, headers=ORIGIN)
    page = client.get(f"/accounts/{card}").text
    assert 'name="closing_day" value="15"' in page and 'name="statement_date"' in page
    manual.delete_account(db, card, "Fake manual card")
    assert not db.execute("SELECT 1 FROM transactions WHERE account_id = ?", (card,)).fetchone()


# ---------------------------------------------------------------- the ledger for manual investment accounts (phase 2)

def test_activity_added_by_hand_is_a_ledger_row_and_moves_the_holdings(client, db):
    from fintrack import ledger
    account = proxy_account(db)                                                     # SPY line: 10 shares from Jan 1
    spy = db.execute("SELECT id FROM manual_positions WHERE ticker = 'SPY'").fetchone()[0]
    r = client.post(f"/accounts/{account}/activity", headers=ORIGIN, data={
        "date": "2026-02-13", "kind": "contribution", "holding": str(spy), "amount": "1020", "description": "Payroll"})
    assert r.status_code == 303
    rows = db.execute("SELECT type, subtype, quantity, amount_cents, security_id FROM investment_transactions"
                      " WHERE account_id = ? ORDER BY plaid_investment_transaction_id", (account,)).fetchall()
    assert [tuple(x) for x in rows] == [("cash", "contribution", None, -102000, None),
                                        ("buy", "buy", None, 102000, f"mpos:{spy}")]     # in dollars: no units of its own
    assert quantity_on(db, account, "SPY", "2026-02-13") == pytest.approx(12)          # 10 + $1,020 / $510
    assert pretax(db) == {"2026-02": 102000}                                           # counted like a connected 401(k)'s
    activity = db.execute("SELECT id FROM investment_transactions WHERE type = 'buy'").fetchone()[0]
    sql, params = __import__("fintrack.investment_activity", fromlist=["x"]).as_transactions(db)
    listed = db.execute(f"SELECT * FROM ({sql}) WHERE account_id = ?", params + [account]).fetchall()
    assert len(listed) == 2                                                            # on the Transactions list too
    assert client.post(f"/activity/{activity}/delete", headers=ORIGIN).status_code == 200
    assert not db.execute("SELECT 1 FROM investment_transactions").fetchone()          # both rows went
    assert quantity_on(db, account, "SPY", "2026-02-13") == 10
    with pytest.raises(ValueError, match="less than nothing"):
        ledger.add(db, account, "sell", "2026-02-14", str(spy), "", "50", "", "25500", "")  # more than it holds
    with pytest.raises(ValueError, match="less than nothing"):
        ledger.add(db, account, "buy", "2026-02-14", str(spy), "", "1", "510", "", "")  # no cash to buy with


def test_a_statement_overrides_that_days_activity(db):
    account = proxy_account(db)
    from fintrack import ledger
    spy = db.execute("SELECT id FROM manual_positions WHERE ticker = 'SPY'").fetchone()[0]
    ledger.add(db, account, "contribution", "2026-03-01", str(spy), "", "1", "520", "", "")
    text = "Positions as of 03/01/2026\nSymbol,Description,Quantity,Price,Value\nSPY,SPDR,15,520,7800\n"
    engine.apply(db, prepare(db, account, text))
    assert quantity_on(db, account, "SPY", "2026-03-01") == 15                          # the statement's balance


def test_a_buy_needs_what_it_cost(db):
    from fintrack import ledger
    account = proxy_account(db)
    with pytest.raises(ValueError, match="amount"):
        ledger.add(db, account, "buy", "2026-02-14", "new", "FAKEQ", "5", "", "", "")


# ---------------------------------------------------------------- converting typed entries (docs/parity-plan.md phase 4)

def typed_401k(db):
    """A manual 401(k) as people typed them before the ledger: dollar contributions on a proxy line, a fee, a balance."""
    from fintrack import manual_positions
    account = proxy_account(db)                                                     # SPY: 10 shares from Jan 1
    for day, kind, amount in (("2026-01-15", "contribution", "500"), ("2026-02-13", "contribution", "510"),
                              ("2026-02-20", "fee", "5"), ("2026-03-01", "withdrawal", "52")):
        manual_positions.record_event(db, account, "SPY", day, kind, "value", amount)
    manual_positions.record_event(db, account, "SPY", "2026-03-05", "balance", "quantity", "13")
    return account


def test_converting_typed_entries_keeps_every_value_and_savings_figure(db, settings, tmp_path):
    from fintrack import convert_positions
    account = typed_401k(db)
    db.commit()
    count, differences = convert_positions.check_on_copy(settings.db_path, tmp_path / "copy.db")
    # values, snapshots, balance, holdings match to the cent; since phase 6 typed entries no longer count on the Savings
    # page, so converting them brings their contributions back: the only differences are those months (the real
    # database was converted while they still counted, and matched everywhere)
    assert count == 4 and differences and all(d.startswith("Savings ") for d in differences)
    assert len(convert_positions.entries(db)) == 4                                  # the real one untouched
    before = convert_positions.snapshot(db)
    assert convert_positions.convert(db) == 4
    assert all(d.startswith("Savings ") for d in convert_positions.compare(before, convert_positions.snapshot(db)))
    kinds = sorted((r["type"], r["subtype"]) for r in db.execute(
        "SELECT type, subtype FROM investment_transactions WHERE account_id = ?", (account,)))
    assert kinds == sorted([("cash", "contribution"), ("buy", "buy")] * 2 + [("fee", "fee"),
                                                                            ("sell", "sell"), ("cash", "withdrawal")])
    assert convert_positions.entries(db) == []                                      # all now activity
    assert quantity_on(db, account, "SPY", "2026-03-05") == 13                     # the balance stays yours


def test_a_difference_stops_the_conversion(db, settings, tmp_path, monkeypatch):
    from fintrack import convert_positions
    typed_401k(db)
    db.commit()
    real = convert_positions.convert

    def off_by_a_cent(conn):
        count = real(conn)
        conn.execute("UPDATE manual_valuations SET value_cents = value_cents + 1 WHERE id = (SELECT MAX(id) FROM"
                     " manual_valuations)")
        conn.commit()
        return count

    monkeypatch.setattr(convert_positions, "convert", off_by_a_cent)
    count, differences = convert_positions.check_on_copy(settings.db_path, tmp_path / "copy.db")
    assert differences and "largest difference 0.01" in differences[0]
