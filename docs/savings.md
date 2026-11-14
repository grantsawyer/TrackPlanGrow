# Savings

The Savings page: the savings model (savings.py), its cards and the Net savings rate table.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

- **Savings (new model, user's request 2026-10-11; `savings.py` docstring is the reference):** post-tax income =
  income-kind transactions; pre-tax income = contributions seen only inside an investment account, connected
  or manual alike (since 2026-11-14, docs/parity-plan.md: a manual account's contributions are activity rows, counted
  by `contributions.py` as a connected one's; until then they were "Contribution" events on its position lines,
  `manual_positions.retirement_contributions`, now removed; user's decision 2026-10-12, which replaced a monthly pre-tax calculator on the
  Savings page built 2026-10-11: migration 0024's table was dropped by 0025) + contributions seen only inside a
  connected investment account (`MonthFlow.outside_into`); retirement = recorded contributions − recorded withdrawals
  + net flows into retirement accounts; events on non-retirement manual accounts only move shares (their money comes
  from a bank account, already counted); investment = net flows into other investment accounts;
  net cash = post-tax − spending − net money moved into investments THROUGH the bank (`MonthFlow.bank_net`);
  saved = retirement + investment + net cash; rate = saved ÷ (post-tax + pre-tax), `None` (shown "—") when ≤ 0.
  With nothing entered and no investment flows, saved = income − spending (the old figure). The Overview tile and
  Projection's default monthly savings use the same `saved_cents` (so pre-tax contributions now count there too).
  Multi-month rate = Σ saved ÷ Σ (post-tax + pre-tax). Table columns: Month · Post-tax · Pre-tax · Spending · Retirement ·
  Investment · Net cash · Rate (definitions in header titles; the Saved column was dropped as their sum). Since
  2026-10-12 (user's request): whole dollars (`money_whole`) and a "% inc." column after Spending, Retirement, Investment and
  Net cash = that column ÷ (post-tax + pre-tax), via `_rate.html` ("—" without income); the three savings shares sum to
  the Rate. Net cash also subtracts net OTHER transfers on checking/savings (`savings._other_transfers`: transfer-kind,
  not `is_transfer` pairs, not CREDIT_CARD_PAYMENT, not investment codes; transfers that paired with an investment row
  are taken back out via `MonthFlow.paired_transfers`). Production 2026-10-12: a recurring large account transfer out of
  checking (destination not yet known) drives the 12-month rate negative; asked the user where it goes. Dashboard "this month vs last" compares
  days 1..N of both months (N clamped to last month's length).
  **Savings page (user's requests 2026-10-11):** three tiles, then "Net savings rate" (the monthly table; its `ratebars`
  chart beside it was removed 2026-10-12, with the `ratebars` kind and the alignRows plugin), then Transfers in / out. **Date range in the ribbon** = Spending's controls, shared as `_range_controls.html`
  (`spending.RANGES`, `resolve_period(..., default="365")`, plain GET `<form id="txn-filters" action="/savings">`); default 365
  days. It sets the third tile (range label, combined rate, total saved), the table + bars (`savings.range_series`: one row per
  calendar month the range touches; the end months count only their days inside it, as Spending's monthly totals do, and read
  "Oct 12–31, 2025" via `MonthSavings.clipped_days`; month cells don't wrap, since the bars are fitted to equal rows) and the
  transfers charts (`visuals.investment_transfers(start=, end=)` → `contributions.between`, flows dated inside the range only;
  Portfolio keeps the last 12 months). "This month so far" and "Last full month" ignore the range. Removed that day, so not reintroduced by accident: the income vs spending columns,
  the savings-rate line, the "Where income went" income split (`mix` kind, `visuals.income_mix`,
  `budgets.fixed_spending_by_month`), "Saved vs invested" (`savedinvested` kind, `visuals.saved_vs_invested`), and the
  page's helper paragraph and card captions (minimal text).
- **Retirement savings rate chart (Savings, user's request 2026-10-12):** card after "Net savings rate", follows the
  range. `visuals.savings_mix(..., "retirement")` + the `sharebars` chart kind (stacked columns on a % axis; was a stacked area until the
  user's request later on 2026-10-12, which also added a big **Average** figure = Σ retirement ÷ Σ total income over the
  range, the data's `average`, like `savings.combined_rate`, not a mean of monthly %). Layout (user's request
  2026-10-12): the card shares one `.grid-2` with Transfers in / out (the partial's own wrapper is skipped via
  `transfers_in_grid`), same width and chart height (`short`, minus the measures row: `.measure-row + .chart-box.short`);
  two measures in one row, **Average** % and **Total** $ retirement savings for the range, each label UNDER its figure
  (`.measure`, a column; user's request 2026-10-12, after first having them to the right). Figures 2.4rem (user's request
  2026-10-12); the chart gives up the measures' height (`- 71px`): measured 326 vs 326px at 1500 and 1100px.: per month and account type, net
  retirement savings ÷ (post-tax + pre-tax). Sources (since 2026-11-14): every account's flows from
  `contributions.py`, manual and connected alike (was: recorded events on manual retirement accounts,
  `manual_positions.retirement_by_account`, sharing `_retirement_events` with `retirement_contributions` so totals
  match) + connected retirement accounts' flows (`MonthFlow.retirement_by_account`, None = bank-side with no account →
  "Other retirement"). Type = `investment_type_label` (your category's name, else Plaid's subtype), merged by `_type_key`;
  bands in fixed alphabetical order so colours don't move between ranges; the stack's top = the table's Retirement %
  inc.; tooltip "% ($)" + Total; "Show data" table; a month without income has no point.
- **Investment savings rate card (Savings, user's request 2026-10-12):** the retirement card's twin for non-retirement
  investment accounts, from one shared function and one template macro: `visuals.savings_mix(conn, series, start, end,
  group)` + `savings_mix_card` in savings.html. Investment: net flows by account
  from `MonthFlow.by_account["investment"]` (replaced the retirement-only dict; `retirement_by_account` is now a property);
  bank-side transfers with no account are "Account unknown" (`UNKNOWN_ACCOUNT`); no manual-account events (their money
  comes from a bank account). Stack top = the table's Investment column; Average / Total measures; negative totals red.
  Layout since later on 2026-10-12 (user's request): Savings = tiles, then the Retirement and Investment savings cards
  side by side (`.grid-2`), then the Net savings rate table; the Transfers in / out cards were REMOVED from Savings (they
  stay on Portfolio; the route no longer computes `transfers`). An **Other assets** card (money lent) existed only in the
  Balances view and was removed with it (2026-10-14). A further card, **Net cash** (user's request 2026-10-12):
  `visuals.net_cash_mix(series)`: one series straight from `MonthSavings.net_cash_cents` (Total = the table's Net cash
  column; it can't be split by account), `legend: False` (the sharebars kind shows a legend unless the data says
  `legend: false`, so a one-segment card that names it, e.g. its institution, keeps its legend). Four cards: 3 + 1 at full
  width, 2 × 2 at ~1100px. **By day for This / Last month** (user's request 2026-10-12; `period.mode == "days"`): the cards' bars
  come from `savings.range_series(by_day=True)` (one row per date; `contributions.between`, `retirement_contributions` /
  `retirement_by_account` all take `by_day`), and each day's bar is a share of the PERIOD's
  income (`visuals._share`: a day's own income is mostly zero), so the bars add up to the Average; Total and Average equal
  the monthly view. The table stays monthly. **Hidden when empty + layout by count** (user's request 2026-10-12): a
  Retirement / Investment card with no amounts in the period is hidden, and so are its table column and
  % inc. (`show` in the route; the table's money columns come from one `money_cols` list so header and cells stay in
  step); Pre-tax hides when all zero; Net cash always shows. Cards sit in `.savings-cards > .cards.nN` (N = cards shown),
  laid out by CSS container queries on their own width (not the window: the rail can be open or closed): all in one row,
  two per row, or one per row, never 3 + 1; three cards' middle step = two, then Net cash (always last) full width.
  Net worth page's cards (`kind_cards(fit4=true)`, user's request 2026-10-13): four per row from a
  1440px grid (checked at 1710px with the rail open), measures 2rem there, the 5th card starting the next row; narrower
  = two per row. Five cards (the old Balances view: Net savings, Retirement, Investment, Other assets, Net cash): one row from 2064px, else 2 + 2 + Net
  cash full width, else one per row (odd counts: the last card spans the 2-column row).
  Thresholds 816 / 1232 / 1648px give each card >= 400px (the widest pair of figures, negative amounts, measured 365px).
  Checked in headless Chrome at 1920 / 1710 / 1500 / 1100 / 800px for 4 and 3 cards: no 3 + 1, no wrapped figures, 16px gaps.
  **Net savings card** (user's request 2026-10-12): the FIRST card in the grid, sized like the rest (it was full width
  above the cards until later that day); same format:
  `visuals.net_savings_mix(series, by_day)`: parts Retirement / Investment /
  Net cash from MonthSavings (parts with no amounts hidden, Net cash always), each a share of the month's income (period's
  by day); a stack's top = the table's Rate, Total = saved, Average = combined rate (= the range tile). Spending is inside
  Net cash, not a part. **Colours by kind** (user's request 2026-10-12): one colour per kind on every Savings / Balances
  card (`visuals.KIND_COLORS`: retirement `--series-1` blue, investment `--series-2` orange, net cash `--series-3` green,
  other assets `--series-5` magenta). A kind's own card sends `hue` and draws its segments in shades of it (charts.js
  `shade`: mixed toward `--surface` by 0 / 40 / 60 / 75%, so darker in dark mode); the Net savings card sends `colors`
  (each part in its kind's colour). Shades are close, so the legend and "Show data" table name them.
  Since later on 2026-10-12 (user's requests): titles "Retirement savings" / "Investment savings" / "Net cash"
  (the range was in the titles until the user removed it later on 2026-10-12; the ribbon shows it), measures **Total** first then **Average**, labels without the range; the investment
  chart's segments are institutions (the institution's name; a manual account by its name), the retirement chart's stay account types.
- **Savings / Balances bars (`sharebars`):** the month in progress is drawn solid like the other months (user's
  request 2026-10-12; it was 45% transparent); only its tooltip says "(so far)".
- **On a phone (2026-11-06, user's request):** the three tiles on one row, smaller; the four cards two a row with small charts (`data-mini-charts`), their Average starting halfway across each card (a two-column grid, no gap; the figures a step smaller so a Total fits its half); the tiles read "This month (Nov)", "Last month (Oct)" and "… total" (`.w-long` / `.w-short`); docs/ui.md Phones.
- **The Net savings rate table on a phone (2026-11-06, user's request):** months across (newest first, "Nov '26", a cut month "Oct 12–31 '25", the current one "so far"), a row per field (Post-tax, Pre-tax, each money column and its % inc., Rate) in a phone-only copy of the table (`.savings-months`, `.phone-block`); the field names stay put as the months scroll sideways (snapping lightly to a month). The usual table (`#savings-table`) shows everywhere else; tests count its columns there.
