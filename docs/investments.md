# Investments and Portfolio

Security prices, performance cards and chart, Portfolio, transfers in and out of investment accounts, destination rules, investment transactions and activity, trading-day ranges.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

- **Destination rules + raw bank text (migration 0030, user's request 2026-10-12):** a bank-side investment transfer says
  which bank account it came from, never which investment account received it; pairing with the institution's own row
  decides that, but an institution's investment history can start late (unpaired → "Account unknown"). Plaid's
  `original_description` (raw bank text, e.g. "… ***********NNNN") is now stored (`/transactions/sync` with
  `include_original_description`; history via `fintrack descriptions --backfill [--dry-run]`, read-only, only that
  column; run on Production 2026-10-12). `destination_rules` (account, pattern): after pairing, an UNPAIRED bank transfer
  whose raw text / name / merchant contains a rule's text (case-insensitive, oldest rule first) takes that account and its
  group (retirement vs investment by the account; `contributions._apply_destinations`). Managed on a connected investment
  account's page ("Destination rules": each rule with how many transfers it covers, last 24 months). The transaction edit
  panel shows "Raw: …" to find rule text. Checked on Production: the raw reference is the same for every transfer to one
  brokerage (and a different reference also reached it), so it identifies the funding link rather than a specific
  account; a rule on it filed every pre-history transfer under that brokerage, nothing left unknown. A rule only places a
  transfer for these totals (and its logo): it isn't a matched transfer (user's request 2026-10-31); a bank transfer
  paired with the account's own activity is (transfers.detect_transfers; docs/transactions.md, Transfers page).
