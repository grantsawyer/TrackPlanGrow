# Importing bank files (CSV, Excel, OFX): plan

Written 2026-11-13 at the user's request: "enable manual import of data from banks via csv, excel etc. the structure of
these files may not always be consistent and will be different for each account." Decisions made the same day are in
**Decisions** at the end; progress in **Where we are**.

**The premise (user, 2026-11-13):** someone may use fintrack with manual imports only, no Plaid at all. So every account
at an institution, of every type (checking and savings, credit cards, loans, investment and retirement), connected or
manual, can receive files.

## Where we started

`csv_import.py` + `fintrack import-csv` (2026-10-29, notes in `docs/transactions.md` "CSV import") already do the hard
parts for **one** bank's card download:

- **Safe to repeat:** each row gets an id `csv-<hash of its fields + its place among identical rows>`, so importing the
  same file twice adds nothing, and no Plaid sync ever changes or removes those rows.
- **No double counting with Plaid:** only rows posted **before** the account's first Plaid transaction are taken.
- **Categories:** a merchant Plaid already knows (`merchant_key`: same bank text, store numbers and order codes aside)
  gets Plaid's category, name and logo; otherwise the bank's own category maps to the nearest Plaid one.
- **Behaves like Plaid's rows:** written through `sync._upsert_transaction`, then rules, transfer matching and recurring
  detection run, as after a sync. Preview first; `--apply` backs up and holds the sync lock.

What it can't do: the column names are fixed in code (`BANK_CSV_COLUMNS`), it reads only CSV, only from the command
line, only into Plaid-connected accounts, and only cash transactions (no investment activity or positions).

Manual accounts today (`manual.py`) hold dated values only, with an institution name you type (migration 0033); manual
investment accounts can hold positions (`manual_positions.py`). There is no manual credit card kind.

## File types

All readers produce the same thing: rows of text cells, so detection never cares where they came from.

