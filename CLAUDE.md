# CLAUDE.md: fintrack (personal finance tracker)

Local, single-user personal finance app on macOS (Apple Silicon). It uses Plaid
for bank, credit card, brokerage, retirement, and loan data, with FastAPI,
SQLite, Jinja2, and HTMX. The milestone plan and
session history are in `docs/history.md`.

## Working style: show progress as you go (user requirement)

The user wants visibility into work **while it happens**, not just a summary
at the end of a milestone. For every step:

- **Before acting:** say in a sentence or two what you're about to do and
  why. Example: "Adding a migration for `balance_snapshots` because net worth
  history needs one row per account per day."
- **After each meaningful step** (a file written, a command run, a test run, a
  doc checked): say what happened and what it means. Include surprises,
  failures, and how you're handling them.
- **When you make a design choice**, name it, give the reason, and mention the
  main alternative you passed on. Don't silently pick.
- **When you check something external** (Plaid docs, package versions), say what
  you checked and what you found, with the source.
- Keep each update short and in plain language. The end-of-milestone summary
  still happens, but it shouldn't be the first time the user hears about something.

## Hard constraints (never violate)

1. **Data stays outside the repo.** Code lives here. The venv and `fintrack`
   executable, `.env`, database, certificates, backups, logs, and anything
   personally identifiable live in `FINTRACK_HOME` (default `~/.fintrack/`).
   `config.check_home_outside_repo()` enforces this. Never write real data,
   real account names, or real transactions into the repo, including test
   fixtures. Test data must be obviously fake.
2. **Local only.** The server binds to `config.HOST = "127.0.0.1"`. That is a
   constant, not a setting. The only outbound calls are to Plaid and, **by the user's
   decision on 2026-10-07** (replacing Finnhub, approved 2026-10-04), Alpaca for stock prices:
   live quotes and daily history (`quotes.py` only, server-side, only when `ALPACA_API_KEY_ID`
   and `ALPACA_API_SECRET_KEY` are set; it sends ticker symbols and the keys, in headers, nothing else).
   **By the user's decision on 2026-11-04**, `quotes.py` also fetches Alpaca's list of active US stocks and ETFs
   (`paper-api.alpaca.markets/v2/assets`, about weekly, the keys only) for the watchlist's company search, which then runs
   on the stored copy: nothing is sent as you type.
   **By the user's decision on 2026-10-08**, fintrack also downloads merchant logo images from
   Plaid's logo hosts (`plaid-merchant-logos.plaid.com`, `plaid-counterparty-logos.plaid.com`;
   `logos.py` only, server-side, plain file downloads with no key or account data, each logo once,
   cached in `FINTRACK_HOME/logos/`). The browser never loads anything from them.
   **By the user's decision on 2026-11-05**, phones may reach fintrack from outside through the **gate** (`gate.py`,
   `remote.py`; no app on the phone, no outside service: the user declined Tailscale and WireGuard, and Android must
   work). With `REMOTE_ACCESS=on`, the launchd job `com.fintrack.gate` runs gate.py under `sandbox-exec` on
   `REMOTE_PORT` (all interfaces; the router forwards it): TLS 1.3 only, a phone's client certificate required in the
   handshake, then a hand-off to fintrack's second listener on `127.0.0.1:REMOTE_INNER_PORT`, behind a passcode, never
   for Link or Item routes (`web/remote.py`). fintrack's own listeners stay on loopback. The gate is the only
   non-loopback listener: keep it standard-library only and sandboxed, and never route phone access through a third
   party.
   Don't add any other outbound service without asking. Frontend
   libraries are vendored into `fintrack/web/static/vendor/`, never loaded from
   a CDN.
3. **Sandbox first.** Build and test everything against Plaid Sandbox.
   Production is used only at milestone 11, to connect real accounts once each.
