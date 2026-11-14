# History: milestones and sessions

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

## Milestone plan

Stop at the end of every milestone for the user to test. Don't start the
next one without a go-ahead.

| # | Milestone | Status |
|---|---|---|
| 1 | Project skeleton, config, local HTTPS | Done (2026-10-02), user verified |
| 2 | Sandbox Link flow and account display (+ Production consent gate, update mode, `/item/remove`) | Done (2026-10-03), user verified (connect, OAuth, duplicate block, reconnect, remove) |
| 3 | Transaction sync (`/transactions/sync`, cursor per Item, `fintrack sync`, launchd instructions) | Done (2026-10-03); user verified scheduled launchd sync; UI tests 1–3 not explicitly reported |
| 4 | Net worth and daily snapshots (+ investment holdings) | Done (2026-10-03); user reviewed the chart (area, zoomed axis); other UI checks not explicitly reported |
| 5 | Expenses, categories, rules, transfer exclusion | Done (2026-10-03); user verified rules + kind changes and stale-page fix (Back button, second tab) |
| 6 | Savings view and dashboard polish | Done (2026-10-03); committed at user's request; UI checks 1–4 not explicitly reported |
| 7 | Manual accounts with dated valuations | Done (2026-10-03); user verified all 6 checks (backdating, latest-by-date, validation, liability, archive/restore, delete) |
| 8 | Budgeting (rollover, suggestions, history, unbudgeted bucket) | Done (2026-10-03); user tested and approved ("looks good") |
| 9 | Recurring detection and cash flow forecast | Done (2026-10-03); user tested all 6 checks; revision: "All checking & savings" first in the chart dropdown |
| 10 | Net worth projection and what-if scenarios | Done (2026-10-03); user tested all 5 checks |
| 11 | Switch to Production | Done (2026-10-04); committed at user's request: PLAID_ENV=production, 4 of 10 Items connected (3 non-OAuth providers failed in Link, no Item used → manual accounts); fixed negative card minimum payment crash; UI review checks not explicitly reported |
| 12 | Visual audit + redesign (layout, navigation, typography, tables, chart conventions) | Done (2026-10-04); committed at user's request; UI checks 1–6 not explicitly reported |
| 13 | Visualizations: Overview (net worth 90/180/365, this vs last month spending, spending by budget category with % used; cash flow = list of recurring items in the next 30 days; user removed budget progress and the 30-day chart as redundant) + charts on Spending, Budgets, Transactions, Recurring, Net worth, Savings, Forecast (none on Settings pages) | Done (2026-10-05); committed at user's request. Also: Finnhub quotes, card statements in the forecast, events-only cash flow, subcategories, layout refinements |
| 14 | Faster transaction editing (rule from a transaction with preview, bulk recategorize, search) | Done 2026-10-08; built and adjusted with the user (amount search, date range dropdown, column sort, group edit, click-to-edit rows with editable descriptions, status icons, merchant logos); committed at user's request |
| 15 | Spending analytics: merchant breakdown (category trends moved into M13) | Reworked with the user 2026-10-08/09 into the Spending page redesign (date range like Transactions, treemap, monthly totals + average, transactions card with chart/category filters); the merchant table was built then removed (its data function too, 2026-11-05). Committed 2026-10-09 |
| 16 | Investments: holdings allocation detail, contributions vs growth (needs months of snapshots) | Partly done 2026-10-08 (Investments/Portfolio pages, performance vs benchmarks, price history); 2026-10-11/12 added investment transactions, account pages (holdings, activity), live balances; contributions vs growth left |
| 17 | "What's unusual" dashboard flags + subscription audit | Planned |
| 18 | Year in review | Planned |

Milestones 12–18 were chosen by the user on 2026-10-04 (beyond the original brief); on the same day the
user moved visualizations ahead of transaction editing and asked for charts on every non-settings page.
Not chosen for now: review inbox, reimbursement splitting.

## Page and module moves

