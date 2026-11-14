# Phone layout: review and plan

Review of every page at phone width (2026-11-05), done when phone access went live (history.md's open item "how the
pages look at phone width"). When a phase is built, its rules move into `docs/ui.md` (or the page's doc) and it's
marked done here.

**Status (2026-11-11):** phases 0-6 built (phase 4 by the user's page requests). Since phase 5: swiping between pages
(docs/ui.md Phones). Phase 6 is not committed at the time of writing. Start a new session with "Where we are" just below.

## Where we are (2026-11-11): read this first in a new session

**Built** (rules in `docs/ui.md` Phones, which also lists the reusable pieces; each page's own doc has an "On a phone"
note): the shell (top bar, seven icons at the bottom, the tabs line above them whose current tab has a handle that
slides the controls panel up: tap or swipe up / down); every main table fitted (phase 2); charts sized (phase 3); 10px
spacing and card padding everywhere; and the user's page-by-page requests of 2026-11-06 (Overview, Transactions,
Spending, Net worth, Accounts, Investments, Portfolio, Savings, Cash flow, Credit cards, Recurring). `node
tools/phone_check.mjs --width 360,390,412` passes 60 of 60 page runs.

**Phase 5 (touch) built 2026-11-11** (rules in `docs/ui.md` Phones, "Touch" and "Categories and Account types rows"):
targets under 40px went from about 1,400 to 217 over the 20 pages (`phone_check --min 40`; it now skips closed
`<details>`, which had counted ~3,000 hidden menu options, and measures a checkbox by its label); 60 of 60 page runs
pass at 360 / 390 / 412. Left: the calendar's days (the user kept them compact), counts and short descriptions a few
px narrow (33-37px wide, 40+ tall), Tags' narrow table links. Categories / Account types rows: one line + ⋯ (user's
choice). Account types 3.7 -> 2.8 screens; Budgets 5.3 -> 6.0 (its amount fields are 40px now).

**Next: phase 6** (below). For the record, the measurements that drove phase 5 (controls under 32px, at
390px, 2026-11-07): Categories ~3,330, Tags ~1,360 (mostly calendar days), Account types ~300, an account's page ~200,
Overview ~130, Transactions ~120, Budgets / Rules / Transfers 85-96 each. Page lengths (screens at 390px): an account's
page 20.6, Transactions 7.5, Categories 6.8, Spending 5.8, Transfers 5.6, Overview 5.4, Net worth 5.4, Budgets 5.3.

**Decided 2026-11-11:** the Overview's card order (by where each card would sit in the two columns) and an account's
page (25 transactions, then Show more): both built (phase 6).

**How the user works on this:** they review on their Android phone (installed app, dark mode) and ask for changes a page
at a time, phones only; desktop must not change. Keep each change ≤640px, confirm it in phone emulation, keep the
tests passing, update the docs in the same change.

**How to check a phone change** (no scratch scripts are kept; write one when needed):
- `node tools/phone_check.mjs [--width 360,390,412] [--shots DIR] [pages]`: widths, sideways overflow, space above
  content, small targets. `--shots` writes screenshots: keep them **outside the repo** (they show real data).
- For one-off measurements, the same Chrome DevTools approach (Node 24's WebSocket; `Emulation.setDeviceMetricsOverride`
  with `mobile: true`, then `Runtime.evaluate`, `Page.captureScreenshot`; `Input.dispatchTouchEvent` for swipes). Measure
  text with a Range (`selectNodeContents`) against the card's padding, not just element boxes.
- The emulator reaches https://localhost:8443, **not** the phone listener, so server-side phone defaults don't apply
  there (`remote.via_phone`): e.g. Net worth's Compact cards need `?cards=compact`.
- Template, CSS and JS changes are live at once; Python changes need `~/.fintrack/venv/bin/fintrack autostart restart`.

**Pitfalls met so far:** `.num { text-align: right !important }` (a phone line-2 cell needs `!important` to read left);
several base rules outweigh a plain phone selector (`.kpis.one-row > .tile`, `table.data.compact td`, `.grid-2`,
`.tag-table.with-budget`): match or raise their weight; a fixed-layout table takes column widths from its first row
(use a `<colgroup>`, and hide a `col` whose cells are `display: none`); a flex `tr` inside a table becomes a cell of the
first column; many tests pin exact markup (search `tests/` for the string before changing it, and update the test to
the new markup when the behaviour is unchanged); `.w-long` / `.w-short` for phone wording (never `aria-hidden`).