| Type | Reader |
|---|---|
| **CSV** (comma, semicolon, tab; any encoding a bank uses: UTF-8 with or without BOM, Windows-1252) | Python's `csv`, delimiter and encoding sniffed. The first and main format. |
| **Excel `.xlsx`** | **our own small reader, standard library only** (user's decision): `.xlsx` is a zip of XML. Read the first sheet (or let you pick one), shared strings, numbers, and dates from the cell's number format (Excel stores dates as day counts). Guarded: the zip's unpacked size is capped and checked before reading (zip bombs), and the XML is parsed with entity expansion refused. |
| Old Excel `.xls` | not read (a binary format needing a library); the page asks you to re-save it as `.xlsx` or CSV. |
| **OFX / QFX** | structured and the same for every bank (bank, card, loan and investment statements), so no column mapping. Standard library parsing of its SGML/XML. |
| **QIF** | a simple line format (`D` date, `T` amount, `P` payee, `L` category); bank and investment variants. |

## What varies between files, and how it's handled

Each difference is **detected**, shown in a preview, and **corrected once per account**; the correction is remembered.

| Varies | Examples | How it's handled |
|---|---|---|
| Preamble | account name and date range lines above the header; totals rows below | find the header row (first row that looks like labels above rows that look like data); drop rows that don't parse |
| Column names | "Date" / "Trans. Date" / "Posting Date"; "Description" / "Payee" / "Memo" | guess from a list of known names **and** from the values (dates, money, tickers), then let you change it |
| Dates | `03/04/2026`, `2026-03-04`, `4 Mar 2026`, Excel day counts | try each format against the whole column; any day over 12 settles month-first vs day-first; if still ambiguous, ask |
| Amounts | one signed column; Debit / Credit columns; a Type column (DR / CR); `(12.34)`; `$1,234.56`; `1.234,56` | parse each style; normalize to cents with `money.to_cents` |
| Sign | a purchase is `+` in one bank's file and `-` in another's | Plaid's rule inside the app (positive = money out). Proven by a running **Balance** column when there is one (each balance must equal the last plus or minus the amount), else from the account's type and the preview, and you confirm once |
| Extra data | card last 4, the bank's category, check number, running balance | optional columns: card -> `account_owner` (as today), category -> mapped once, balance -> proves the sign and gives a manual account its balance history |
| Layout changes | a bank renames a column next year | a profile is matched by its header; a changed header shows the mapping screen again with the old choices filled in |

## What a file can hold

Detection also decides which of three kinds of file it is (you can change it):

1. **Transactions** (checking, savings, cards, loans): date, description, amount, maybe balance / category / card.
2. **Investment activity** (brokerage and retirement): date, action (buy, sell, dividend, interest, fee, contribution,
   transfer...), symbol, quantity, price, amount. The action words differ by broker, so they're mapped once per
   profile to Plaid's types (`buy` / `sell` / `cash` / `fee` / `transfer` + subtype), like bank categories are.
3. **Positions** (a holdings statement): symbol, description, quantity, price, value, maybe cost basis, as of a date
   (from the file, its name, or you).

## How an import works

1. **Read** the file into rows of text cells (reader per file type). Held in memory only.
2. **Detect** the kind of file and its layout: header row, columns, date format, amount style, sign. Each guess carries
   how sure it is.
3. **Profile:** if you've imported a file with this header into this account before, reuse what you confirmed then. A
   profile belongs to an account (a new table, `import_profiles`: account, the header's fingerprint, the kind of file,
   column choices, date format, amount style and sign, your mapping of the bank's categories or the broker's actions).
4. **Check for overlap** (below).
5. **Preview** every row as it would be added, before anything is written:
   - new, already imported (same id), or **probably already there**, with a count of each;
   - the category it would get and why (Plaid knows this merchant / the bank's category / Uncategorized);
   - rows that couldn't be read, and why ("no date", "amount `n/a`").
6. **Import** the rows you keep, in one database transaction, through the same paths as Plaid's data, then rules,
   transfers and recurring run as after a sync. Each import is recorded as a **batch** (file name, a fingerprint of the
   file, account, row counts, dates covered, time) so a whole import can be **undone** from its batch. The file itself
   is not kept (user's decision): to fix a mapping, undo and import again.

### Overlap (user's decision: check, and ask)

Before the preview, the file's date range is compared with what the account already has: Plaid's transactions, earlier
imports, and transactions you added. If the ranges **don't overlap**, nothing is asked. If they **do**, the page shows
the overlap ("this file has Mar 1 - Jun 30; the account already has transactions from Apr 15") and asks which you
prefer, each with its row count:

- **Only the rows before what's there** (before Apr 15): the safe choice when the existing data is complete.
- **Every row, skipping duplicates:** each file row matching an existing one on amount (exact) and date (within 3 days)
  is left out; the rest are added. For filling gaps.
- **Every row, and let me review:** like the previous, but the likely duplicates are listed unticked in the preview so
  you decide each one.

The choice applies to that import only; the next overlapping import asks again, with your last choice selected.
Exact repeats (same id, from importing the same rows twice) are always skipped, whatever you pick.

## Accounts a file can go into

Any account at an institution: connected or manual, of any type. "Money lent" and property accounts don't come from
institutions and don't take files.

| Account | Transactions file | Investment activity | Positions |
|---|---|---|---|
| **Connected** (Plaid) | history from before Plaid's reach, or gaps (overlap rules above). The balance stays Plaid's. | into `investment_transactions` (ids `imp-…`), shown on the Transactions list like Plaid's (`investment_activity.py`) | not taken: Plaid reports holdings |
| **Manual, cash / card / loan** | the account's transactions; its balance from the running Balance column (each day's closing balance becomes a valuation), else from the values you enter, as now | — | — |
| **Manual, investment / retirement** | cash moves into and out of the account | adjusts the account's position lines (`manual_positions`: a buy adds shares from that date, a sell removes them, dividends / fees / contributions as dollar events), so value, Savings' contributions and the performance cards follow | sets each line's quantity as of the statement date (creating lines for new symbols) |

What manual accounts need for this (phase 4):

- **Kinds:** add **Credit card** (liability, `credit`) to the manual kinds; split Cash into **Checking / Savings** so
  the account's type is right; Loan and Investment / retirement exist. A new manual account asks for its institution
  (already stored, migration 0033) so the logo and grouping work as for connected ones.
- **Transactions on manual accounts:** they count in Spending, Budgets, Savings, Transfers and Cash flow like any other.
  "Add a transaction" lists manual accounts too.
- **Balance:** from imports when the file has a running balance; otherwise your dated values. A manual card's or loan's
  balance from imports is the amount owed (Plaid's convention), so net worth needs nothing new.
- **Transfers:** a payment from a manual checking account to a manual card pairs like any two accounts (the matching
  already takes "visible non-loan accounts"; loans keep today's rule).

## Where you do it

- **Desktop first** (user's decision): an **Import** tab under Settings, and **Import a file** on each account's
  page (the account already chosen). Phones don't get the page yet; `web/remote.py` blocks the import routes for phones
  until that's decided.
- The page: pick the account (or create a manual one from the same screen), choose the file, see the detected kind and
  layout as a small table with a picker above each column (Date / Description / Amount / Debit / Credit / Balance /
  Card / Category / Action / Symbol / Quantity / Price / Ignore), the overlap question if any, the preview, and
  **Import**. Below: past imports with their dates covered and **Undo**.
- **The command** `fintrack import-csv` keeps working, generalized (`fintrack import FILE --account N`), using the same
  profiles; when a file needs a choice the command can't guess (a new layout, an overlap), it says so and stops.

## Security and privacy

- The file never leaves the Mac and nothing new is contacted: parsing is local, standard library only (no new
  dependency). No change to the CSP.
- Uploads are a POST with the existing Origin check (CSRF), capped at 10 MB, held in memory and dropped after the
  request; nothing of the file is stored but its fingerprint, the rows it produced and their ids.
- Parsing treats everything as text: no formulas are evaluated, no macros, no external references followed.
- `*.csv` is already git-ignored; add `*.xlsx`, `*.xls`, `*.ofx`, `*.qfx`, `*.qif` so a download dropped in the repo
  folder can't be committed.
- Test files are made up (obviously fake merchants, tickers and amounts), one per layout below, and the `.xlsx` test
  files are built by the tests themselves (a zip of XML), not committed binaries.

## Build in phases (stop after each for you to test)

1. **Engine, CSV, no new screens.** Split `csv_import.py` into reader -> detection -> normalized rows -> plan -> apply,
   keep today's bank as the first built-in profile (its tests must still pass unchanged), detection for header rows,
   dates, amount styles and sign (with the running-balance check), the overlap check and its three choices. Migration:
   `import_profiles`, `import_batches`, a batch id on transactions. The CLI `fintrack import`. Tests with made-up files:
   signed amounts, Debit / Credit, a Type column, parentheses, preamble and totals lines, day-first dates, semicolons
   with decimal commas, overlapping and non-overlapping ranges.
2. **Import page** (desktop) for connected accounts: upload, mapping pickers, overlap question, preview, import, past
   imports with undo.
3. **More file types:** the `.xlsx` reader, OFX / QFX, QIF.
4. **Manual accounts take files:** the new kinds (credit card; checking / savings), transactions on manual accounts,
   balances from a running Balance column, creating a manual account from the Import page.
5. **Investments:** activity files (action mapping, into `investment_transactions` for connected accounts and position
   lines for manual ones) and positions statements (manual accounts), OFX investment statements.
6. **Later, if wanted:** imports from the phone, several files at once.

## Decisions (user, 2026-11-13)

1. **Excel:** a small reader of our own, standard library only (no openpyxl). CSV is the first and main format.
   Old `.xls`: re-save as `.xlsx` or CSV.
2. **Accounts:** every account at an institution, of every type, connected or manual, can take files; the app must work
   with manual imports alone.
3. **Phones:** desktop first.
4. **Original files:** keep nothing.
5. **Overlap:** check the file's dates against what the account has; when they overlap, ask which option to use
   (rows before / every row skipping duplicates / every row with review).
6. **Investment accounts:** in scope (activity and positions).

## Where we are

**Phase 1 built 2026-11-13** (as built: `docs/transactions.md` "Importing bank files"). `fintrack/imports/` (readers,
layout, engine) replaces `csv_import.py`; `fintrack import` replaces `import-csv` (kept as an alias); migration 0068
(`import_profiles`, `import_batches`, `transactions.import_batch_id`). Ids keep the first importer's formula, so its rows
count as already imported. Tests: `tests/test_imports.py` (the first bank's tests ported with the same assertions, plus
made-up layouts, overlap choices, profiles, the command).

Settled while building, beyond the plan above:

- **Overlap** works on spans of dates (`covered_spans`), not one range: Plaid's rows cover from their first on (first
  to last once a connection is removed); imported and your own rows cover their dates, split at gaps over 35 days, so
  two imports months apart don't cover the months between.
- **A likely duplicate is an overlap even off those dates:** Plaid can date a row a day after the file does, so the
  file's row just before Plaid's first is often Plaid's first. `outside` leaves likely duplicates out too.
- **Manual accounts are refused** until phase 4, so imported rows never count in Spending without moving a balance.
- **The command can't map bank categories** yet: unmatched ones are listed and imported as Uncategorized (rules can
  sort them); the picker comes with the page (phase 2).

**Phase 2 built 2026-11-13** (as built: `docs/transactions.md` "Import page"): the Import tab in Settings and "Import a
file" on each connected account's page. Settled while building:

- **Bank categories map to your own categories** (any, custom ones included), not to Plaid's terms: the profile keeps
  category keys, and a mapped row's category is set ahead of Plaid's (rules still win).
- **Nothing is preselected in the overlap question** (the plan said "your last choice selected"): it's marked "last
  time" instead, so every overlapping import is an explicit choice.
- **The file is sent with every change** of the form rather than held between steps: nothing is stored, and choosing
  another file or account starts the answers over.

**Phase 4 built 2026-11-13, ahead of phase 3** (user's request: "i want to load transactions for a manually added
credit card"): a **Credit card** manual kind; manual bank accounts, cards and loans take files and transactions you
add; migration 0069. As built: `docs/accounts.md` "Manual accounts that hold transactions" and `docs/transactions.md`
"Into a manual account". Settled while building:

- **A manual account's balance follows its transactions** (the plan said: from a running Balance column, else your
  values, as now): your latest value, moved by every transaction after it, and worked back before it. Most card
  downloads have no balance column, so values alone would leave an imported card's balance frozen.
- **Cash isn't split into Checking / Savings** yet: the Cash kind (`depository`) takes files as it is; the Type picker
  (Account types) still says which it is.
- A negative closing balance from a file (a card paid past zero) isn't stored as a value (values are 0 or more); the
  transactions carry the balance through it.

**Phase 3 built 2026-11-13** (as built: `docs/transactions.md` "Reading"): `.xlsx` (`imports/xlsx.py`, standard
library), OFX / QFX / QBO and QIF (`imports/money_files.py`). Settled while building:

- **The type is told by the file's content** (a zip, OLE, `<OFX>`, `!Type:`), then its name, so a mislabelled
  download still reads.
- **OFX and QIF become Debit / Credit columns**, so they go through the same detection, profiles and preview as CSV,
  and never ask about the sign. OFX's own ids (FITID) and closing balance (LEDGERBAL) aren't used yet: ids stay the
  same formula as every other file.
- **Excel**: the first visible sheet holding transactions (a cover sheet is skipped); no sheet picker.

**Phase 5 built 2026-11-13** (as built: `docs/transactions.md` "Investment files"): activity into connected and
manual investment accounts, holdings statements into manual ones, OFX investment statements. Settled while building:

- **A manual line's change records what was paid in** (`contributed_cents`, migration 0070) rather than adding a new
  event type: a deposit and a buy on one day net into one change, and only money paid in counts on the Savings page.
- **Activity into a manual account needs what was held before it:** a sale or the cash going below 0 stops the import
  with the reason. The way in is a holdings statement from before the activity's first date, then the activity; or
  holdings statements alone.
- **Imported changes are read-only in History** (their type may be a buy, which the Add form has no type for); Undo on
  the Import page removes them.
- **Funds tracked through a proxy ticker** (asked the same day): each fund in the file is matched to a line, and one
  matched to another ticker's line (or a dollars line) moves by the file's dollars only, never its units or prices.
- Not done: QIF investment files; OFX's own ids and closing balances; a sheet picker for Excel; phones.

The plan's phases are all built. Left for later, if wanted: imports from the phone, several files at once.