4. **Protect the Production Item budget.** The Trial plan allows 10 Production
   Items, and **`/item/remove` does not refund a slot**.
   - No code path may create a Production Item without explicit user
     confirmation on a screen that shows used/remaining (`item_budget.py`).
   - Syncs, scheduled jobs, reconnects, and retries must never create Items.
     Broken connections are repaired only with **Link update mode**.
   - Before a Production Link session, warn if the institution is already connected.
   - Production rows in `items` are a permanent ledger. A DB trigger blocks
     deleting them; mark them `status='removed'` and null out the token instead.
   - Never run anything against Production during development or tests.
5. **Free to run.** Prefer free endpoints. Routine syncs read balances from
   `/accounts/get` and `/transactions/sync`. `/accounts/balance/get` (Balance
   product) is only for an explicit user-triggered "live balance" action. **Investment transactions
   (`/investments/transactions/get`) are NOT free to assume**: they are their own Plaid subscription per Item, so they are
   fetched in the Sandbox only, and in Production only with `INVESTMENT_TRANSACTIONS=on` in `.env`
   (`Settings.sync_investment_transactions`), after the price has been checked in the Plaid dashboard.

## Security policies

- Plaid access tokens are encrypted with Fernet before storage (`crypto.py`).
  The key is held in the macOS Keychain via `keyring`, scoped per data folder.
  A missing key is an error. Never auto-generate a replacement, because old
  tokens would become unreadable.
- Never log or display access tokens, public tokens, secrets, or full account
  numbers. `logging_setup.RedactingFilter` is a safety net, not a license to log them.
- Store and display only account masks (last 4). The schema rejects longer masks.
- Bank credentials are entered only into Plaid Link and never reach this app.
- Web app defenses (keep them when adding routes): `TrustedHostMiddleware`
  (DNS rebinding), an Origin check on all non-GET requests (CSRF), strict CSP
  (`default-src 'self'`; milestone 2 adds only Plaid Link's origins), and
  `Cache-Control: no-store`. Responses are gzip-compressed (2026-11-11, for phones and their read-only copy): keep
  secret tokens out of page bodies (BREACH; reasons in `web/app.py`).
- State-changing actions are POST, never GET.
- Framing: `frame-ancestors 'none'` on the Mac's pages. A phone's pages (the phone listener only) have
  `frame-ancestors 'self'` and `frame-src 'self'` added: a swipe shows the page beside in a frame of the app's own page
  (ui.js; user's request 2026-11-11). Never allow another origin to frame the app.
- Phone access (`web/remote.py`): a phone's request is one on the phone listener, decided by `scope["server"]` (the
  socket), never a header; each listener accepts only its own Origin. No certificate authority's key is ever written
  to disk (`remote.make_server_certificate`, `add_phone`), and a phone's private key exists only in the one-time `.p12`
  download. The gate's sandbox profile (`remote.gate_profile`) denies by default; widen it only with the user's say.
  Don't run `fintrack remote phone` for the user: its output holds the `.p12` password.
- The phone's read-only copy (`PHONE_OFFLINE_COPY`, `web/offline.py`, `static/offline-*.js`; user's decisions
  2026-11-11): phones only, encrypted in the phone's browser storage (AES-GCM, a non-extractable key per passcode
  session); the browser's HTTP cache stays off (`no-store`); nothing financial is stored unencrypted (static files
  only); a new login, the session's end, a new passcode or the setting off wipes it. Pages shown from it are
  read-only (`hx-disable`). Keep new page data reachable by GET with no HTMX-only content, or it won't be in the copy.
- Files in `FINTRACK_HOME` are mode 600 and folders are mode 700.

## Docs (read the one for the area you're changing)

This file holds what applies to every change. Each page and feature has its own notes in `docs/`, moved there verbatim
on 2026-10-16. Read the matching file before changing an area, and update it in the same change.