## How the review was done

Headless Chrome driven over the DevTools protocol with true mobile emulation: a 390×844 viewport, `mobile: true`,
touch on, an Android user agent (not `--window-size`, which crops, see ui.md). Every page in `NAV` plus one account page
was loaded from the running server. For each one the script took a full-page screenshot and measured:
`documentElement.scrollWidth`, the rail and tab-bar heights, every element inside a sideways-scrolling `overflow-x`
box, elements past the screen edge, and tap targets under 32px. The script is a throwaway in the session scratchpad;
phase 0 proposes keeping a version of it in the repo.

## What the measurements show

| Page | Length (screens) | Above content (px) | Sideways tables | Widest table (px) | Small tap targets |
|---|---|---|---|---|---|
| Overview `/` | 6.2 | 319 | 3 | 459 | 140 |
| Transactions | 5.6 | 421 | 2 | 821 | 391 |
| Spending | 7.1 | 368 | 0 | — | 22 |
| Net worth | 6.6 | 411 | 5 | 761 | 16 |
| Accounts | 2.6 | 411 | 1 | 829 | 23 |
| An account `/accounts/{id}` | 9.5 | 263 | 1 | 660 | 217 |
| Investments | 3.2 | 340 | 1 | 627 | 22 |
| Portfolio | 6.1 | 319 | 2 | 546 | 15 |
| Savings | 3.6 | 368 | 3 | 784 | 12 |
| Cash flow | 2.8 | 367 | 1 | 436 | 8 |
| Credit cards | 3.5 | 368 | 0 | — | 47 |
| Budgets | 4.1 | 372 | 1 | 930 | 109 |
| Tags | 1.8 | 372 | 4 | 371 | many (calendar days) |
| Recurring | 4.3 | 327 | 3 | 596 | 39 |
| Projection | 3.7 | 368 | 1 | 358 | 12 |
| Setup | 2.8 | 315 | 0 | — | 24 |
| Account types | 3.9 | 315 | 0 | — | 311 |
| Categories | 7.7 | 315 | 0 | — | many (every row's controls) |
| Rules | 5.0 | 315 | 0 | — | 95 |
| Transfers | 7.3 | 421 | 3 | 761 | 48 |

"Above content" = rail + tab bar (tabs and ribbon controls), the space before the first card. One phone screen is
844px. Only the Accounts page actually makes the page itself scroll sideways (by 12px), and Tags has two controls past
the edge; everywhere else the overflow is hidden *inside* sideways-scrolling tables, which is why the earlier "nothing
runs past 390px" check passed while the pages still look broken.

## The six problems behind "messy"

1. **The chrome eats a third to half of the first screen.** The desktop rail becomes a wrapped top row at ≤760px
   (`app.css` `@media (max-width: 760px)`): seven sections over three lines, then the theme switch, Sync now, the
   Production badge and the sync time on a fourth: 213px on every page. Under it the tab bar wraps (Settings' five tabs
   take two lines) and the ribbon controls wrap raggedly (`365 days ▾ From [date]` / `To [date]` / a stray `+`). On
   Transactions and Transfers the first card starts at 421px.
2. **Tables hide their most important column.** `.table-wrap { overflow-x: auto }` is the only narrow-screen
   treatment, and the money is in the right-most columns. At 390px: the Transactions list shows a checkbox, date and
   description, with no amount or category (821px table); Budgets cuts off mid-amount in the Budget column and Spent / Left
   are off screen (930px); Investments' holdings stop at Price (no Value); the account page's transactions show no
   amounts; Savings' Net savings rate table is 784px. The Overview's Accounts card is clipped on the right: the totals
   are cut off after their first digits.
3. **Charts get *taller* on phones.** Chart heights are `clamp(…, N vh, …)` (`.chart-box.short/medium/tall`, app.css
   ~1460). A phone's viewport is tall, so a chart is 450–550px: most of a screen. Recurring spends 520px on two bars;
   the Net worth page stacks five charts; Overview's "this month vs last" is 450px.
4. **Tile rows don't reflow.** Rows of 3–4 tiles stay side by side: Spending's three tiles wrap their labels word by
   word, Investments' four benchmark tiles wrap to "Russell / 2000 / IWM", and its range buttons break into "1 / day".
   Meanwhile Projection's four one-number tiles stack full width with large padding (a whole screen for four numbers).
5. **Editing rows don't fit.** Categories gives each name field 2–3 letters ("Ba", "Cas") because the icon, +, count,
   reorder and delete each keep a fixed width. Budgets' amount inputs are clipped. Most controls are under 32px
   (Android's guideline is 48dp; 40px is a reasonable floor here).
6. **Desktop density everywhere.** 18–20px card padding plus a 16px gutter leaves ~316px for content; most text and
   headings are desktop sizes; pages run 4–9 screens long.

## Principles for the phone work

- **Phone rules only below 640px**, mostly in one `@media (max-width: 640px)` section of `app.css`, so desktop and the
  installed Chrome app don't change. (760px stays the "small window" step; 640px is phone.)
- **Same templates, no second phone site.** Rows get a few classes (`phone-hide`, `phone-sub`, `data-label`) and CSS
  rearranges them. Only when a table genuinely needs another shape does a template get a phone-only block.
- **Keep the user's rules:** no page titles (the tab names the page), controls in the ribbon, minimal UI text (no new
  helper text to explain the phone layout), no inline JS or styles, charts always keep "Show data".
- **The money column always shows.** On every table: the label and its main amount are visible without sideways
  scrolling; secondary columns move under the label or are dropped on phones.
- **Measured, not eyeballed:** each phase ends with the phone check (phase 0) and a screenshot pass at 360 and 412px as
  well as 390 (small Android, iPhone, large Android).

## Phase 0: a repeatable phone check (built 2026-11-05)

- Keep the review script as `tools/phone_check.mjs` (Node 24, no dependencies: headless Chrome + DevTools protocol,
  true mobile emulation). It prints the table above and writes screenshots to a folder you pass (never the repo: they
  hold real data). Read-only: GET pages only, sets no cookies.
- Targets to pass on every page: page `scrollWidth` = viewport; **no main table in a sideways scroller** (the "Show data"
  tables may keep scrolling); above-content height ≤ 120px (after phase 1 it was ≤ 170px on pages with ribbon controls, which
  took their own line; since 2026-11-06 they're in a drop-down and every page starts at 69px); no control under 40px tall
  (phase 5; the script counts under 32px for now).
- Built as described: `node tools/phone_check.mjs [--width 360,390,412] [--shots DIR] [pages…]`. Baseline before phase
  1: 0 of 20 pages pass at 390px.
- Run it after each phase, and in a future session before calling a phone change done.

## Phase 1: the app shell (built 2026-11-05, rearranged 2026-11-06)

Goal: content starts at ~110px instead of 263–421px. First built 2026-11-05 (top bar, icons at the bottom, tabs and
controls each on one line under the top bar: content at 115–171px). Rearranged on 2026-11-06 at the user's requests:
the tabs moved to the bottom above the icons and the controls into a panel the current tab opens (first tried as a
drop-down from the page's name in the top bar, turned down for the brand); Transactions' filters into that panel and
its two cards into a swipe row; the date ranges' From / To back; Account types' extra 16px at the top fixed (a card's
margin collapsing through its wrapper). **Result:** content at 69px on every page at 360, 390 and 412px; 6 of 20 pages
pass at 390px, the rest only on their sideways tables (phase 2).

As built (this list replaced the 2026-11-05 plan's bullets; the rules are in `docs/ui.md` Phones):

- **Top bar (one line, 52px, not sticky):** fintrack, the dark-mode switch (moon + track; it stayed here, as it fit,
  rather than moving to Setup), Sync as an icon (the sync time in its label and title), the Production badge (a safety
  signal).
- **Sections: seven icons in a bar fixed at the bottom** (56px + `env(safe-area-inset-bottom)`; no labels, each named
  by aria-label; decision 1). The installed app's Android gesture bar under it is always dark (the manifest's
  `theme_color`; it can't follow the switch).
- **Tabs: a line fixed just above the icons,** names in equal columns (two pages: halves), each centred, 24px in from
  the edges; natural widths that scroll sideways once they don't fit (Settings' five), the current tab scrolled into
  view; its mark a bar the name's width, a few px inside the line. Hidden for a one-page section unless its page has
  controls.
- **The page's controls: a panel the current tab opens** (its chevron), fixed just above the tabs, over the page; it
  closes on scroll, an outside tap or Escape. Inside: dropdown lists and pop-outs open upward; date ranges are the
  preset menu, then From and To in halves; range buttons have short labels (1D 1W YTD 90D 180D 1Y); groups wrap.
  Transactions' filters live in it too (`data-phone-controls`).
- **Transactions' two cards** (Summary or by category, and over time) are one swipe row: equal heights, the gutter
  between them, 24px of the next card showing.
- Content starts at 69px on every page (target ~110px). The pages that fail `tools/phone_check.mjs` do so on their
  tables only (phase 2).

## Phase 2: tables that fit (the main fix for pages looking broken)

A small shared pattern plus a per-table choice of which columns stay. **Built 2026-11-06:** every table in the list
below; `tools/phone_check.mjs` passes 58 of 60 page runs at 360 / 390 / 412px (the two left, Portfolio and Cash flow at
360px, are 2px from their tiles: phase 4). The checker now skips a closed `<details>` (a "Show data" table, which Chrome
still lays out) and swipe rows. Desktop is unchanged (every rule is in a ≤640px block; the phone-only pieces are hidden
outside it).

- **Transaction lists, as built** (Transactions, an account's page, Categories to review; app.css "Phone tables"): each
  row a grid of the category's icon (`.phone-cat`, in place of the logo) · the description with its status icons and a
  calendar icon when tagged (`.phone-tagged`, in place of the chips; muted: tried in the tag's color 2026-11-06 and
  undone at the user's request) · the amount; the description without its underline, 1.05rem (user's requests
  2026-11-06); the date, category name, account,
  card and rewards columns hidden. While sorted by date, `<tr class="date-head">` rows head each date (the wrapper has
  `data-by-date`; hidden on wider screens); sorted another way, each row shows its date small (`.phone-date`). The head
  keeps Date and Amount (sort links) and, on Transactions, Select (`data-select-mode`): the boxes show, a tap ticks the
  row, the group-edit bar is fixed above the tabs; Done clears the selection. Tapping a row opens its edit panel as a
  full-screen sheet headed by the description, amount, date, account and category (`.sheet-head`; the rule builder is
  a sheet too), the page under it held still; no autofocus there (the keyboard would cover it). The review card's
  category menu takes its own line. The lists' wrapper drops its container (`container-type: normal`), which would
  otherwise trap the sheet. Account pages' rewards-balance lines group by their date too.
- **Row cards, as built** (`table.row-cards`, app.css): cells tagged `rc-main` (name, line 1 left), `rc-amt` (amount,
  line 1 right), `rc-sub` (line 2, small, a `data-label` before it, a "·" after it so a wrapped line ends cleanly),
  `rc-act` (buttons or a form, a line of their own), `rc-tool` (a small button after the amount), `rc-hide`; the head
  goes. `rc-cols` lays line 2 out in two columns (many figures a row; a figure is never cut, it wraps after its label).
  Used by: the Overview's Recent transactions, Coming up (Overview and Cash flow; its Balance after, pinned by a test, is
  the row's last cell), Cash flow's Credit cards (its total row by position, pinned too), Savings' Net savings rate and
  Net worth's Change in value (`rc-cols`; these were already a row per month, so pattern C wasn't needed), Recurring's
  lists and Next 30 days, Transfers' three tables, Projection's Loans and cards.
- **By position** (tables whose cell markup tests pin, or that share seven cells per row): Accounts card and list (grids:
  name … value, the change or statement under), Investments' holdings (name … value / ticker · shares @ price … the
  range's %; the 1-day and $ change go, so the panel's range buttons still mean something) and watchlist, Portfolio
  (name … value / cost / four range columns, label over value), Budgets' month table (name … remaining / budget [input]
  · actual · upcoming, its bar under; the budget stays an input on line 2 rather than a tap-to-open panel; the table's
  own hiding is repeated with `!important` after the flex display), Tags' tables (category … amount / budget · share
  with its bar; **replaced 2026-11-11 at the user's request** by the four columns of a wider screen, amounts in whole
  dollars: docs/tags.md "On a phone"). The Overview's movers / watchlist only got tighter cells.
