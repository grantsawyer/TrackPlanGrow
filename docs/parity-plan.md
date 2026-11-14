# Manual and connected accounts alike: plan

Written 2026-11-14 at the user's request: "we should be orienting the app to treat accounts and transactions the same
regardless of their source. a manual credit card and a connected credit card should function the same and a manual vs
connected investment account should look and feel the same." Progress in **Where we are** at the end.

## The rule

**Every feature works on the kind of account (bank, card, loan, investment), never on where its data comes from.** What
may differ is only what the source itself provides: syncing and reconnecting, Plaid's statements, the live-balance
button, Plaid's merchant names and logos; and that a manual account's balance has to be worked out (from your values,
its transactions, or its holdings) where Plaid reports one. Added to CLAUDE.md's conventions.

## Why they differ today

Manual accounts began (milestone 7) as a value on a date, for a house or a car. Positions came later (2026-10-07) so a
manual 401(k) could follow prices, as their own model: lines with dated changes (`manual_positions.py`), not activity.
Connected accounts grew around Plaid's data: transactions, holdings, investment activity (`investment_transactions`).
Each feature was built for the model it started with; phase 5 of the import plan then wrote a manual account's imported
activity as position changes, which widened the gap.

## Where they differ (audit, 2026-11-14)

**Bank accounts and cards** (mostly alike since the import work, 2026-11-13):

| Where | Connected | Manual |
|---|---|---|
| Cash flow (`forecast.py`) | checking / savings forecast | left out (`is_manual = 0`) |
| A card's paying account (`cards.automatic_payer`) | picked when it's the only one | never picked |
| Card statement (balance, due date, minimum) | from Plaid (`card_statements`) | none: estimates only |
| Deleting one transaction | yours (`manual-…`) only | the same; imported rows only through the batch's Undo |

**Investment accounts** (the large gap):

| | Connected | Manual |
|---|---|---|
| Activity | `investment_transactions` rows: on Transactions and the account page, categories (Investments › Buy, Investment income › Dividend), edits, tags | position changes (`manual_position_changes`) in a History table: no categories, not on Transactions |
| Holdings | Plaid's | worked out from position lines, mirrored into `holdings` |
| Contributions (Savings) | `contributions.py`: bank transfers paired with the account's deposits; contributions seen only inside it are pre-tax income | the lines' "contribution" events (`retirement_contributions`) |
| Accounts page activity (`balances.py`) | activity rows | History events |
| Live prices (`live_values.py`) | holdings marked to market | lines revalued |
| Imports (phase 5) | activity rows | position changes |

Your data (read-only count, 2026-11-14): 3 manual investment accounts, 5 lines, 80 entries (75 dollar contributions,
3 opening values, 2 balances, none imported). They must come out the same: every day's value and every month's
Savings figures.

## The model

- **One activity ledger for every investment account:** `investment_transactions`, with a source (Plaid / imported /
  you). A manual account's buys, sells, dividends, contributions and fees are rows there, shown, edited, tagged and
  categorized exactly like Plaid's; `contributions.py` reads them the same way, so the Savings page counts both alike
  and `retirement_contributions` goes.
- **Holdings from the ledger** for accounts Plaid doesn't report: a security's shares on a day = its last **balance
  checkpoint** (a holdings statement, or a balance you enter) + the shares its activity moved since. A security can be
  **priced by another ticker** (a proxy, e.g. a fund through SPY): its activity is in dollars, turned into the proxy's
  shares at that day's price (re-derived when a better price arrives, as dollar events are now). The account's value
  per day = the sum of its securities' values, written as its values (as now), and mirrored into `holdings` so
  Investments, Portfolio and the movers need nothing new.
- **Checkpoints aren't activity:** they're on the account's page (History), not the Transactions list (they move no
  money).

## Phases (stop after each for you to check)