| File | Covers |
|---|---|
| `docs/transactions.md` | Transactions page (search, date range, group edit, row edits, transactions you add), bank removals, authorized users, CSV import, categories and subcategories, rules, merchant logos and category icons, Spending page, Credit cards page, Transfers page. |
| `docs/tags.md` | The Tags page: tags, their cards and budgets, the calendar (also on the Overview), colors, calendar labels. |
| `docs/accounts.md` | Net worth and Accounts pages (balances.py), each account's page, account types, manual accounts, money lent (purpose accounts), manual positions, live balances, account names. |
| `docs/savings.md` | The Savings page: the savings model (savings.py), its cards and the Net savings rate table. |
| `docs/investments.md` | Security prices, performance cards and chart, Portfolio, transfers in and out of investment accounts, destination rules, investment transactions and activity, trading-day ranges. |
| `docs/planning.md` | Budgets (sections, 1-year / 10-year views, formulas, rollover), recurring detection, the cash-flow forecast and card statements, the projection. |
| `docs/ui.md` | The rail and tabs, page widths and grids, chart kinds, palette and axes. |
| `docs/phone-plan.md` | Phone layout: the review, the phased plan and **"Where we are"** (phases 0-3 built, 5-6 next, open decisions, how to check a phone change). The rules as built are in `docs/ui.md` Phones. |
| `docs/phone-offline.md` | The phone's read-only copy for when the Mac is asleep (built 2026-11-11): how it saves, locks, reads and wipes; what was checked. |
| `docs/import-plan.md` | Importing bank and broker files (CSV, Excel, OFX, QIF) of any layout into any account, connected or manual, including investments: the plan, decisions (2026-11-13) and **"Where we are"** (all five phases built). As built: `docs/transactions.md` "Importing bank files". |
| `docs/parity-plan.md` | Plan (2026-11-14, done): manual and connected accounts alike: the rule, the audit, one investment activity ledger (`ledger.py`), holdings from it, the conversion of manual positions, the parity test. |
| `docs/demo.md` | `fintrack demo`: the made-up world in the Sandbox database. |
| `docs/plaid.md` | Verified Plaid and Alpaca facts (endpoints, billing, Trial plan, Production observations). Re-check before relying on one. |
| `docs/decisions.md` | Decisions and the alternatives rejected, with reasons. |
| `docs/history.md` | Milestone plan and status, the last session's notes, open items. |

## Architecture