Moved from CLAUDE.md's Architecture tree on 2026-10-20 so that tree only says what each module owns.

- **Net worth** (`web/networth.py`): Breakdown table removed 2026-10-07; own/owe chart 2026-10-12; the date range,
  cards per kind of account and Change in value table added (user's request 2026-10-13); its Accounts card moved to
  Balances 2026-10-13.
- **Accounts** (`web/accounts.py`, template `accounts.html`): was `/balances` in `web/balances.py` (template
  `balances.html`) until 2026-10-14; its tiles were removed 2026-10-13. Since 2026-10-15 `/accounts/{id}` also holds
  what `/accounts/{id}/activity` and `/manual/{id}` showed (sortable investment activity, Kind, Manage = archive /
  delete); both old addresses redirect.
- **Setup** (`web/setup.py`): the old Settings → Accounts page was folded into `/setup` on 2026-10-14.
- **Rules** (`web/spending.py`): got its own Settings tab on 2026-10-13.
- **Cash flow** (`web/forecast.py`): `/cash-flow` in the Transactions section since 2026-10-14 (Analysis since 2026-10-25) (the Overview's
  projected balance chart + Coming up list; no Show data table, the list shows the same events); `/forecast`
  redirects there. The cash-flow section of `/recurring` was removed 2026-10-16, and `static/forecast.js` with it.
- **Prices** (`price_history.py`): official Alpaca closes replace differing Plaid prices (user's report 2026-10-08).
- **Demo** (`demo.py`): built at the user's request 2026-10-09.
- **Analysis section** (2026-10-25): Savings, Cash flow and Credit cards moved under Analysis.
- **Tags** (`web/tags.py`): in Analysis after Credit cards from 2026-10-28, moved to Planning after Budgets 2026-11-02.
- **Transfers** (`web/transfers.py`) and **Account types**: Settings tabs.
- Schema for newer modules: purpose accounts migration 0028, live values 0029, institution logos 0031, account types
  0032.

## Where we left off (2026-11-14)

Read this first, then 2026-11-11 below. Committed and pushed at the end of this session. Tests: 1,043 passing.
Migrations through **0073**, all applied on Production (each after its automatic backup).
- **Rename and logo** (2026-11-12/13): the app is "Track Plan Grow" (the command, package and `~/.fintrack` stay
  fintrack); logo R-E5 in the app's blues (`docs/ui.md` Name, Logo).
- **Importing bank and broker files** (`docs/import-plan.md`, all five phases; `docs/transactions.md` "Importing bank
  files"): `fintrack/imports/` (readers for CSV, `.xlsx`, OFX / QFX / QBO, QIF; layout detection; per-account profiles;
  the overlap question; batches with undo), `fintrack import`, **Settings → Import**. Primary card picker, funds
  tracked through another ticker (by dollars), reinvested dividends paired with their dividends (one added when the
  file has none). Migrations 0068-0070.
- **Manual and connected accounts alike** (`docs/parity-plan.md`, all six phases; the rule is in CLAUDE.md's
  conventions): manual bank accounts, cards and loans hold transactions and their balance follows them; a manual card's
  statement (closing / due days); manual investment accounts' activity is the ledger (`fintrack/ledger.py`,
  `investment_transactions` like Plaid's) and their holdings are rebuilt from it; the user's 75 typed entries were
  converted 2026-11-14 (`fintrack convert-positions`, checked to the cent on a copy, then on the real database); one way
  of counting contributions (`contributions.py`); a parity test (`tests/test_parity.py`). Migrations 0071-0073.
- **UI:** the rail stays at the side down to 641px and an expanded rail stays expanded between pages; the current
  section's icon is filled blue (no side bar); no Production badge; the sync time sits under Sync now with the rail
  foot's rows evenly spaced; Portfolio's ranges are 1 day, YTD, 1 / 3 / 5 / 10 years (ten years of closes fetched once
  per ticker); the Categories to review card has filters, sorting and a bulk bar; a manual account's History is
  "Balances" (balance updates only). Details in `docs/ui.md`, `docs/investments.md`, `docs/transactions.md`.
- **Open:** a checking account's balance dropped (Nov 14) with no transaction yet (the bank's balance came first):
  check the transaction arrives with the next syncs. PDF statements were discussed and set aside (they'd need a library; pypdf suggested).

## Where we left off (2026-11-11)

Read this first, then 2026-11-07 below. Not committed yet at the time of writing. Tests: 971 passing.
- **The phone's read-only copy** (`docs/phone-offline.md`, all of it): built in phases 0-6 and switched on
  (`PHONE_OFFLINE_COPY=on`) for the user to try on their phone. Open: the user's check on the phone with the Mac asleep.
- **Session cookie Lax** (`web/remote.py`): the passcode was asked on every launch of the installed app; fixed and
  confirmed in the log. Passcode minimum 10 (a passcode already set keeps working).
- **gzip** on every response (`web/app.py`).
- **Scheduled sync at 06:30 / 18:30** under `caffeinate -i` (committed `75f3bdb`); the user set a daily wake at 06:28
  and no sleep on power (README, scheduling).
- No migrations (still 0067).

## Where we left off (2026-11-07)

Read this first, then the 2026-11-05 section below and the 2026-11-03 map under it. Committed and pushed through
`a2feb8b`. Tests: 958 passing. Since 2026-11-05 the work has been the **phone layout** (`docs/phone-plan.md`, its "Where
we are" section: what's built, phase 5 next, the open decisions, how to check a phone change). No migrations since
0067. The phone gate is live (the user's Android phone, installed app); the router forward and the Mac's reserved
address were fixed 2026-11-05 (README, phone access).

## Where we left off (2026-11-05)

The section before 2026-11-07's (still accurate apart from what's listed there).
Committed and pushed through `cd4c61f` (2026-11-05); Production has migrations through **0067** applied (checked
read-only 2026-11-05). Still on Production, 4 of 10 Items, investment transactions on. Tests: 958 passing.

Built after `cd4c61f` (2026-11-05): **fintrack as an app.** Chrome's Install page as app (manifest, icons, title-bar
color) and `fintrack autostart install|uninstall|status|restart`, a launchd job that runs `fintrack serve` at login and
after a crash; both jobs' logs are mode 600 (`Umask`, and `_install` creates the log first: launchd made it 644).
Installed on the Mac 2026-11-05 (serve job; sync job reinstalled, same times). pywebview was considered and dropped
(user's choice). `docs/ui.md` (As an installed app), README "As an app", `docs/decisions.md`. `fintrack.log` and its rotated
copies are now mode 600 too (`logging_setup.PrivateRotatingFileHandler`; older files in logs/ tightened at start).

Built 2026-11-05 and set up the same evening on the user's Android phone (works over cellular: client certificate, passcode, home-screen icon): **phone access with no app on the phone** (`gate.py`, `remote.py`,
`web/remote.py`, `fintrack remote setup | phone | remove | passcode | enable | disable | status`, launchd job
`com.fintrack.gate`, `REMOTE_ACCESS` / `REMOTE_HOST` / `REMOTE_PORT` / `REMOTE_INNER_PORT`; new dependency `segno`
for the download link's QR code). The user declined Tailscale (outside service) and then WireGuard (an app on the phone);
the WireGuard version was built and replaced before any commit. The gate: TLS 1.3 + a phone's client certificate, under
`sandbox-exec` (tested on macOS 27.0.1: it can't read the home folder, FINTRACK_HOME, write files, or connect anywhere
but the phone listener), then fintrack's phone listener on 127.0.0.1:8444 with a passcode (30-day cookie; 5 wrong tries
lock 15 min); Link / Item routes refused. End-to-end through the real sandbox: phone certificate -> passcode page
(https links), no certificate / a stranger's / TLS 1.2 refused before fintrack. Fixed on the way: an IP address as the
server certificate's common name fails OpenSSL's name-constraint check, so the common name is "Private server".
Learned during setup: the one-time download on the home Wi-Fi first failed (ERR_ADDRESS_UNREACHABLE: the Mac saw no
other device on the network) until something on the phone or router changed; Chrome's home-screen option was under
a renamed menu item. Still open: iPhone not tried.

**Phone layout (2026-11-05, `docs/phone-plan.md`):** every page reviewed at a true 390px phone viewport; the user chose the plan's options (7 icons at the bottom; grouped-by-date transaction rows; keep everything and shrink; benchmarks as one list). Built: phase 0 (`tools/phone_check.mjs`, the measuring script) and phase 1 (the phone shell, ≤640px: one-line top bar, sections as icons in a bottom bar, one-line tabs and ribbon controls; `docs/ui.md` Phones). Content now starts at 115–171px on every page (was 279–437). 2026-11-06, the user's changes: tabs fixed at the bottom above the icons, the ribbon controls in a panel the current tab opens (closes on scroll), `viewport-fit=cover` and a live `theme-color` for the Android bars (the installed app's gesture bar comes from the manifest's `theme_color`, fixed at install: the user chose always dark, `#121211`; reinstall the app on the phone to see it); then the tab names spread evenly with the mark inside the line, Transactions' filters in its controls panel and its two cards as a swipe row; the date ranges' From / To back in the panel; Account types' extra top space fixed. On phones `theme-color` is the bars' `--rail` (the page's colour overrides the manifest's once loaded; the gesture bar now follows the switch). The Overview's range moved onto its Net worth over time card (every width), so a phone shows no tab line there. Phase 1 is complete; phase 2 (tables that fit) built 2026-11-06: 58 of 60 page runs pass at 360/390/412px (docs/phone-plan.md); phase 3 (charts: fixed phone heights, ~4 axis labels, compact legend) built the same day; then the user's page requests: Net worth Compact on phones (2 a row), Investments' tiles and one-line holdings, Savings 1×3 tiles + 2×2 cards, Credit cards and Recurring swipe rows; then 10px phone spacing everywhere (edges and gaps; 60 of 60 page runs pass); Spending's lists by date with icons and its charts as a swipe row; no Show data on phones; content starts at 69px. Next: phase 3 (chart heights), then phase 2 (tables).

Built 2026-11-04 (details in the area docs):
- **Rewards as transactions** (`manual_transactions.py`, migrations 0061-0064; 0062 replaced 0061's redemptions table):
  Add a transaction on Transactions (cash, or a rewards cost + same-category credit), Rewards used on a credit, Also
  paid with rewards on a bank charge (`sync_parts`); Credit cards shows Redeemed and Cash rewards. `docs/transactions.md`.
- **Recurring items' actions** (migration 0060, `recurring_actions`): Then do this, applied ahead of every rule.
  `docs/planning.md`.
- **Tags**: category budgets inside the total, the budget in the card's table, view / edit modes; on Budgets a tag's
  category lines are added only when chosen (migration 0065). `docs/tags.md`, `docs/planning.md`.
- **Watchlist** (`watchlist.py`, migrations 0066-0067): the Investments card's Watchlist tab; company search over
  Alpaca's stored asset list (user's decision 2026-11-04, CLAUDE.md). `docs/investments.md`.
- **Sync**: network errors become PlaidError (one dropped connection no longer skips rules, transfers, recurring and the
  snapshot), retried once. **Cash flow**: today's point is the actual balance; a statement counts as paid once the exact
  payment has left. The unfinished location map was removed from Transactions and Tags.

Cleanup 2026-11-05 (no feature changes): `budgets.py` split into the `fintrack/budgets/` package (lines, amounts,
actuals, month, plan; `budgets.X` unchanged for callers) and `web/transactions.py` into the page plus
`transaction_filters.py`, `transaction_edits.py` and `transaction_groups.py` (same routes); `docs/tags.md` split from
`docs/transactions.md`, which is now grouped by page; `spending.merchant_breakdown` removed; unused imports, dead CSS (old rules table, tag budget box, redemptions form and
others), the unused `data-remember-open` behavior and `_budget_status.html` removed; Transfers and Credit cards share
`deps.back_with_range`; rule and search amounts and live holding values convert through `money.to_cents`. A real bank's name in code and doc examples became
Acme (the Plaid OAuth note now says "one large bank").

## Where we left off (2026-11-03)

Everything below is committed and pushed (`2ac013e` on `origin main`,
2026-11-03); Production has migrations through **0059** applied (`fintrack serve` backed up before each). Still on
Production, 4 of 10 Items, investment transactions on. Tests: 887 passing. Details live in the area docs; this is the map.

Built 2026-10-13 to 2026-11-03 (with the user, step by step; each checked with tests, read-only Production queries and
headless Chrome on the real pages, never saving to the real database):
- **Tags** (`tags.py`, `web/tags.py`, Planning section after Budgets; migrations 0048, 0050, 0052, 0057-0059): tags on
  transactions and investment activity; the Tags page's cards, newest first, outlined when on the calendar, in their own
  scrolling list that scrolls to the first such card or to `?focus=<id>`; budgets by total and by category, added on
  Budgets as lines that supersede categories (`budgets.actuals._actuals_with_tags`, any amount allocated month by month); colors
  (36 + yours); inactive tags (latest transaction >365 days) left out of pickers. See `docs/tags.md` and
  `docs/planning.md` (Tags as budget lines).
- **Tags calendar** (`_tag_calendar.html`, shared with Overview): 1 month / 3 months (one row; a column on phones) /
  1 year (2 rows of 6; not offered on phones); at most 40% of the window; weeks line up across a row (subgrid); a 6-row
  month drops its thinner end row unless it holds the range's first/last day or today (`month_rows`); February may stay
  4 rows; another month's grayed day is blank (no lines) where its own month shows it; holidays by rule
  (`calendar_labels.py`: US federal + shopping days, estimated observed days) and your day labels; today = blue background
  + outline; Today link; arrows step a month (a year in the 1-year view); clicking a bar brings its tag's card to the top.
  A timeline (days-free) view was built and removed the same day at the user's request.
- **Overview**: calendar card under Accounts (arrows swap only the card, `GET /overview/calendar`) with two countdowns
  (next two events / holidays / labels; click a title to choose; every choice returns to automatic after its date);
  Cash flow card without explanatory text; Spending by budget category axis from $0 with a label-sized right margin.
- **Rules redesign** (`rules.py`, migration 0054; `/rules`): conditions and actions, category keywords section edited
  inline, delete asks undo/keep, merge offers for similar rules. `category_rules` is dead (emptied by 0056).
- **Categories page**: per-group Add category card with Plaid's default categories, + panel with Plaid subcategories one
  by one (0055), Move to menu, indented subcategories in every category drop-down (`category_option`).
- **Transactions page**: All / Income / Spending / Transfers toggle, Summary card, charts as filters (pie, top
  descriptions, flows), Categories to review card (0053), aligned columns, redone edit panel, investment activity
  editable (0052).
- **Analysis**: Savings, Cash flow, Credit cards (reward rates and balances, 0039-0044); Settings: Transfers page (0043),
  Account types; authorized users (0051); `fintrack import-csv` for a card's earlier history (one bank's layout).
- **Budgets**: frameworks (Fixed / Flexible, Needs / Wants, Custom groups; 0045-0047).

How the user works (keep doing this): show progress before and after each step; name each design choice and the
alternative; minimal on-page text; verify in the real pages without changing real data or saved settings; scan the
staged diff for personal data before every commit, and push after each commit. Commit only when asked.

Open items and known limits:
1. A tag card's Spent includes transfers out; its Budgets lines count expense-kind only, so they can differ.
2. Spending by budget category: one large budget stretches the axis, so most bars are small (bars relative to each
   line's budget were suggested, not built).
3. On a 320px window the Tags page scrolls sideways ~16px (not investigated); light mode wasn't checked for the
   2026-11 calendar and Overview changes.
4. Overview Cash flow card: the "N items: in / out" line was kept (the user may want it gone).
5. app_settings `tags_calendar_style` may hold a leftover value from the removed timeline; nothing reads it.
6. Earlier items still stand: M16 (contributions vs growth), M17 (what's unusual), M18; a pre-history move between
   investment accounts can't be seen; large single pre-tax entries skew a month's shares.

## Where we left off (2026-10-12)

The app is back on **Production** (`PLAID_ENV=production`; 4 of 10 Items, scheduled sync 06:00/18:00) with
**investment transactions on** (`INVESTMENT_TRANSACTIONS=on`, user's decision 2026-10-11: Trial plan, no billing set up).
Migrations 0024–0030 were applied to Production on 2026-10-11/12 (a backup before each). The Sandbox demo world is kept
in the sandbox database. Milestones 1–15 are committed; the 2026-10-11/12 session below is committed with this file.

The 2026-10-11/12 session (built with the user step by step; checked with tests, Production read-only queries, and
headless-Chrome screenshots/measurements of the real pages, run from the job's temp folder, never in the repo):
- **Savings page rebuilt** and a twin **Balances** page (Accounts section): date range in the ribbon; Net savings,
  Retirement, Investment, Other assets (Balances) and Net cash cards (Total / Average + stacked bars, by day for This /
  Last month, colours by kind, hidden when empty, container-query layout); the Net savings rate table with Post-tax /
  Pre-tax / Spending / Retirement / Investment / (Other assets) / Net cash / Rate and % of income. Savings = saved from
  income (investments -> bank and money lent left out); Balances = every movement. See "Savings vs Balances" and the
  Savings notes under Conventions.
- **Transfers matching** (contributions.py): sweeps ignored, miscategorized bank deposits paired, moves between your own
  investment accounts, destination rules on the raw bank text (migration 0030, `fintrack descriptions --backfill` run).
- **Manual investment accounts**: History + Add (Contribution / Withdrawal / Fee / Balance × Quantity / Value; migrations
  0025–0027); recorded retirement contributions are the pre-tax income. The pre-tax calculator of 0024 was dropped.
- **Purpose accounts** ("Money lent", migration 0028): linked transfers by rule / panel / group edit, adjustments.
- **Investment activity** shown (Transactions kind + connected account pages with Holdings and Activity).
- **Live prices reach net worth** (migration 0029, reversing the earlier rule).
- Card spacing (16px both ways), other-transfer accounting in Net cash, investment transactions in Production.

Checked facts this session: Plaid Transactions never covers investment accounts; the user's brokerage sent ~3 months of
investment history; **one card issuer provides only 90 days of transaction history** (Plaid docs,
transactions/troubleshooting, names the issuer) and reconnecting won't extend it, so that card's spending before the
connection's 90-day window is missing (only the card payments from checking show it); a non-OAuth 401(k) provider fails in
Link with Plaid's INTERNAL_SERVER_ERROR after MFA (Plaid/institution side; a support ticket is the user's option); it
stays a manual account. Institution specifics are in Claude's memory, not here.

Next steps / open items:
1. Possible: a CSV import for older card history from the 90-day card issuer (built later as `fintrack import-csv`, 2026-10-29).
2. A one-off move between the user's own investment accounts before the brokerage's history (a brokerage -> traditional IRA
   -> Roth contribution) can't be seen; offered a way to record it; not built.
3. Large single pre-tax entries (e.g. one month's big 401(k) entry) make that month's shares look odd; the user can
   split them into monthly contributions.
4. Earlier open items still stand: M16 (contributions vs growth), M17 (what's unusual), Alpaca prices during market
   hours, the narrow-window rail height.