- **Security prices (M13, migration 0012):** Plaid holdings only carry the current price, so
  `store_holdings` records one row per security per price date (`institution_price_as_of`, else
  the sync day) in `security_prices`. Movers = latest vs previous price date, combined across
  accounts, cash excluded; columns price / change / value (user removed gain vs cost); ranked by the size of the %
  change, up or down (user's request 2026-10-23: the Overview's top 5 are the biggest moves), by value until two price
  dates exist. **SPY is always the 6th row** on the Overview (`movers(benchmark="SPY")`, `dashboard.MOVERS_BENCHMARK`):
  live quote, else its last two `benchmark_prices` closes; your holding (with its value) when you hold it, kept out of
  the ranking; "Show all" lists the rest after it. **The card is titled "Investments"** (was "Investment movers"; user's
  request 2026-11-04) **and its title is a dropdown**: Investments (your holdings, as above) or **Watchlist**
  (`prices.watch_movers`: your watched tickers, price = live quote else latest `ticker_prices` close, change vs the previous
  close else between the last two closes, biggest moves first, SPY the same 6th row; no Value column), remembered in
  app_settings 'overview_movers' (POST `/overview/movers`); switching keeps the page where it was (the card is
  `data-keep-scroll`; keep-scroll.js now restores against the same area the save came from, as the Overview has two). Only `prices.QUOTABLE_TYPES` (equity, etf, mutual fund) are
  sent to Alpaca or take a live price (a crypto "BTC" would otherwise match the BTC ETF). **Live
  prices DO reach net worth since 2026-10-12 (user's decision, reversing the earlier rule):** see "Live balances" below.
  Holdings rows themselves stay Plaid's.
  `store_holdings` wipes holdings for all of the Item's accounts (not just type investment);
  merged tax lots have a cost basis only if every lot does. Checked at 1710 (15" MacBook full
  screen), 1440, 1024, 900, 800. Panels use `--surface` (raised), the rail
  `--rail` (sunken); no shadows. System font (SF Pro): no font files.
- **Performance cards (Investments, migration 0018, user's request 2026-10-07):** four tiles above
  the Performance chart: Your stocks, S&P 500 (SPY), Russell 2000 (IWM), MSCI World (URTH). Headline =
  % change over the chart's selected range: 1 day, 1 week, YTD (from last Dec 31), 90, 180,
  365 days (YTD moved after 1 week, user's request 2026-10-07) (`performance.ranges()`, user's request 2026-10-07; each has a stable key, "ytd" etc., that
  the buttons remember and tiles match, plus `chart_days` = look-back + 1 so the chart starts where
  the % does). Each tile renders every range; `charts.js` `showRange` shows the chosen one via
  `data-range-of`/`data-range-show`; `ranges(..., options=)` draws named buttons (`data-range`),
  "since <date>" when history is shorter, "—" without an earlier price; second line = today's change
  (live quotes only). Your stocks = current non-cash holdings weighted by current value (user's
  choice), each from its `security_prices` price on/before the range start (else its first) to its
  live price (else latest recorded): holdings' change, not money-weighted. Benchmarks are ETFs
  because Alpaca's stock data has no indexes. `benchmark_prices`: today's price recorded on
  Investments page loads and after every sync (date = the quote's local date), past days filled
  with official closes by `price_history.backfill` at every sync; unchanged prices aren't rewritten. **Benchmark cards are toggles**
  (user's request 2026-10-07): `role="button"` + `aria-pressed`, `data-compare-for`/`data-compare`;
  clicking one draws that benchmark on the chart (again = off; per page view, not remembered).
  **Investments table rows toggle lines too** (user's request 2026-10-07): a priced holding's row
  (`tr[data-row-toggle]`, name = a `<button class="row-toggle">`) adds "sec:<security_id>"; prices from
  `performance.holding_series` (security_prices + today's live price) merged with the benchmarks into
  the chart's own data. Up to 4 lines at once (`MAX_COMPARE`, palette slots 2-5; a 5th drops the oldest);
  canvas `data-compare` = comma list; each active toggle gets `data-slot` for its color marker.
  **The chart is % change** (user's request 2026-10-07; was account value from snapshots, and lines were
  then scaled as "your investments if…"): `charts.js` kind `performance`, data `{series, mine}` from
  `holding_series` (+ `value_cents` weights) and `benchmark_series`. "Your stocks" = Σ value-weighted %
  change of each holding from its price on/before the range start (else its first price, 0 before it),
  over holdings with a change by today, so it ends at the Your stocks card. Toggled lines = each
  security's own % change. % axis padded 5% each side (flat: ±0.5%), includes 0. Title "Performance",
  "as of" = newest live-quote fetch time (else last sync). The old line logic below is gone:
  (was: `compareLine`: your value on the range's first day with
  both a value and a price, times the benchmark's % change since (latest price ≤ each day), in
  `--series-2` with a legend, on the same money axis (never a second % axis), included in the
  padded y-range and the "Show data" table. Prices reach it via `performance.benchmark_series`.
  **Watchlist (user's request 2026-11-04; migration 0066 `watchlist`, `watchlist.py`):** the Investments card has two tabs
  (`[data-card-tabs]`, ui.js; `?tab=watchlist` opens the second, and adding or removing a ticker returns to it): your
  holdings, and tickers you watch, in the same table without Quantity and Value ($ change = one share's, in dollars and
  cents; `performance.watch_rows`), each row toggling its line on the chart (`tick:<T>`, `watch_series`; not part of "Your
  stocks"; its minute prices on "1 day" too). **Find one by ticker or company** (user's decision 2026-11-04; migration 0067
  `alpaca_assets`): the + at the card's top right (on the Watchlist tab only, `data-tab-only`; user's request 2026-11-04) opens a
  popup (`[data-popover]`) whose box lists up to 40 matches as you type, in a list that scrolls (HTMX GET `/investments/watchlist/search`,
  `watchlist.search`: exact ticker, then tickers starting so, then name matches: good ones first (a word starting so, or a
  loose match, `_loose`, user's request 2026-11-04: a whole word of 3+ letters then the rest later, so "spacex" finds
  Space Exploration Technologies, SPCX, whose name never says SpaceX), then a name merely containing it; among each,
  companies before funds, warrants and units (`_FUND`: by their names, as Alpaca doesn't say), then word starts before
  loose ones; OTC after the rest; already watched ones left out), each a one-click Watch with its company name. They come from Alpaca's
  list of active, tradable US stocks and ETFs (`QuoteGateway.assets`, docs/plaid.md), stored here and refreshed about
  weekly after a sync (`refresh_assets`; the first search fetches it when it's missing), so nothing leaves the app as you
  type; a trailing "Common Stock" is dropped from names. Or type a ticker and Watch (its name: your holdings', else
  Alpaca's): it's added only when Alpaca has a live price (a typo is
  refused), and its ~400 days of closes are fetched at once into `ticker_prices` (source alpaca, like manual positions'),
  then kept by `price_history.backfill` with everything else. Only ticker symbols go to Alpaca. Trash can = stop watching
  (its prices stay; a manual position may share them).
  **Investments table** (below the chart, user's request 2026-10-07; first ETFs/funds only, then every
  non-cash holding, titled "Investments", **no type column** by the user's choice): `performance.holding_rows`
  = held non-cash holdings (Plaid + manual positions) combined by security_id; columns name, ticker,
  quantity (shown as whole shares, halves up; exact for sorting; value and $ change also shown in
  whole dollars via `money_whole`, user's requests 2026-10-07), price (live else latest recorded), value (shares × live price, else recorded value; same
  rule as movers), $ change (= your shares × price change, user can ask
  for per-share instead), % change, per range with the same start-price rule as the cards (cell
  title "since <date>" when shorter). Sortable by any column (`ui.js` `sortTable`: `data-sortable`,
  header `button.sort[data-sort]`, rows' `data-sort-<col>[-<range>]`, empty last both ways, `aria-sort`);
  default % change desc (also the server order). Follows the range buttons: `charts.js` `showRange`
  dispatches `fintrack:range`, and tables with `data-range-table` re-sort on that range.
- **"1 day" by the minute + a "1 day" column (user's request 2026-10-27):** on the "1 day" range the Performance chart
  draws the last session minute by minute (`performance.intraday_series` → `perf_chart.intraday` = `{series: {key:
  [[unix seconds, price], …]}, base: {key: previous close}}`, keyed like the daily series; `charts.js` `intradayLines`,
  picked when the canvas's `data-range` is "1", set by the range buttons). Each line = % change vs the previous close,
  so it ends at the cards' "today"; "Your stocks" is value-weighted over holdings with minute prices (live-quoted ones:
  mutual funds Alpaca doesn't price are left out of that line on 1 day only). Minutes come from
  `QuoteGateway.intraday` (Alpaca `/v2/stocks/bars?timeframe=1Min`, regular hours 9:30-16:00 New York): all exchanges
  (`sip`) up to 16 minutes ago, then real-time `iex` bars after the last SIP minute; one call per feed, side by side,
  at most `TOTAL_SECONDS`; cached in memory 5 minutes (`_minutes`), never stored. The live quote is the last point, at
  4 PM at the latest (an after-hours IEX trade replaces/follows the 4 PM bar, so the axis stays on the session). Axis:
  a label on each hour (every other hour under 480px); tooltip and "Show data" show times and 2-decimal %. Without
  minute prices (no keys, Alpaca down) "1 day" falls back to the daily two-point line. **Investments table "1 day"
  column** (right of Value, sortable, not tied to the range; an "N day Period" header row spans $ change and % change,
  the columns that follow the range buttons; N = `Range.period_days`: the look-back, but 5 for 1 week (trading days)
  and the days since Dec 31 for YTD; it switches with the buttons like the cards: `tr.th-groups` / `th.th-group` in app.css; both user's requests 2026-10-27): `HoldingRow.day_change` = the live quote's change vs the
  previous close (the cards' "today"), else the recorded 1-day range change.
- **Transfers in and out of investment accounts (Portfolio, and copied to Savings after "Net savings rate"; user's requests
  2026-10-09; one shared partial `_investment_transfers.html`, chart id `inv-transfers`, context `transfers`):** `contributions.monthly` /
  `visuals.investment_transfers`, the existing `flow` chart kind with `in_label="Into investments"` / `out_label="Out of
  investments"` (blue up = money moved into investment/retirement accounts, red down = out; the "Show data" headers now
  follow those labels). Two sources, combined so nothing is counted twice: (1) **bank side**: transactions whose Plaid
  detailed category is `TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS` (into) or `TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS`
  (out) on non-investment accounts (checked in Production 2026-10-09: both codes occur, only on bank accounts; signed sums so
  reversals net out; hidden accounts out; a transaction you moved out of the transfer categories is left out); (2)
  **investment side** (migration 0023 `investment_transactions`, see "Investment transactions" below): `cash` transactions
  with subtype `contribution`/`deposit` (in) or `withdrawal`/`distribution` (out); buys, sells, dividends, interest and fees are
  ignored. A bank row and an investment row of the same direction and amount within `MATCH_DAYS` (5) days are ONE event: counted
  once, dated by the investment row, each investment row pairing at most once (nearest first). Unpaired rows still count:
  payroll/employer contributions (investment side only), and transfers into accounts we can't see inside (bank side only).
  **Two kinds of account (user's request 2026-10-11):** every flow is assigned to `retirement` or `investment`
  (`contributions.GROUPS`), shown by the `transfers` chart kind (not `flow`): 4 stacked series, colour = kind (slot 1 retirement,
  slot 2 investment), above the line = in, below = out, legend lists each kind once, tooltip/"Show data" name the direction.
  **Pairing (2026-10-11, checked on Production):** each investment-side cash row is paired, in order, with (1) a bank
  row with Plaid's investment-transfer code, (2) any other bank row whose effective kind is `transfer` with the same
  amount within `MATCH_DAYS` (`_bank_transfer_candidates`; Production's brokerage withdrawals reached checking as
  "… PAYMENTS" filed by Plaid as INCOME_SALARY and recategorized by the user; unpaired candidates are ignored), (3) a
  row of the opposite direction in ANOTHER investment account (`_pair_internal`: e.g. brokerage -> traditional IRA, a Roth
  conversion; counted in both groups, neither bank money nor new money). Unpaired investment-side contributions are
  "outside" money (pre-tax income). Cash rows whose description has the word "sweep" (`SWEEP_WORDS`) are ignored:
  a brokerage reported settlement-fund sweeps as deposit/withdrawal (a large "Sweep out" beside the real withdrawal).
  **Investment transactions are ON in Production since 2026-10-11** (user: Trial plan, no billing set up;
  `INVESTMENT_TRANSACTIONS=on` in `.env`): the brokerage returned only ~3 months of history. Plaid's Transactions
  product never covers investment accounts (see Verified Plaid facts).
  **Two charts since 2026-10-11 (user's request):** "Transfers in" and "Transfers out" cards side by side (`.grid-2`), each the
  `transfers` kind with `data-direction="into"|"out"`, drawn upward on its OWN y-axis (one large withdrawal had flattened the small
  monthly contributions on the shared axis); colour per kind is the same in both; an empty direction shows a note
  (`has_into` / `has_out`).
  Investment-side flows are assigned by their ACCOUNT with the same rule as Portfolio's Retirement tile
  (`visuals.is_retirement_account`: your account type if set, else Plaid's subtype, in `RETIREMENT_TYPES`; Portfolio now
  calls the same function). A pair takes the investment account's kind. A bank-side transfer with no matching deposit has no
  destination account (Plaid doesn't say), so its description decides (`group_of_text`: ira, roth, 401k/403b/457b, hsa, pension,
  retirement, rollover as whole words; else investment). Checked on the real Production data 2026-10-11: every
  bank-side transfer in the last 12 months classified as investment and none as retirement, because the descriptions don't name
  the account type; so until investment transactions are enabled in Production (or transfer rules exist), real retirement
  contributions will show as investment. Without investment transactions (Production's default) it is bank-side only. Not seen at all: payroll deductions into an
  account that reports no activity (e.g. a manual 401(k)), reinvested dividends. Last 12 months ending with the current one, fixed (not tied to the
  ribbon's range buttons); the card shows a plain note instead when there are none. The demo data has monthly contributions,
  a yearly April Roth IRA lump sum and two withdrawals (`demo.WITHDRAWALS`); its investment side (`demo._investment_activity`)
  mirrors each bank transfer two days later and adds a $400/month payroll-style employer contribution into the IRA that
  the bank never saw, plus buys and quarterly dividends.
- **Investment activity shown (user's request 2026-10-12):** Plaid investment transactions are DISPLAYED in two places,
  never mixed into `transactions_effective` or any total outside the Transactions page: (1) **Transactions page, in the
  list with your transactions** (user's request 2026-10-30; was its own kind "Investment activity", with a Type column):
  `investment_activity.as_transactions` = `transactions_effective` UNION ALL the activity as rows of the same columns
  (id = −its id; `category_source` 'investment', or 'you' for your choice; sweeps left out). Each activity's category is found **by name**
  (`CATEGORY_CHOICES`, "for now"): dividend → Investment Income › Dividend, interest → … › Interest, sell → … › Sell
  (else Investment Income, else INCOME / TRANSFER_IN), buy / fee → Investments › Buy / Fee (else Investments, else
  TRANSFER_OUT), cash in / out → Transfer in › Deposit / Transfer out › Withdrawal (else TRANSFER_IN / TRANSFER_OUT); its
  kind is that category's. The page's list, counts, pie and Money in and out read it (`visuals.category_rings` /
  `period_totals` `source=`); everything else doesn't. **Editable like transactions** (user's request 2026-10-31;
  migration 0052): your description (`user_description`; Plaid's wording back = automatic) and category
  (`user_category`, which sets the row's kind; blank = the one by type), and tags (`investment_transaction_tags`;
  `tags.py` takes signed ids, `tags.LINKS_SQL` reads both tables, the Tags page sums tagged activity). Sync's upsert never
  writes these columns, and `categories.delete_category` moves them like transactions'. Same row (`_txn_row.html`
  `activity` = id < 0: box, edit panel, the ticker and a trade's quantity × price from
  `investment_activity.details`), same routes with the negative id (`web/transaction_edits._shown`, `_save_activity`; group
  edit's `_targets` / `_ids`, "Select all matching" = every row). The panel shows "From Plaid: name · type · ticker" and
  leaves out rules, recurring, reward bonus, purpose links and transfer matching (they're for bank rows; group "link to a
  purpose account" skips activity); (2) `/accounts/{id}/activity` (connected investment accounts only): value
  tile + all activity, linked from the account's name in the Accounts card and on Settings → Setup (now the account's
  page; since 2026-10-31 its activity is in the bank / card table there, editable, server-sorted: docs/accounts.md). Above it a **Holdings** card (`performance.account_holdings`, user's request
  2026-10-12): holding, quantity, price (live Alpaca quote for quotable types, else latest recorded), value (shares ×
  live price, else Plaid's value: the Investments table's rule, so the pages agree), cost basis where reported ("—"
  otherwise); cash = one value-only line; largest first, sortable; totals (cost basis summed over reported ones). The
  value tile at the top shows the holdings' total (user's request 2026-10-12; the reported balance is in its tooltip, and
  is what net worth keeps using); without holdings it shows the balance. Sweeps are
  hidden (`lower(name) NOT LIKE '%sweep%'`, the SQL twin of `contributions.SWEEP_WORDS`). Sign = Plaid's investment
  sign, shown like bank rows (negative = cash in, "+").
- **Investment transactions (migration 0023, user's request 2026-10-09, built after testing in the Plaid Sandbox):**
  `investment_transactions` (Plaid investment sign: amount > 0 = cash debited, < 0 = credited; enum fields stored as text).
  `PlaidGateway.get_investment_transactions(access_token, start, end)` pages 500 at a time (SDK dates are `date` objects).
  `sync.sync_item(..., investment_transactions=False)` / `sync_all` read them for Items whose `products` include
  `investments` and that have investment accounts: the first time 730 days, then from 30 days before the newest row (the API
  has no cursor; upserts are idempotent and skip unchanged rows so a quiet sync doesn't bump `app_revision`). A Plaid error
  there (e.g. PRODUCTS_NOT_SUPPORTED) becomes a note, not a failed Item; login-required still fails it. **Policy:**
  `Settings.sync_investment_transactions` = Sandbox always, Production only with `INVESTMENT_TRANSACTIONS=on` in `.env`
  (documented in `.env.example`), because the first call adds a billed subscription; web `/sync`, `fintrack sync` and
  `demo.seed` pass it. Tested against the real Sandbox (default `user_good` at ins_109508, 2026-10-09/11): 1,169 rows over
  24 months for the IRA and 401(k) - 439 buy, 365 sell, 146 cash/contribution ("EMPLOYER PAYROLL CONTRIBUTION" $1,500 and
  "INVBANKTRAN DEP CO CONTRIB CURRENT YR EMPLOYER CU" $1,200, three a month each), 73 each of dividend, interest, account
  fee; with the flag off the endpoint is called 0 times; a second sync is incremental and quiet. Subtype does not separate
  employee from employer money (only the description does; not used yet). Sandbox data repeats identically month to month.
- **Portfolio by account type (user's request 2026-10-07; replaced the investment mix chart):**
  `visuals.investments_by_type`: investment snapshots per day (range buttons in the ribbon, top right,
  user's request 2026-10-07), carried like net worth history, grouped
  by `investment_type_label` = your account type's name > Plaid subtype (`SUBTYPE_LABELS`, e.g.
  401k → "401(k)") > "Other investments"; labels equal after `_type_key` (alphanumerics, lowercase)
  merge, the category's spelling wins (a "401K" category + Plaid 401k = one band). Largest last-day
  first; >5 → 4 + "Other". `stackedarea` now takes range buttons (`data-days`) and an optional `owed`.
  **Top tiles (user's request 2026-10-07):** Total investments, Retirement, Investment accounts
  (`performance.portfolio_measures`): balances of counted investment accounts (retirement = type key in
  `visuals.RETIREMENT_TYPES`: 401(k)/403(b)/IRA/Roth/HSA…, user's choice incl. HSA; the rest = investment
  accounts; so they add up to the total) + **market performance** of each group's current holdings per
  range (user's choice over snapshot value change; `_holdings_change`, shared with "Your stocks"),
  following the chart's buttons: 1 day, YTD (selected first), 1 / 3 / 5 / 10 years (`performance.portfolio_ranges`;
  user's request 2026-11-13, was YTD / 90 / 180 / 365; Investments keeps `ranges()`, and every change is worked out for
  `all_ranges()`). Ten years of closes are fetched once per ticker (`price_history.DEEP_DAYS`, app_settings
  `price_history_deep`), then the usual 400 days; the By-account-type chart takes up to 3651 days of snapshots (it
  shows the history the app has). Hand-valued manual accounts count in totals only. Always one
  row of three equal columns (`.kpis.one-row.large`, user's request; figures up to 2.25rem, labels/% lines up to
  1rem, all shrinking via clamp on narrow windows). Investments' 4 performance cards use `.kpis.one-row` too
  (one row at any width, normal sizes; `one-row` sizes columns by tile count). **Holdings table** (user's request 2026-10-07): Holding,
  Value, Cost basis, then % change for the same ranges as the buttons (`performance.holding_rows` by security_id,
  same rules as the Investments table); no Quantity; whole dollars and whole percents (halves up); account
  rows at 1rem (`table.data.holdings tr.group th`). **Filters** (user's request 2026-10-07): name/ticker
  text, account, kind (Stocks/ETFs/Funds/Bonds/Cash/Other via `visuals.SECURITY_GROUPS`, only kinds held);
  client-side in `ui.js` `filterTable` (`table[data-filterable]`, controls `[data-filter-for][data-filter]`,
  rows' `data-account/-kind/-search/-value`); account rows hide when empty and their `[data-subtotal]`
  shows the visible rows' total (original text when unfiltered); "No holdings match." row; not remembered.
- **Ranges end on the last trading day (user's report 2026-10-08):** `performance.market_date(live)` =
  the newest live quote's date (else today); cards, Portfolio measures, both holdings tables and the
  Performance chart (`data.end`) run "as of" it, and a live price is stamped with its quote's own date
  (`quote_date`). Before, after midnight / at weekends "1 day" compared yesterday's close with itself
  (≈0, plus Plaid-price noise) while "today" showed yesterday's session (▲0.1% vs ▼0.1%). Now 1 day = today.
- **On a phone (2026-11-06, user's requests):** the four performance tiles stay one row, each line unwrapped (the symbol for the name, 90D, 1D: `.w-short`); holdings and the watchlist are a table of ticker (its button; the name in the tooltip, no underline) · price · 1 day · the range's % change, the other columns and the watchlist's remove button hidden; both tables share fixed columns (each has a `<colgroup>`: ticker 24%, price 30%, 1 day 22%, range % 24%; the hidden columns' `col`s are `display: none` like their cells), so they line up exactly and a tap (bold + swatch) never moves a column; docs/ui.md Phones.
- **Portfolio on a phone (2026-11-06, user's requests):** the third tile reads "Investment", the tiles' figures fit a third of the row and their change is one line ("▼ 1.5% 90D"); Transfers in and out side by side, small (`.phone-pair`, `data-mini-charts`); the holdings filters in the controls panel (`data-phone-controls`); the holdings table one row a holding, ticker (name in the tooltip) · YTD / 90D / 180D / 365D · value (last, in line with the accounts' subtotals: a `.phone-only` cell, the second column hidden) under the head's labels, the cost column and cash tag hidden; an account's row runs name … subtotal across the row (its name cell takes no width, the subtotal at the right edge).