1. **Bank and card gaps.** Manual checking / savings in Cash flow; a manual account can be the automatic paying
   account; an imported transaction can be deleted on its own (and a re-import doesn't bring it back); a manual card's
   statement (decision 1).
2. **The ledger for manual investment accounts.** Activity rows for manual accounts, "Add activity" on their page (the
   same kinds as imports: buy, sell, reinvestment, dividend / interest, contribution, withdrawal, fee), shown on
   Transactions and the account page like Plaid's; `contributions.py` and Savings read them.
3. **Holdings from the ledger.** Securities (ticker, or priced by a proxy), balance checkpoints, shares and values per
   day, the `holdings` mirror; the account page's Holdings and History read them.
4. **Convert your positions.** Each line becomes a security (ticker, or proxy), each entry a ledger row or a
   checkpoint. Done on a copy of the database first: every manual account's value on every day and every month's
   Savings figures compared before and after, to the cent; any difference stops it and is reported. Then on the real
   one, after a backup. The old tables stay (migrations are append-only) but nothing reads them.
5. **Imports write the ledger** for manual accounts (activity, holdings statements as checkpoints, proxies as
   "priced by"); the position-change path goes.
6. **The rest, and the rule kept.** `balances.py` activity, `live_values.py`, anything left that asks `is_manual` for
   behaviour; a test that builds a manual and a connected account of each kind with the same activity and checks every
   page shows them alike; docs.

## Decisions (user, 2026-11-14)

1. **A manual card's statement:** (a), set its closing and due days once; the balance is the card's on the closing
   date; and the current statement's closing date can be changed, its balance following.
2. **"Add activity"** with the import's kinds plus Balance replaces the History form: yes.
3. **Conversion check** to the cent, nothing converted until it matches: yes.

The questions as asked:

1. **A manual card's statement:** (a) set its closing day and due day once; each statement's balance is the card's
   balance on the closing day (it follows its transactions), the minimum optional; or (b) type each statement's
   balance, due date and minimum. *Suggested: (a), with any statement's figures changeable.*
2. **Entering activity by hand on a manual investment account:** the History table's Add / edit form (Contribution /
   Withdrawal / Fee / Balance, in quantity or value) becomes "Add activity" with the import's kinds, plus Balance (a
   checkpoint). *Suggested: yes.*
3. **Conversion check:** stop on any difference of a cent or more in any day's value or month's Savings figures?
   *Suggested: yes; nothing is converted until they match.*

## Where we are

Plan written 2026-11-14. **Phase 1, built the same day except the card statement (decision 1):**

- Cash flow includes manual checking / savings (`forecast.cash_flow`), and a manual one can be a card's automatic paying
  account (`cards.automatic_payer`).
- An imported transaction can be deleted from its edit panel like one you added (`manual_transactions.deletable`;
  "Imported from a file" in the panel); `deleted_imports` (migration 0071) keeps its id, so a re-import lists it as
  "Deleted by you" instead of adding it again; a manual account's balance follows.
- **A manual card's statement** (decision 1; migration 0072 `card_statement_settings`): "Closes on day", "Due on day",
  "Minimum" on its page; `cards.refresh_manual_statements` writes its current statement into `card_statements` (the
  balance it owed at the end of the closing date, `manual.balance_on`; due on the next due day), before anything reads
  that table, writing only what changed; "Closed" (the closing date) is editable within the current period
  (`set_statement_date`) and the balance follows. Every page reads it as it reads a connected card's.
- Fixed on the way: deleting a manual account now also deletes its transactions, activity, statement and paying
  account (it failed once a manual account had transactions).

**Phase 1 done.** Changed after: a deleted imported row comes back when its file is imported again (user's request
2026-11-14: to recreate it); `deleted_imports` dropped in migration 0073.

