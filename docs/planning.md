# Budgets, Recurring, Cash flow and Projection

Budgets (sections, 1-year / 10-year views, formulas, rollover), recurring detection, the cash-flow forecast and card statements, the projection.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

- **Budget sections (migration 0015, user's layout 2026-10-06/07):** the Budgets page is a summary
  card + one table: Income / Fixed expenses / Flexible expenses (collapsible) + a Savings row;
  columns Budget (editable input; a change opens the scope dialog, then HTMX) / % inc. / Actual /
  % inc. / Upcoming / Remaining, progress bars in their own row under each line. Nothing else on
  the page (pace, spent-vs-budget, month-end projection, bulk edit form, history were removed).
  `budget_lines` (category PK, section) says which categories are lines; it isn't dated. A line's
  kind must fit its section (income ↔ `income`, fixed/flexible ↔ `expense`), else it's ignored.
  Defaults (migration SQL): every top-level income/expense category; Rent & utilities and Loan
  payments fixed, the rest flexible; already-budgeted subcategories join their parent's section.
  Insurance (`GENERAL_SERVICES_INSURANCE`) is a fixed line by default: `cli._migrate` runs
  `budgets.add_insurance_line` once when 0015 applies (splits Services). Actuals with no line =
  "Not in budget" rows (with Add buttons), so totals match Spending/Savings. Summary: left to
  budget = planned income − planned expenses (budget amounts, no carries); bars = actual vs
  planned income and expenses (expenses incl. not-in-budget). Clicking a line opens a panel
  (ui.js): Prior month / 3-month / 6-month average of the line's actuals (months with data
  only, whole dollars) fill the input; rollover checkbox (spending only); move; remove (amounts
  kept). Section totals: Remaining = Σ available − Σ actual of all its lines. Table progress bars
  have no text: each sits in its own full-width row under its line/section (`bar_row` macro,
  `tr.has-bar` + `tr.bar-row`; user's request 2026-10-06); status is in the bar's title/aria-label
  and Remaining; the summary card keeps its text.
- **Grouping frameworks (migrations 0045-0047, user's requests 2026-10-28):** Fixed / Flexible, Needs / Wants, or
  Custom (spending groups you add and name), chosen in the main card's **settings popup** (`_budget_settings.html`: a
  gear button (a plain secondary button; the toolbar's buttons are right-aligned, user's request 2026-10-28) at the
  right of the toolbar, `details[data-popover]` closed by an outside click or Escape in
  ui.js; the 1-year / 10-year views have it too). It also holds "Group lines without a budget" (a checkbox; still
  localStorage `ft_budget_grouped`). A radio choice posts at once (`data-autosubmit`) to `POST /budgets/framework` and is
  saved in `app_settings` 'budget_framework' ('fixed' | 'needs' | 'custom'), so the page opens in your last choice.
  **Custom groups** (`budget_groups`: key 'customN', name, position; 2 to `MAX_GROUPS` = 6): each group's name is a box
  in its section header (`_budget_section_head.html`, all three views; looks like a header until hovered / focused):
  Enter or leaving it posts `POST /budgets/groups/rename`. "+ Add group" (toolbar, Custom only, below the cap) posts
  `POST /budgets/groups`; an empty group (no lines, nothing spent) shows a × (`POST /budgets/groups/remove`) while more
  than `MIN_GROUPS` = 2 remain. Names: non-empty, ≤ 40 characters, unlike the others (case-insensitive). These forms
  redirect back to the same view and month (`here`; only /budgets addresses; a refusal as `?error=`) and keep your
  place (`data-keep-scroll` on the main card). A removed group's key can be reused (its lines were let go by ON DELETE
  SET NULL). Every spending line has a place in each framework: `budget_lines.section` (fixed / flexible), `need_want`
  (NULL = `budgets.default_need_want`: Rent & utilities, Loan payments, Medical, Transportation, Bank fees and their
  subcategories, plus keys ending GROCERIES / INSURANCE / CHILDCARE / DRUGSTORES / PHARMACIES_AND_SUPPLEMENTS /
  TAX_PAYMENT are needs; the rest wants: Subscriptions is fixed but a want) and `custom_group` (NULL = your first group
  if fixed, else your second). 0047 rebuilt `budget_lines` to drop 0046's two-group CHECK (rows and triggers kept).
  `line_sections` returns the active framework's sections, so the month table, 1-year / 10-year tables and summary
  (each spending section + Savings with % of income; palette slots 1, 2, 4-7 for the sections, Savings always slot 3),
  "Move to …" (one per other section; sets only the active framework's column), "+ Add" (outside Fixed / Flexible it
  stores section flexible) and "Add to …" on Not in budget all follow it; "Not in budget" goes with the last spending
  section. Switching never changes a line's other places or any amount. No 50/30/20 target markers (not asked for).
