# Accounts, Net worth and account pages

Net worth and Accounts pages (balances.py), each account's page, account types, manual accounts, money lent (purpose accounts), manual positions, live balances, account names.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

- **Balances = change in account values (user's request 2026-10-12, later that day; replaced the savings-rate "view" the
  page had shared with Savings):** `/accounts` (was `/balances` until 2026-10-14; Accounts section, beside Net worth) has its own route (`web/accounts.py`),
  template (`accounts.html`) and module (`balances.py`, its docstring is the reference). Everything comes from
  `balance_snapshots` (whole days carried, signed like net worth), so it agrees with the Net worth page: change of a period
  = value at its last day − value the day before its first (a period starting before the first snapshot counts from it;
  periods entirely before it are left out). Groups (`balances.GROUPS`, in display order; Cash first since 2026-10-13, user's request): retirement (by account type, `is_retirement_account`)
  / investment (by institution, manual by name) / purposes "Other assets" (by account) / cash "Cash" (checking & savings, by
  account; "Net cash" = minus cards until the user renamed it, then split the cards out, 2026-10-12) / credit_cards
  "Credit cards" (by card, negative = owed; own card, violet `--series-7` via `visuals.BALANCE_COLORS`, not red) / property & loans (the rest; no card of its own, only a Net worth part + table column); they add up to
  net worth's change (checked on Sandbox demo + Production read-only 2026-10-12: parts = total, months sum to the range,
  range = `networth.history`). Tiles: net worth change this month so far / last month / range + % of the starting value.
  Cards (`visuals.balance_change_mix` / `networth_change_mix`): measures **Total value** (`value_cents`: the value at the
  range's end) and **% change** over the range, stock style (green ▲ / red ▼ + absolute %, as on Investments; % = change ÷
  |starting value|; "—" only when it started at $0. Credit cards use the OPPOSITE colour logic (user's request
  2026-10-12; `visuals.LIABILITY_GROUPS`, chart `invert`): the % follows the amount owed, owing more = red ▲, paying down
  = green ▼; their Total value, negative = owed, is never red) (user's request 2026-10-12; no "savings" in any
  header); bars = the change, stacked
  `sharebars` in `money` mode (dollar axis, can go negative), colours by kind, hidden when the kind held nothing (Cash
  always; six cards = 2 per row, 3 per row from 1232px, `.cards.n6`), by day for This / Last month. Table "Change in value": kinds, Net worth change, %, Net worth at the end.
  **$ / % toggle in the ribbon** (user's request 2026-10-12; `.seg` links before the date range, in `.tabbar-group`;
  `?show=pct|value`, default pct, kept by the range form's hidden field): what the cards' BARS show (the measures don't
  change): % = each month's (day's) % change, a segment = its change ÷ |the card's value before the period| so the stack
  = the card's % change (`visuals._bar_pct`; credit cards follow the amount owed), 1 decimal (`decimals`); $ = the total
  value at the end of each period as STACKED AREAS (`stackedarea` kind, user's request 2026-10-12; % stays bars): owed
  kinds stack downward from $0 (each area fills to the previous series of the same sign), `hue` / `colors` by kind,
  `total_footer` adds the Total to the tooltip; Net worth's parts add up to net worth. The $-change bars are
  gone (the $ change is in the % tooltip).
  Known limitation: a newly connected account shows its whole balance as a change on its first day. Production's
  snapshots start 2026-10-03 (9 days by 2026-10-12), so its history is short and grows with each sync.
  **Compact / Detailed switch (Net worth ribbon, user's request 2026-10-13; `?cards=compact|detailed`, default **detailed** (user's request 2026-10-13),
  kept by the other controls and the range form):** Detailed = each kind's card twice as wide (`detailed_card` in
  `_balances.html`): left half = the compact card exactly (`card_body`), right half = "Recent activity", the newest 7
  movements dated inside the range (`balances.recent_activity`): cash / credit cards = `transactions_effective` rows;
  retirement / investment = every account's `investment_activity`, manual or connected alike (sweeps hidden; since
  2026-11-14, docs/parity-plan.md) + a manual account's balances (`manual_positions.events`, kind balance: its value,
  grey, not a movement); other assets = `purposes.entries`. Investment accounts are grouped by institution, a manual
  account's own (`manual_institution`) alike, else its name.
  Amounts in the Transactions page's sign (+ green = money in). Each row's source (account name, `Activity.source`) is written in its
  segment's colour in the card's chart (user's requests 2026-10-13: tried a dot first, then coloured text;
  `Activity.segment` = the chart's segment label; `.seg-text.hue-<kind>.shade-N` in app.css = the kind's hue mixed toward
  `--surface` by 0 / 40 / 60 / 75% with `color-mix(in srgb …)`, the same as charts.js `shade()`; the lightest shades are
  faint as text (measured 1.3-1.9:1). Lightening them for text only (mixing toward `--fg`) was tried and undone at the
  user's request 2026-10-13). Rows are two lines, longer text cut with "…" (full on
  hover). Layout (`.cards.detailed`): one per row, two per row from a 1440px grid (where compact sits four per row);
  halves side by side from 816px. Activity is only queried in Detailed. One card height in both views (user's request 2026-10-13): `--kind-card-h` = the list's
  title + 7 rows of exactly 52px; `.kind-body` (each card's left part) is a flex column at least that tall whose chart
  grows to fill it, in Detailed and in Compact (`.cards.fit4`); Balances' Net worth card isn't affected. The Over time card matches it too (`.over-time-body`; the variables live on `:root`). Its title is "Net worth, <range>" (e.g. "Net worth, 90 days";
  custom = the dates; user's request 2026-10-13, was "Over time"). The switch shows icons, not words (`_icons.html`
  `layout-compact` = collapse (filled arrows pointing in, inside a box), `layout-detailed` = expand (filled arrows
  pointing out, inside a box);
  bolder in the switch: 18px, stroke 2.2; `.seg-icons`; named by aria-label
  and title; user's request 2026-10-13).
  **Account cards (Balances, user's request 2026-10-13; the three tiles were removed):** after the Net worth card, one
  card per kind shown (Cash, Retirement, Investment, Other assets, Credit cards; no Property & loans), title = kind +
  the period's $ and % change right after it, total value at the end of the period on the right, in line with the
  rows' values (the last column has no right padding; user's request 2026-10-13) (balances.summary, the same figures as Net
  worth's cards; credit cards opposite colours); rows (`balances.account_rows`, largest first): institution logo (else
  a letter), account name (manual ones link to their page) + institution, last refresh = the connection's
  `items.last_synced_at` (`localtime`) or a "Manual" tag (that column was removed 2026-10-13, user's request; a credit
  card's row shows its statement balance there instead, then ✓ Paid / ⚠ Overdue / "due <date> · ◷ in N days" /
  Nothing due, from `cards.statements`; user's requests 2026-10-13), value signed like net worth (cards negative). Logos:
  `institution_logos` (migration 0031: ok / none / failed, retried after 30 days), fetched by
  `logos.fetch_institution_logos` after the web and CLI syncs (`PlaidGateway.get_institution_logo`, SDK option
  `include_optional_metadata`; the demo's made-up institutions return none), saved as <sha256 of the PNG>.png (small
  PNG check, mode 600), served by `/logos/<hash>.png`. Layout = the Budgets page's sizing (user's request 2026-10-13;
  `.balances-layout`): the account cards are the main column, one per row (`.account-cards`), the Accounts card (+ "Not counted") takes the summary's
  column (minmax(325px, 400px), sticky) from 1200px and is hidden below it (it doesn't move above like the budget
  summary). The Net worth card stays full width above. Balances' right-hand card is **Summary** (user's request 2026-10-13: was the Accounts card;
  `_balances_summary.html`, no drop-down rows, the Overview keeps `_accounts_card.html`). It groups accounts by kind in the cards' order, not by account type
  (`balances.regroup_by_kind`: Cash, Retirement, Investment, Other assets, then "Property & other"; Credit cards, then
  "Loans & other"; user's request 2026-10-13; the Overview keeps types and category names) and (`account_bars`) has a proportion bar per side (assets,
  liabilities): a segment per account type sized by its share, coloured like the Net worth cards (the kind holding most of
  the type's value: `balances.kind_of_accounts` + `proportion_bars`; shades 0-3 by order within the kind, mixed toward
  `--surface` like charts.js `shade()`), widths via ui.js `sizeMeters` (`data-share`), a swatch on each type row as the
  legend, an aria-label listing the shares (user's request 2026-10-13).
  **Account pages (user's request 2026-10-13):** each row on a Balances account card opens `/accounts/{id}`
  (the name is the link; the whole row via ui.js `tr[data-href]`); under `/accounts`, so the Accounts section with
  its Accounts tab. A card titled "<source> · <account>" (institution, or Manual) and "← Accounts", then a
  **Transactions** card (user's request 2026-10-13; `web/accounts.account_transactions`): the Transactions page's row
  format without the account column (`_txn_row.html` with `hide_account` /
  `hide_select`), no select box; rows are **editable** like on Transactions (user's request 2026-10-13): `account_view`
  puts `?view=account` on the row's edit link, the edit and rule panels span 5 columns and post `view=account`, and
  every route that returns a row (`_row(view=)`: save, transfer buttons, category) keeps this format; a rule saved
  here returns to this page (`_back` accepts `/accounts/<id>`). Investment activity is edited the same way since 2026-10-31
  (user's request; docs/investments.md), so an investment account's table is the bank / card one: filters, headings that
  sort on the server, edit panels (`account_transactions` reads `investment_activity.as_transactions` for it; the
  browser-sorted Type table and `_inv_row.html` are gone). Newest first, at most 200 with a link to the rest on Transactions (`?account=<id>`; investment activity is in that list
  too since 2026-10-30). Bank / card accounts: their transactions; connected investment accounts: their investment activity
  (sweeps hidden); money lent: its linked transfers; other manual accounts: "No transactions." (their events are on
  their own page).
  **Account details (top card, user's request 2026-10-13):** rename (Plaid: `account_names.rename`, blank = automatic;
  manual: its name), **Type** = a type or subtype from your table or Automatic ("Automatic · Cash › Checking" =
  `account_types.automatic`, display only), from-the-bank type / subtype / name, balance, last refresh; credit cards:
  statement balance + issue date, due date + minimum, Paid / Not yet / Overdue (`cards.statements`: payments since
  the statement), **Bills paid from** (the forecast's `cards.set_payer`: Automatic / an account / Don't forecast).
  POSTs `/accounts/{id}/name|type|pay-from|institution` redirect back with a note.
  **Rewards balance (credit cards, user's request 2026-10-26; `_reward_balances.html`, `rewards.balances`, migration 0044
  `reward_balances`):** a card under the top card on every credit card's page: the balances you type as of dates
  (points / miles, or cents of cash back), newest first, each editable in place (date and amount; Save, trash =
  delete via formaction), the latest in the card head (`rewards.bonus_label`), and an Add form (As of defaults to today,
  no future dates). One per card per date: adding on a date that has one replaces it, and moving one onto another's date
  replaces that one. A new balance takes the card's rewards unit (Credit cards' setup, `rewards.balance_unit`), else a
  unit picker; each row keeps its own unit. POSTs `/accounts/{id}/reward-balances[/{balance_id}[/delete]]` redirect back
  with a note (`data-keep-scroll` keeps your place). **Current balance (user's request 2026-10-26;
  `rewards.current_balance`):** on a card with rates set up, the head shows the latest balance you entered + the rewards
  of every transaction dated AFTER it (a balance as of a day counts that whole day), "N on <date> + M earned − R
  redeemed" (bonus points count as earned and redemptions come off, both after the date; 2026-11-03); none when
  the latest is in another unit than the card earns (then the latest as entered). **Rewards column:** the card's
  Transactions table gets a Points / Miles / Cash back column (`rewards.transaction_rewards`, via
  `web/transaction_edits._account_columns`, also for a row, edit panel or rule panel returned alone with view=account, so the
  cells and `colspan` line up): spending at its matching rate (fees 0x, a refund takes rewards back) + a bonus on it,
  rounded per transaction (the Credit cards estimate rounds per category, so it can differ by a little); blank when it
  earns nothing. **Balance lines:** your entered balances sit among the transactions by date (`accounts._with_balances`),
  above that day's transactions, bold; with more than 200 transactions, those older than the oldest shown are left off.
  **Bonus points (user's request 2026-11-03; `_reward_bonus_points.html`, migration 0061):** a card under Rewards
  balance on a card whose rewards are set up: date, amount (its unit), note; they add to the balance only (a sign-up bonus
  paid in points). **Rewards spent are transactions (user's request 2026-11-04, migration 0062; replaced 0061's
  Redemptions card here the next day):** Add a transaction on Transactions with Paid with rewards, or Rewards used on a
  credit's edit panel (docs/transactions.md); the current balance takes them off after its date. Not these: card credits
  (a travel credit against a purchase) stay refunds that offset the expense and its rewards; a cash sign-up bonus is income.
  **Holdings (investment accounts, user's request 2026-10-13):** the connected investment account page's Holdings card
  (`_holdings_card.html`, shared; data `web/accounts.holdings_context` = `performance.account_holdings` + totals) sits
  between the details card and Transactions on any investment account's page (connected or manual: manual positions
  are mirrored into `holdings`); the route fetches the cached live quotes and runs `live_values.revalue_all` first, as
  the investment account page does, so the header balance and the holdings use the same prices.
  **Account rules on the Balances account page (user's request 2026-10-13):** a money-lent account's Rules
  (`_purpose_rules.html`, moved from /manual/{id}) and a connected investment account's Destination rules
  (`_destination_rules.html`, moved from /accounts/{id}/activity) sit at the bottom of its page; their add / remove
  routes return to `/accounts/{id}`. **Category rules** moved from the Categories page to their own Settings tab
  **Rules** (`/rules`, after Categories; `web/spending.rules_page`, `rules.html`); `/categories?pattern=` forwards there.
  **Manual accounts' History (moved from /manual/{id}, user's request 2026-10-13):** `_manual_history.html` (data
  `web/accounts.manual_history_context`, which revalues positions / relinks money lent first, as /manual did) sits on
  the Balances account page in place of the Transactions card: money lent = linked transfers + adjustments (Unlink,
  Add adjustment); investment = your events (Add, edit in place, Set price); other kinds = typed values + Add a value.
  The ten routes behind those forms (events add/edit/delete, price, values add/edit/delete, adjustments add/delete,
  unlink) now return to `/accounts/{id}`. /manual/{id} keeps the value tile, Rules (money lent) and Settings,
  with a card linking to the history.
  **Card layout (reorganised 2026-10-13, user's requests):** header = logo (36px) + "<source> · <account>" +
  "Account ····NNNN" on the left, the balance large on the right with "Updated <time>" or the Manual tag; then
  **Details**, fields edited in place (Name, Institution for manual, Type with "Edit types" beside its label, Bills paid
  from for cards): no Save buttons (`data-autosubmit` -> `requestSubmit`, which runs the fields' own `required` /
  `maxlength` rules first, so an invalid value isn't sent; `<noscript>` Save buttons; the server refuses bad input
  too); no "From the bank" row; and, for credit cards, **Statement** beside it from 760px (Balance + issued, Due +
  "in N days", Minimum, Status = ✓ Paid / ◷ Due · $X owed / ⚠ Overdue with `--status-*-text` colours).
  **Manual accounts' institution (migration 0033, user's request 2026-10-13):** `accounts.manual_institution`, typed on
  the account page (Institution field, manual only); shown as the page's source and on Balances rows (instead of
  "Manual"; the Overview's Accounts card dropped its rows' institution text 2026-10-23, and its Manage link and Net worth row 2026-11-04). Logo: `logos.find_manual_institution_logos` asks Plaid's `/institutions/search`
  (`PlaidGateway.search_institutions`; exact name match, else the first result) when you save the name and after each
  sync for names not looked up; `institution_searches` remembers each name (lower-cased: ok / none / failed, retried
  after 30 days); the logo is saved through the same `_save_logo` as connected institutions'. Saving still works
  without Plaid keys (no logo).
  **Account types (migration 0032, user's request 2026-10-13):** `account_type_nodes` (two levels; a type has a
  `kind`, subtypes follow their type), default hierarchy Cash / Retirement / Investment / Other assets / Property /
  Credit card / Loan / Other asset / Other liability with subtypes. Managed on Settings → Account types, where each node's count = visible accounts given it + those whose
  automatic match it is ("3 accounts (2 automatic)"; `tree(with_automatic=True)`; user's request 2026-10-13). An account's
  choice (`accounts.type_node_id`, with `type_kind` + `type_label` kept in step on rename / move / delete) decides its
  effective type / classification (`account_types._effective` / `_apply`; sync leaves them
  alone while set, `items.upsert_accounts`), retirement vs investment and chart labels (`visuals.is_retirement_account`
  / `investment_type_label` check `type_kind` / `type_label` first; queries feeding them select those columns).
  Nothing changes for an account until you choose; deleting a type sends its accounts back to automatic (a manual
  account keeps the type it had, as with categories).
  **Page layout (user's request 2026-10-25: like Categories, columns aligned):** names save when changed (no Rename
  button; `<noscript>` keeps one), the count is the number alone ("3 accounts (2 automatic)" on hover), subtypes are
  indented, saving keeps your place (`data-keep-scroll`). Every row (a type's head and its subtypes, in every card) is
  the same CSS grid (`.ar-row`: name · count · Counts as / Move to · ↑↓ · trash can, fixed widths; Move to is an up-down icon opening a menu, its current place grayed out), so the columns line
  up down the page; an opened delete confirmation drops down under its trash can. On phones the drop-down moves under
  the name. (Per-card tables were the alternative; they can't align with the header or other cards.) Categories uses the
  same grid (`.ar-row.no-order`, 2026-10-25).
  **Split across two pages (user's request 2026-10-13):** Balances = $ / % toggle + date range, tiles (removed later that day), the Net worth card
  (alone, `.cards.n1`), the Accounts card + "Not counted" note (moved from Net worth). Net worth = the $ / % toggle (default **$** there, % on Balances;
  user's request 2026-10-13: `balances_context(default_show=)`) + date range in the ribbon (default **90 days** there, 365 on Balances;
  `default_range=`; the hero / Assets / Liabilities tiles (all three figures in the hero's 2.9rem `hero-value`, user's request
  2026-10-13) three equal columns on one row at every width, the figures scaling with the row (`.nw-kpis` in a
  `.nw-kpis-wrap` container, font clamp(1.1rem, 5cqi, 2.9rem); user's request 2026-10-13, which replaced a
  half + quarters layout tried that day); each show the range's % change
  (`networth.range_change`, same baseline as balances.py; liabilities = % of the amount owed, opposite colours; it
  replaced the hero's "since last month" dollar change, which the Overview still uses); same
  `show_toggle` macro and `_range_controls.html`, its own `<form id="txn-filters" class="form-anchor"
  action="/networth">`; user's requests 2026-10-13), hero tiles, **Over time** following both
  (`visuals.networth_trend_range`: every day of the range up to today, empty before the first snapshot; $ = net worth,
  % = change since the day before the range (else the first snapshot), dollars in `net`; `area` kind with
  `data-unit="pct"`: % axis padded by `pctRange` (±0.5 when flat), tooltip/table add the dollars; its 90 / 180 / 365
  buttons were removed; Overview's area chart unchanged), then the cards per kind (Retirement, Investment, Other assets,
  Cash, Credit cards) and the Change in value table. Shared: `web/accounts.
  balances_context(request, conn, path)` and the `_balances.html` macros (`signed`, `pct`, `arrow_pct`, `change_card`,
  `kind_cards`, `change_table`, `show_toggle`). A template that includes `_range_controls.html` must not import
  `_charts.html`'s `ranges` macro under that name: the control reads a `ranges` variable, and the macro shadows it (bug
  found while building, 2026-10-13).
- **Account page: filters and cards (user's request 2026-10-29; `web/accounts.account_transactions`):** a bank or card
  account's Transactions card has the Transactions page's filters: search (words and amounts), Card (only for an account
  with an authorized user's card: Primary / each AU card, `?card=<id>:main|<id>:<last 4>`, anything else ignored),
  Category, From / To; all dates by default (newest 200, as before). Built with `transactions._filters_from` / `_query`
  (the account, or the card, as the account filter), so search and category work exactly as on Transactions; "All of
  them on Transactions" carries the same filters. A plain GET form; with HTMX only `#acct-txns` refreshes, so the search
  box keeps focus. While filtering, the rewards balance lines are left out (a balance is the whole account's). With an
  AU card: a **Cards** card (`authorized_users.card_summaries`: each card's transactions, spending over 365 days, last
  used; each links to its filter) and a **Card** column in the table ("1234", "1234 (AU)" for an authorized user's;
  `transactions._account_columns` gives `card_col` to rows, edit panels and rule panels returned alone, like the
  rewards column).
  **Sorting (user's request 2026-10-29):** Date, Description, Category, Card and Amount headings sort on the server
  (`transactions.SORTS`, `?sort=&dir=asc`; the whole filtered list before the newest-200 cut), again = reverse, ▲ / ▼
  as on Transactions. With HTMX a heading refreshes only `#acct-txns` (no jump to the top); the current sort sits in
  hidden inputs inside it tied to the form (`form="acct-filters"`), so a filter change keeps it. The rewards column
  isn't sortable (worked out per row in Python). Rewards balance lines only show in the default order (newest first).
- **Manual accounts** (`accounts.is_manual = 1`, no Item): `current_balance_cents` = latest
  `manual_valuations` row by date (no future dates; values ≥ 0; liabilities = amount owed).
  After any valuation/kind change, `manual._refresh` rebuilds that account's
  `balance_snapshots` on every **existing** snapshot day (step function: latest value ≤ day)
  and retakes today's snapshot. It never creates days before the first snapshot.
  Archive = `is_hidden` (past rows kept, today dropped); delete = rows + valuations + snapshots
  removed (typed name). Sync never touches manual accounts. Manual types: depository,
  investment, **property** (own breakdown row), other, loan, **credit** (Credit card, 2026-11-13).
- **Manual accounts that hold transactions (user's request 2026-11-13; docs/import-plan.md phase 4):** manual
  checking / savings ("Cash"), credit cards and loans (`manual.TRANSACTION_TYPES`, `takes_transactions`) hold
  transactions: imported (Import page) or added by you ("Add a transaction" lists them, as do the Transactions filters
  and Recurring's form). They count everywhere like a connected account's (the view only joins `accounts`); a manual
  card is on Credit cards, rewards and statement estimates like any `type = 'credit'`. **Balance**
  (`manual.value_on`, user-approved design 2026-11-13): your latest value on or before the day, moved by the
  transactions after its date (owed: + purchases; have: − purchases); before your first value, worked back from it, from
  the first transaction on; so "owes $1,234 today" + an imported year gives a year of history. An import's running
  balance adds values (engine.py). `manual.refresh` after an import, its undo, or a transaction you add or delete. The
  account's page shows History (values) and Transactions, and "Import a file" beside Manual. **A manual card's
  statement** (2026-11-14, docs/parity-plan.md): closing and due days (and a minimum) on its page
  (`card_statement_settings`, migration 0072); `cards.refresh_manual_statements` writes its current statement into
  `card_statements` like Plaid's: the balance owed at the end of the closing date (`manual.balance_on`), due on the next
  due day; the closing date can be changed within the current period and the balance follows. Deleting a manual
  account deletes its transactions, activity, statement and paying-account rows too. **Last 4** (Details,
  manual accounts; POST `/accounts/{id}/mask`, 2-4 letters or digits): the account's number, which tells a card's own
  rows from authorized users' (`authorized_users.AU_SQL` compares each row's card with it).
- **Account categories: REMOVED 2026-10-13 (user's request; migration 0035).** Your account types (migration 0032,
  `account_types.py`) replaced them: an account's Type decides its effective type / classification (`account_types._apply`
  / `_effective`, moved from the deleted `account_categories.py`), retirement vs investment and chart labels
  (`visuals.is_retirement_account(account)` / `investment_type_label(account)`: type first, else Plaid's subtype; the
  categories argument is gone), and the Overview's Accounts card groups by the account's top-level type
  (`account_types.top_types`, `dashboard.account_groups(types=)`). The `account_categories` table and
  `accounts.category_id` stay in the schema, empty and unused: SQLite can't drop a column in a foreign key, and dropping
  the table makes every later write to `accounts` fail ("no such table", tested). Don't reintroduce categories.
- **Purpose accounts (migration 0028, user's request 2026-10-12):** money moved out of a bank account toward a defined
  purpose (first use: money lent to someone for school, possibly partly written off later). A manual account of kind
  **Money lent** (`manual.KINDS["lent"]`, `accounts.type = 'receivable'`, asset; Net worth row "Money lent"). Its balance
  is never typed (`manual._refuse_if_positions` refuses typed values): `purposes.refresh` writes the running total of its
  linked transfers (Plaid sign: money out of the bank adds, money back reduces) + `purpose_adjustments` into
  `manual_valuations` (note "from transfers"), floored at $0, only when changed; a typed starting value at creation becomes
  an "Opening balance" adjustment. **Links** (`purpose_links`, one per transaction): `purpose_rules` ("bank name or
  merchant contains", applied by `purposes.update` after every sync, on rule changes, category edits, bulk edits and
  page loads of the account; lowest rule id wins) or by hand from the Transactions edit panel's **Purpose** select (shown
  for transfer-kind rows); a hand link or unlink (`account_id` NULL) wins over rules. **Group edit** (user's request 2026-10-12): Transactions'
  bulk bar has "Link to purpose…" + **Link** (`formaction="/transactions/bulk-purpose"`, `formnovalidate` because the
  category menu is required for Apply only); ticked rows or all matching (`all=1`, the confirm text names the action via
  `event.submitter`); "None (unlink)"; non-transfers are skipped and counted in the message; links set this way are yours. Only transfer-kind transactions on
  visible non-investment accounts link. **Savings:** linked transfers and adjustments are left out entirely (not "other transfers", not
  investment pairing candidates, not Net cash); the Savings page is what was saved from income. The account's value
  (net worth, Accounts, Net worth cards) includes them. (An Other assets column on the Balances view of Savings counted
  them as saved until that view was removed, 2026-10-14.)
  Account page: History (linked transfers with Unlink, adjustments with Delete, "Add adjustment") and Rules. Known
  limitation: a hand link on a pending transaction is lost when it posts (rules re-link).
- **Manual positions (migration 0019, user's request 2026-10-07):** a manual account of type
  `investment` can hold lines (`manual_positions`): ticker × quantity, or a named asset with a $
  amount (cash etc.). Amounts are dated (`manual_position_changes`, "from D: N"; latest ≤ day
  applies; "+N"/"-N" = relative to that date's amount; 0 = sold). Prices (`ticker_prices`, one per
  ticker per day; source rank you > alpaca (= old finnhub rows) > plaid, a weaker one never replaces a
  stronger; migration 0020 added 'alpaca'): live quote or Alpaca daily history, else Plaid's latest
  `security_prices` for the same ticker, else the last known; a day uses
  the price ≤ it, else the earliest known. `revalue` writes `manual_valuations` (note "from
  positions") for today, the change date and every value/snapshot day after it, then
  `manual._refresh`; with lines, typed values are refused and the Add-a-value form is hidden.
  **Since 2026-11-14 (docs/parity-plan.md; user's request the same day): the card is "Balances" and lists balance
  updates only** (yours, a statement's, values typed before the account had holdings; for a bank account / card / loan
  its values, a file's running balance included). A manual account's contributions, withdrawals, fees, buys and sales
  are activity (Add activity under Transactions, `fintrack/ledger.py`), which rebuilds the lines' changes
  (`from_activity`, not listed in Balances). "Add balance" (a holding's balance) and, with no holdings, "Add value" /
  "Add balance" (the whole account; was the separate "Add a value" card). The rest of this paragraph is how it was
  before.
  **Account page = History + Add (user's requests 2026-10-12; the separate Positions card, line "Change" panels, Add line,
  Remove line and Refresh prices were removed as redundant):** an investment account's page is Current value, then one
  **History** table and an **Add** button (`details.add-event`) whose form is: date · ticker (datalist of the account's
  lines; a new ticker becomes a line, kind 'etf', name = ticker) · **Contribution / Withdrawal / Fee / Balance**
  (`EVENT_TYPES`) · **Quantity / Value** (`EVENT_UNITS`) · amount → POST `/manual/{id}/events` (`record_event`). Each
  History row edits in place (date, type, unit, amount; POST `/positions/{id}/changes/edit` → `edit_event`, inputs tied
  to the row's form with `form=`) and shows the event's own shares and dollars; Delete of a ticker's last event removes
  the line. A ticker with no price shows a "Set price" form above the table. Values typed before the account had
  tickers are listed below (editable while it has none, with "Add a value"). Mapping to rows (migrations 0025-0026):
  Contribution/Withdrawal + Value = `entry='contribution'`, `cash_cents` ±; + Quantity = `entry='contribution'`,
  `delta_quantity` ±; Balance + Quantity = plain `quantity` (entry NULL); Balance + Value = `entry='opening'`,
  `cash_cents` (shares = value ÷ that day's price). Derived shares are re-computed on every revalue (`rederive`, writes
  only on change) so typed dollars stay exact when a better price for that day arrives; an event that can't be priced
  or would go below zero is refused and rolled back (a new line with it). **Fee** (migration 0027, user's request 2026-10-12) = stored like a withdrawal with `fee = 1`; it
  reduces the line but the Savings page leaves it out (a cost, not money taken out). **Balance exists so a starting holding isn't
  counted as a contribution** (Savings counts Contribution/Withdrawal on retirement accounts only, shares priced that
  day). The daily "from positions" valuations are NOT shown but remain the source of the account's balance and snapshot
  history (net worth, Portfolio charts). Non-investment manual accounts keep "Add a value" + editable values.
  Lines are mirrored into `holdings` (+ `security_prices`) for Investments/movers/performance; a
  ticker also held in a Plaid account reuses its security_id. **Revalue runs on page loads**
  (Overview, Investments, Portfolio, the account page: user's decision 2026-10-07, an exception to "POST
  only"), at Sync now / CLI sync, and on every line change. Every such write is skipped when
  unchanged: a no-op page load must not bump `app_revision`, or open tabs reload each other.
- **Live balances (migration 0029, user's decision 2026-10-12: "live prices should impact net worth"):** a connected
  investment account's `current_balance_cents` (what net worth, snapshots and every page read) = `reported_balance_cents`
  (the institution's balance at the last sync; `items.upsert_accounts` writes both) + Σ over its quotable holdings with
  a live quote (shares × live price − the value reported at sync). Cash and unquoted holdings (e.g. mutual funds Alpaca
  doesn't cover) keep the reported figure: the balance is the institution's, marked to market, never replaced by a sum
  of holdings. `live_values.revalue_all` (manual positions, then connected accounts) runs wherever quotes are fetched:
  Overview, Investments, Portfolio, a connected investment account's page, Sync now and the CLI/scheduled sync; every
  caller fetches the SAME full set (`prices.held_tickers` + manual tickers, 5-min cache) so a partial set can't drop
  another account's move. Writes only on change, then retakes today's snapshot (past days keep their values). Rejected:
  balance = Σ holdings (holdings and balances can disagree; the balance is the institution's figure).
- **Account names:** `accounts.name` is what every page shows. For Plaid accounts it's
  `account_names.short_name(plaid_name, official_name, mask)` on every sync unless
  `name_is_custom = 1` (you renamed it on Accounts). Plaid's own name is kept in `plaid_name`.
  Rules are generic (no per-institution cases). Manual accounts keep their own names. Two accounts in one connection that would get the same automatic name (e.g. two cards a bank calls
  "Fake Rewards") each get " ····<last 4>" (`account_names.unique_names`, used by sync, startup and clearing a name;
  decided over all the connection's accounts so the paths agree; user's request 2026-10-14).
- **An account's page on a phone (user's choice 2026-11-11):** the first 25 transactions, then **Show more** (ui.js:
  the later rows and their date headings get `phone-later`; the button removes it and marks the list `data-show-all`,
  so an edit panel's swap doesn't hide them again; a list swapped in by a filter or sort is limited again). The page
  went from about 20.7 screens to 4.1.
- **On a phone (2026-11-06, user's request):** the Net worth page's cards default to Compact (`remote.via_phone`; the toggle still switches), two a row with small charts (`data-mini-charts`), and the Net worth chart is 100px tall; the three tiles' change reads on one line with the range's short form ("▼ 1.5% 365D"); docs/ui.md Phones.
