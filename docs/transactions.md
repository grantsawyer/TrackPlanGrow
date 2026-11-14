# Transactions, Spending and categories

The Transactions page (search, date range, row edits, group edit, transactions you add), categories and subcategories,
rules, merchant logos and category icons, the Spending, Credit cards and Transfers pages. Tags are in `docs/tags.md`.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change. Grouped by page on 2026-11-05 (entries unchanged).

## Transactions page

- **Transactions page (M14, user's choices 2026-10-08):** search splits terms (`parse_search`): amount
  terms (`42.50`, `$1,200`, `>100`, `<=20`) compare `ABS(amount_cents)`; other words must each match
  name / merchant / category_label / account_name (LIKE with `%`/`_` escaped). **Phrases and exclusions (user's
  request 2026-10-26, for the Credit cards links; `parse_search_terms`):** `"acme air"` in quotes must appear in
  the description you see, `-acme` / `-"fake rail"` must not (description only, as reward rates match).
  **Category filter = a checklist like the accounts'** (user's request 2026-10-29; was a dropdown): any number of
  categories, none = all; a category with subcategories lists "No subcategory" (the category itself) and each
  subcategory under it, and ticking the category ticks them all. Values (`_category_filter` / `_category_where`, repeated
  `?category=` or comma-joined): `<key>` = with its subcategories, `<key>:self` = without them, `<key>:<sub key>` = one
  subcategory; a ticked category's own child values are dropped, so a choice always has one spelling. An older link's
  **`only=1`** (Credit cards, Tags) becomes `<key>:self` (the "Not its subcategories" box is gone). **The main groups
  sit above the categories** (user's request 2026-10-30; `_category_groups`): Spending, Transfers, Income
  (`KIND_ORDER` / `KIND_LABELS`), each a box with no value of its own that ticks its categories (`k-<kind>` keys; ui.js
  any depth, as the accounts' types). The button names the topmost ticked boxes, joined ("Transfers", "Spending,
  Income"), title = the same; it said "N categories" before. **Date range, always in the
  ribbon** (user's requests 2026-10-08; replaced the month list, then a button toggle, a floating panel
  and a row below the ribbon, all tried that day): a dropdown (`RANGES`: This month / Last month /
  90 / YTD / Last year / 365 (default, `DEFAULT_RANGE`; user removed All 2026-10-08) / Custom, which is
  listed only while a date you entered is in use: hidden `<li>`, ui.js `setMenuSelect` un-hides it) + From/To, all `form="txn-filters"`. The dropdown is a custom
  `[data-menu-select]` (hidden `range` input + button + role=listbox; ui.js: mouse, arrows, Enter/Space,
  Escape, Tab), NOT a `<select>`: macOS opens native menus with the chosen option under the pointer, so
  lower options opened upward; the list must always open below (user's request). A preset
  fills From/To with the dates it
  applies (`range_bounds`); a blank Custom side shows the earliest/latest visible transaction
  (`data_first`/`data_last`), and a Custom range with both blank = everything (old `range=all` → 365). Editing a date sets the dropdown to
  Custom (ui.js). **Typed dates apply only on Enter or when focus leaves the From/To pair** (user's
  request: Chrome fires "change" per typed digit, so 2023 loaded year 0002); From <-> To doesn't apply
  (the refresh would replace the box being clicked into); a change with no key press in the last 1 s =
  a calendar pick, applied at once; one submit per rendered ribbon (`data-submitted`), because Chrome
  blurs the old box during the swap. The dropdown submits at once; all via document-level ui.js
  listeners + `requestSubmit()` (#tx-range is replaced by `hx-select-oob` on every update, so listeners on it are
  lost; HTMX event filters need eval, blocked by the CSP). `_filters_from`: range > old `?month=` link
  (= Custom with that month; Budgets/Spending use it) > From/To given (= Custom) > All; a preset ignores
  dates in the fields; a blank Custom date = open-ended. Chart by day for ranges ≤ 62 days. **The over-time chart follows the toggle** (user's request 2026-10-30): All = money in and out (below); **Spending / Income = one total per bar** (`visuals.period_sums`: Σ amount, refunds lower spending; income sign-flipped; charts.js `totals`) with a **dashed line at the average** and the corner "$X per month" (or per day), over `_range_days`; **Transfers = "Transfer flows"**: by default a **circle** (user's request 2026-10-31; charts.js `chord`, drawn as SVG by `drawChord` since Chart.js has no chord type: each account once, its arc = all it sent (its colour) plus all it received (a lighter shade); a ribbon per route in the sender's colour, pointed at the receiver; hovering an arc dims the other ribbons and shows sent / received; labels spaced per side with leaders; redrawn on resize), or, picked in the card's gear (saved: app_settings `transfers_chart`, `transfers.chart_style`, POST `/transactions/transfers-chart`), a flow (Sankey) chart (`visuals.transfer_flows`, charts.js `sankey`): a matched pair = one flow from the account money left to the one it reached (counted once; a half can be investment activity, found in the list's source by its negative id, so a matched activity row listed alone still draws the flow), an unmatched transfer (a bank transfer only a destination rule places included, since 2026-10-31) = its account ↔ its category (out: account → "Credit Card Payment"; in: "Transfer in" → account); left = from, right = to (`L:` / `R:` keys, no loops); accounts in palette slots by amount moved, categories gray; tooltip "From → To: $ (count)"; Show data lists every flow. **Clicking a flow filters the list** (user's request 2026-10-31; a ribbon on the circle, a band on the flow chart): `?flow=<from>><to>`, each end the node key without its side (`a<account id>` / `c<category key>`; category nodes are keyed by key, labelled by name), `FLOW_PICK`, `_flow_where`: account → account = both halves of every matched pair between them (a half can be investment activity), account ↔ category = that account's unmatched transfers in the category, out or in. Like the bar and slice picks: the chart leaves out its own pick (every flow stays, each with its `url` from `toggle`; `picked` dims the rest: sankey's scriptable `alpha`, the circle's `.chord.picked`), clicking the picked one again clears it, and the filter bar shows "From → To ✕" (`flow_label`, `flow_clear_url`; a hidden `flow` keeps it while other filters change and goes with group edit). For All: **Its card's corner = the average** (user's request 2026-10-30; was "by month, for these filters"): "$X in · $Y out per month", or per day when the bars are days; money in and out (`period_totals` `in_cents` / `out_cents`) over the range's days up to today, ×30.44 for a month (`_flow_average`; not the mean of the bars, so empty or partial months count as they are). (The *Uncategorized* checkbox was removed 2026-10-30, user's request: the category checklist has Uncategorized; an older `?uncat=1` link becomes that category, `_category_filter`.) server-side sort by heading (`SORTS`
  whitelist; ties newest first; sort kept by filters via hidden fields, filters kept by sort links). The Category
  filter includes the category's subcategories (2026-10-25), matching its count on Categories and its budget line.
  **Group edit:** row checkboxes (`form="bulk-form"`) + `#bulk-form` bar (ui.js `bulkState`); POST
  `/transactions/bulk-category` with ids, or `all=1` + the filters (re-runs `_query`); the category to
  apply is `apply_category` (NOT `category`, which is a filter: a bug found while building);
  `__auto__` = back to automatic; `categories.set_categories` skips unchanged rows; transfers
  re-detected; redirect only to /transactions with msg. **Rows (user's requests 2026-10-08):** plain text
  (description, category label; no inline select) + status icons instead of text tags, on the description's own line right after its text (never an
  extra line; the bank's own text is not shown in the row, only as "From the bank" in the edit panel, user's
  request 2026-10-17; a separate icon column was tried and
  undone 2026-10-08) and the rule icon after the category (`_icons.html` `badge`: role=img +
  aria-label + title): clock = pending, ⇄ = matched transfer (implies not counted; a bank transfer matched with investment activity and that activity row too, since 2026-10-31; the 2026-10-30 icon for transfers a destination rule places was dropped: they have no other half),
  eye-with-slash = not counted (kind transfer / excluded), bolt = category set by a rule; "income" stays text; clicking a row (not its checkbox /
  controls; the description is a `.row-open` button for the keyboard) toggles `_txn_edit.html` under it via
  `htmx.ajax` (HTMX click filters need eval): description (`transactions.user_description`, migration 0021;
  view column `description` = yours > merchant > bank's; search + sort use it; rules still match the
  bank's name/merchant; the box is prefilled with the shown description, and saving the bank's own
  wording unchanged keeps it automatic), category, transfer buttons, "Create a rule…" (swaps in the rule panel). Save
  (`POST /transactions/{id}/edit`) returns the row; `[data-close-panel]` closes the panel. (The old rule panel was replaced by the rule builder, 2026-11-02: see Rules.)
- **All / Income / Spending / Transfers toggle (user's request 2026-10-30; replaced the Kind dropdown, `KIND_FILTERS`):**
  across the page above the other filters (`fieldset.kind-toggle`: radios named `kind` in the filter form, so a pick
  refreshes the list; equal segments like `.seg`). All = no kind; Transfers = kinds transfer + excluded (loan account
  entries). A link's `kind=excluded` (Spending's "N loan entries", `LOAN_KIND`) still narrows to loan entries, Transfers
  shown picked; an old `kind=investment` = All. Investment activity is in the list (docs/investments.md).
- **Account column (user's request 2026-10-28):** the account's name only (no "····1234"; the mask stays in its hover
  title and in the Account filter). Narrow tables (container query on `.txns-wrap`): up to 1000px wide the name is cut
  with … at 8em and the description and category wrap (whole words), so the list fits without scrolling sideways down
  to ~700px; up to 600px the name stops at 4.5em, cells sit closer, the logo column is hidden and the text is 0.85rem.
  On a phone (~360px) the table is still ~70px too wide (the Amount column scrolls into view); breaking words mid-word
  to close that read worse.
- **Categories to review (Transactions page, just above the table; user's request 2026-11-01; migration 0053,
  `categories.review_rows`, `_category_review.html`):** shows only when a new spending or income transaction has Plaid's
  first-level category and that category has subcategories (not a category you or a rule chose; not transfers).
  Collapsed: its count and net value. Opened: the table's rows (`_txn_row.html` with `review_row`: no box and no id, since a row can also be in the table) with the same click-to-open edit panel (`?view=review`: a save answers with a reload, `_reload_with_review_open`; `row_target` = this, as the row's id may not be on the page; ui.js allows one panel per transaction; keep-scroll.js keeps your place). Columns line up with the table's: from 700px wide every column hugs its content and the description takes the rest (`.txn-columns`), and ui.js `syncTxnColumns` gives both tables the wider of each width; Category, Account and Amount also take at least 20%, 16% and 9% of the table's width, so they space out on wider screens, and the description takes the rest, at least 38% of the table: Category gives up the difference, while Account and Amount never abbreviate. Nothing wraps: a long description or category ends in "…" (hovering shows it in full, a description with its tags), and below 700px the table scrolls sideways. Sorting and paging don't move the columns: a hidden row (`.txn-sizer`, `_widest`) reserves the width of the whole filtered list's longest account, category, date and amount; the card's table runs its full width. Each has a dropdown of every category, by bucket; picking one is
  your choice, as in the edit panel, and the row leaves the card (`POST /transactions/category-review/{id}`, back with
  `?review=1` so it stays open). **Dismiss** accepts the rest as they are (`category_reviewed` = 1, `POST
  .../accept`, only the ids shown, so a later arrival still appears); no category changes. The flag follows a pending
  transaction to its posted one (sync) and a removed one to its return (removals). The migration marks everything
  already synced as accepted: only new transactions are reviewed. The card ignores the page's filters.
  **Its own filters, sorting and bulk edits** (user's request 2026-11-14; `transaction_groups.review_view`, query
  parameters `r_q` / `r_cat` / `r_account` / `r_sort` / `r_dir`, so they never mix with the list's): search (description,
  category, account), a category and an account picker (those among its rows), sortable headings (date and amount
  newest / biggest first, the rest A-Z; again reverses), "N of M" when filtered; each row has a box (`.review-select`,
  `form="review-bulk"`) and the heading a select-all (`data-check-all`, ui.js); the bulk bar sets one category for the
  checked rows (your choice, they leave the card) or keeps theirs (`POST /transactions/category-review/bulk`,
  `action` set / accept). Dismiss accepts the rows shown, so a filtered view can be cleared on its own.
- **Spending by category chart (Transactions page, user's request 2026-10-26; `visuals.category_rings`, charts.js
  `rings`):** left of "Money in and out", a third of the width to its two thirds once the main column is 1280px wide
  (`.tx-charts`, a container query), half and half from 640px, one per row below that, a two-ring pie of the filtered
  transactions (every page of the list, all filters applied): spending, or income when the toggle says Income; no chart for
  Transfers. The summary line above the charts is gone (user's request 2026-10-31): the pie's corner has its total and transactions,
  the flow chart's its total and transfers (a matched pair once, `transfer_flows` `count`), and the page count sits in the
  pager at the bottom. **The card's title is a dropdown that picks what it shows** ("Spending by category" / "Spending by description", no gear; the corner on the same line, "$total · count" of what the list shows now, picks and filters included, its title spelling out the count; user's requests 2026-11-01; saved: app_settings `transactions_breakdown`, `spending.breakdown`, POST `/transactions/breakdown-chart`): the pie by category, or the **top 10 descriptions** (`visuals.top_descriptions`, `.top-list`): each one's share of the net total (refunds lower it) as a number and a bar drawn to that percentage, on one line: name, the bar in the space between, share. No Other row. One filters the list exactly (`?description=`; again or its tag's ✕ clears it), the rest dimmed. **All shows a Summary card in its place, as a balance** (user's request 2026-11-11, replacing a table of In, Out
  and Net per group: "focus on net", something to see at a glance; `_summary` -> `_balance`, `.tx-balance`): the list's
  **net** big at the top, named **Net in** (green with a "+") or **Net out** (a plain amount) by its sign, and its
  count; money out is a plain amount everywhere on the card, no minus (user's request 2026-11-11); then **two bars on one
  scale**, what came in and what went out, each a segment per group on the side its net falls (income on "in"; spending
  and transfers sent away on "out"; a group whose net is positive goes on "in"), colours as the Money in and out chart
  beside it (income `--series-1`, spending `--diverge-neg`, transfers `--series-other`); the shorter bar is completed to
  the longer's length by a **dashed piece: the net**, so the gap reads as the shortfall (or, on "out", what was kept).
  Then a **key**: each group with rows (its name links to its view, as before), its count, its share of its side ("54%
  of out"; none when it's alone there) and its net. Nets include each group's both ways (Spending's refunds; a transfer
  between two shown accounts nets to $0). **Everything is a link, none underlined** (until hover / focus; user's request
  2026-11-11): a bar segment and a key row's name and net open that group's view; a bar's total opens what moved money
  that way (`?money=in` / `out`, the gross transactions behind it; again clears it). Checked against the table it
  replaced on the real data: every net equal to the cent; "went out" is the out groups' nets summed before rounding,
  so it can differ by $1 from adding the rounded rows. **One layout at every size**, scaled to the card
  (2026-11-11): the net, the bars' thickness, the totals and the key grow with the card's width (`cqi`, phone sizes as
  the minimums; above a phone's width larger factors: net 47-58px, bars 23-29px on 1280-1920 screens), and the **five
  rows (two bars, three in the key) are spaced exactly evenly** in the height under the net: one grid of five equal rows,
  the bars and the key lining up with it (`subgrid`), each row's content centred, so a card made taller by the chart
  beside it has no empty band. On a phone each row is at least 39px (user's request 2026-11-11: 50% more than its
  content's 26). Over the list's filters (picks included). (The table it replaced, 2026-10-31 to 2026-11-11:) (user's requests 2026-10-31; `_summary`, `.tx-summary`): a table with a row
  per group (All first, the sum of the others, under a rule; Income; Spending; Transfers with loan entries) and columns In,
  Out and Net, over the list's filters (picks included). In = money in (green with a "+", as in the list), Out = money out,
  Net = In - Out (green with a "+"; money out on net is a plain amount, no minus, as in Out; never red), each over that group's rows: Spending's In is refunds; both
  halves of a matched transfer count, one on each side. Figures are metrics in aligned columns: bold, sized to the card
  (`.tx-summary-card` container, `cqi`; 5.8 is about the most that fits three columns). A group's name and its count in
  parentheses sit on their own line above its figures, so they get the width (2026-11-01). "—" when there's none. **Each
  figure filters the list** (2026-11-01): In / Out = that group's money in / out (`?money=in|out`, its view too; All's stays
  here, again clears it, underlined while picked), Net = the group's view (All's clears a pick); a "Money in ✕" tag shows it,
  a hidden field keeps it and it goes with group edit. The card leaves out its own pick, so its figures stay put. A group's name opens its view (the other filters kept). Inner ring = categories (a subcategory counted in its parent), largest
  first, slots 1-8 like Spending's treemap. **Other holds at most 5%** (user's request 2026-10-29, `OTHER_MAX_PERCENT`;
  was everything past the 8th): the smallest fold in only while together they're ≤ 5% of the total, a lone leftover
  keeps its name, and a category past the 8th that doesn't fold is its own gray slice (8 validated colours, never
  cycled). Other carries its `members`; its tooltip and legend title name them. Outer ring = each one's
  subcategories, largest first, in shades of its hue (cycling 3 shades so neighbours differ), then a blank arc for what
  sits on the category itself. A category whose refunds outweigh its spending is left out (no negative slices). Legend
  = the categories, an HTML list beside the rings at every width (`.rings-legend`, labels never wrapped. The rings fill the card's
  height (`--rings-size` = the chart beside them, `clamp(200px, 24vh, 320px)`, square), smaller only to keep the legend
  7rem wide (a size container, `100cqw`), and the legend takes the width left: a long name ends in "…" (full
  name in its title) only when that runs out; each entry links like its slice), not Chart.js's legend, which wraps inside the canvas; tooltip = name ("Food › Coffee"), amount, share;
  **the charts filter the list** (user's request 2026-10-26): clicking a slice sets the Category filter to it (a category
  with its subcategories; a subcategory's slice = `<parent>:<sub>`; **Other = every category in it**, user's request
  2026-10-29, ticked in the checklist), clicking a bar (a month, or a day on short ranges) sets `period` (`substr(date, 1, n)`, inside
  the date range; a "Mar 2026 ✕" tag in the filters clears it, and it rides along in the form and group edit), and
  clicking the picked one again clears it (`toggle` in `transactions_page`: each link is these filters with that pick on
  or off). Both combine with each other and the other filters. Each chart leaves out its own pick, so it keeps every
  slice / bar to pick from, the picked ones lit (each slice's `lit`, set by `_category_lit`; `dim` only when a pick is on
  the chart) and the rest dimmed (the legend's picked category bold), and applies the
  other's: the pie follows a picked month, the bars a picked category. A category keeps its colour from the whole range
  (`category_rings(slots=)`) while a picked month reorders the pie;
  Show data lists categories and subcategories. The card's corner shows the total.
- **The edit panel (`_txn_edit.html`, redone on the user's request 2026-11-01):** fields in columns, each label above it, and one Save for description, category, tags, recurring (one dropdown: `item:<id>`, `new:<frequency>`, `exclude`, `undo`; adding to an item takes its category unless you chose one in the same save) reward bonus (one it can't read comes back in the panel before anything saves) and, last so the others keep their place on every row, purpose, then the transfer status and its button. Only a tag's × and the transfer buttons act at once. The bank's text and the location are below the fields, the panel's full width. Save and Cancel, then Create a rule… on the right.
- **Transactions you add (user's request 2026-11-04; `manual_transactions.py`, migration 0062):** + at the ribbon's right
  (outside `#tx-range`, which each update replaces) opens a form: a **Cash / Rewards redemption** toggle (`type`, user's
  request 2026-11-04; the redemption's **Rewards points used** field shows only then, ui.js's row switch reading the checked
  radio), date, account (visible, connected), description, amount, money out / in, category, tags (comma-separated). A
  redemption needs its points (in the card's unit; a card whose rewards are set up, money out only); a cash one ignores them. POST `/transactions/new`, then `rules.run`. Ids `manual-<random>` (no sync
  sends or removes them; an import counts them as the account's own dates, not Plaid's: `imports/engine.covered_spans`); category and description are your own, so rules
  leave them; each row shows a page-and-pencil icon, "Added by you" (`_icons.html` `manual`, 2026-11-04; the rewards
  pairs' lines too). **Paid with rewards = two transactions**, the cost (tags) and a credit "… (Credit)" (2026-11-04; migration 0064 renamed the old "Paid with rewards: …") of the same
  amount in the same category carrying the rewards (`transaction_rewards_used`, `charge_id` = the cost): the category nets
  to zero (no money moved) while a tag counts the cost; both earn the category's rate, which cancels too. Changing either
  half's category in its panel moves the other's (`follow_category`); the panel's trash can (Delete; manual only, `hx-confirm`,
  POST `/transactions/{id}/delete`, the page reloads) takes both. **Their panels (user's request 2026-11-04):** a rewards
  redemption's lines (a pair, partly paid charges' too) have no Recurring or Reward bonus; the cost shows the points used
  (read-only; edited on the credit), and the source line reads "Rewards redemption · credit / cost: <the other line>,
  <date>, <amount>" (`_pair_context`); a manual cash transaction's reads "Manual cash transaction", not "From the bank". **Rewards used** on any credit's panel (a card set up):
  points turned into cash (a credit you file as Credit Card Rewards income): it records the points used.
  **Also paid with rewards** (user's request 2026-11-04; migration 0063, `manual_transactions.set_part`): on a bank's
  charge (a card set up), $ and rewards used: a $50 charge for a $200 ticket with $150 in points gets a rewards pair of
  $150 (ids `manual-part-…`: "… (with rewards)" + its rebate), the cost pointing at the charge (`rewards_part_of`).
  `sync_parts` keeps the pair on the charge's date and category and the cost on its tags (after category and tag changes
  in categories.py / tags.py and every `rules.apply_all`; plain SQL, only what differs), so the category counts the $50
  and a tag $200; only the $50 earns. Both blank removes it; sync moves it from a pending charge to its posted version,
  and a pair whose charge is gone with no successor is deleted.
- **Recurring from the Transactions page (user's request 2026-10-16; migration 0036 `recurring_links`):** a row that belongs
  to a CONFIRMED recurring item shows a "repeat" icon (`_icons.html` `repeat`, tooltip "Recurring: <item>") with the other
  status icons, on Transactions and on account pages (`recurring.items_of(conn, rows)`: your link decides, else matching by
  description + account + direction, and for an amount band the amount within `BAND_MATCH_TOLERANCE` 10%; pending rows
  only by link). The edit panel's **Recurring** line (`_txn_edit.html`, POST `/transactions/{id}/recurring`, returns the
  row): "Recurring: <item> (monthly) · Not part of it" (a link with item NULL, so matching leaves it out; "Undo" removes
  the link), else **Add to** <a confirmed item of the same direction> (the transaction also takes the item's category,
  as your choice, when the item has one that differs and no Set category action of its own, which applies it instead,
  2026-11-03; `_take_recurring_category`, user's request 2026-10-29; transfer
  matching and purpose links rerun as after a panel save) or **Make it recurring** <schedule> →
  `recurring.create_from_transaction`: a confirmed local item keyed to this amount ("<description> @<cents>", so other
  payments with the same text stay out), reusing a stored item for that description and amount, the transaction linked.
  Linked transactions also advance an item's last-seen / next date (`recurring._advance`, every confirmed item, so items
  you added by hand advance too). Known limitation: a link on a pending transaction is lost when it posts (it's a new
  transaction then), as with purpose links.
- **Location (migration 0049, user's request 2026-10-28):** Plaid's `location` per transaction is stored in
  `transactions.location_*` (address, city, region, postal_code, country, lat, lon, store_number; blanks as NULL,
  `sync.location_fields`); an update that leaves a part out keeps the known value (COALESCE, as for logos). Only
  in-person purchases have one (see docs/plaid.md). The edit panel shows it on a muted line ("Location: Store 42 · 1 Main
  St, Springfield, IL 62701"; the country only when not US; `web/transaction_edits.location_label`); nothing else uses it
  yet. History: `fintrack locations --backfill [--dry-run]` (`sync.backfill_locations`: /transactions/get, fills only
  missing parts, under the sync lock).

## Where transactions come from

- **Removed by the bank (migration 0038, `removals.py`, user's request 2026-10-22):** on 2026-10-22 Plaid's
  `/transactions/sync` listed 7 *posted* checking transactions (Oct 7-16) as removed with nothing in their place, and
  deleting them also deleted 4 money-lent links (two money-lent accounts' values dropped). Since then
  `sync.apply_changes` deletes only *pending* removed transactions; a posted one gets `removed_at` and stays, with your
  category, description and links (`ItemSyncResult.flagged`; the sync message adds "N removed by the bank to review").
  Plaid sending the same id again clears `removed_at`. After every sync `removals.match_returns` pairs each one with
  its likely return (`returned_id`): same account, posted, not removed, first seen in the sync that removed it or later (not one already there), within `RETURN_DAYS` (7)
  days, same amount (preferred) or same merchant / name / raw bank text; nearest date, then lowest id; one return per
  removed transaction; refused pairs in `removal_not_same`. **Counting:** the removed one keeps counting (it carries
  your choices); its return is `excluded` in `transactions_effective` until you decide, and transfer matching skips
  `excluded`. **Review:** a "Removed by the bank" card on the account's page, above its Transactions card
  (`_removed_review.html`, `removals.pending_review(conn, account_id)`; each row: the removed one with its category and
  money-lent link, the return with "Amount / Date / Description changed" chips, or "Not back yet"). It was first at the
  top of Transactions with a line in the Overview's Needs attention; moved the same day (user's request: less
  pronounced, with the account it belongs to). Actions
  (`POST /transactions/{id}/removal`, then transfers + money-lent values are recomputed): **Use the returned one**
  (your category, description, "Not a transfer", money-lent and recurring links move onto the return where it has
  none of its own; the removed one is deleted), **Not the same** (remembered; another match may be found), **Keep it**
  (clear the flag), **Delete it** (accept the removal). Rows (Transactions and the account page) show a flag icon: "Removed by the
  bank" (tooltip: review it on the account's page) / "Not counted until you review".
- **Authorized users (user's request 2026-10-29; `authorized_users.py`):** a transaction on an authorized user's card
  (its `account_owner`, migration 0051, looks like a card's last 4 and isn't the account's own mask: `AU_SQL` /
  `is_authorized_user`; the issuer sends the card's last 4 there) shows "(AU)" after the account name (`.au-mark`,
  outside the name's span so the "…" never hides it; the card's last 4 in its hover; Jinja test `authorized_user`).
  **Account filter = a checklist** (`details.checklist`, ui.js `[data-checklist]`): any number of accounts, none = all
  ("All accounts" row clears them). An account with AU cards (`cards_by_account`) lists its cards under it: Primary
  (its own mask, and any row without a card) and each AU card. Ticking the account ticks its cards; ticking some shows
  it partly ticked (indeterminate). **Grouped by account type** (user's request 2026-10-30; `_account_groups`: each
  account under its top-level type, `account_types.top_types`, your type table's order, "Other" last): a type's box
  (`.check-group`, no value of its own) ticks every account under it, e.g. all credit cards; ui.js handles any depth
  (type → account → card). The button names the topmost ticked boxes, joined (`data-checklist-join`; a fully ticked
  type by its name), title = the same. A ticked account's card values are dropped (`_account_filter`). Values: `?account=<id>` (whole
  account), `<id>:main`, `<id>:<last 4>`, repeated; `_account_filter` joins them with commas so URLs and group edit's
  hidden input carry one value (`_account_where` = any of them). The capture-phase listener settles the boxes before
  the form's HTMX request reads them.
- **Importing bank files (user's requests 2026-10-29 and 2026-11-13; `fintrack/imports/`, `fintrack import`; plan,
  phases and decisions in `docs/import-plan.md`):** a bank's own download fills in what Plaid doesn't reach. Phase 1
  (built 2026-11-13): CSV of any layout into a **connected** account, from the command line; phase 2 (same day): the
  **Import page** (Settings, below); phase 4 (same day, moved ahead of 3 at the user's request): **manual** bank
  accounts, credit cards and loans take files too (`manual.TRANSACTION_TYPES`; their balance follows their transactions,
  `docs/accounts.md` "Manual accounts that hold transactions"). Other manual accounts (property, positions, money lent)
  are refused.
  - **Reading** (`readers.py`, type by content, then name; 10 MB cap): CSV, delimiter (`,` `;` tab `|`) and encoding
    (UTF-8, else Windows-1252) sniffed. Phase 3 (2026-11-13): **Excel** `.xlsx` (`xlsx.py`, standard library only: the
    first visible sheet holding transactions, shared / rich / inline text, a day count is a date when its cell's number
    format is one, built-in or custom; 1904 workbooks; formulas' saved values; refused: password-protected, over 60 MB
    unpacked, any DOCTYPE / ENTITY); old `.xls` refused ("save it as .xlsx or CSV"). **OFX / QFX / QBO** and **QIF**
    (`money_files.py`, regular expressions, no XML parser): turned into Posted Date / Transaction Date / Description /
    Debit / Credit (+ QIF's Category), money out (negative in the file) in Debit, so the sign is never asked; OFX: each
    STMTTRN (DTPOSTED, DTUSER, TRNAMT, NAME else MEMO / PAYEE); QIF: D (3/4'26 -> 3/4/26), T else U, P else M, L.
    Refused: several accounts in one file, investment statements (`INVSTMTRS`, `!Type:Invst`; phase 5).
  - **Detection** (`layout.py`): the header is the first row of labels followed straight away by a data row (a date and
    an amount), so account-name and date-range lines above it are skipped; no such row = no header, columns from values.
    Columns by name (`NAMES`, then words in a name) and checked against their values (80%), the rest from values. Two
    dates: the posted one is the row's `date` (Plaid's choice for the first bank, checked 2026-10-29), the other its
    `authorized_date`. Dates: every format reading 80% of the column; month-first vs day-first by any day over 12, else
    by which keeps the rows in date order, else a **question**. Amounts: one signed column, Debit / Credit (signs ignored),
    or an amount with a DR / CR type column; `(12.34)`, `12.34-`, `12.34 CR`, `$1,234.56`, `1.234,56` (decimal comma when
    more cells look like it). **Sign** of a signed column: proved by a running Balance column (each balance = the last
    +/- the amount, in date order; a card's positive balance is what you owe), else a guess from telling words (PAYROLL;
    a card's PAYMENT) or most rows being spending (a tie: cards positive, bank accounts negative), which is a
    **question**. Card numbers keep their last 4 digits only. Rows without a readable date or amount are listed, not
    imported (totals lines).
  - **Profiles** (`import_profiles`, migration 0068): your confirmed layout and answers per account and header
    (`fingerprint`: the column names in order); the next file with that header uses them, and a profile that no longer
    reads the file is ignored (detected again).
  - **Ids** `csv-<sha256 of account, posted, purchased, card, amount, description + its place among identical rows>`,
    unchanged from the first importer (`csv_import.py`, 2026-10-29, replaced by `imports/`), so its rows count as
    already imported. A re-import adds nothing; no sync touches them.
  - **Overlap** (user's decision 2026-11-13: check and ask): the account's other transactions (not this file's own rows
    imported before) cover spans of dates (`covered_spans`): Plaid's from its first row on while connected (first to
    last once removed), imported and your own rows their dates, split at gaps over 35 days. Each file row is matched to a
    **likely duplicate**: an existing row of the exact amount within 3 days (its date or purchase date), the closest, each
    used once. A row on covered dates or with a likely duplicate is in the overlap (Plaid often dates a row a day after
    the file does, so the row before Plaid's first can be it). Any overlap = a question, asked every time: `outside`
    (neither), `skip` (all but likely duplicates), `review` (skip + the duplicates you `--take`). Nothing is imported
    until it's answered. `--before DATE`: everything from that date is covered, `outside` chosen.
  - **Categories:** a card payment (money in on a card, PAYMENT / PYMT) -> credit card payment; a merchant Plaid knows
    (same bank text, store numbers and `*` order codes aside: `merchant_key`) -> its category, name and logo; your mapping
    of the bank's category (profile; picked on the page from phase 2), then `CATEGORY_MAP` (the first bank's names
    plus common ones, ignoring case) -> Uncategorized; the preview lists unmatched bank categories.
  - **Batches** (`import_batches`; `transactions.import_batch_id`): written through `sync._upsert_transaction` (rules,
    splits, remaps as for Plaid's rows) in one database transaction, then rules, transfer matching and recurring
    detection. `fintrack import --undo N --apply` deletes everything that import added (your edits to those rows too) and
    reruns them. The file is never kept: only its name and sha256. Preview by default; `--apply` backs up first and holds
    the sync lock. Downloads are git-ignored (`*.csv`, `*.tsv`, `*.xlsx`, `*.xls`, `*.ofx`, `*.qfx`, `*.qif`).
  - **Import page** (`/import`, Settings tab after Setup; `web/imports.py`, `import.html`, `_import_preview.html`): one
    form (account, file) that posts itself to `/import/preview` on every change (htmx, multipart: the browser keeps the
    chosen file and sends it each time; the server never stores it). The preview: the file's first 8 rows under a picker
    per column (`ROLE_LABELS`; once shown, the pickers are every column's role, `exact_columns`; unchanged pickers keep a
    saved profile), Dates (month / day first) and Money out (positive / negative) when they apply, with "Choose…" and
    an outline while they're questions, the overlap's three choices with their row counts (nothing chosen for you; your
    last choice says "last time"), the bank's categories with a category each (your choice -> the row's
    `plaid_category_key` ahead of Plaid's terms; rules still come first; kept in the profile as category keys), totals,
    and every row (newest first, up to 400) with its category and status; under `review`, likely duplicates get an Add
    box. Answers count only for the file and account they were made for (`previewed` = account + sha256). Import
    (`/import/apply`) prepares the plan again from what's posted and is refused until `plan.needs` is empty; it backs up
    and holds the sync lock like the command. Past imports with Undo (behind a details; backs up first). Each connected
    account's page links "Import a file" (`/import?account=N`). Phones: the page shows "Import files on the Mac." and the
    gate refuses `/import/...` (`web/remote.IMPORT_PREFIX`).
  - **Whose card** (user's report 2026-11-13: a manual card has no number, so every imported row with a card read as
    an authorized user's): the file's cards (`plan.cards`) and the primary (`plan.primary_card`): the account's number
    if the file has it; else the file's only card on a manual account with no number; else a question ("Primary
    card", `--primary-card`; "None of these" = all authorized users'). On import a manual account takes the primary's
    last 4 as its number; a connected account keeps Plaid's, and the primary's imported rows are stored with no card.
    A file with nothing new can still be imported to settle it (`plan.primary_change`).
  - **Investment files** (phase 5, 2026-11-13; `imports/investments.py`, layout.py's investment roles): the file's
    **kind** (`layout.file_kind`, "File holds" picker, `--kind`): a Symbol column with Quantity / Price / Value / Action
    is **activity** when its rows' dates differ, else **holdings**; anything else transactions. Roles: symbol (60% of
    filled cells: totals rows sit there), action, quantity, price, value; a holdings header may be followed by a
    holding (a ticker and two numbers) rather than a dated row. **Activity**: each action's words -> ACTION_KINDS by
    keywords (`classify`), changeable per key
    (`action_key`: the first keyword, else the first three words), kept in `Layout.actions`; `effect`: shares in / out,
    cash in / out, paid in from outside, fee (a fee naming shares comes out of the shares). Ids `imp-<hash>`. Into a
    connected investment account: `investment_transactions` (Plaid's types; + = cash out; security by ticker from
    `security_prices`; `import_batch_id`), overlap against its own rows (Plaid's open-ended while connected). Into a
    manual one: one change per line and day (`manual_position_changes`: `delta_quantity` on the symbol's line, cash on a
    "Cash" line, `contributed_cents` = paid in, so a buy contributes 0 and the Savings page's retirement contributions
    count only money in; migration 0070), rows recorded in `import_rows`; refused (`plan.problems`) while a line would
    go below 0 or a line already has an entry that day. File prices go into `ticker_prices` as source 'plaid', the
    lowest rank (a broker's reported price; the CHECK allows no new name). **Holdings** (manual investment accounts
    only): each line's balance on the statement's date (`statement_date`: a one-date column, else the first date above
    the header, else asked, `--as-of`); no symbol, or no quantity with cash / money market / sweep words = the Cash
    line in dollars; lines not listed go to 0 (`plan.zeroed`); a day you entered is kept. Imported changes show
    read-only in the account's History (`Event.label`: Buy / Sell / Contribution / Withdrawal / Fee / Balance) with a
    link to Undo. **Reinvested dividends** (user's decision 2026-11-13: the dividend is income, the reinvestment a
    buy, as for tax): kind `reinvest` ("Reinvestment (buy)"; a buy whose words say REINVEST counts too), paired with a
    dividend row (same fund or none named, same dollars, within 3 days; `pair_reinvestments`); a connected account gets
    a dividend (Investment income › Dividend) and a buy (Investments › Buy), a manual one nets them on its Cash line. A
    reinvestment without a dividend in the file: the preview offers to add one for each (`plan.unpaired`, ticked by
    default, kept in the profile as `(add dividends)`; `--no-added-dividends`); an added one is marked "added", gets an
    id from its reinvestment's, and goes with the import's Undo. The below-zero check uses each day's closing amounts,
    so a day's rows can come in any order (a newest-first file lists a reinvestment above its dividend).
    **Funds tracked through a proxy** (user's request 2026-11-13): into a manual account, each symbol in
    the file goes to a line (`investments.resolve`; the preview's Holdings pickers, `--holding SYM=TICKER|new`, kept in
    `Layout.holdings`): your choice, else the same ticker's line, else a line named like the fund, else a new line. A
    line with another ticker (a proxy, e.g. a fund without a price of its own tracked through SPY) or no ticker is
    **by dollars**: activity becomes dollar changes on it (turned into the proxy's shares at its price that day, like
    your dollar events), a statement's value becomes its balance in dollars (`opening` at the proxy's price; funds
    through one line add up), the file's units are ignored and its prices are never saved for the proxy. A by-dollars
    fund with no dollar amount, or a line getting shares and dollars on one day, is a problem that stops the import.
    OFX investment statements (`money_files.read_ofx_investments`): activity, else holdings with "As of"
    and AVAILCASH. QIF `!Type:Invst` is still refused.
  - **Deleting one imported row** (2026-11-14, docs/parity-plan.md): from its edit panel, like one you added
    (`manual_transactions.deletable`); importing the file again brings it back (the user's choice: to recreate it).
  - **Investment activity into a manual account** goes into the ledger (`fintrack/ledger.py`, docs/parity-plan.md phase
    2), as into a connected one; its holdings follow (`ledger.rebuild_positions`).
  - **Into a manual account:** listed on the page after connected ones (institution: yours, else "Manual"; a "New
    manual account" link beside the file); overlap uses its transactions only (a value isn't one). With a running
    Balance column, each day's closing balance (`engine.closing_balances`: the day's last row in date order; a card
    file showing what's owed as negative is turned round) becomes a value (`manual_valuations.import_batch_id`,
    migration 0069, note "from an import"); a day you gave a value keeps yours, and a negative closing balance (a card
    paid past zero) is left to the transactions. Then `manual.refresh`. Undo deletes the batch's values too.

## Categories

- **Unclassified new transactions are Uncategorized (user's rule 2026-10-25):** Plaid's `OTHER` (it couldn't
  classify the transaction) maps to `UNCATEGORIZED` in `plaid_key`, like no category at all. Transactions stored as
  Other before then keep Other when Plaid sends them again (a `CASE` in `_upsert_transaction`'s update), so no page
  changes; Other stays available to choose. Both count as spending.
- **Subcategories (migration 0014, user's request 2026-10-05):** a Plaid category can be split
  (`categories.is_split`); its Plaid-mapped transactions then use the detailed code as their key
  (`plaid_key(primary, detailed, split_set)`), and subcategory rows (`parent_key` = primary, kind
  from the parent) are created on first sight (`ensure_subcategory`, in sync and on split).
  Fixed splits (CREDIT_CARD_PAYMENT, CASH_WITHDRAWAL) still win; custom/catch-all categories can't
  split. Merge back re-points to the parent and deletes only unused subcategories. Pickers show
  "Parent › Sub" (`category_name` filter). On Budgets a split category is never a line: its
  subcategories are (migration 0016; labels without the parent, user's request 2026-10-06). The
  split parent's `budget_lines` row stays hidden and only gives new subcategories their section
  (`ensure_subcategory` inserts the line). Spending still filed under the split category itself
  (by hand, a rule, or an unmapped Plaid code) shows in that section as a non-budgeted item
  (`split_sections`, `Section.extras`; user's request 2026-10-06), never "Not in budget"; it can't
  be budgeted and future plan columns count it as 0. An unsplit parent's line covers subcategories without
  their own line (`budgets.actuals._by_line`; e.g. after merge back).
  Overview budget chart = budgeted spending lines + one "Other" bar (`budget_bars(with_other=True)`).
- **The category table (Categories page, user's request 2026-10-13; designed like Account types):** a card per category
  (icon, its name right beside it, Counts as = its subcategories' kind too, transaction count incl. subcategories
  (the number alone; "N transactions" on hover; a link to them on Transactions, all dates: `?range=custom&category=`,
  user's request 2026-10-25), "Use Plaid's subcategories" = the old split toggle for Plaid
  categories, Delete… at the far right) with indented rows per subcategory (rename, count, Move to another category =
  takes its kind + budget section, Delete = a trash can icon opening the confirmation, 2026-10-25) and Add subcategory / Add a category.
  **Aligned like Account types (user's request 2026-10-25):** every row (a category's head and its subcategories, in
  every card) is the shared `.ar-row.no-order` grid (icon + name · count · Move to, an up-down icon opening a menu: a group is itself a choice (its own category there), that group's categories indented under it, and what isn't allowed grayed out · trash can or "Built
  in"; see docs/accounts.md), so the selects and counts line up down the page; a category's Move into and Plaid's
  subcategories toggle sit on a line under its name (`.ar-extra`), not in the columns. **Alphabetical (user's request
  2026-10-23):** categories, and each one's subcategories, sorted by name case-insensitively in
  `categories.list_categories`, so every category drop-down follows. Every drop-down of all categories has a heading per bucket (user's request 2026-11-01; `<optgroup>`, the `by_kind` filter, `group_kind`). **Grouped by Counts as first (2026-10-25):**
  Spending, then Transfers ("Not counted" until the user's request 2026-10-30; names in `categories.KIND_LABELS`), then
  Income (`categories.KIND_ORDER`; a subcategory goes by its parent), with an
  `h2.section` heading per group on Categories and the Counts as choices in that order; the ↑↓ buttons, `move_category` and
  `POST /categories/{key}/order` were removed (`sort_order` stays in the schema, unused for display). Your own subcategories (`add_subcategory`, key `PARENT__NAME`) get transactions by your choice or a
  rule. **Delete** (`categories.delete_category`) asks where its transactions go (default: the parent / Uncategorized):
  Plaid's mapping and your choices, rules and recurring items move there; its budgets and budget lines are removed; a
  category's subcategories go with it; `category_remaps` (migration 0034) remembers deleted keys so syncs send Plaid's
  future transactions to the chosen category (`sync._upsert_transaction`, right after `plaid_key`) and never re-create
  them (`ensure_subcategory`, `set_split` skip them); a new category never reuses a taken or deleted key (`_new_key`).
  Protected (`PROTECTED`, shown "Built in"): Uncategorized and Credit card payment (code relies on them). Keys never
  change on rename. **Moving between levels** (user's request 2026-10-13; `categories.move_to(key, parent | None)`): a
  category without subcategories can go into another ("Move into" menu; takes its kind + budget section, stops using
  Plaid's subcategories so there's never a third level); a subcategory can become a category ("Make it a category",
  last in the list, keeps its kind). A category with subcategories can't be moved under another (two levels only).
- **Adding a category (Categories; user's request 2026-11-02):** each group's heading (always shown, even empty) has Add category at its right, which opens a card below the heading with an icon button, a name and Add, replacing the old card at the bottom of the page. The picker holds the icon in the form until Add (`data-icon-target`, ui.js; left alone, it's automatic by the name). "From Plaid" lists `categories.PLAID_CATEGORIES` that aren't categories now and whose name no category of yours has, in their default group, each with an Add (`add_plaid_category`, `POST /categories/plaid-category`): htmx swaps only that group with the card open, the remap is removed, and transactions sync would file there come back from where you'd sent them (one whose deleted subcategory was sent elsewhere stays). A default you moved under another category still exists, so it isn't offered: Move it instead. Other isn't offered, since `plaid_key` sends Plaid's OTHER to Uncategorized (rule of 2026-10-25). So the list is often empty.
- **Adding a subcategory (Categories) or subtype (Account types; user's request 2026-11-02):** a + beside the name, or a click anywhere on the row that isn't a control (`data-panel-row`), opens or closes a panel under the row (`data-toggle-panel`, ui.js; no Cancel), replacing the always-visible Add line; Categories' also lists Plaid's subcategories seen on your transactions and not yet categories (`plaid_subcategory_options`), a deleted one offered again, and one a subcategory of yours has the name of (case and "and" / "&" aside) left out, each with an Add (`add_plaid_subcategory`, migration 0055 `plaid_added`, `POST /categories/{key}/plaid-sub`): it moves their transactions that are on Plaid's category and later syncs' into it, your choices and rules still winning; htmx swaps only that card with the panel open. This replaced the all-or-nothing "Use Plaid's subcategories" (categories already split keep `is_split`; a subcategory goes with its trash icon). The Move to menu has a "Move to…" header above its options.
- **Category drop-downs (user's request 2026-11-02):** a subcategory shows as its own name indented under its parent (`category_option`, deps.py; non-breaking spaces, since native drop-downs ignore padding on options), not "Parent › Sub", which `category_name` keeps for elsewhere.
- **Saving keeps your place (user's request 2026-10-25):** every Categories form posts and redirects back, which
  would land at the top. The page's content is a `data-keep-scroll` area: keep-scroll.js (in `<head>`, not deferred)
  remembers the scroll position on submit (sessionStorage) and restores it after the reload, shifted by how far the
  area moved; until then the page is hidden (`html.keep-scroll-pending`, revealed after 1 s regardless), so it never
  flashes at the top first (2026-10-25). After an error it stays at the top so the message shows. Other pages can opt in the same way.

## Rules

- **Rules (redesigned on the user's request 2026-11-02; `rules.py`, `rule_form.html`, migration 0054; replaces the
  single-text category rules described elsewhere in this file):** a rule is conditions, ALL of which must match, and actions.
  **Conditions:** description (the merchant or the bank's text; contains, doesn't contain, is, starts with; a box that grows, one alternative per line, `|` too; shown alphabetically, as are several categories or accounts), amount (its size: over, under, is, between), direction (money in / out), date (on or after, on or before,
  between), category (Plaid's, before any rule, so rules can't feed each other; picking a parent includes its subcategories),
  account. Merchant and bank text exist as fields only for rules converted from the old ones. **Actions:** set description,
  set category, add tag (a new name makes it), recurring (add to an item / not recurring), link to purpose, not a transfer,
  accept (skips Categories to review). A confirmed recurring item's own actions (docs/planning.md, 2026-11-03) run ahead of every rule. **Order:** custom rules by `position` (dragging or the arrows), then keywords; the first rule to set a category or
  description wins, tags add up. **Your choices win:** the view reads yours > `rule_category` / `rule_description` > Plaid's; a
  rule's tag you take off stays off (`rule_tag_removals`), one you add is yours (`from_rule` = 0). **Applying:** `apply_all`
  recomputes every transaction after a sync (`sync_all`) and when a rule is saved, deleted or moved (`run`, which also matches
  transfers again), so editing or deleting follows; only changed rows are written. Recurring, purpose, not-a-transfer and accept
  are one-time (`rule_applied`, per rule and transaction): they never redo what you undid, never override your own link, and a
  purpose waits until the row is a transfer. **Deleting asks** (2026-11-02): return to original (its results leave, its one-time
  actions are undone) or keep its results (they become your own category, description and tags; one-time actions stay), or
  cancel. **Adding checks** (`find_similar`, new rules only): same actions and conditions apart from one "contains"'s keywords
  offers to add them to that rule (`add_keywords`) or create it anyway; nothing new says that rule already does this. **Pages:** `/rules` has two sections (2026-11-02). **Category keywords:** rules that are only "text contains any of these → this category" (`is_keyword`), a row per rule by category name, keywords alphabetical and separated by dots, no quotes or "or"; no order of their own. Edited in place, category and keywords only (`_keyword_edit.html`, `update_keywords`; one per line, `|` or the row's dots too; the field and "contains" stay; `/rules/{id}/edit` sends a keyword rule back here). There are no built-in keywords: default categories come from Plaid's own model, so this is what you and your rules added. **Custom rules:** everything else, conditions and actions, a line each, in two columns, and they always run before keywords (`_run_order`), so one can override a keyword;
  with how many transactions each matches, and filters in the browser (search over what the rules say, what they check, what they do; ui.js; kept for the tab); `/rules/new` and `/rules/{id}/edit` are the builder, with a live preview
  (`GET /rules/preview`; half-filled rows are skipped, no tag is made). "Create a rule…" opens it prefilled
  (`?from=<id>`; `return_url` back to the list). Not applied to investment activity; purpose and destination rules are
  separate, on account pages. **Migration:** the 113 existing rules converted with the same ids and order, and their results
  stamped; rehearsed on a copy of the real data: no category, description or tag changed. `category_rules` and `rule_id` stay, unused and
  emptied by migration 0056 (its rows blocked deleting categories they named), because rule_id's foreign key can't be removed without rebuilding `transactions`.

## Logos and icons

- **Merchant logos (migration 0022, user's request + approval 2026-10-08):** `sync.logo_fields` saves Plaid's
  `logo_url` (else a merchant/marketplace/payment-app counterparty's) and `merchant_entity_id` on every
  upsert (kept if a later update omits them). After every sync (web + CLI) `logos.fetch_missing` downloads
  new logo addresses: https on `ALLOWED_HOSTS` only, PNG signature, ≤ 256 KB, ≤ 200 per run, files named
  sha256(url).png, 600 in a 700 folder; failures recorded in `logo_files` and retried after 7 days.
  `/logos/<64 hex>.png` serves them (nothing else); rows show the logo (own, else same merchant via
  `logos.lookup`) or a letter, 20px, in its own column between Date and Description (`td.logo-col`, heading
  visually hidden "Merchant logo", not sortable; panels span 7 columns). History: `fintrack logos --backfill
  [--dry-run]` (`/transactions/get` with `include_logo_and_counterparty_beta`, under the sync lock; only
  the logo fields change). Run once on Production 2026-10-08.
- **Transfer logos and category icons (migration 0037, user's request 2026-10-20):** the logo cell picks, in order:
  1. **a transfer's other side** (kind `transfer` only; `logos.transfer_logos`): money in shows where it came from,
     money out where it went. The account on the other side, by its institution's logo (`institution_logo_files`):
     your money-lent link (`purpose_links`) > the matched pair (`transfer_pair_id`; a card payment shows the card's
     bank) > the investment / retirement account it paired with or a destination rule names
     (`contributions.transfer_accounts`, the same matching as Portfolio/Savings over all history; recorded in
     `_Flow.paired_account`, which totals never read). Else Plaid's `financial_institution` counterparty logo
     (`sync.transfer_logo_field` -> `transactions.transfer_logo_url`, downloaded by `fetch_missing` like the others;
     kept out of `logo_url` so a purchase never shows a bank's logo; `fintrack logos --backfill` fills it for history);
  2. the row's own logo, then the same merchant's (as before);
  3. **its category's icon** (`category_icons.resolve`; `.category-icon` on the same 20px tile). It replaced the letter.
  Plaid's counterparty types, checked in Plaid's transactions API docs 2026-10-20: `merchant`, `financial_institution`,
  `payment_app`, `marketplace`, `payment_terminal`, `income_source`.
- **Category icons (`category_icons.py`, migration 0037 `categories.icon`, user's request 2026-10-20):** a fixed library
  (`ICONS`, ~56 general concepts in 8 groups, each with keywords; drawn for fintrack in `_icons.html`'s 24x24 stroke
  style, rendered by the `category_icon` macro from the `CATEGORY_ICONS` template global). Icons aren't tied to
  categories, so any category you create gets one. Order: your choice > a match on the name (`guess`: singular whole
  words; a keyword phrase scores its word count, so "Gas & electricity" beats "Gas"; ties go to the icon listed
  first) > `BUILT_IN` (Plaid's top-level keys, for renamed ones whose name matches nothing) > the parent's icon >
  `KIND_DEFAULT`. The name comes before the built-in default so renamed categories follow their name and Plaid's
  subcategories need no table of their own. Categories page: an icon button before each name opens ONE shared
  `#icon-picker` dialog (ui.js sets its form's action; not a grid per category, ~2,000 SVGs), with "Matches the name"
  (`suggestions`, best 6) first, then every group, a search box (name + keywords) and **Automatic** (posts "",
  back to the default). `POST /categories/{key}/icon` validates against the library.

## Spending page

- **Spending page (user's requests 2026-10-08/09):** date range in the ribbon = the Transactions page's own controls
  (`spending.RANGES` / `range_bounds` / `iso_or_blank`, shared: web/transaction_filters.py imports them; dropdown This month /
  Last month / 90 days / YTD / Last year / 365 days / Custom + From/To, run by the same ui.js code via `#tx-range` and a plain
  GET `<form id="txn-filters">`, so the page reloads). **Default: This month** (`spending.DEFAULT_RANGE`; Transactions keeps
  365). `spending.resolve_period`: `range` > old `?month=YYYY-MM` (= Custom with that month) > From/To (= Custom) > default; a
  preset ignores the date fields; blank Custom From = earliest spending, blank To = today; reversed dates swap. The month arrows
  and the 12-month by-category chart (`visuals.category_trend`) were removed.
  **Two modes** (`Period.mode`): *days* = This month / Last month: `month_report` (previous month like-for-like while in
  progress), the cumulative "this vs last" line chart (`visuals.cumulative_spending(month=)`), and two transaction tables (the
  month and the month before); *months* = every other range, including Custom: `range_report` (compared with the equal-length
  period right before, `spending.prior_period`; the header says "vs prior period", the dates in its tooltip), the **monthly totals**
  bar chart (`monthbars` kind, `visuals.monthly_spending`: end months count only days inside the range; for 90 days / YTD / Last year /
  365 days (`spending.AVERAGE_RANGES`, not Custom; user's request 2026-10-09) a dashed orange **monthly average** line + legend,
  = total spent ÷ (days in the range ÷ 30.44), deliberately NOT the mean of the bars, which would count a clipped or
  in-progress month as whole) and ONE table (a
  previous-period table couldn't share a month filter). Large ranges render every counted transaction (365 days is ~1500 rows).
  **Layout:** category table (left) beside the chart from 720px (halves to 1200px, then 1/3 + 2/3), stacked chart-over-table below;
  the table card matches the chart's height and its rows scroll (`.spend-layout`, `.spend-fill`). Top row: three equal tiles that
  stay one row at any width (`kpis one-row large`), all three in the hero format (label, big figure, sub-line; user's request 2026-10-09): Spent, **12 month average spend** (replaced Income, user's request 2026-10-09;
  `spending.trailing_average_cents`: counted spending over the last 365 days ÷ (365 / 30.44) months, independent of the chosen
  range, the same formula as the monthly chart's 365-day average line via `spending.average_per_month_cents`) and Transfers
  (was "Not counted" until 2026-10-30; links to the Transactions page's Transfers view).
  The average card's sub-line sets the range against the average (▼ less = good). This/Last month show the month's ACTUAL
  spending ("N so far" / "N last month"): extrapolating a few days of a month, or rescaling a 30-day month by days/30.44,
  gave misleading figures on real data. Longer ranges show "pace N a month" (same formula as the average). Known
  limitation: accounts with short history make the 12-month average understated; the calculation is unchanged.
  Above the table a treemap (`treemap` chart kind, plugin
  chartjs-chart-treemap 3.1.0 vendored, MIT, loaded in `base.html`; was a pie until 2026-10-09; `visuals.category_shares`: every
  category its own colour except the smallest ones making up the last 5% of spending, combined into a gray Other; capped at 8
  colours, the validated slots `--series-1..8` (6-8 = green, violet, red; all 8 validated on our surfaces), extra smallest join
  Other; no text inside the tiles, the tooltip and the table's swatches name them; `no_table`; the picked category's tile stays solid
  and the rest dim). The plugin calls option functions with no tile in the context: each must tolerate that (a bug found
  2026-10-09). A colour swatch (`slot-1..8`/`slot-other`, same assignment) sits left of each category name; the table combines the
  gray-swatch categories into one "Other" row at the end (`visuals.grouped_category_lines`: amounts, counts and last period's
  added up so rows still sum; a lone leftover keeps its name). **Categories spent in only the prior period** (user's request
  2026-11-04; `spending._build_report`) follow, after Other, at $0 with their drop (largest prior first, no count, no
  treemap tile). Category names don't wrap: a long one is cut short with … so the table fits its card, the full name in its title (2026-11-04). The whole row is the click target (`tr.pick-row`; the name is a
  plain-looking button for the keyboard); category names no longer link to Transactions.
  **Transactions card** (last on the page): counted spending (`spending.range_transactions`; each table adds up
  to its report total), rendered once by the server, each table as long as its rows (no inner scroll box or sticky header; the card shrinks when a
  filter hides rows; user's request 2026-10-09); `static/spending.js` shows/hides rows (no request per mouse move). Filters:
  the point hovered on the chart (days mode: every transaction THROUGH that day, because the lines are cumulative; months mode:
  that calendar month) - hover follows the pointer, a click locks the point (hover ignored) until the next click on the chart -
  and the category clicked in the treemap or on a category row (again = clear; the gray Other slice/row = every category folded
  into it). The viewed month's table is on the left (the user tried the reverse and went back). Chips in the card header show what
  filters and clear it. Charts opt in with `emit="1"` (`charts.js` `bindEmit` fires `fintrack:pick` {type: hover|click, index}; it
  knows nothing of transactions).
  Tests: the matching rules are pure functions run in Node (`tests/spending_rules.js` via `test_spending_js.py`, skipped without
  Node); `tests/chart_kinds.js` runs charts.js's treemap kind with stubbed browser globals. Claude has no browser tools: it
  drives the real page in a scratch jsdom + node-canvas harness (outside the repo, in the job temp folder) to check drawing,
  hover/click filtering, the ribbon's form values and errors.
  A Categories | Merchants toggle with a merchant table was built for M15 and then removed at the user's request; its
  data function (`spending.merchant_breakdown`) was removed too on 2026-11-05.
- **Spending comparisons while a month is in progress** are like-for-like (days 1..N of the
  previous month, N clamped), on the dashboard and the Spending page (`compare_through_day`).

## Credit cards page

- **On a phone (2026-11-11, docs/phone-plan.md phase 6):** Redeemed and Cash rewards at the size of the card's other
  figures (1.4rem, not 2.4rem), in two columns split where the card's right column starts (ui.js sets the split), so
  they line up under Points Available and Spent / Earned.

- **Credit cards page (Analysis section, user's request 2026-10-25; `web/credit_cards.py`):** a card per visible
  credit card (`accounts.type = 'credit'`, largest spending in the range first, ties by name (user's request 2026-10-26); a card with no spending in the range still shows) with its total and
  spending by category over the ribbon's date range (Savings' `_range_controls.html`, default YTD;
  `spending.card_spending`, counted as everywhere else: kind `expense` in `transactions_effective`, refunds reduce it,
  payments to the card don't count). Each card: treemap + By category table as on Spending, coloured by size within
  the card (`visuals.card_category_charts` = `category_shares` per card; its gray categories are one "Other" row). A
  category links to `/transactions?range=&from=&to=&account=&category=`. One colour per category across all the cards
  was tried first and dropped: 8 colours for many more categories left up to a third of a card gray.
  **Reward estimates (user's request 2026-10-25; `rewards.py`, migration 0039; Plaid has no rewards data, see
  docs/plaid.md):** a card's "Estimate rewards" / "Reward rates" section (collapsed, at its foot) sets what it earns
  (points, miles, cash back) and a base rate, then rates for a spending category (covering its subcategories unless
  one has its own), a merchant, or a category at a merchant. **Merchant rates (user's request 2026-10-26):** e.g.
  airfare at United 5x, other airfare 2x, other United 3x. The merchant is text you type ("United"), matched against
  the description shown on Transactions (`transactions_effective.description`: yours > Plaid's merchant > bank text),
  case-insensitive "contains", like Rules. The user's choice over a picker of the card's merchants (built first, migration
  0040, keyed like Spending's Merchants table): typing is simpler, and a false match such as "United Healthcare" is
  unlikely to share a category with airfare. Migration 0041 rebuilt `card_reward_rates` with `merchant_text` (a picked
  merchant became its name), optional `category_key` (at least one of the two) and a unique index on (card, category,
  LOWER(text)). The most specific rate wins (`matching_rate`): category at the merchant > parent category at the
  merchant > the merchant > the category > its parent > base (a merchant beats a category, as co-brand cards pay
  their merchant bonus in any category); within a step the longest matching text wins ("Acme Air" over
  "United"). Adding a rate for the same pair replaces it; removing posts the rate's id. Rates
  are integer hundredths (`parse_rate`: "3", "1.5", "2%", "3x"; 0-100). `estimates(conn, cards, start, end)` reads
  each card's transactions with card_spending's conditions and sums them per (rate, category, merchant text when a
  merchant rate applied); rewards per line = spending cents x rate / 10,000, half-up (points for x, cents of cash back for %),
  so refunds take rewards back and rows add up to the total. A merchant line reads "Airfare · United". **Every row
  links to exactly its transactions (user's request 2026-10-26):** `kind=expense` (the rows count spending only; a
  transfer in the same category had made 3 links one too many), `category=…&only=1` (its subcategories have rows
  of their own; without it "Transportation 7" opened 15) and `q=` `RewardLine.search`: its merchant text in quotes,
  less `-"text"` for each other merchant rate on the card that could apply in its category. Measured equal (link count =
  the row's count) in a test; not exact only when one description holds two merchant texts. Rows on cards without
  rates take `only=1` too. **Card fees earn nothing (user's request 2026-10-26):** `rewards.NO_REWARDS` = Plaid's Bank
  fees (`BANK_FEES`, which the user renamed "Credit Card Membership Fees"; also ATM, foreign transaction, late and
  interest fees) and its subcategories: 0x ahead of every rate (an "Acme" merchant rate can't reach "ACME ANNUAL
  FEE"), still spending, listed as "0x (Credit Card Membership Fees)" with a gray bar (no palette slot), only on a card
  that has some. Other fee categories (e.g. your Banking › Fees) take a 0x rate. **Bonuses on single transactions
  (user's request 2026-10-26, migration 0042 `transaction_reward_bonuses`):** one-offs, added in a transaction's edit
  panel ("Reward bonus", only on a card you've set up; points / miles as whole numbers, cash back in dollars, stored
  in the card's unit: points / miles or cents; `parse_bonus`, `set_bonus`, POST `/transactions/{id}/bonus` action
  save / remove; a value it can't read comes back in the panel via HX-Retarget, which keeps the panel open, ui.js).
  The row shows a gift icon ("Reward bonus: 5,000 points", `bonus_labels`). The Credit cards page adds the bonuses
  dated in the range to the estimate and lists them under a "Bonuses" heading after the rates (description + date,
  linked to that transaction; no spending, no bar). Sync carries a bonus from a pending transaction to its posted
  version, like the category. Shown as "≈ N points" under the card's total and a column in its
  table. **The table groups categories by rate (user's request 2026-10-25):** a `<tbody>` per rate, highest first, with
  a `tr.group` heading (rate, its spending, its rewards; `Estimate.groups` / `rewards.RateGroup`), categories largest
  first under it. Categories at the same rate share a group whether the rate is their own, their parent's or the base.
  A grouped table lists every category (no folded "Other" row, which could mix rates). **Charts are vertical bars (`colorbars`, user's requests
  2026-10-26; a treemap before):** a card with rates has a bar per rate, lowest rate on the left, $0 rates included; every card with rates has the same bars, all the rates on all the cards, $0 where it lacks one (user's request 2026-10-26) (`visuals.card_rate_charts`,
  keys `rate-300`; the table runs highest first). **Each card's figures** (user's requests 2026-10-26: styled like the Net worth page's cards, a big value over its label, 1.75rem / 1.4rem on phones): on the left under the card's name, **Points Available** (Miles / Cash back), linked to the card's page where balances are entered: now (`rewards.current_balance`: the latest entered + rewards since) or, without rates or in another unit, the latest as entered, its date in the label ("Miles Available, Mar 1"); none until one is entered. On the right, **Spent** in the range at the top, and under it **Points Earned**, the estimate with its rate on the spending in parentheses, "95 (2.5%)" (rewards per dollar, a point or mile counted as 1¢, so 1x = 1%; none without spending). The right column sits at the card's right end with its figures left-aligned to each other; the columns are the same height and Points Available is at the bottom of its own, level with Points Earned. Two columns at every width; the name wraps. **The table shows whole dollars** (`money_whole`; cash back too, user's request 2026-10-26). **Rate groups start collapsed** (user's request 2026-10-26): clicking a rate's row (or its expander button, ui.js `data-toggle-row` / `data-toggle-group`) shows its categories; Bonuses too. No ▸ marker is shown (user's request). **A rate is one colour on every card:** all the rates on
  all the cards, highest first, take the palette slots in order (3x points and 3% cash back share one; past the 8th,
  gray), so colours don't depend on a card's spending. A card without rates has a bar per category, coloured by size
  within the card as before (smallest 5% one gray "Other" bar). A card with no spending (≤ $0) shows "No spending in
  this range." instead of a chart. The swatch sits on the rate's heading row; its categories (`tr.rate-line`)
  are indented 18px to line up with the rate after the swatch. The table (`.card-table`) gives its `th` cells the
  compact `td`'s 8px right padding, so the column names and rate rows' figures share the category rows' right edge
  (they had table.data's 12px: 4px off; measured equal at 390px and 1280px, 2026-10-26). Cards without rates keep the category treemap and the
  folded table. **Every rate is listed, even with no matching transactions, and each heading says what earns it
  (user's requests 2026-10-26):** "5x (Travel · Acme)", "1.5x (Everything else)" (`RateGroup.definitions`, muted
  `.rate-for`; rates sharing a number are listed together, the base last as "everything else"; "Any category · United"
  for a merchant rate without a category). This replaced $0 rows under the heading for unmatched rates. The base rate's
  group always exists; a rate with no spending keeps its heading at $0 and its colour. A card with rates and no
  spending in the range shows "No spending in this range." in place of the chart and still lists its rates. Forms post to `/credit-cards/{id}/rewards`,
  `/rewards/delete`, `/rates`, `/rates/delete` and return to the same date range (`_back`). Not modelled: caps,
  rotating categories, sign-up bonuses (unless added to a transaction), point values in dollars.

## Transfers page

- **Transfers page (Settings section, user's request 2026-10-26; `web/transfers.py`, `transfers.py`, migration 0043):**
  the ribbon's date range (Savings' control, default YTD) picks the pairs with either half dated in it.
  **Bank transfers matched with investment activity** (user's request 2026-10-31; was a separate read-only "Investment
  account transfers" card, 2026-10-30): `detect_transfers`, after the bank pairs, marks a bank transfer that
  `contributions.investment_links` paired with an investment account's own deposit / withdrawal ("activity": same amount,
  ≤ 5 days, all history) `is_transfer` with `transfer_pair_id` = the activity's id negated (no migration; the Transactions
  list's id for it). Not if it's already in a bank pair, ineligible (ELIGIBLE) or "Not a transfer". Worked out after the
  bank pairs are written, so a row's own match never makes it a candidate. They're ordinary pairs: `matched_pairs` builds
  the activity half from investment_transactions, Remove match works from either half (`unmatch` finds the bank row from
  the negative id), `as_transactions` marks the activity row matched (kind transfer). Savings is unchanged
  (`_other_transfers` keeps `transfer_pair_id < 0` rows: range_series takes paired ones out itself). Transfers only a
  **destination rule** places aren't matched (no other half; the user's call: until the history is found); the rule
  still places them for Savings / Portfolio and their logos. **Matched
  transfers**: total and count in the card head, a summary by route (from account → to account, count, total; largest
  first), then every pair newest first (date, From and To each with account, mask and description linked to it on
  Transactions; the To side's own date when it differs; amount; "Matched by you"; an unlink button). **Removing a match**
  (`POST /transfers/{id}/unmatch`, `transfers.unmatch`) marks BOTH halves "Not a transfer", so neither pairs with
  something else on its own, and forgets a pair you matched. **Can be matched** (`transfers.match_options`): each
  transaction in the range with no match that some other transaction meets the rules with (`ELIGIBLE` + `_can_pair`:
  posted, visible, not a loan, not `excluded`, not income, opposite amounts, different accounts, within 3 days; paired
  or "Not a transfer" partners included). Two unmatched ones that could pair show once, under the money out; a partner
  already paired says "(matched with …)", and matching takes it from that pair. **Matching** (`POST /transfers/match`,
  `transfers.match`) checks the rules again, stores the pair in `transfer_matches` (out_id, in_id; each transaction in
  one pair; cascade on delete; revision triggers), clears "Not a transfer" on both and re-matches. `detect_transfers`
  applies your pairs first (while both halves are still eligible with opposite amounts in different accounts; a pair
  set aside comes back if that changes back), then pairs the rest automatically, skipping "Not a transfer". "Not a
  transfer" on a row drops your pair (`set_not_transfer`).
- **On a phone (2026-11-06, user's request):** the Credit cards page's cards are a swipe row (`.phone-swipe`); docs/ui.md Phones.
- **Spending on a phone (2026-11-06, user's requests):** the running total and By category are a swipe row of the same height (By category's list scrolls inside, `.spend-fill` as on wider screens); its transaction lists sit under a heading per date with the category's icon in place of its name (`category_icon_of` from the route; spending.js hides a heading when its rows are filtered away). Side by side was tried and dropped: at ~160px a description was one or two letters. Then (same day): the chart cards 25% taller (the tall chart 327px); the tiles "Spending <range>" and "12-mo Avg" with their change in whole dollars and no words, Transfers hidden (`.w-long` / `.w-short`); the two lists cards of their own in a swipe row, top-aligned.