- **Savings + 1-year / 10-year views (user's rules 2026-10-06):** toggle Month · 1 year · 10 years
  (`/budgets?view=year&start=YYYY-MM` = 12 months from that month, default last month + 11 ahead, arrows step
  a MONTH; `?view=decade&start=Y` = 10 annual columns, default last year, arrows step a year).
  **1-year budget cells are editable from the current month on** (user's request 2026-10-06; same
  `budgets` rows as Month view): a change opens a dialog (ui.js, `#scope-dialog`) → POST
  `/budgets/plan-amount` → `budgets.set_budget_scoped`: "Only <month>" sets that month and puts
  the previous amount back from the next month (unless it has its own row); "<month> and every
  month after" sets it and deletes later rows. Cancel/Escape restores. Past months refused.
  **The Month view asks the same** (user's request 2026-10-06): its boxes carry
  `data-scope-form="amount-save"` and POST `/budgets/amount` with `scope`; the dialog is shared
  (`_scope_dialog.html`). `/budgets/amount` without `scope` still means "from this month until
  your next change" (`set_budget`). The 1-year view shows whole dollars everywhere, boxes included
  (`money_whole`, half-up; user's choice 2026-10-06); a box's `data-exact` holds the stored cents,
  shown while editing and used to decide whether anything changed. Per-month rule (`budgets.plan._monthly`): months before the current one = actuals only (incl.
  "Not in budget"); the current month and later = budget amounts only (current month counts as
  budget: incomplete). **No rolled-over savings** (user removed it 2026-10-06, with the tables'
  balance): savings = income − expenses for that month / column / range only: a Savings row at the
  bottom of each table (Month: planned + actual with % of income; 1-year/10-year: per column,
  `Plan.savings`) and in the summary cards. Per-line rollover ("Roll over leftovers") is separate and stays.
- **% of income columns + Savings in the summary (user's request 2026-10-06):** Month view columns:
  Category | Budget | % inc. (of PLANNED income) | Actual | % inc. (of income RECEIVED this month) |
  Upcoming | Remaining. Summary card adds Savings (actual income − expenses vs planned = left to
  budget). The card shows amounts and bars only (user removed the income-share lines, the
  "% received" label and the set-a-budget hints; with no plan a bar is drawn empty). Savings bar:
  green left→right when positive, red right→left when negative, no label. Early in a month the
  table's actual %s run high until income arrives.
- **Range summary card (1-year / 10-year, user's request 2026-10-06):** same `.budget-layout` as the
  Month view: above the table below 1200px, at the right (sticky) from 1200px (user's request;
  the table scrolls sideways in its own box). Title = the range; Income total, then Fixed,
  Flexible (incl. Not in budget) and Savings totals for the range with "% of income" + a share bar
  (Savings green, or red from the right if negative). Same actual-past / budget-ahead rule as the
  table (`Plan.section_total`, `income_total`, `savings_total`); Fixed + Flexible + Savings = Income.
- **Line order (user's request 2026-10-06):** largest first within each section. Month view: by
  budget, then (no budget) by actual, then name. 1-year/10-year: by the row's total over the
  columns shown.
- **Upcoming column (Month view, user's request 2026-10-06):** between Actual and Remaining:
  confirmed recurring items due in the month that haven't posted (`budgets.upcoming_recurring`).
  Past month: none; current month: occurrences from the next expected date to month-end, plus an
  overdue one; future month: all occurrences. Each counts toward the row its category's actuals
  do (line, split category, or Not in budget); item type must match the category kind. Hover lists
  the items. Remaining is NOT reduced by it (shown beside).
- **Tags as budget lines (migration 0057, user's requests 2026-11-02):** "+ Add budget" also lists tags (spending sections only; `budgets.add_line` with `tag:<id>`, `tag_budget_lines`, which holds its place in each framework). A tag is a line `tag:<id>`, and each category budget on its card can be a line `tag:<id>:<category>` ("Trip › Restaurants") **only if you add it** (user's request 2026-11-04; migration 0065: `tag_budget_lines` is per (tag, category), '' = the tag's own): + Add budget lists each tag's category budgets indented under it (a tag already on the budget stays above them, grayed out); removing a category budget on the card removes its line. A category line's total is that category budget and its allocations are its own; the tag's line keeps the tag's total. **Amounts:** you type any amount into any month (`tag_budget_allocations`, `set_tag_allocation`): that month only, never spread or carried, no rollover, no scope dialog (`data-direct-form`, ui.js, through the same `/budgets/amount` and `/budgets/plan-amount`). The user rejected spreading a total evenly over its months. The line's panel shows the total, allocated and left to allocate; going over is allowed. **Tags supersede categories** (`_actuals_with_tags`): while a tag is on the budget, its expense-kind transactions count on its category line for their category or that category's parent if you added one, else on the tag's own line if that's on the budget, else they stay in their category (as Ride share without a line of its own rolls into Transportation), and not on their category's line, so totals still match Spending and a category's rollover and suggestions leave them out. A transaction with two budgeted tags goes to the older tag. A tag not on the budget changes nothing. `actuals_by_month` (the projection) still reads categories. Past columns of the 1-year / 10-year views are actuals, as for any line. A tag's line leaves a month with nothing on it once its whole total is allocated; removing the tag's line keeps its amounts and returns the spending to categories.
- **Overview's Cash flow card (user's request 2026-11-02):** its heading links to the Cash flow page (was Recurring until 2026-11-03); no explanatory text: warnings or the no-low-balances status, the chart, and the Coming up list with its in and out figures. The Cash flow page keeps its notes (`_coming_up.html` `no_notes`).
- **Spending by budget category chart (Overview; user's request 2026-11-02):** its axis starts at $0. Its marker is what's available (budget + carry), which rollover can take below zero: that marker is held at zero, and the tooltip and Show data keep the real amount. Its right margin is worked out from the labels (`rightRoom`, charts.js), not fixed, so the plot fills the card on small screens.
- **Add budget's category list (user's request 2026-11-02):** subcategories are indented under their parent (`category_option`, as in other drop-downs). A parent that can't be chosen (on the budget, or split) is still listed above its offered subcategories, grayed out; with none offered it's left out (`_adding`).
- **Budgets toolbar (user's request 2026-10-06):** "+ Add budget" (renamed from "+ Add budget category" and placed after
  "+ Add group" 2026-10-28; in every view since then: `_budget_add_line.html`, the Month view swaps its table via HTMX,
  the 1-year / 10-year views post with `back` and come back to the same range; one form: section +
  existing category or new name; replaced the per-section "Add to …" rows) and "Group lines
  without a budget" (ui.js, localStorage `ft_budget_grouped`): lines with no amount or $0, plus
  income not in budget, fold into one "Non-budgeted items (N)" row per section; clicking it shows
  them for that page view. **1-year / 10-year views too** (same toggle and state): there a line
  is grouped only if it has no budget and no actual in ANY column of the range (`PlanLine.empty`),
  recomputed on every render, so moving the range regroups. Category names link to `/transactions?category=`
  (opens on Transactions' default range, 365 days, since 2026-10-08); 1-year past actuals link to that month.
- **Budget formulas (user's request 2026-10-06):** the Budget box accepts a calculation
  (`money.parse_amount_formula`, used via `budgets.parse_amount`): optional leading `=`, numbers
  (`$`/commas ok), + − * / ( ), `%` = percent (spreadsheet style, not modulo). Decimal math,
  half-up to cents at the end; only the result is stored. A small recursive-descent parser:
  never `eval`. Errors: divide by zero, negative result, anything else.
- **Budgets:** rows (category, start_month, amount|NULL, rollover) apply from start_month
  until the next row; NULL amount = no budget. `expense`- and `income`-kind categories (income:
  no rollover). Rollover:
  available = budget + carry_in; leftover = available − spent; carry_in(M) = leftover(M−1)
  only if M−1's row has rollover **and** M has a budget; the chain starts at the first
  budget row and breaks at months with no budget. Carries are **computed, never stored**.
  Status: over if spent > available or available ≤ 0, else ok. **No warning level** (user removed
  the ⚠ near status and its threshold setting 2026-10-06; the old `app_settings.budget_warn_threshold`
  row is unused). Saving an unchanged amount writes nothing.
- **Recurring (local detection; Plaid Recurring isn't on Trial; user chose option 1):**
  group posted txns (800-day lookback, so yearly items have 2 occurrences) by
  (merchant_key, direction, account); schedule from gaps (biweekly checked before
  semimonthly; semimonthly = two day-of-month clusters); ≥75% gaps fit; ≥75% amounts
  within ±20% of the median; not stale (≤1.5 gaps + 3 days). Expected amount = median of
  the **latest 3**. Month-based `days_of_month` = most common day if ≥75% within ±3 days,
  else the latest day (30-day drifting bills). Known limitation: slowly drifting bills with
  <~2 years of history land a few days off until confirmed. Next date ≥ last + minimum gap.
  `refresh` runs after every sync: updates `detected` in place, never resurrects
  `dismissed`, advances `confirmed` (keeps user edits), deletes stale `detected`.
  **Only confirmed items feed forecasts.**
  **Amount bands (user's report 2026-10-16):** rent paid by bank transfer had the same text as that bank's other transfers
  (a large transfer moves several times a month), so the description's group failed the schedule and amount checks (31% of
  amounts within 20%) and rent was never suggested. A group that fails as a whole is now split by amount
  (`_amount_bands`: by size, a new band when an amount is > 20% above the band's smallest); each band is checked on its own
  with stricter rules, because one amount out of many unrelated purchases can line up by chance (seen on Production:
  restaurants at the same price a year apart): a fixed amount (75% within 2% of the typical one,
  `BAND_AMOUNT_TOLERANCE`), and occurrences by the band's top-level category (user's request 2026-10-16, which replaced
  "never yearly, at least 4" from earlier that day): everyday spending (`EVERYDAY_CATEGORIES`: food, shopping,
  entertainment, travel, personal care, transportation, cash) needs `EVERYDAY_MIN_OCCURRENCES` (monthly 3 like bills: 3 months of a fixed price is worth
  suggesting, the user can dismiss it, user's request 2026-10-16; quarterly 4,
  yearly 3, weekly 8, biweekly / twice a month 6); bills, subscriptions and everything else the usual `MIN_OCCURRENCES`
  (monthly 3, yearly 2). A band's key is "<description key> @<typical cents>" (`AMOUNT_MARK`, `split_key`; no migration: the unique
  index on (merchant_key, account, direction) holds); its item only matches transactions within 20% of its own amount
  (`matching_transaction_ids`), and `refresh` treats a stored band of the same description within 20% as the same item
  (`_stored_item`), so a rent increase updates it; a confirmed band claims transactions within 10% of its amount
  (`BAND_MATCH_TOLERANCE`; 20% had tagged an unrelated purchase to a small monthly item on Production). Checked read-only on
  Production: a monthly rent and a small monthly payment were found, nothing else new.
- **The item's window and its actions (user's request 2026-11-03; migration 0060):** Confirm (a detected row) and Edit open
  `/recurring/{id}/edit` (`_recurring_form.html`, also the Add your own form) in two parts like the rule builder: **Matches**
  (name; for a detected item the description it looks for, whether it's an amount band, and how many transactions it has;
  account, money in / out, amount, how often, next date, days) and **Then do this** (the rule builder's action rows, shared
  `_rule_actions.html`, without "Recurring": `recurring.ACTION_KINDS`). Saving confirms the item, replaces its actions
  (`recurring_actions`) and runs `rules.run`. **The item's category is its Set category** (what Budgets and the forecast
  use); an item with a category and no actions yet opens with that Set category row (`form_actions`), applied when you
  save. The old Category field is gone. Why: the item's category used to describe the item only and never reached its
  transactions (rent arriving as Transfer out stayed out of spending, user's report 2026-11-03). Existing items got no
  actions by migration, so nothing changed until each is saved. **Applying** (`rules.item_actions`, `_with_item`): each
  CONFIRMED item's actions go to its transactions (`recurring.transaction_ids`: your links, else matching), recomputed in
  `rules.apply_all` after every sync, item save, status change and panel link change; its category and description
  beat every rule's (an item is one payment, more specific than a rule), your own still win; tags add up (from_rule);
  purpose, not a transfer and accept are one-time per item and transaction (`recurring_applied`). Dismissing or removing
  an item takes its category, description and tags back off. A category an item set has no badge of its own (the row's repeat icon
  already shows it, user's request 2026-11-03; `category_source` = `recurring`, from `transactions.rule_category_item`, so
  it isn't shown as a rule's), and the rule preview says
  "keeps its recurring item's category".
- **Forecast:** per visible Plaid depository account (+ any account chosen to pay a card).
  **Known events only, no averaged trend (user's decision 2026-10-05):** confirmed recurring items
  and card statements still owed move the balance on their dates; it's flat in between (everyday
  spending is on cards and reaches checking as the statement payment). Overdue bill → today,
  overdue income → not assumed. The chart's today point is the actual balance; what's overdue or due today lowers
  tomorrow's point (user's report 2026-11-03: today didn't match the balance). Low-balance threshold in
  `app_settings.low_balance_threshold_cents` (default 50000). Chart y-axis reuses
  the user's 5%-each-side rule and includes the threshold.
  **Where it shows (user's request 2026-10-16):** the 90-day per-account chart and table on Recurring were removed; the
  Cash flow page (projected balance + Coming up) carries the forecast and its threshold. **Range (user's request
  2026-10-23/24):** the ribbon holds a cash-account picker (the forecast's checking / savings accounts; greyed out
  with one; default `main_payer`) and 30 / 90 / 180-day links (`web/forecast.CHART_DAYS`, default 30). Both reload the
  page as `?account=&days=` (server-rendered rather than JS toggles, so the tiles and both charts always agree; the
  choice isn't remembered between visits). "Warn below" is in the projected chart's header (a `{% call %}` block of
  `projected_balance`), by the line it draws; saving keeps the view. **Tiles** (`forecast.cash_measures`): Balance +
  change since `days` ago; Projected change per month = (projected end − today) ÷ days × 30.44; Lowest projected
  balance + its date (✕ when under the threshold). **Balance history** card (`area` chart): `forecast.balance_history`
  works back from today's balance through posted transactions (snapshots only start at the first sync; pending and
  likely returns of bank-removed transactions left out). Checked 2026-10-24 against the main checking account's snapshots: 19 of
  21 days equal; the other two are deposits dated a day before they posted (after that day's snapshot).
  **Coming up follows the account and range** (user's requests 2026-10-24): `forecast.upcoming_for` = the selected
  account's projection events in the selected 30 / 90 / 180 days (heading too, `coming_up_days`) (so the list matches the chart; card-charged recurring items reach checking
  through the statement), plus statements with no paying account. The Overview's list still covers every account.
  Each row carries **Balance after** (`balance_after`): today's balance minus every row down to it, in the order shown
  (agrees with the chart); the Overview's list (several accounts) has no such column.
  **Layout (user's request 2026-10-24):** the three tiles stay in one row at every width (`.kpis.equal.three`);
  Balance history | Projected balance share a row at every width (`.chart-pair`), past on the left.
  **Both charts are the `balance` kind on one y-axis (2026-10-24):** the history gets the warning line and its day's
  transactions on hover (`label`, `todayIndex`, `dots: false`); the route sends both charts the same `range`
  (low / high of both series), which charts.js pads identically; equal header heights line the plots up.
  **Lists:** "In the last N days" (`forecast.past_for`: the history's transactions, newest first, balance after
  each) | Coming up, side by side at every width (`.list-pair`, each scrolling inside its card, sticky header);
  no Account column on this page (one account; the Overview keeps it). The `_coming_up.html` partial takes
  `list_title`, `empty_text`, `past_list` for the past card. The name column is headed "Transaction" here
  ("What" on the Overview). When either list has a date in another year ("Jan 4, 2027"), the route sets `long_dates`
  and both get `.long-dates`: a wider Date column (7.5em; 6.5em ≤1100px) so the year doesn't run into the name
  (user's request 2026-10-24). At 760px and below (the app's small-screen width) the two lists stack, past first;
  the charts stay paired (user's request 2026-10-25).
  Both lists share fixed columns (`table-layout: fixed`: Date 5.5em, Amount 7.5em, Balance after 8em; narrower with
  0.85rem type at ≤1100px) so they line up; names stay on one line with "…" (full name on hover).
  No legends on these two charts (`legend: false`). Warn below saves when changed (`data-autosubmit`; Save only in
  `<noscript>`) and stays on the title's line; the title shortens with "…" when the card is narrow.
  **Cards (user's request 2026-10-24):** Projected balance; Coming up in the next 30 days in its own card
  (`coming_up_card`; the Overview keeps it inside its Cash flow card); **Credit cards**: per card its average
  monthly spend (and months averaged; a bold Total row), due day of the month and paying account
  (`cards.estimates`; the next estimated payment column was removed 2026-10-24, it's on each card's page). Neither it nor the Overview's Cash flow card shows
  account masks (names, statement events, Coming up; user's request 2026-10-23). Recurring
  keeps a "Next 30 days" table (`web/recurring.next_30_days`: the forecast's events for all checking & savings together)
  under two charts, **Recurring income this month** and **Recurring expenses this month** (`visuals.recurring_by_day`,
  `daybars` kind): one bar per day of the current month = the confirmed items due that day (`recurring.occurrences` from
  the next expected date, so past days count too), **annual items left out**; income blue, expenses red, no legend,
  the tooltip and table list the day's items; above each chart a measure, **Total income** / **Total expenses** for the
  month (the bars' sum), in the Net worth cards' `.measure` format. (Tried and replaced the same day, user's requests
  2026-10-16: a monthly average by category as horizontal bars, then as pies.) The
  60-day income/bills chart (`visuals.upcoming_flow`) and "Bills per month by category" were removed.
- **Projection:** monthly for 120 months from current balances. Investments ×(1+r)^(1/12) +
  monthly savings; property ×(1+g)^(1/12); cash/other constant. Loans amortize only with BOTH
  rate and payment, and Plaid credit-card terms are informational (cards held constant unless
  `source='you'`). Payoff month: only the unused remainder goes to savings, then the full payment
  from the next month (`proj_redirect_paid_off`). Payment ≤ first-month interest → flagged.
  Savings default = mean saved over the last ≤12 complete months. Loan payments are already in
  spending, so net change = savings + growth + principal (don't double count).
  Scenarios = JSON adjustments (category_changes, recurring_changes, savings_rate_pct,
  investment_return_pct, extra_payments); cuts are capped at the category's average; extra
  payments come out of savings while the loan is open. Scenarios never write real data.
  Plaid Liabilities covers credit/mortgage/student only (verified in Sandbox 2026-10-03).
  Chart: history (monthly latest snapshot) + baseline (lighter blue) + scenario (orange);
  y-axis uses the user's 5%-each-side rule.
- **Card statements in the forecast (migration 0013, user's request 2026-10-04):** each sync
  saves the current statement per card (`card_statements`). Still owed = statement balance −
  card-account transactions in `CREDIT_CARD_PAYMENT` dated ≥ statement date (or Plaid's
  last_payment if larger and on/after it), or, before the card side posts (user's report 2026-11-03), an unmatched
  `CREDIT_CARD_PAYMENT` out of the paying account since the statement date for exactly what's still owed (one
  account may pay several cards, so the exact amount picks the card; each counts once, earliest due first). It leaves the paying account on the due date;
  overdue → today. Paying account (`card_payment_accounts`): no row = automatic (the only
  visible Plaid depository account, else none → "choose one"); NULL = don't forecast; an id =
  that account (any visible non-card/loan account; hidden → back to automatic). Confirmed
  recurring card payments on a paying account are skipped (no double count). A non-depository paying account
  is forecast on its own but not in "All checking & savings". Only the current statement is
  known; **later ones are estimated (user's request 2026-10-23, `cards.estimates` / `estimated_payment_events`):**
  average monthly spend per card = its charges, refunds, fees and interest (not `CREDIT_CARD_PAYMENT`, not transfers
  in) over the last `AVERAGE_MONTHS` (3) complete months, fewer with less history, none without a complete month
  (3 rather than 6 or 12: follows changes in spending sooner, still smooths one odd month); paid on the card's usual
  day = the statement due date's day of the month, else the median day of its last six payments (clamped to short
  months); one payment a month from the month after the known statement's due date, from the card's paying account,
  named "… statement (estimated)" on the chart and in Coming up. An account paying an estimate also skips a confirmed
  recurring card payment (as for known statements). The card's account page shows its average monthly spend and next
  estimated payment. Cash flow's Credit cards table: Card, **Last 30 days** (`cards.recent_spend`: the same rule
  over the last 30 days, today included, never negative; user's request 2026-10-27), **Monthly Avg.** (renamed from
  "Average monthly spend" the same day), Months, Due, Paid from; Total row for both amounts. Overview: 30-day balance chart (`balance` kind,
  account picker) + "Coming up in the next 30 days" list (recurring + statements).
- **On a phone (2026-11-06, user's request):** Recurring's two charts are a swipe row (`.phone-swipe`); docs/ui.md Phones.
- **Cash flow on a phone (2026-11-06, user's requests; `.w-long` / `.w-short`):** tile 1's change reads "▲ $X vs <date>" on one line (sized by the tile, `min(0.75rem, 10cqi)`: ~8.5px at 360px, ~10px at 412px); tile 2 is "Balance in <N> days": the projected end balance, then "▼ $X vs Today" on one line like tile 1; tile 3 "Min. Balance". The past and coming-up lists are a swipe row (`.phone-swipe`; top-aligned, each card as long as its list: the desktop's 520px scrolling cap is off on a phone), each one line a row in whole dollars (`table.cf-list`: under a heading per month, the day · the category's icon (forecast rows carry `category`: a transaction's, a recurring item's `category_key`, card statements the card-payment category) · name … · amount · "Ending Balance" (two lines); the columns hug their contents 10px apart, 6px between icon and name, days right-aligned, the Date header read but not shown; `_coming_up.html` keeps its two-line row cards on the Overview only). Credit cards one row a card: card "(due 16th)" in muted text · last 30 days · monthly avg, whole dollars; the Months, Due and Paid from columns hidden.
