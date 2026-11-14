# Layout, navigation and charts

The rail and tabs, page widths and grids, chart kinds, palette and axes.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

- **Chart palette:** `--series-1` blue, `--series-2` orange (validated as a pair, light + dark).
  Light orange is 2.98:1 on `--bg`, so any chart using it **must** show a table view.
- **Layout (M12; sections reorganized by the user 2026-10-05):** left rail = sections (Overview;
  Transactions: Transactions, Spending; Accounts: Net worth, Accounts (`/accounts`, change in account values; named Balances at `/balances` until 2026-10-14, user's request; `/balances` and `/accounts/{id}` redirect to `/accounts` and `/accounts/{id}`); Investments: Investments, Portfolio (user's request 2026-10-07: was Wealth: Net worth
  with holdings; Portfolio holds measures, value by account type and holdings; Settings → Accounts was folded into Setup 2026-10-14);
  Analysis (user's request 2026-10-25, icon "bars"): Savings and Cash flow (moved from Transactions, where Cash flow was
  added 2026-10-14; URLs unchanged) and Credit cards (`/credit-cards`); Planning: Budgets, Recurring,
  Projection; Settings; the Forecast page was folded in 2026-10-05: cash flow on Recurring,
  `/forecast` → 307 to `/recurring` (to `/cash-flow` since 2026-10-16, when Recurring's cash-flow section was removed); its month-end projection was removed 2026-10-06), header tabs = the section's pages (`deps.NAV`; a tab owns
  its path prefix; `NAV_ALIASES` for pages elsewhere, e.g. `/manual`, `/connect` → Setup). **Settings tabs (user's request 2026-10-14):** Setup, Account types,
  Categories, Rules (later Import after Setup, 2026-11-13, and Transfers last). New pages: add a
  tab to `NAV` (and a section icon to `NAV_ICONS`, drawn in `templates/_icons.html`; no icon
  fonts/CDNs). The rail collapses to icons via its toggle button or a click anywhere on the rail
  that isn't a link (not on page content; not on narrow windows); the state is the first-party cookie
  `ft_rail=collapsed` (set by ui.js, read in `deps._page_context`) so it renders collapsed from the
  first paint. **The current section** (user's request 2026-11-14): the gray background (`--surface`) and its icon
  filled blue (each section icon has `.icon-fill` shapes in `_icons.html`, drawn only for `.rail nav a.active`: the
  accent at 40% under a blue outline); no bar at its side (the 3px transparent border stays so labels don't shift).
  Phones' bottom bar uses the same fill. **Rail foot (user's request 2026-11-14):** no Production badge (the Sandbox
  one stays: test data); the last sync sits under "Sync now" (`.sync-time`, 0.7rem, from the icon's left edge),
  placed in the space below its button (absolutely), so Dark mode, Sync now and Collapse are the same 36px rows 10px
  apart, open or collapsed (collapsed: no time; it's in the button's tooltip). "Sync now" lives once, at the bottom of the rail (user's request 2026-10-06): icon + label +
  "Synced …" time; collapsed = the sync icon only, time in its tooltip; clicks on it never toggle the rail.
  **Collapse animation (user's request 2026-10-06): icons never move.** The rail keeps 8px side
  padding in both states and every icon sits 21px from its left edge (centre of the 60px rail);
  only `.rail .label` animates (max-width → 0 + fade, rail `overflow: hidden`). The brand is "f" +
  label "intrack", the badge first letter + label rest. Don't reintroduce centring/padding swaps. **No page titles (no `<h1>`), user's rule
  2026-10-05:** the bold, underlined tab in the ribbon names the page (tabs show even for one-page
  sections like Overview, on wider screens; a phone hides that lone tab, user's request 2026-11-06). **Page controls go in
  the ribbon, top right** (base.html's
  `{% block tabbar_controls %}`, `.tabbar-controls`; user's requests 2026-10-06): Budgets (← → +
  Month/1 year/10 years), Recurring (Check again), Projection (Compare with + New scenario), Investments (1 day … 365 days) and
  Portfolio (YTD/90/180/365) range buttons (user's requests 2026-10-07). A control that changes one card only goes on
  that card instead: the Overview's 30 / 90 / 180 / 365 days sits on Net worth over time (user's request 2026-11-06;
  in the ribbon 2026-10-23 to 2026-11-06), so the Overview has no ribbon controls. A
  `.page-head` row (back links etc.) remains for other pages; pages without controls start
  straight with content.
  **Widths:** content is fluid (no max-width); page edges (`--gutter`) = the space between cards
  (`--gap`, 16px) on every side, tab bar included (user's request); only running text is capped at 80ch. A page whose content starts with an empty form for the ribbon's controls (`<form id="txn-filters">` on
  Savings / Balances) marks it `class="form-anchor"`, so the first card keeps the 16px edge (it had 32px; fixed 2026-10-12). Chart heights use clamp(…vh…). Two-column grids need
  ~400px per column. 641–1100px: rail starts icons-only but the toggle stays usable (hiding it broke collapse/expand
  for zoomed or non-full-screen windows, 2026-10-05); opening or closing it is saved in the `ft_rail` cookie
  (`.rail-expanded` / `.rail-collapsed`) and kept from page to page (user's request 2026-11-13; it used to be for one page
  view only). The rail stays at the side down to 641px; ≤640px is the phone layout (the 641–760px top row was removed
  2026-11-13 at the user's request).
  **Phone width (fixed on the Settings tabs 2026-10-25):** nothing may run past a 390px screen. Setup's Configuration
  `table.status` shares the nowrap `.status` badge class, so it turns wrapping back on (`overflow-wrap: anywhere` for
  paths); Account types / Categories put the drop-down under the name (≤640px) and cap opened delete confirmations at the
  screen width. To check a page, measure at a
  true 390px viewport (DevTools device emulation); `chrome --headless --window-size=390,…` screenshots are misleading
  because Chrome won't make a window that narrow and crops a wider layout. Card grids (`.grid-2`) use one 16px `--gap` both ways and their cards drop their margins (user's request
  2026-10-12: vertical gaps had doubled to 32px); measured 16px everywhere on Savings, Recurring and Portfolio. Overview cards sit in one
  `.overview-grid`: exactly two columns (each card half width, user's request), one below 900px.
  Cards keep their own height (`align-items: start`, user's request), on every grid. Overview is
  headline row (`.kpis.equal`, a grid): Net worth, Spent this month, Savings rate a third each; at 900px and below
  Net worth takes a row and the other two split the next (user's request 2026-10-23). Their figures share one size,
  `clamp(1.5rem, 14cqi, 2.9rem)` of the tile's width (container query), so a 10-character figure stays on one line.
  Spent and Savings rate each compare with the same days of last month (`savings.month_to_date`: spending, and
  `last_month_same_period_rate`), ▲/▼ with the gap (savings: points between the rounded rates; up is good). Then
  two independent columns (`.overview-col`, user's request: a card starts right below the one
  above it). Net worth over time's range (30 / 90 / 180 / 365 days, default 90) is in the ribbon, top right
  (`tabbar_controls`; moved from the card with 30 days added, user's request 2026-10-23). Left: net worth, budget
  categories, this vs last month (moved under budgets 2026-10-23), recent transactions. Right: accounts by type (expandable; `_accounts_card.html`: every value on one right edge so decimals
  line up, account names flush left with the type names, no institution text or mask, a type's count without "accounts", types open without a ▸ and have bold values, a proportion bar under Assets / Liabilities and a swatch per type
  (`balances.proportion_bars` on the Overview's type grouping, as on the Accounts page's Summary card), the last row
  "Net worth", Assets / Liabilities / net rows
  1.1rem over 0.95rem rows, and a 1-day change column before the
  values: `networth.day_changes` = current balance − the latest snapshot before today, summed per type / side / net,
  ▲▼ green when good for net worth, blank when unchanged or unknown; user's request 2026-10-23),
  investments (movers, or the watchlist by its title dropdown, 2026-11-04), cash flow (recurring next 30 days) (accounts and movers moved to the top of
  the right column, user's request 2026-10-23).
- **Messages (user's request 2026-10-25):** a "saved" message (`<p class="flash ok" role="status">`, from `?msg=`) is a
  toast: fixed at the bottom center of the screen wherever you're scrolled, clicks pass through it, it fades after ~4 s
  (CSS animation, so it works without JS); ui.js removes it and drops `msg` from the address so a refresh doesn't
  repeat it. Errors (`flash bad`) stay at the top of the page until you leave it.
- **Legend swatches in tables (user's request 2026-10-26):** in a `table.breakdown` cell that starts with a `.swatch`
  (Spending's and Credit cards' category rows and rate headings), the swatch hangs in the cell's left padding, so a
  name that wraps lines up with its first line rather than under the swatch (`text-indent: -18px`; an expander button is
  top-aligned so the swatch stays by its first line). The cells are baseline-aligned, so a wrapped row's values sit level with its first line.
- **Date axes keep flat labels:** `dateAxis` merges a chart's own `ticks` into the shared ones (2026-10-24; a plain
  assign had dropped `maxRotation: 0`, tilting labels on narrow charts).
- **Chart axes:** value axes use `afterBuildTicks: window.fintrack.roundTicks` +
  `ticks.callback: window.fintrack.axisMoney` (round labels inside the 5% padded range, precision
  from the tick values); date axes use `window.fintrack.axisDate`. Don't use `maxTicksLimit` on
  axes with an explicit min/max: measured to produce a single label.
- **Charts (M13):** add a chart with `{% from "_charts.html" import chart as viz %}` then
  `{{ viz(id, kind, data, size="medium", label=...) }}` (import as `viz`: several pages already
  have a `chart` variable). Kinds in `static/charts.js`: area (+ `ranges()` 90/180/365 buttons; the
  x-axis spans the whole range ending today, empty days stay null), lines, bullet (its budget marker's half-length
  is 0.65 of its bar's thickness, at most 9px, so it never runs into the next row; on a phone, once laid out, its
  bars keep their thickness and the box grows 2px a row, 2px more between categories (`ftBulletGap`): 2026-11-11; value vs
  budget marker, % used drawn past the bar: ✕ over; canvas text isn't readable, so pages
  also list attention items as text; the Overview's budget card dropped its list 2026-10-23, user's request, and
  relies on the chart's Show data table; the "Other" bar (unbudgeted spending) shows its $ total where the
  others show % used, instead of a line under the chart), flow (in up / out down), rings (the Transactions page's spending by category and subcategory, user's request 2026-10-26: a doughnut with two datasets, the outer drawn first),
  sharebars (the Savings / Balances cards), transfers, monthbars (Spending's monthly totals), stackedarea (optional range buttons + owed line), performance (Investments % chart; minute prices on the "1 day" range, from the canvas's `data-range`), totals (Transactions' Spending / Income views: one total per bar, red for spending / blue for income, a dashed average line in the text colour, bars pick their period), chord (Transactions' Transfers view, the default: an SVG circle drawn by `drawChord`, returned as `svg` instead of a Chart.js config; `render` hides the canvas and puts the SVG and its `.chart-tip` in the box), sankey (Transactions' Transfers view when the gear says Flow: the vendored chartjs-chart-sankey 0.14.0; account nodes in their slot on both sides, categories gray, labels in the text colour), daybars (Recurring's income / expenses by day of the month, one series, `data-direction="out"` = red;
  user's request 2026-10-16, replaced a `pie` kind built and removed that day), colorbars (Credit cards, user's request 2026-10-26: vertical bars, treemap's data shape, but each bar's colour
  comes from the page's `slots` map rather than its position, so a reward rate is one colour on every card; tooltip
  "5x (Travel · Acme)"; long labels may tilt to 45° instead of being skipped), treemap (Spending; needs the vendored chartjs-chart-treemap 3.1.0, MIT, loaded in `base.html`, user's request 2026-10-09). `charts.js` builds the "Show data" table from the same JSON
  (opt out with `no_table="1"` only where the page already lists the same data: user's choice for the
  Overview cash-flow chart, whose "Coming up in the next 30 days" list sits right below it)
  (textContent) and redraws on theme change and after HTMX swaps (only undrawn canvases) on
  `htmx:afterSettle`, never afterSwap: HTMX's settle step resets class/style/width/height of elements whose
  id was already on the page, which wiped charts drawn at afterSwap (flash then blank; fixed 2026-10-08). Data comes
  from `visuals.py`, which reuses the owning module (never recompute a number a table shows).
  Chart.js + charts.js load once in `base.html`; pages must not include Chart.js again.
- **Chart palette (validated 2026-10-04 with dataviz `validate_palette.js` on our surfaces,
  light #ffffff / dark #1f1f1d):** slots `--series-1..5` (blue, orange, aqua, yellow, magenta;
  light and dark steps from the reference palette) pass all gates; light aqua/yellow/magenta are
  < 3:1, so tables are mandatory (they always exist). `--series-other` gray for "Other"/context;
  `--diverge-neg` red for money out (blue/red pair PASS). Fixed slot order, never cycled; a 6th+
  category folds into Other. Emphasis charts: the current series in `--series-1`, context in gray.

- **Light / dark switch (user's request 2026-10-28):** in the rail above Sync now (`.rail-theme`, `role="switch"`, moon
  icon, "Dark mode" with a small track; icon only when the rail is collapsed; moon + track on phones, where the rail is a top
  row and the label pushed it past the screen). Until you use it the system's setting
  applies. ui.js flips it and writes the `ft_theme` cookie ("light" / "dark", a year, SameSite=Lax: Strict cookies are left off when the installed app is opened from the home screen); the server
  renders `data-theme` on `<html>` from it (`deps._page_context` `theme`), so pages open in your choice from the first
  paint with no inline script. app.css has the dark tokens twice: under `prefers-color-scheme: dark` for
  `:root:not([data-theme="light"])`, and for `:root[data-theme="dark"]`. Flipping dispatches `fintrack:theme`;
  charts.js and projection.js redraw on it (their colours are CSS variables read when drawn).

## Phones (≤640px; 2026-11-05, docs/phone-plan.md phase 1; rearranged 2026-11-06)

- **The read-only copy's banner** (`.offline-banner`, added by `offline-sw.js` first in `<body>` of a page shown from
  the copy; docs/phone-offline.md): "Copy · <when the data last synced>" and Try again, neutral colours (no
  `--status-*`: it has no icon), above the top bar and scrolling with it. Post forms on such a page are greyed out.

- **The phone shell is two `@media (max-width: 640px)` blocks in `app.css`:** the first stacks the shell and turns the
  rail into a top row (was ≤760px until 2026-11-13), the second finishes the phone layout; wider, the rail is at the side. Same markup as desktop (base.html), rearranged by CSS.
- **Top bar (52px), not sticky:** fintrack (the brand; the user tried the page's name there on 2026-11-06 and chose the
  brand), the theme switch (moon + track), Sync as an icon (the time in its title / aria-label), the environment badge.
  The rail's desktop `flex: 0 0 188px` must be `flex: 0 0 auto` here: in the stacked shell the basis is its height (it
  made a 188px-tall bar).
- **Ribbon controls: a panel opened from the current tab** (user's requests 2026-11-06). base.html wraps the
  `tabbar_controls` block in `<div class="tabbar-panel" id="page-controls">` (`display: contents` off phones, so the
  desktop ribbon is unchanged) and, only when that block isn't empty, gives the current tab `data-controls-toggle`,
  `aria-controls`; its mark (the name's width, 4px; it replaced a chevron, user's requests 2026-11-06) is a handle the
  panel carries: `.tabbar-panel::before` is its twin, placed by ui.js from the tab (`--handle-left` / `--handle-width`),
  lying over the tab's mark while closed and riding up with the panel, so the bar stays near the panel's top; the tab's
  own mark hides while open and returns once the panel has slid down. On a phone ui.js turns that tab's tap into
  open / close (`aria-expanded`); elsewhere it stays a plain link. The panel is fixed just above the tabs line, over the
  page (opening it moves nothing), sliding up from behind the tabs and back down (0.2s; at once with reduced motion;
  `visibility: hidden` while closed), groups side by side from the left and wrapping. Inside it, dropdown lists open
  upward and pop-outs (+ Add a transaction, New tag, Budgets' settings) open above the panel across the screen (their
  `<details>` made static so the panel is what they're placed against), scrolling when tall. A swipe up on the tabs line opens it and a swipe down on the line or the panel closes it (user's request 2026-11-06;
  mostly vertical, 30px+, not from inside a list, pop-out or text box; such a drag doesn't scroll the page). ui.js closes it when the
  page scrolls 24px+ (not while you type in one of its text boxes, since the keyboard scrolls too, or while a pop-out
  in it is open), on a tap outside, and on Escape. A page's controls must keep working as plain children of the panel:
  no `.tabbar > …` selectors (use `.tabbar-panel > …`). An element in the page marked `data-phone-controls` is moved
  into the panel by ui.js on a phone (a comment keeps its place; back there on a wider screen): Transactions' filters
  (All / Income / Spending / Transfers, search, accounts, categories, tags; user's request 2026-11-06), which keep
  working there because HTMX swaps only `#results` and the range, never the form.
  Inside: range buttons show short labels (`seg_label` in `_charts.html`: "90 days" → "90D", "1 week" → "1W"; the full
  label is the button's aria-label). Date ranges: the preset menu across the panel, then From and To sharing the next
  line in halves, words and all (user's request 2026-11-06; from 2026-11-05 they showed only for Custom, which was then
  always listed; Custom is again listed only while in use, as on desktop). A group of controls (`.tabbar-group`, e.g.
  Accounts' $ / % beside its range) wraps, so the range gets the full width under the rest. Projection's "Compare
  with" word is hidden (`.range-word`); Tags' search takes what its line has left.
- **At the bottom, fixed, from the top down: the section's tabs, then the seven section icons.**
  - Icons (user's choice 2026-11-05: icons only, each link has `aria-label`): the rail's own `<nav>` with
    `position: fixed`, which escapes the rail's `overflow: hidden`; `--bottom-bar` (56px) + 8px padding + 1px border +
    `env(safe-area-inset-bottom)`. Current section = accent icon on `--surface`.
  - Tabs (user's request 2026-11-06): the `.tabs` nav, also fixed, `--tabs-bar` (40px) high, just above the icons; one
    line inset 24px each side, a grid of equal columns (`grid-auto-columns: minmax(max-content, 1fr)`, 16px apart;
    user's requests 2026-11-06): two pages take half the line each, every name centred in its column; natural widths
    that scroll sideways once they don't fit (Settings' six), scrollbar hidden; ui.js sets its `scrollLeft` so the
    current tab is in view (never `scrollIntoView`, which could scroll the page). Each name is only as wide as its text;
    the current tab's mark is a 3px accent bar spanning the name (not its chevron), 5px inside the line's top (on the
    edge it was lost against the page scrolling behind). Hidden for a one-page section (`:has(a + a)`) unless its page has controls
    (`.tabs.has-controls`, which would show a lone tab to open them; no one-page section has controls since the
    Overview's range moved onto its card, so the Overview shows no tab line on a phone).
  - `main`'s bottom padding and the toast (`.flash.ok`) keep clear of both (more when the tabs show).
- **Android's gesture bar** (under the icons; user's request 2026-11-06 that it follow the theme):
  - In a Chrome tab: the viewport meta has `viewport-fit=cover`, so Chrome (135+, its edge-to-edge mode) draws the page
    behind the gesture bar instead of its own light strip ("the chin"); the icon bar's `env(safe-area-inset-bottom)`
    padding fills that strip in `--rail`.
  - In the installed app this doesn't happen: Chrome colours the gesture bar from the manifest's `theme_color`, baked
    into the installed app at install time, so no page can switch it with the toggle. The user chose **always dark**:
    `theme_color` = the dark `--rail` (`#121211`; test_schedule pins it), a dark strip under the light bar in light
    mode. Alternatives turned down: `display: fullscreen` (follows the toggle but hides the clock and battery), a
    manifest served per theme (Chrome only re-reads it on a reinstall or its own update check, days later). A change
    shows after removing the app from the home screen and installing it again.
  - Once a page has loaded, the page's `theme-color` takes over from the manifest's (dark at launch, then white was
    seen 2026-11-06, while the phone's tag was still `--surface`). So base.html lists phone tags first
    (`media="(max-width: 640px)"`, the first match wins) in the bars' `--rail` (`#eeeee9` / `#121211`): the status bar
    and the gesture bar match the top and bottom bars and follow the theme; desktop's tags stay `--surface` for the
    title bar. Each tag has `data-light` / `data-dark`; the theme switch sets every tag to its colour for the new theme
    at once (keeping only the phone-width condition), so no reload is needed. Confirmed working on the user's phone
    2026-11-06.
- **Swipe rows:** Transactions' two cards (Summary or by category, and over time; `.tx-charts.both`) are one row you
  swipe through (user's requests 2026-11-06): `grid-auto-flow: column`, running to the screen's edges, the gap between
  cards = the gutter, each card the content width less 24px (~92%; the user's 90%, adjusted so the gaps line up) so 24px
  of the next card shows (of the first, once swiped), `scroll-snap-type: x mandatory`, both cards the same height. phone_check doesn't count a `scroll-snap-type: x` box as sideways.
- **Charts on phones** (phase 3, 2026-11-06): fixed heights in app.css "Phone charts" (200 / short 160 / medium 200 /
  tall 240px, not vh), about 4 axis labels and a compact legend (charts.js and ui.js check `(max-width: 640px)` when a
  chart is built). A new chart kind that must keep its own size: exempt it there.
- **Spacing on phones** (user's request 2026-11-06): 10px everywhere between cards (across and down) and at the page's
  edges, the gap of Net worth's two-a-row cards: `--gap: 10px` in app.css's "Phone spacing" block (`--gutter` follows
  it), plus overrides of the rules that hard-code 16px between cards (`.card`'s margin, the tile rows, the Overview's
  columns, chart pairs, the page-level grids). New layouts: space cards with `var(--gap)`, not a number. The padding inside
  every card and tile is `var(--gap)` too (user's request 2026-11-06); a few headline figures went a step smaller to
  fit inside it at 360px (Net worth's, Portfolio's, Cash flow's tiles, Investments' 1D line).
- **Phone building blocks** (user's requests 2026-11-06):
  - `.phone-swipe` on a grid: one row you swipe through on a phone (each card the width less 24px, gutter-wide gaps,
    snapping, equal heights; shares its rule with Transactions' two cards). Credit cards' cards, Recurring's charts.
  - In Transactions' swipe row the chart card is as tall as the Summary beside it, and its chart fills the rest of the
    card (flex, at least 160px; user's request 2026-11-06).
  - `data-mini-charts` on a container: on a phone its charts drop the legend and keep ~3 labels an axis (charts.js
    `mini`; `roundTicks` honours a chart's `maxTicksLimit`). With app.css's small-card rules
    (`.savings-cards .cards[data-mini-charts]`): two cards a row, smaller figures, 90px charts. Net worth's Compact
    cards and its 100px chart, Savings' cards.
  - `.w-long` / `.w-short`: two wordings, the short one on a phone ("SPY" for "S&P 500 SPY", "90D", "1D"); only one is
    displayed, so a screen reader reads one.
  - No "Show data" on a phone (user's request 2026-11-06): `details.data-view` is hidden there; every chart still has its
    table on wider screens (the dataviz rule), and the "Show data" tables were already not counted by phone_check.
  - A per-page phone default on the server: `remote.via_phone(request)` (the listener the request came in on, never a
    header), e.g. Net worth's cards Compact on a phone.
- **Tables on phones** (phase 2, 2026-11-06; details in docs/phone-plan.md): no main table scrolls sideways. A list of
  things with one amount uses `table.row-cards` (tag its cells `rc-main` / `rc-amt` / `rc-sub` (+ `data-label`) /
  `rc-act` / `rc-tool` / `rc-hide`; `rc-cols` for many figures a row); the transaction lists have their own shape
  (date headings, category icon, Select mode, the edit panel as a full-screen sheet); a few tables are placed by
  position in their own ≤640px blocks. A new table: give it one of these, then check it with phone_check.
- **Touch (phase 5, 2026-11-11; the "Phones: touch" block, last in `app.css`):** everything tapped is at least 40px
  (`node tools/phone_check.mjs --min 40 --why` lists what isn't, by kind). Fields (inputs, selects, textareas) are
  really 40px with **16px text** (iPhone Safari zooms into smaller); buttons with a face (secondary, danger,
  `.button`, `summary.button`, icon buttons, segmented controls) are min-height 40px (`.button` / `summary.button`
  are `box-sizing: border-box`, or their padding adds to the 40). Links, text buttons (`.link-button`), sort buttons
  and icon buttons (delete, edit, add, move) keep their drawn size and get a **bigger tap area: padding with an equal
  negative margin**, so nothing moves and rows don't grow (only for things with no background, which the padding
  would show). Up and down for links (12px takes a 16px line to 40); icons 11px all round. **Sideways, inside a
  table, the extra room goes to the left with left padding as large as the margin** (`.table-wrap`): past a scroll
  box's right edge it makes the table scroll, past its left it doesn't, and unequal padding moves the words (seen on
  Transfers). A new control: use these classes, or give it the same treatment in that block. **Exception, toggle
  groups** (user's requests 2026-11-11): every `.seg` (range, view, $ / %, layout, the ← Today → arrows) and
  Transactions' `.kind-toggle` are 37px on a phone (35px items, text centred), not 40+, so they match each other; the
  rule comes after the touch block and also undoes the card heads' tap padding on their links. (The Investments card's
  Investments / Watchlist tabs, `.card-tabs`, are a title, not a filter: 32px as on wider screens.) Not done, by choice: the
  calendar's days stay about 50x21 (user's choice 2026-11-11: the compact month matters more). **`.table-wrap` is
  `overflow-y: hidden` on phones**: a last-row link's tap padding hangs below the table, and `overflow-x: auto` alone
  made that a few px of vertical scroll on Tags, Spending, Recurring, Setup and Transactions (found 2026-11-11).
- **Categories and Account types rows on phones** (user's choice 2026-11-11): one line, the name field as wide as it
  can be, the count (min 2.6rem, so names end in line) and a vertical ⋮ button (`.icon-more.phone-only`,
  `data-row-more`, ui.js; vertical at the user's request) close together; ⋮ shows the row's +, order arrows, Move to
  and delete on a line under it, at the right (`.ar-row.more-open`). The + of a head row is a second, phone-only
  button (`.ar-add`, same `data-toggle-panel`, so both stay in step); the one beside the name is hidden on phones.
  Account types' "Counts as" is behind ⋮ too (user's request 2026-11-11), on a full line under the other controls. Flex on phones, the desktop grid above 640px.
  Chosen over two lines per row (the plan; about 3 more screens on Categories) and over hiding the count.
- **The Overview's Accounts card on a phone** (user's requests 2026-11-11; it replaced phase 2's change under the
  value): a table, one line a row, name … 1-day change · total, so the changes right-align in their column and the
  totals in theirs; amounts in whole dollars there (`amount()` in `_accounts_card.html`, `.w-long` / `.w-short`); the
  name takes the rest and would end in "…" (`max-width: 0` + ellipsis); Assets / Liabilities at 1rem so their total
  doesn't widen the column. Every name fits at 360-412px. Group and account rows are both 32px: the card's own
  `margin: 0` for a group's name button had cancelled the touch rules' negative margin, so its 12px tap padding made
  group rows 56px; the phone rule restores `margin-block: -12px` (the tap area stays 40px+). A trap for any control
  with its own margin rule: check its row's height after the touch block.
- **The Overview on a phone** (user's choice 2026-11-11): after the tiles, the cards in the order of where each would
  sit in the two columns (each column's cards stacked by their phone heights, then all by their top, the left first on
  a tie; ui.js, `[data-overview-order]`; the columns are `display: contents` there so `order` applies), so the
  columns take turns: Net worth, Accounts, Investments, calendar, ... (Investments sits under Net worth over time in
  the left column on every screen since 2026-11-11, user's request; it was in the right column)
- **Swiping between pages** (user's requests 2026-11-11): on a tab's own page, a sideways swipe moves to the page
  beside it in the bottom bar's order (every section's tabs, left to right; `deps.nav_for` prev / next -> `<body
  data-swipe-prev / data-swipe-next>`; ui.js). **Like a carousel:** once this page has loaded, the pages beside it are
  built in untouchable frames behind the bars (`iframe.swipe-peek`, z-index 27, from 50 ms after its scripts run; the frame's own bars sit under the
  page's). The page follows the finger 1:1 and the page beside comes in with it; **letting go past 40% of the width
  or with a fling (0.4 px/ms over the last moves, as Android's pagers) finishes the move**, else both spring back.
  Finishing slides both the rest of the way (0.22 s), then loads the page the frame already shows (sessionStorage
  `ft-swipe` = `peek`, so `swipe-reveal.js` skips the View Transition: an identical page replaces the frame). The 40%
  is measured from where the finger first touched; the page itself starts moving where the swipe is recognised as
  sideways (12px), so it doesn't jump. **Galleries and tables that scroll sideways scroll first; at their end in the
  swipe's direction, the page moves** (charts are swipeable: galleries are mostly charts). Never from the screen's
  edges (24px: Android's own Back, which a page can't take over), nor on fields, dialogs, open details, fixed bars or
  sheets, `[data-no-swipe]`. Peeks are rebuilt after an HTMX request that isn't a GET; a frame's own scripts don't
  swipe, refresh the copy or fetch ahead (`window.top !== window`). **CSP:** a phone's pages (phone listener only)
  allow frames of this app (`frame-ancestors 'self'`, `frame-src 'self'`); the Mac's keep `'none'` (web/app.py).
  **The bars move with it** (user's request 2026-11-11): the current tab's mark slides toward the tab it's going to
  (`.tab-mark`, a moving copy; the usual `::before` hides meanwhile, `.tabs.marking`); into another section, this tabs
  line slides out and that section's slides in beside it (`.tabs-incoming`, built from `<body data-swipe-next-tabs /
  -prev-tabs>`: that section's tabs, from `deps.nav_for`, so it's there from the first touch with or without a peek;
  a copy taken from the peek's page wasn't always ready on the phone, 2026-11-11), and the section's highlight moves to
  the next icon (`.section-mark`, the real highlight's own top and height, with the two icons' colours blended by
  `color-mix`). Across sections there's **one** mark: both lines' own marks hide and `.tab-mark.travelling` (fixed)
  goes from the current tab, moving out with its line, to the new one, moving in with its line. Without a peek, on
  letting go, the tabs line that came in takes the name `ft-tabs` (the old line drops it), so the View Transition
  swaps it for the new page's identical one rather than sliding tabs in a second time (user's report 2026-11-11).
  Peeks are built 50 ms after the page's scripts run (not after every image has loaded): ready in about 240 ms on the
  harness; before, a phone swiping on soon after arriving had none. All by the
  same share as the page; finishing ends them where the new page draws them, springing back puts them back.
  **No peek yet** (the first second, no worker, reduced motion): the page follows alone and, when the move finishes,
  carries on out (0.3 s) while the next page arrives, which then slides in with a cross-page **View Transition**
  (`@view-transition` and the `ft-slide-*` keyframes in app.css, phones only; `swipe-reveal.js`, not deferred, sets
  its direction from `ft-swipe` in `pagereveal` and skips it for anything else and for reduced motion). The top bar,
  the section icons and the tabs line have view-transition-names, so they stay put. Traps found: content following a
  finger rightward stuck out past the screen and widened the page, so Chrome skipped every rightward slide
  (`.workspace { overflow-x: clip }`); the top bar's transition name made it a layer of its own, so the sliding page
  covered the section icons (`.rail { z-index: 31 }`).
- **The next page is ready before the swipe** (the user's first try waited for a load, 2026-11-11): the read-only
  copy's worker (`offline-sw.js`) is told each page's neighbours (`offline-copy.js`, message `neighbours`), fetches
  them and keeps them a minute (a copy each time it's used: the peek, then the page itself); a swipe gets one at once. Any request that isn't a GET drops them (no page from before
  an edit). The app's CSS, scripts and icons come from the worker's store and are refreshed behind (pages are
  no-store, so a phone used to fetch them all on every page). Measured on a harness answering pages after 600 ms: the
  new page's first frame 150-170 ms after the finger lifts, against about 720 ms without. Chrome's own prerendering
  (Speculation Rules) was tried first and dropped: the phone fetched the rules and never prerendered (the installed
  app or a phone setting; it can't be checked headless either). So swipes are only quick with the copy on
  (`PHONE_OFFLINE_COPY`), which registers the worker.
- Measure with `node tools/phone_check.mjs` (true mobile emulation; phase 0), not `--window-size` screenshots. It
  skips what's inside a closed `<details>` (Chrome lays it out anyway) and measures a checkbox / radio by its label.
  It fails a page with a box that scrolls up and down inside it ("Boxes up/down"), unless that box or one it's in has
  a height on purpose (a `max-height`, or `position: absolute` filling a fixed box): Tags' calendar and Spending's By
  category scroll inside their cards by the user's choice.

## As an installed app (2026-11-05)

- **User's choice 2026-11-05:** fintrack runs as Chrome's installed app (Install page as app): its own window and
  Dock icon, no tabs. The server runs at login as the launchd job `com.fintrack.serve` (`fintrack autostart`,
  `schedule.py`; README "As an app").
- `static/manifest.webmanifest`: name, `display: standalone`, `start_url` and `scope` `/`, icons, `theme_color`
  `#121211` (Android's gesture bar in the installed app; see Phones). Served by the app, so `default-src 'self'` covers
  it; no CSP change.
- Name (user's choice 2026-11-12): **Track Plan Grow** on every page title (`Transactions · Track Plan Grow`;
  Overview just the name), beside the logo in the rail, on the passcode page, in the offline copy's pages and as the
  installed app's `name` / `short_name`. Only what you see: the command, package, `~/.fintrack`, launchd jobs,
  cookies, caches and events keep `fintrack`, and so do sentences naming a command (`fintrack init`). On phones
  under 390px the top bar shows the logo alone (the name, theme switch, sync and environment badge need about
  386px; `tools/phone_check.mjs --width 360` found the page 26px too wide).
- Logo ("R-E5", user's choice 2026-11-12): a money tree (five pointed leaves, a two-strand braided trunk, a pot) in
  full color, in the style of a reference shield icon (its curves, light and shadow) but in the app's blues around
  `--accent` (`#8cc0f7`, `#62a7f2`, `#3987e5`, `#2a78d6`, `#1f62b8`, deepest `#144c9e`; user's choice over the
  reference's purple), leaves stepping from light on the left to deep on the right, each split on an S-curve into a
  lit and a shaded half, soft deep-blue shadows only inside the drawing (each leaf on the parts behind it, the rim on
  the pot; nothing on the background), light from the top left, the pot two ribbons (light, deep) meeting on an
  S-curve under a mid-blue rim (`#b3d3f7` to `#7fb0ee`, darker than the white outline below).
  Three files, the same drawing (user's choices 2026-11-12): `static/icons/favicon.svg`, the browser tab only, on a
  white rounded square (corner radius 23% of the side, the tree 80% of its height). `static/icons/logo.svg`, the logo
  beside the name: at the top of the rail (a 20 px `<img class="brand-logo">`, 2 px over the section icons, alone
  when collapsed; Overview keeps the house) and on the passcode page; transparent, framed tightly, with a thin white
  outline around the whole icon so the deep blue reads on a dark background (invisible on a light one, so it needs no
  theme switch, which an `<img>` couldn't follow anyway). Both are images rather than inline SVG because their
  gradients and masks use ids, which would clash if the drawing were inline twice on a page. `icon.svg`, `icon-192.png`,
  `icon-512.png`: the installed app's icon, the drawing on a full white square filling 70% of its height, inside
  Android's 80% safe circle, so a circle or squircle crop never cuts it (listed `any` and `maskable` in the manifest).
  Both SVGs were generated from the design canvas (not hand-edited paths); the PNGs are icon.svg rendered by headless
  Chrome at 192 and 512 px. To change the logo, regenerate the three SVGs and render the PNGs again. An installed app
  picks up a new icon when Chrome next checks the manifest (Android: on a launch, at most about daily), or on
  reinstalling.
- `theme-color` (base.html) tints the app window's title bar: `--surface` of each theme (`#ffffff` / `#1f1f1d`) by
  `prefers-color-scheme`, or the one theme chosen with the rail's switch (`ft_theme`). Keep them in step with app.css.