- A gotcha: `.num { text-align: right !important }` (all widths), so line-2 cells that are `.num` need `!important` to
  read left.

- **Pattern A, "row card":** a `<tr>` becomes a two-line grid on phones (`display: grid` on the row, so the HTML
  stays a table for screen readers). Line 1: name … amount (right, tabular figures). Line 2, muted and smaller: the
  secondary columns joined with " · ". Used for transaction lists and any list of things with one amount.
- **Pattern B, "keep two columns":** label + the one or two key numbers; other columns get `phone-hide`. Used for
  summary tables.
- **Pattern C, "transpose":** tables whose columns are months (Savings' Net savings rate, Portfolio, Net worth's
  change-in-value tables) become one row per month, newest first, with the 2–3 key measures.

Per table:

| Table | Phone shape |
|---|---|
| Transactions list, account page's transactions, Overview's recent transactions | **User's design (2026-11-05):** rows grouped under a heading per date (no date column); each row: the category's icon (not its name), the description, a calendar icon when the transaction has a tag, and the amount; no account. The checkbox only in a "Select" mode (a button in the list's head) so group edit still works. Tapping a row opens a **reformatted edit view** that shows the full information (date, account, category name, tags, bank text, …) laid out for a phone. Phones only; desktop keeps one line per row (2026-10-17). |
| Categories to review | A, with its category drop-down on its own line. |
| Budgets (Month view) | A: line 1 category … left (or over) with its progress bar under it; line 2 spent of budget. The budget amount becomes editable from the row's tap (the current input in a drop-down panel) instead of an always-visible input. |
| Investments holdings, Portfolio holdings by account | B: name + ticker stacked in one cell … value, with day change under the value; quantity and price under the name. |
| Overview Accounts card | B: name … value; the 1-day change moves under the value (smaller). Fixes the clipped totals. |
| Accounts list | B: account … balance; institution and the change under the name. Fixes the 12px page overflow. |
| Savings Net savings rate, Net worth change-in-value, Portfolio by account type | C. |
| Recurring lists, Cash flow's Coming up, Transfers' pairs | A. |
| Tags cards' category tables | B: category … amount, budget used as the bar (the Share column's bar already exists). |
| "Show data" tables | Unchanged: they're opt-in and may scroll sideways. |