```
finance_tracker/                 (code only; in git)
  bootstrap.sh                   one-time setup: venv in ~/.fintrack, editable install, `fintrack init`
  pyproject.toml                 deps; `fintrack` console script -> fintrack.cli:main
  .env.example                   template; real file is ~/.fintrack/.env
  fintrack/
    cli.py                       subcommands (full list: README "Commands"): init, certs, serve, migrate, backup, items,
                                 sync, schedule, autostart, remote, preflight, demo, logos, descriptions, locations, import
    sync.py                      /transactions/sync paging, atomic apply, holdings, investment transactions (opt-in),
                                 file lock, sync_all (+ snapshot)
    networth.py                  signs, current net worth + breakdown, daily snapshots, history, month-ago change
    balances.py                  change in value per month/day by kind of account, from balance_snapshots
    categories.py                plaid_key mapping, categories + kinds, recategorize (one or group edit)
    rules.py                     rules: conditions on description / amount / date / category / account, and actions; recomputed
                                 after a sync and any rule change; one-time actions once per rule and transaction
    transfers.py                 own-account transfer pair matching (recomputed from scratch; your pairs from
                                 transfer_matches first), "Not a transfer", match / unmatch, the Transfers page's lists
    removals.py                  posted transactions the bank removed: kept + flagged, return matching, your review
                                 (use the returned one / not the same / keep / delete)
    spending.py                  month helpers, monthly report, date ranges, card_spending (per credit card)
    savings.py                   savings series (income, spending, retirement / investment savings, net cash, rate)
    manual.py                    manual accounts + dated valuations; snapshot history rebuild; manual bank accounts, cards
                                 and loans hold transactions and their balance follows them (value_on, 2026-11-13)
    purposes.py                  purpose accounts ("Money lent"): linked transfers + adjustments -> value history;
                                 left out of the Savings page
    manual_positions.py          positions in manual investment accounts (ticker x quantity, or $ events -> shares),
                                 ticker_prices, revalue, holdings mirror, retirement contributions for Savings
    budgets/                     the budget model (explained in __init__.py, which re-exports budgets.X for callers):
      lines.py                   sections, frameworks (fixed / flexible, needs / wants, custom groups), lines, tags as lines
      amounts.py                 effective-from amounts, set_budget (+ scoped)
      actuals.py                 actuals per line and month, tags superseding categories, rollover carry
      month.py                   the month view (status, suggestions, upcoming recurring)
      plan.py                    savings and the 1-year / 10-year views
    recurring.py                 schedules, merchant keys, local detection, refresh, review
    forecast.py                  cash flow (per depository account, 90 days); low-balance threshold
    loans.py                     liability terms (Plaid /liabilities/get + your entries; yours win)
    projection.py                assumptions, starting balances, scenarios, monthly simulation
    preflight.py                 Production readiness checks (never creates an Item)
    gate.py                      the phone gate (standard library only, run sandboxed by launchd): TLS 1.3 + a phone's
                                 client certificate, then a hand-off to 127.0.0.1:REMOTE_INNER_PORT
    remote.py                    phone access: one-off certificates (server; one per phone, .p12), the gate's sandbox profile
                                 and command, one-time download on the home Wi-Fi, passcode + 30-day sessions, throttle,
                                 the phone listener thread (ForceHTTPS)
    schedule.py                  launchd jobs: build/install/status of the scheduled sync (com.fintrack.sync), the
                                 server at login for Chrome's installed app (com.fintrack.serve, restart) and the phone
                                 gate (com.fintrack.gate)
    account_names.py             readable account names: short_name() rules, rename, refresh_auto_names
    account_types.py             your account type table: types + subtypes, a type's kind, an account's choice,
                                 the Automatic match
    logos.py                     merchant logos (allowlisted Plaid hosts, cached in FINTRACK_HOME/logos), institution
                                 logos (/institutions/get_by_id once per institution), transfer logos (the other side)
    category_icons.py            icon library (keywords, groups), each category's icon (yours > name > built-in >
                                 parent > kind)
    investment_activity.py       display of Plaid investment transactions as rows of the Transactions list (categories
                                 by name; counted on that page only); your description / category edits (migration 0052)
    contributions.py             money moved into/out of investment & retirement accounts by month, destination rules;
                                 bank and investment sides paired so each counts once
    prices.py                    security_prices history + Overview movers
    watchlist.py                 tickers you watch (Investments card's Watchlist tab): add (Alpaca must price it, history
                                 fetched at once into ticker_prices) / remove
    performance.py               performance cards: your stocks vs SPY/IWM/URTH; records benchmark_prices
    live_values.py               live prices in balances (reported balance + market move of quotable holdings)
    quotes.py                    QuoteGateway: the ONLY module that calls Alpaca (5-min cache, 6 s page limit)
    price_history.py             ~400 days of Alpaca daily closes -> security/benchmark/ticker prices, at most every 6 h
                                 (`refresh_if_due`); official closes replace differing Plaid prices
    cards.py                     card statements, paying account per card, still-owed math, forecast events
    authorized_users.py          authorized users' cards: account_owner = another card's last 4 -> "(AU)", the account
                                 checklist's cards per account
    imports/                     bank files of any layout into an account (docs/import-plan.md; phases 1-5: CSV, Excel, OFX,
                                 QIF; connected and manual accounts, investments; `fintrack import`, the Import page):
                                 readers.py (file -> cells; xlsx.py, money_files.py: Excel, OFX / QIF), layout.py
                                 (header, columns, kind, dates, amounts, sign), engine.py (profiles, ids csv-<hash>,
                                 overlap + likely duplicates, categories, batches, undo), investments.py (activity and
                                 holdings: actions, effects, position lines, investment_transactions)
    ledger.py                    investment activity you enter or import, for every investment account (investment_transactions
                                 rows like Plaid's; docs/parity-plan.md); a manual account's holdings rebuilt from it
    tags.py                      your tags on transactions (emoji welcome): add / remove / rename / delete, the Tags page's
                                 sums by tag and category; never changes a category or kind; budgets (total, by category), which budgets.py
                                 turns into lines that supersede categories on the Budgets page only
    calendar_labels.py           Tags calendar's days: US federal and shopping holidays by rule, estimated observed days,
                                 your labels
    rewards.py                   credit card reward rates you enter (points / miles / cash back; by category, merchant
                                 or both), card fees at 0x, one-off bonuses on transactions -> estimated rewards;
                                 rewards balances as of dates, each transaction's rewards, current balance (a card's page);
                                 rewards used (rebates, statement credits; earn nothing) and bonus points: value per
                                 point, rewards' share of cost (Credit cards)
    manual_transactions.py       transactions you add (ids manual-…): one, or paid with rewards a cost + same-category
                                 rebate pair (follow_category, delete both); a bank charge partly paid with rewards
                                 (set_part, rewards_part_of; sync_parts keeps the pair on the charge's category and tags)
    visuals.py                   chart data (dicts in dollars) built on the owning modules
    config.py                    Settings from FINTRACK_HOME/.env; HOST constant; repo guard
    crypto.py                    Keychain key + TokenCipher
    db.py                        sqlite3 connect (WAL, FKs on) + migration runner
    migrations/NNNN_*.sql        append-only schema changes
    money.py                     to_cents (Decimal, half-up) / format_cents
    plaid_client.py              PlaidGateway: the ONLY module that calls Plaid; PlaidError; LINK_KINDS
    demo.py                      SANDBOX-ONLY made-up demo world; DemoAwareGateway serves "demo-access-*" tokens
    items.py                     Item lifecycle + consent gate (create/update/refresh/remove)
    item_budget.py               Production Item used/remaining
    backup.py                    SQLite online-backup to dated file
    logging_setup.py             redacting log handlers
    web/app.py                   app factory, security middleware, CSP; injectable gateway/cipher factories
    web/deps.py                  request dependencies; NAV (sections/tabs) + page context; template filters;
                                 redirect (303 after a POST)
    web/setup.py                 /setup (Settings' first tab): connections, manual accounts, config checks; /connect,
                                 /oauth-return, Link JSON endpoints, item actions
    web/account_types.py         /account-types (Settings tab)
    web/transactions.py          POST /sync, GET /transactions (paging, charts' choices), logo files, + Add a transaction
    web/transaction_filters.py   the list's search, filters, range and sort as SQL; summary and chart data (also accounts)
    web/transaction_edits.py     one row's HTMX edits (edit panel, category, transfer, tags, recurring, bonus, removal
                                 review, delete); row helpers shared by the other transaction modules
    web/transaction_groups.py    group edit (category, money lent, tag), Categories to review
    web/networth.py              /networth (Accounts section): hero, tiles, trend, cards per kind, change in value
    web/accounts.py              /accounts (Accounts section) + /accounts/{id}, THE page per account (activity, holdings,
                                 History, Manage, destination rules); shared contexts and macros (_balances.html)
    web/manual.py                /manual/new + manual account forms, all returning to /accounts/{id}
    web/investments.py           /investments: performance cards, Performance % chart, holdings table
    web/portfolio.py             /portfolio: tiles, value by account type, transfers in/out, holdings by account
    web/spending.py              /spending, /categories, /rules + /rules/new, /rules/{id}/edit, preview (Settings tabs) + forms
    web/savings.py               /savings (Analysis section): tiles, savings cards, monthly table
    web/budgets.py               /budgets: Month view and 1-year / 10-year views; HTMX POSTs return fragments
    web/recurring.py             /recurring: charts by day, Next 30 days, review, confirm/dismiss/edit/add
    web/forecast.py              /cash-flow (Analysis section): projected balance + Coming up; threshold form
    web/credit_cards.py          /credit-cards (Analysis section): each card's spending by category over a date range
    web/tags.py                  /tags (Planning section, after Budgets): a card per tag with its totals and categories
    web/transfers.py             /transfers (Settings tab): matched transfers by route and pair, remove a match, match by hand
    web/imports.py               /import (Settings tab): a bank's file into an account (imports/): the form re-posts itself
                                 (file included, never kept) to /import/preview on every change; /import/apply, undo
    web/projection.py            /projection (tiles, chart, compare, assumptions, loan terms), scenario CRUD
    web/dashboard.py             / home (Overview); its calendar card's month swaps at /overview/calendar
    web/remote.py                checks on phones' requests (no Link/Items, passcode session) + /remote-login
    web/offline.py               the phone's read-only copy (PHONE_OFFLINE_COPY): its worker (/offline-sw.js), the pages a
                                 phone saves (/offline/pages); static/offline-copy.js registers or removes it
    web/templates/               Jinja2 pages and _partials
    web/static/                  app.css; ui.js (shared behaviors + chart axis helpers window.fintrack.*); charts.js
                                 (shared chart kinds, with _charts.html macros); keep-scroll.js (place kept after a save); link.js, spending.js, projection.js;
                                 vendor/ (htmx, Chart.js 4.5.1, treemap plugin, sankey plugin 0.14.0); manifest.webmanifest +
                                 icons/ (Chrome's installed app)
  tests/                         pytest; temp FINTRACK_HOME + in-memory keyring; tests/fakes.py FakeGateway
  tools/phone_check.mjs          Node, no deps: every page in headless Chrome as a phone; what doesn't fit (docs/phone-plan.md)
  docs/                          notes per area of the app (index under Docs above)

~/.fintrack/                     (FINTRACK_HOME; never in git)
  venv/  .env  fintrack-sandbox.db  fintrack-production.db  certs/  backups/  logs/  logos/
```