**Phase 2 built 2026-11-14, with the part of phase 3 it needs** (activity that didn't move the holdings would leave a
manual account's activity and value disagreeing), as built in `fintrack/ledger.py`:

- **The ledger:** a manual investment account's activity is `investment_transactions` rows like Plaid's ('act-…' yours,
  'imp-…' imported); on the Transactions list (and its filters) and the account's page with the same categories, edits,
  tags; yours and imported can be deleted (both rows of a two-row one). A contribution that buys shares is two rows, a
  `cash` / `contribution` (cash in) and a `buy` (`<id>/2`), as Plaid shows a 401(k)'s, so `contributions.py` counts it on
  the Savings page exactly as a connected account's; imports into connected accounts write it the same way now.
- **Holdings follow:** `ledger.rebuild_positions` turns the account's rows into its lines' changes
  (`manual_position_changes.from_activity`, migration 0073): shares, or dollars for a row without units (a proxy, a
  dollars line), cash on the Cash line; they record nothing as paid in (the contribution rows do). A balance of yours
  or a statement's that day stays (it's the closing amount); another entry of yours that day is a clash, refused.
  Rebuilt after every add, delete, import and undo; a line going below 0 refuses the change.
- **"Add activity"** on a manual investment account's page (under Transactions; decision 2): date, kind, holding (or a
  new ticker), shares, price, amount, description; a buy, sale or contribution needs its amount (or shares and price).
  History's Add is now **Add balance** (your balances); activity-made changes show there read-only ("From activity").
- **Imports** of activity into manual accounts write the ledger (the position-change path and `import_rows` are gone).
- Your 80 existing entries are untouched (marked yours) until phase 4.

**Phases 3 and 4 done 2026-11-14** (`fintrack/convert_positions.py`, `fintrack convert-positions [--apply]`):

- **Phase 3's remainder** needed nothing new: a line already is a holding priced by its ticker (a fund tracked through
  SPY is the SPY line under the fund's name); activity points at lines (`mpos:<id>`), and a row without units is in
  dollars (a proxy's). Imports match a fund's own symbol to its line (`Layout.holdings`).
- **The conversion:** your typed contributions / withdrawals / fees on manual investment accounts became activity rows
  (ids `act-conv-…`; a contribution: a contribution and a buy in dollars; a withdrawal: a sale and a withdrawal; a fee:
  a fee; on the Cash line itself: cash in / out only), then each account's holdings were rebuilt; balances and opening
  values stay yours. Checked on a copy first: every manual investment account's values, snapshots, balance and
  holdings, and every month's Savings figures, compared to the cent (`snapshot`, `compare`); any difference stops it.
- **Done on the real database 2026-11-14** (backup `fintrack-production-2026-11-14_074034.db`): 75 entries, 123
  activity rows; the copy and the real database both matched to the cent. 5 balances / opening values stay yours.

Phase 5 (imports write the ledger) was done in phase 2.

**Phase 6 done 2026-11-14:**

- **The Savings page counts contributions one way:** `contributions.py` for every account; the manual path
  (`retirement_contributions`, `retirement_by_account`, `_retirement_events`) is gone. History's forms take balances
  only (route and editor refuse other kinds). Typed entries can't be made any more; the conversion was one-time (the
  command stays: on a database restored from before it, its check reports the Savings months the converted entries
  bring back, since typed entries no longer count).
- **The Accounts page's investment activity** lists every account's activity rows (plus a manual account's balances);
  **investment accounts are grouped by institution** there and on the Savings chart, a manual one's own alike.
- **Imported activity on a connected account keeps its ticker** when Plaid has no security for it (`ticker:<symbol>`),
  as a manual account's does (found by the parity test).
- **The parity test** (`tests/test_parity.py`): the same file into a connected and a manual card, and into a connected
  and a manual investment account: the same rows, categories, kinds, Credit cards figures, Transactions list, Savings
  contributions, Accounts page activity and account pages.
- **What still asks `is_manual`** is what only the source decides: how a manual account's balance is worked out
  (`manual.py`, `account_types._effective`), Plaid's names and logos (`account_names.py`, `logos.py`), the synced time,
  live marking of Plaid's holdings (`live_values.revalue_connected`; a manual account's lines are revalued by
  `manual_positions.revalue`), money lent (`purposes.py`), and transactions you added (`manual_transactions.py`).

**The plan is done.**