## Phase 3: charts sized for phones (built 2026-11-06)

**As built** (app.css "Phone charts", charts.js / ui.js `phone`): `.chart-box` 200px, short 160, medium 200, tall 240,
a short one under a measure row 140, Recurring's daybars 140; the Overview's budget bullets keep their height (a bar
per category); treemap, card bars and rings keep their own sizes. Net worth's / Accounts' cards stop sharing a height
(`--kind-card-h`) on a phone: their charts take 200 (over time) and 160 (each kind). Money axes aim for 4 labels
(`roundTicks`), date and category axes at most 4 (`maxTicksLimit`, where labels are skipped anyway), a compact legend
(10px swatches, 12px text). **Result:** pages only 0.1–0.4 screens shorter at 390px (Recurring 4.7 → 4.3, Net worth 6.4
→ 6.2): on a phone the length is the content (an account's 200 transactions, Categories' rows), not the charts.


- On phones, **fixed heights instead of vh:** short 160px, medium 200px, tall 240px (`.chart-box.*` in the 640px
  section). Treemap / card bars / rings keep their own smaller sizes.
- Daybars (Recurring) 140px: they show a handful of bars.
- Fewer axis labels on phones (x: ~4 dates; y: 3–4 values) via the shared axis helpers in `ui.js`, so labels never
  crowd; legends wrap to one compact line above the chart.