### Conventions (all code)

- **Accounts behave alike whatever their source** (user's rule 2026-11-14, `docs/parity-plan.md`): features work on the
  kind of account (bank, card, loan, investment), never on connected vs manual. Only what the source provides may
  differ (syncing, reconnecting, Plaid's statements, the live-balance button, Plaid's merchant names and logos), and a
  manual account's balance is worked out where Plaid reports one. Don't add an `is_manual` branch for behaviour.

- **Money is integer cents** in the DB and in Python. Convert Plaid floats once
  at the boundary with `money.to_cents()`, which uses Decimal and rounds half-up.
  Don't use `round(x * 100)`: float artifacts mis-round values like 1.005.
- **One database per Plaid environment** (`fintrack-{env}.db`). Never mix
  Sandbox and Production rows in one file.
- **All Plaid calls go through `PlaidGateway`.** Routes and services receive
  it by injection, so tests use `tests/fakes.FakeGateway` and never hit the network.
- **Sync is atomic per Item:** fetch all pages, then write changes + cursor in
  one DB transaction. Upserts run before deletes (pending -> posted category
  carry-over). Upserts never overwrite `user_category`.
- **Plaid's "removed" deletes only pending transactions** (user's rule 2026-10-22). A removed *posted*
  transaction is kept with `removed_at` set (your edits and links stay), matched to its likely return
  (`removals.match_returns`, after every sync) and listed for review on its account's page. Until you decide, the
  removed one keeps counting and its return is `excluded`. Never delete posted transactions on Plaid's say-so.