- Keep every chart open, only smaller (user's choice 2026-11-05, decision 3): nothing collapses behind its title.

## Phase 4: tiles and headline figures

**Mostly done 2026-11-06 by the user's requests, which replace the bullets below where they differ:** tile rows stay one
row on a phone (three a row: Net worth, Savings, Cash flow, Portfolio; Spending shows two, its Transfers tile hidden),
the figures and lines sized to fit (`.w-short` wording: "90D", "vs Oct 8", "12-mo Avg"); Investments' four tiles stay one
row instead of the planned 1×4 list. **Left:** Projection's four tiles as a 2×2. Also
that day, beyond the plan: Net worth Compact by default on a phone with its cards two a row and its chart 100px;
Savings' four cards 2×2; Investments' holdings and watchlist a table of ticker · price · 1 day · range % (name in the
tooltip; quantity, value, $ change and the watchlist's remove button hidden); Credit cards' cards and Recurring's charts
as swipe rows (docs/ui.md Phones, "Phone building blocks").

- Tile rows: **at most two per row on phones**; a third tile takes a full row. Spending: Spent this month full width,
  then 12-month average and Transfers side by side.
- Investments' four benchmark tiles become one compact card, a 1×4 list (user's choice 2026-11-05): one row each, name
  … % for the range, today's change beside it.
- Projection's four tiles: a 2×2 grid of smaller figures (Today, 1 year, 5 years, 10 years).
- Headline figure sizes keep their container-query clamps; only the label text drops a step.

## Phase 5: forms, editing and touch (built 2026-11-11; as built in docs/ui.md Phones)

- **Tap targets ≥ 40px** on phones: buttons, segmented controls, icon buttons (edit, delete, reorder), checkboxes
  (bigger hit area via padding, the box itself can stay small), links in table rows.
- **Inputs 16px font** on phones (iPhone Safari zooms into anything smaller; iPhone is still untried).
- **Categories:** each row on two lines: icon + full-width name on line 1; count, +, reorder, delete on line 2 (or the
  icons behind a ⋯ button). Same for Account types.
- **Rules:** already one per row; give the edit / delete icons 40px targets and tighten the three-line rows to two.
- **Edit panels** (transaction edit, budget amount, recurring form): full width with their buttons at least 40px;
  consider a bottom sheet (fixed, slides up) later if panels inside long lists prove awkward.
- Card padding on phones 14px (from 18–20), gutter stays 16px → ~330px for content (from ~316px).

## Phase 6: page-by-page polish (built 2026-11-11)

Done: the Overview's order (user's choice: by where each card would sit in the two columns, so they take turns); an
account's page shows 25 transactions then Show more (20.7 -> 4.1 screens); Credit cards' Redeemed / Cash rewards at
the other figures' size and lined up with them. Tags' calendar labels listed under the month was tried and removed the
same day (the user keeps them in the squares). The Overview's Accounts card: one line a row on a phone (below). Rechecked: Spending 5.8, Transfers 5.6, Categories 7.4, Transactions 7.5 screens (kept open,
decision 3); every page passes phone_check at 360 / 390 / 412. As planned:

- **Overview:** keeps every card (decision 3); whether to change their phone order is still open (Accounts appears
  after four left-column cards today).
- **Transactions:** the Summary table and the two charts below it push the list to the third screen; with everything
  kept open (decision 3), phase 3's smaller charts are what brings the list up.
- **An account's page (9.5 screens):** mostly phases 2–3; recheck after.
- ~~Account types: its first card sat 16px lower than on the other Settings tabs~~ fixed 2026-11-06 (app.css
  `main > [data-keep-scroll]:first-child > :first-child`; desktop too).
- ~~At 360px: Cash flow's chart-head form (13px) and a Recurring table (10px) widened the page~~ fixed in phase 2.
- **Spending (7.1 screens), Transfers (7.3), Categories (7.7):** mostly fixed by phases 2–3; recheck after.
- **Credit cards:** the second card's four figures in a 2×2 of the smaller size (the big rewards figure wraps today).
- **Tags:** the calendar's day labels ("11 V…", "26 T…") are unreadable at this width; on phones show the bars only
  and the labels in a list under the month.
- **Setup / Account types:** low priority on a phone (Link and Item actions are refused there anyway); they only need
  phase 5's touch fixes.

## Order and size

| Phase | What | Size | Pages helped |
|---|---|---|---|
| 0 | Phone check script | small | all (verification) |
| 1 | App shell: top bar, bottom bar, one-line tabs and controls | medium | all 20 |
| 2 | Tables: row cards, two-column, transposed | large (the most templates) | 14 |
| 3 | Chart heights and axes | small | 12 |
| 4 | Tiles | small | 5 |
| 5 | Touch targets, inputs, Categories rows | medium | all, Categories most |
| 6 | Page polish | medium | as listed |

Recommended order: 0 → 1 → 3 → 2 → 4 → 5 → 6. Phases 1 and 3 are mostly CSS and fix every page quickly; phase 2 is the
largest but is what makes the money visible. Stop after each phase for you to try it on the phone.

## Decisions (answered by the user 2026-11-05)

Answers: 1 (a) seven icons; 2 the grouped-by-date design in phase 2's table; 3 keep everything and shrink it;
4 one compact list (1×4). The questions as asked:

1. **Sections on phones:** (a) a bottom bar with all seven section icons and short labels (recommended); (b) a bottom
   bar with five (Overview, Transactions, Accounts, Planning, More → Investments, Analysis, Settings); (c) a ☰ menu
   button in the top bar that opens the full list.
2. **Transaction rows on phones:** two lines (description … amount, then date · category · account), which breaks
   the one-line rule of 2026-10-17 below 640px only (recommended); or one line with the category and account dropped
   and the amount kept.
3. **Shorter pages:** on phones, collapse secondary charts (Net worth's per-kind cards, Transactions' charts) behind
   their titles and reorder the Overview; or keep everything open and only shrink it.
4. **Investments' benchmark tiles:** one compact list card (recommended) or a 2×2 grid of tiles.