- **CLI commands that open the DB go through `cli._migrate`** (backup before
  migrating). Never call `db.migrate` directly from a command.
- **Our JS lives in static files**, and server data reaches it via `data-`
  attributes. No inline scripts, so the CSP never needs `'unsafe-inline'`.
- **Transaction sign follows Plaid:** positive `amount_cents` = money out,
  negative = money in.
- **Net worth sign comes from `accounts.classification`** (`asset` /
  `liability`), not from the balance sign. Plaid reports credit and loan balances
  as positive amounts owed.
- **Net worth** = Σ asset `current` − Σ liability `current`, balances used exactly as
  Plaid reports them (overdraft = negative asset; card credit = negative liability).
  USD/NULL currency only; others are listed as excluded. Breakdown is keyed by
  (type, classification). Holdings are informational and never added to net worth.
- **Snapshots:** one per account per local day, written by `sync_all` after all
  Items. Same-day rerun replaces the day. Snapshots store classification and type.
  History carries whole days forward (never single accounts) and flags them `carried`.
- **Charts** (dataviz skill): colors are CSS variables (`--series-1` etc.) with
  separate validated light/dark steps; data is passed as `<script type="application/json">`;
  no inline styles (CSP), so widths are set from JS. Every chart gets a "Show data" table.
- **`transactions_effective` view is the single source of "what counts".**
  category = `user_category` > rule (`rule_category`, stamped by `rules.py`: a confirmed recurring item's actions, then rules) >
  `plaid_category_key`; description = yours > `rule_description` > merchant > bank text. kind = `excluded` for loan-account entries, `transfer` when
  auto-matched (`is_transfer`), else the category's kind (`expense`/`income`/`transfer`).
  Reports and lists read this view; never re-derive category or kind elsewhere.
- **Spending** = Σ amount of kind `expense` (refunds reduce it; pending included);
  **income** = −Σ amount of kind `income`. Hidden (removed-connection) accounts are
  excluded from spending, because reconnecting re-sends history under new IDs.
- **Rules** (`rules.py`, migration 0054): all of a rule's conditions must match; conditions test what the bank and Plaid
  sent, never what rules decided. They run in `position` order; the first to set a category or description wins, tags add
  up. `apply_all` recomputes everything after a sync and whenever a rule changes, so edits and deletes follow. Recurring,
  purpose, not-a-transfer and accept are one-time (`rule_applied`); deleting a rule asks whether to undo or keep results. `category_rules` (empty since migration 0056) and
  `transactions.rule_id` are dead: never use them. **Recurring items' actions** (migration 0060, `recurring_actions`): a
  confirmed item's "Then do this" applies to its transactions (`recurring.transaction_ids`) in the same `apply_all`, ahead of
  every rule (`rule_category_item` marks the category; source `recurring`); one-time ones in `recurring_applied`.
- **Transfer matching** (`transfers.detect_transfers`) is recomputed from scratch after
  every sync and every category/rule/kind change. Pairs must be exact opposite amounts,
  in different visible non-loan accounts, posted, within 3 days, not income, and not
  `transfer_ignore`.
- **Status colors** (`--status-*`) are only for state, always with an icon + label. Text
  steps `--status-*-text` measured ≥ 5.3:1 on both themes. Budget progress bars under 100% use `--status-good` (green, user's choice 2026-10-06; over = red).
- **DB connections use `check_same_thread=False`**: async routes (e.g. bulk form via
  `await request.form()`) use a connection opened in FastAPI's worker thread. Safe because
  each request has its own unshared connection.
- **Times:** DB timestamps are UTC (`CURRENT_TIMESTAMP`); display with the `localtime` filter.
- **Stale-page protection:** `app_revision.n` rises via triggers (migration 0006) on
  any write to transactions, accounts, items, categories, category_rules,
  balance_snapshots, holdings. Every page renders `<body data-revision>` (template
  context processor in `web/deps.py`). `ui.js` checks `GET /revision` on back/forward-cache
  restore (`pageshow.persisted`) and on tab refocus, and reloads if it changed. After
  HTMX edits it adopts the new revision instead. **New tables whose data pages display
  need the same three triggers.**
- **Display formats:** headline figures (hero/tiles) use `money_whole` (no cents); tables and
  transactions keep `money`. Dates `date_label` ("Oct 3"; other years "Oct 3, 2025"); months
  `month_label` ("October 2026", `'short'` "Oct 2026"). Form values and URLs stay ISO.
- **No inline JS or styles anywhere** (a test scans templates). Behaviors go in
  `static/ui.js` (e.g. `data-autosubmit`, meter widths).
- **Migrations are append-only.** To change the schema, add a new
  `NNNN_description.sql`. Never edit an applied one. `fintrack migrate` and
  `serve` back up the DB before applying.
- Plain `sqlite3` and SQL, with no ORM. Keep queries readable and commented.
- Readable, commented code. The user wants to understand and maintain it.

## Status

Milestones 1-15 are done and 16 is partly done; 17-18 are planned. The app runs on **Production** (4 of 10 Items) with
investment transactions on. See `docs/history.md` for the plan, the latest session notes and the open items, and stop at
the end of every milestone for the user to test. **Start a new session with history.md's "Where we left off (2026-11-14)"**
(then 2026-11-11's, 2026-11-07's, 2026-11-05's and the 2026-11-03 map): what was built since milestone 16, where it's documented, migrations through
0073 (0068-0070: imports, docs/import-plan.md; 0071-0073: docs/parity-plan.md), the open items; for the phone work, `docs/phone-plan.md`'s "Where we are".

## Procedures

- **User-facing details** (setup steps, every command, what each page shows, switching to Production) live in
  `README.md`. Don't repeat them here; read the README when you need them.
- **Run tests:** `~/.fintrack/venv/bin/pytest` from this folder. Tests must never touch
  `~/.fintrack` or the real Keychain (fixtures in `tests/conftest.py` handle this).
- **Run the app:** since 2026-11-05 the server runs all the time as the launchd job `com.fintrack.serve` (the user uses
  Chrome's installed app). **Don't start another `fintrack serve`:** port 8443 is taken. After changing Python code,
  `.env` or adding a migration, run `~/.fintrack/venv/bin/fintrack autostart restart` (templates, CSS and JS need no
  restart); `fintrack autostart status` shows whether it's running. Without the job, `fintrack serve` opens
  https://localhost:8443. For a smoke test from the shell, use
  `curl --cacert "$(/opt/homebrew/bin/mkcert -CAROOT)/rootCA.pem" https://localhost:8443/`.
- **New dependency:** add it to `pyproject.toml` with a lower bound, then
  re-run `./bootstrap.sh` or `pip install -e .[dev]` in the venv. Tell the user
  what it's for.
- **New frontend library:** download a pinned version into
  `web/static/vendor/` with the version in the filename. No CDNs.
- **Git:** `finance_tracker/` is its **own repo** (branch `main`), nested inside
  the `project-vibes` folder. project-vibes ignores it through its local-only
  `.git/info/exclude` entry `/finance_tracker/`. Never `git add` this folder
  from the parent repo, and never commit project-vibes changes from here.
  Don't commit unless the user asks. Before committing, check that
  `git status --short` lists no data or secrets (`.db`, `.env`, `.pem`, logs).
  Remote: `origin` = https://github.com/grantsawyer/TrackPlanGrow (public). **Push to
  `origin main` after each commit** (user's rule 2026-10-14), so the personal-data scan of the staged diff must pass
  before every commit: a pushed leak is hard to undo. Don't push anywhere else or change the repo's visibility
  without asking. The `gh` on PATH is an Intel build that can't run; push with plain `git`.
- **README:** update it whenever a change adds commands, setup steps, scheduling instructions, or pages.
- **docs/:** when a page or feature changes, update its file in `docs/` (see the Docs index above).
- **At the end of each milestone:** update the status table above, list what
  the user should test manually, and wait.

### Tips and required tests

Tips: the `!` shell prefix doesn't load `~/.zshrc`, so use `~/.fintrack/venv/bin/fintrack`.
If repo files suddenly fail with `EPERM: operation not permitted` (Read tool and shell), a Claude
Code update installed a new version binary (listed by version number, e.g. "2.1.289") without
macOS Documents access: enable it in Privacy & Security → Files and Folders (seen 2026-10-04).
Cancelling Link before finishing a bank login uses no Item.

Required tests (add with the milestone they belong to): sync
added/modified/removed (pending deleted, posted flagged) and cursor persistence, net-worth signs, transfer
exclusion, budget rollover math, recurring detection, cash flow projection.
Use mocked Plaid responses or fixed fake data, never live Production.

## Environment notes

- Apple Silicon Mac, macOS 27, with **no Rosetta**. The old Intel Homebrew in
  `/usr/local` and the pyenv 3.9.7 install cannot run (they fail with "Bad CPU
  type"). Use `/opt/homebrew/bin/...` explicitly when PATH may not include it
  (e.g. launchd jobs, non-login shells).
- Python: `/opt/homebrew/bin/python3.12`. mkcert: `/opt/homebrew/bin/mkcert`.
