# Track Plan Grow (fintrack)

A personal finance tracker that runs entirely on your own Mac. Its pages and installed app are named Track Plan Grow;
the command, the Python package and the data folder (`~/.fintrack`) keep the name fintrack. It connects to your bank, card, brokerage, retirement,
and loan accounts through Plaid, and keeps everything in one local SQLite file.

- Single user, never deployed. The server listens on `127.0.0.1` only.
- Outbound calls go only to Plaid, to Alpaca for stock prices (optional, ticker symbols only), and to Plaid's logo hosts
  for merchant logos (plain image downloads). All page assets (HTMX, Chart.js) are bundled locally.
- Bank credentials are typed into Plaid Link only. This app never sees them.

Detailed notes on how each page works, and why, are in [`docs/`](docs/); the milestone history is in
[`docs/history.md`](docs/history.md).

## Where things live

| What | Where |
|---|---|
| Source code | this folder (in git) |
| Python environment and the `fintrack` command | `~/.fintrack/venv/` |
| Plaid and Alpaca keys, settings | `~/.fintrack/.env` |
| Databases (one per Plaid environment) | `~/.fintrack/fintrack-sandbox.db`, `~/.fintrack/fintrack-production.db` |
| HTTPS certificate | `~/.fintrack/certs/` |
| Backups | `~/.fintrack/backups/` |
| Logs (tokens and secrets are redacted) | `~/.fintrack/logs/` |
| Merchant and institution logos | `~/.fintrack/logos/` |
| Token encryption key | macOS Keychain, item "fintrack" |

Nothing personal is ever stored in the repo. The app refuses to start if `FINTRACK_HOME` points inside the repo. To use a
different data folder, set `FINTRACK_HOME=/some/path` before every `fintrack` command.

## Setup

### 1. Prerequisites (Apple Silicon Homebrew)

```sh
brew install python@3.12 mkcert
```

### 2. Install

```sh
./bootstrap.sh
```

This creates `~/.fintrack/`, a Python environment inside it, the `.env` file, the Keychain encryption key, and the
database. Then add the command to your PATH in `~/.zshrc`:

```sh
export PATH="$HOME/.fintrack/venv/bin:$PATH"
```

### 3. Get Plaid keys

1. Sign up at <https://dashboard.plaid.com>. New US developer accounts get the free **Trial plan**.
2. Open **Developers → Keys**. Copy the `client_id` and the **Sandbox** secret into `~/.fintrack/.env`:
   ```
   PLAID_CLIENT_ID=...
   PLAID_SECRET_SANDBOX=...
   PLAID_ENV=sandbox
   ```
3. Leave `PLAID_SECRET_PRODUCTION` empty until you switch to Production (below).

### 4. Local HTTPS with mkcert

Many big banks log in through OAuth, and in Production Plaid requires an **HTTPS** redirect URI. (On the Trial plan no
other OAuth registration is needed.) mkcert creates a local certificate authority that your Mac trusts, and a
certificate for `localhost` signed by it.

1. Trust mkcert's local CA (once per Mac; asks for your password):
   ```sh
   mkcert -install
   ```
   Firefox users: `brew install nss` first, then re-run `mkcert -install`.
2. Create the certificate:
   ```sh
   fintrack certs
   ```
   This writes `localhost.pem` and `localhost-key.pem` to `~/.fintrack/certs/` (the key is readable by your user account
   only). The certificate is good for about two years; re-run `fintrack certs` to renew it.
3. Register the redirect URI with Plaid. In the dashboard, open the developer settings page that lists **Allowed redirect
   URIs** and add exactly:
   ```
   https://localhost:8443/oauth-return
   ```
   It must match character for character: `https`, `localhost` (not `127.0.0.1`), the port, and no trailing slash. If
   you change `FINTRACK_PORT`, register the new URI too.

> mkcert's CA key (`$(mkcert -CAROOT)/rootCA-key.pem`) can sign certificates your Mac will trust for any site. Never
> share or copy it.

### 5. Stock prices from Alpaca (optional)

Live prices and daily price history (Overview movers, Investments, Portfolio, manual positions, live account values) come
from [Alpaca](https://alpaca.markets) instead of Plaid's last price:

1. Sign up at alpaca.markets for a free **Paper Only** account (email only; no funding).
2. With the **Paper** account selected, generate API keys.
3. Add both to `~/.fintrack/.env`: `ALPACA_API_KEY_ID=…` and `ALPACA_API_SECRET_KEY=…`
4. Restart `fintrack serve`. The first visit to Overview, Investments or Portfolio loads about 400 days of daily closes;
   every sync, or a page visit more than 6 hours later, tops them up. The Investments chart's "1 day" range uses the
   session's minute prices, fetched on page visits (at most every 5 minutes) and never stored.

Alpaca only receives the ticker symbols being looked up (yours, plus SPY, IWM and URTH for the benchmarks); the keys
travel in request headers. Only stocks, ETFs and mutual funds are looked up. Live prices are refreshed at most every 5
minutes, and pages wait at most 6 seconds for them. Mutual funds aren't covered by Alpaca and keep Plaid's price (or the
price you set on a manual position). Without the keys, nothing is sent to Alpaca.

## Running

```sh
fintrack serve
```

Open <https://localhost:8443>. **Settings → Setup** checks your keys, the encryption key, the database, and the
Production Item budget.

Sandbox and Production use separate database files. Switching `PLAID_ENV` switches databases, so fake Sandbox data never
mixes with your real data. Restart `fintrack serve` after changing `.env` or updating the code.

### As an app (no terminal, no browser tabs)

```sh
fintrack autostart install                    # start the server now and at every login
fintrack autostart status                     # running? pid, last exit code, log location
fintrack autostart restart                    # after updating the code or changing .env
fintrack autostart uninstall                  # stop it; back to `fintrack serve`
```

This installs a **launchd** agent at `~/Library/LaunchAgents/com.fintrack.serve.plist` that runs
`~/.fintrack/venv/bin/fintrack serve` at login (backing up and migrating first, as from a terminal) and restarts it if
it crashes, at most every 30 seconds. Output goes to `~/.fintrack/logs/serve-launchd.log`. While it runs, don't also start
`fintrack serve` in a terminal: the port is taken.

Then, in Chrome, open <https://localhost:8443> and click the install icon at the right of the address bar (or **⋮ →
Cast, save, and share → Install page as app…**). fintrack
gets its own window, a Dock and Launchpad icon, and no tabs or address bar. Pin it to the Dock (right-click → Options →
Keep in Dock). Links to other sites still open in Chrome, and Plaid Link works as in a tab.

### Phone access (Android or iPhone, away from home)

Your phone opens fintrack in its ordinary browser: no app to install and no outside service. Your router forwards one
port to this Mac, where a small **gate** answers. The gate lets a connection through only if it carries a certificate
you installed on your phone, so everyone else is turned away before reaching fintrack. Then fintrack asks for a
passcode (once per phone, remembered 30 days). Connecting or managing banks stays on the Mac.

The gate runs in macOS's sandbox: it can listen on its port and pass connections to fintrack, and can't read your
files, fintrack's database or the Keychain. fintrack itself still listens only on this Mac (`127.0.0.1`).

**What it needs**

- **A public IP address at home.** Compare the WAN / Internet address on your router's status page with what a "what's
  my IP" site shows. If they differ, or the router shows `100.64.x.x`, your ISP shares one address among customers
  (CGNAT) and nothing outside can reach your home; this can't work there.
- **A port forward on your router:** TCP **48443** to this Mac (give the Mac a fixed address on your router first;
  `fintrack remote status` shows it). Turn off **Private Wi-Fi address** for your home network on the Mac (System
  Settings → Wi-Fi → Details) *before* reserving the address: the router ties a reservation to the Mac's hardware
  address, and a private one makes the Mac look like a new device that gets a new address.
- **If phones suddenly can't connect** ("address unreachable", or Chrome shows an offline copy): compare the address
  `fintrack remote status` shows with the one the router's 48443 forward points to. They drifted apart once (2026-11-05)
  when the Mac's Wi-Fi address changed.
- **The Mac awake and at home.** A MacBook sleeps with its lid closed and is then out of reach.
- If your ISP changes your home address: `fintrack remote setup --host <new address>`, then install the new
  `fintrack-ca.crt` on each phone (`fintrack remote phone <name>` again). Many home addresses stay the same for months.

**Setup on the Mac**

```sh
fintrack remote setup --host 203.0.113.7   # your home's public IP (or a hostname for it): certificate + gate
fintrack remote passcode                    # what phones will enter (at least 10 characters)
fintrack remote phone pixel                 # each phone: see below
fintrack remote enable                      # start the gate (and restart the server)
```

**Each phone** (`fintrack remote phone <name>`, run in Terminal yourself: it shows a one-time password)

1. With the phone on the same Wi-Fi as the Mac, open the page it shows (or scan its QR code) and download both files.
   They're offered once, for 10 minutes, and saved nowhere afterwards.
2. Install them:
   - **Android:** Settings → Security → More security settings → Encryption & credentials → Install a certificate:
     **CA certificate** → `fintrack-ca.crt` (compare its fingerprint with the Mac's), then **VPN & app user certificate**
     → `<name>.p12` with the password the Mac showed. Menu names vary by phone; search Settings for "certificate".
   - **iPhone:** open each file from Downloads; Settings → **Profile Downloaded → Install** for each (the `.p12` asks for
     the password); then Settings → General → About → **Certificate Trust Settings** → turn on the authority.
3. Away from home (or on cellular), open `https://<your home IP>:48443`. The browser asks which certificate to use: pick
   this phone's. Enter the passcode. To keep it on the home screen: Chrome's **⋮ → Add to Home screen → Install**, or
   Safari's **Share → Add to Home Screen**. (Installed on Android, the bar under the app is always dark; Android only
   reads its color at install, so after an update that changes it, remove the app from the home screen and install it again.)

Certificates last about 27 months (`fintrack remote status` shows when). In their last 30 days, `fintrack remote setup`
makes a new server certificate; then add each phone again. At home on Wi-Fi the address works only if your router
supports "NAT loopback"; otherwise use the Mac.

**Read-only copy for when the Mac is asleep** (`PHONE_OFFLINE_COPY=on` in `~/.fintrack/.env`, then
`fintrack autostart restart`): while the Mac answers, each phone keeps an encrypted copy of the main pages (each page's
default view, every account's page, and every preset range: This month ... 365 days, all of Transactions' pages). When
the Mac doesn't answer within 4 seconds, the same shortcut shows the copy under a "Copy · <when the data last synced>"
line with **Try again**. The tabs and range menus work; searches, filters, Custom dates and edits don't ("Not in the
copy", or greyed out). The copy refreshes whenever you open the app while the Mac is awake (and when Chrome's background
sync runs), only if the data changed or it's over 12 hours old; a refresh is about 2 MB. It opens like the app does,
with no passcode, and ends with the 30-day sign-in: a new sign-in replaces it, and `fintrack remote passcode` or
switching the setting off wipes it the next time the phone reaches the Mac. A lost phone that never reaches the Mac
again keeps its copy until its 30 days end, protected by the phone's lock screen.

```sh
fintrack remote status            # address, port to forward, certificate expiry, passcode, phones, the gate
fintrack remote remove pixel      # a lost phone: its certificate stops working at once
fintrack remote passcode          # a new passcode signs every phone out
fintrack remote disable           # stop the gate; phone access off
```

### Finding your way

The left rail lists the sections; the tabs across the top are the pages in the current section, and each page's
controls (date range, toggles) sit at the top right. The badge at the bottom of the rail shows the Plaid environment;
**Sync now** is just above it.

| Section | Pages |
|---|---|
| Overview | the home page |
| Transactions | Transactions, Spending |
| Accounts | Net worth, Accounts |
| Investments | Investments, Portfolio |
| Analysis | Savings, Cash flow, Credit cards |
| Planning | Budgets, Recurring, Projection |
| Settings | Setup, Account types, Categories, Rules |

Headline figures are rounded to whole dollars; tables and transactions show exact cents. Every chart has a **Show data**
table with the same numbers.

## Connecting accounts

1. **Settings → Setup → Connect an institution.**
2. Choose **Bank, credit card, or loan** or **Brokerage or retirement**. This decides which institutions Link offers: a
   bank must support Transactions, and a brokerage must support Investments. Other products are added whenever the
   institution supports them.
3. **Open Plaid Link** and sign in. In Sandbox, pick any bank (e.g. "First Platypus Bank") and use `user_good` /
   `pass_good`. To test OAuth, pick "Platypus OAuth Bank".
4. Each connection requests 2 years of transaction history, the most Plaid allows (some institutions send less). This
   can't be changed after the connection is made.

Each connection on Setup has:

- **Refresh**: re-reads accounts and balances with Plaid's free `/accounts/get` (Plaid's latest cached values).
- **Add or remove accounts**: change which accounts this login shares, using Plaid's update mode (no new Item).
- **Reconnect** (when a login has expired): repairs the same connection with update mode, never a new Item.
- **Remove this connection**: type the institution's name to confirm. It revokes access at Plaid and hides the accounts
  but keeps their history.

**Manual accounts** cover what Plaid can't see (a home, a car, a 401(k) at an unsupported provider, cash, a credit card
or bank you'd rather not connect, money lent): **Settings → Setup → Add manual account**. A manual bank account, credit
card, loan or investment account works like a connected one (docs/parity-plan.md): its transactions or activity come
from files you import (**Settings → Import**) or that you add, they count everywhere, and its balance follows them from
the last balance you gave it. A manual card can have its statement worked out (its closing and due days) and its own
card number (Last 4), which tells its own transactions from an authorized user's.

## Syncing

Click **Sync now** or run `fintrack sync`. Each sync refreshes balances, fetches new, changed and removed transactions
since the last sync (Plaid keeps a "cursor" per connection), holdings, card statements and loan terms, then saves the
day's net-worth snapshot. The first sync after connecting pulls the history; if it says "history still loading", sync
again in a minute.

- A sync either fully succeeds for a connection or changes nothing for it, so an interrupted sync retries next time.
- When a pending transaction posts, any category you gave it carries over.
- If the bank removes a transaction that had already posted, fintrack keeps it and lists it under **Removed by the
  bank** on that account's page (its rows on Transactions carry a flag). It keeps counting, with your category and
  links, until you decide. When it looks like it came back (same account, within a week, same amount or same merchant),
  the returned one is shown beside it with what changed, and isn't counted until you choose **Use the returned one**
  (your changes move onto it), **Not the same**, **Keep it** or **Delete it**.
- Connections without the Transactions product get balances (and holdings) only; fintrack never calls the transactions
  endpoint for them, because that would add the product to the connection at Plaid.
- Only one sync runs at a time.

**Investment transactions** (deposits, buys, dividends inside brokerage and retirement accounts) are a separate Plaid
subscription per connection. They're always read in the Sandbox, and in Production only with
`INVESTMENT_TRANSACTIONS=on` in `~/.fintrack/.env` (check the price in the Plaid dashboard first).

### Scheduling syncs (macOS)

```sh
fintrack schedule install                     # daily at 06:30 and 18:30
fintrack schedule install --times 07:30       # or your own times (24-hour, comma-separated)
fintrack schedule status                      # installed? last exit code? log location
fintrack schedule uninstall
```

This installs a **launchd** agent at `~/Library/LaunchAgents/com.fintrack.sync.plist` that runs
`~/.fintrack/venv/bin/fintrack sync` under `caffeinate -i` (the Mac stays awake until the sync ends). launchd runs a
sync missed while the Mac was asleep once it wakes (cron would skip it), but it never wakes the Mac. To have a sleeping
Mac wake for the morning sync, set macOS's daily wake (one time per day is all it allows) a couple of minutes before:

```sh
sudo pmset repeat wakeorpoweron MTWRFSU 06:28:00     # check: pmset -g sched; undo: sudo pmset repeat cancel
```

To keep syncs, live prices and phone access working all day while plugged in, turn on System Settings → Battery →
Options → "Prevent automatic sleeping on power adapter when the display is off". On battery the Mac sleeps as usual. Output goes to `~/.fintrack/logs/sync-scheduled.log`; a sync with errors exits with code 1, which `schedule status`
shows. The job syncs whichever environment `.env` names, and needs you logged in (it reads the Keychain).

## Using fintrack

**Overview**: what needs attention (logins to reconnect), net worth over time, spending this month vs the same days last
month, savings rate, spending by budget category, cash flow for the next 30 days with what's coming up, recent
transactions, investment movers and your accounts by type.

**Transactions**: every transaction, with a date range (365 days by default), search (words, or amounts like `42.50`,
`>100`, `coffee <10`; `"two words"` and `-word` match or leave out descriptions), filters (accounts, grouped by account type, categories and tags as checklists, tags with a "No tag" choice; a card with authorized users lists the primary and each AU card under it, a category its subcategories) and sortable columns; an authorized user's transactions show "(AU)" after the account. Above the list, charts of the filtered transactions: over time (All: money in and out; Spending / Income: totals with a dashed line at the average; Transfers: a flow chart of where transfers went between accounts), and spending (or income) by category with its subcategories in an outer ring (the smallest combined into Other, at most 5% of the total); click a slice or a bar to filter the list by that category (Other: all the categories in it) or month (click it again to clear; both can apply at once). Click a row to edit its description, category or transfer pairing,
or to create a rule from it with a live preview, and to make it recurring, add it to a recurring item or say it isn't one
(transactions in a confirmed recurring item show a repeat icon). Tick rows (or *Select all matching*) to change a category or link to a
purpose account in one go. A toggle across the top shows **All**, **Income**, **Spending** or **Transfers** (loan
account entries are with Transfers). A **Categories to review** card appears just above the table when a new transaction lands in a broad category that has subcategories (Food & drink rather than Restaurants): open it to pick better categories, or dismiss it to keep them as they are. It has its own search, category and account
filters, sortable columns, and a bulk bar: check rows, then set one category for all of them or keep theirs. Brokerage and retirement activity is in the list too (edit its description, category and tags like
any transaction), in your matching categories (Investment Income › Dividend / Sell, Investments › Buy / Fee, Transfer in /
out); it counts in this page's totals and charts only. Each row shows the merchant's logo; a transfer shows the bank or account on the other side (where
money in came from, where money out went) when fintrack knows it; anything else shows its category's icon.

**Spending**: what you spent over the chosen range (This month by default): totals vs the period before and your
12-month average, a running-total or monthly chart, a treemap and table by category, and the transactions behind them.
Point at or click the chart, a tile or a row to filter the transactions.

**Savings**: what you saved from income over the range: tiles, cards for net savings, retirement, investment and net
cash (each as a share of income), and a monthly table. Pre-tax income is the contributions you record on manual
retirement accounts; money lent and money moved out of investments are left out.

**Cash flow**: the projected balance of the account that pays your cards over the next 30 days, and everything coming
up: confirmed recurring items and card statements still owed. The low-balance threshold (used by the Overview's
warnings) is set at the top right.

**Credit cards**: each credit card's spending by category over the chosen range (YTD by default): its total, a bar chart
and a table; click a category to see those transactions on that card. Plaid doesn't report rewards, so under
**Estimate rewards** at the foot of a card you enter what it earns (points, miles or cash back), a base rate and any
rates for a category, a merchant or both (e.g. 3x on Food & drink, 5x on airfare whose description contains "United":
the most specific rate wins); the card then shows its estimated rewards for the range, in total and by
category, with its categories grouped by rate and a bar per rate (a rate has the same colour on every card). Card fees (Bank fees)
never earn rewards. To add a one-off bonus to a transaction (a promotion, say), open it on Transactions and enter it
under **Reward bonus**; it's added to the card's estimate. Each category row links to exactly its transactions. It's an estimate: caps, rotating categories and bonuses aren't included.
**Rewards you spend are transactions:** on **Transactions**, **+** adds a transaction by hand: **Cash**, or a **Rewards
redemption** with its rewards points used for something bought with points, such as an award flight, and it's added as the cost plus
a rebate of the same amount in the same category, so the category nets to zero while a tag on the cost (a trip) counts
what it cost. A statement credit you paid for with points: open it and fill in **Rewards used**. A charge that was only
part of the price (a $50 charge for a $200 ticket, the rest in points): open it and fill in **Also paid with rewards**
($150) and the points; the category keeps the $50 you paid and its tags count the $200. The card here then
shows **Redeemed** (their value, its share of the card's cost and what each point was worth) and **Cash rewards** (money
in filed as income, like a cash sign-up bonus), and its rewards balance takes them off.

**Overview calendar**: one month of the Tags calendar under Accounts, with arrows, and two countdowns: by default the next two tags with
a future Start date, holidays or labels of yours. Click a countdown's title to choose what it follows until that date passes.

**Tags calendar days**: shows US federal and major shopping holidays, with estimated observed days. Click a day to add your
own label, for one day or every year.

**Tag budgets**: on a tag's card, set a total budget, budgets by category, or both. Add the tag on Budgets with **+ Add budget**
and type any amount into any month. While it's on the budget, its spending counts on its lines instead of its categories'.

**Tags**: your own labels (emoji welcome) on transactions, added in a transaction's edit panel or to many at once with
group edit, and a filter on Transactions; this page sums each tag: spent, received, its dates, and spending by category. + makes a new tag; a calendar shows
when each happened (its gear can show only tags' own date ranges); the cards are listed newest first, and those on the
calendar now are outlined, with the list scrolled to the first of them.

**Net worth**: net worth, assets and liabilities with their change over the range, net worth over time ($ or %), a card
per kind of account (cash, retirement, investment, other assets, credit cards) and the change in value by month.
Net worth = assets − liabilities, from each account's balance as the institution reports it (cards and loans count what
you owe; investment accounts are marked to market with live prices between syncs). Only USD accounts count; others are
listed as not counted. History starts with your first sync: one snapshot per account per day.

**Accounts**: each kind's accounts with their value and change over the range, and a summary by kind. Click an account
for **its page**: filter its transactions (search, card, category, dates), see a card's authorized users' cards with
what each spent, rename it, give it a type, see a card's statement and choose the account that pays it, record a
card's **rewards balance** as of a date (edit or delete each one; with reward rates set up, the card shows the current
balance = the latest one + the rewards of the transactions since, and the transactions table gets a rewards column with
your balances as lines in it), its **bonus points** (a sign-up bonus paid in points, added to the current balance), holdings
(investment accounts), its transactions or activity, and its rules. A manual account's page also holds its **Balances**
(the balances you've entered or imported: values; for an investment account, its holdings' balances, priced through
Alpaca, while its buys, sells, dividends and contributions are activity under Transactions, entered with **Add
activity**), or, for money lent, its **History** (linked transfers and adjustments); its kind, and Archive / Delete.

**Investments**: performance of your stocks vs the S&P 500, Russell 2000 and MSCI World (through SPY, IWM, URTH) over 1
day to 365 days, a % change chart (click a benchmark card or a holding's row to add its line; on 1 day it follows the
last session minute by minute), and every holding with its change today and over the chosen range. The card's
**Watchlist** tab follows tickers you don't hold: click + at the card's top right, type a ticker or a company name and pick a match (the
company list comes from Alpaca about weekly and is searched on your Mac); click a row to add its line to the chart.

**Portfolio**: total, retirement and investment accounts with their market performance, value over time by account
type, money moved into and out of investment and retirement accounts by month, and the holdings in each account
(filterable).

**Budgets**: one plan per month in Income / Fixed / Flexible sections (or Needs / Wants, or Custom: groups you add
and rename right in the table; switch in the main card's settings button, which also groups lines without a budget)
with a Savings row: type an amount or a formula
(`=795/12`, `=4000*15%`), pick a suggestion from your actuals, choose "this month only" or "every month after", roll
leftovers over, see upcoming recurring bills. The 1-year and 10-year views show actuals for past months and budgets
ahead.

**Recurring**: this month's confirmed recurring income and expenses on each day they're due (annual items left out)
with the month's totals; everything due in
the next 30 days; and the income and bills detected from your history (same merchant, similar amount, regular
schedule). Only items you **confirm** count there, in the cash-flow forecast and in the Budgets' Upcoming column. **Check again** re-scans saved transactions.
**Confirm** and **Edit** open the item's window: **Matches** (what finds its transactions, and its schedule) and **Then do
this** (the rule builder's actions: set category or description, add a tag, link to purpose, not a transfer, accept), applied to
each of its transactions, ahead of your rules; your own choices still win.

**Projection**: net worth month by month for 10 years from today's balances, with editable assumptions (savings,
return, property growth, loan terms) and what-if scenarios to compare.

**Settings**: **Setup** (connections, manual accounts, configuration checks), **Account types** (your table of types
and subtypes; an account's type decides its kind and where it's shown), **Categories** (rename, add, split into
subcategories, choose what each counts as: spending, income or not counted, pick an icon: click the icon before a
name; matches for the name come first, and **Automatic** goes back to the default) and **Rules** (if all of a rule's conditions on description, amount, date, category or account match, it can set the
description or category, add tags, add to a recurring item, link to a purpose account, mark as not a transfer or accept the category, with a
live preview; they run in the order you drag them into; your own choice beats a rule, a rule beats Plaid; editing or deleting a rule updates its transactions) and **Transfers** (the
transfers matched between your accounts over a date range, added up by route; remove a match, or match a transaction
yourself with one of the transactions that meet the matching rules). **Import** brings in a bank's own download (CSV of any
layout, Excel `.xlsx`, OFX / QFX / QBO or QIF) for an account, connected or manual (a manual bank account, credit card or loan: its balance then follows its
transactions from the last value you gave it; "New manual account" makes one): pick the account and the file and a preview appears, with what each column holds (change
a choice above its column), any question the file leaves open (month or day first, which sign is money out, and, if its
dates overlap the account's transactions, whether to take only dates the account doesn't have, every row but likely
duplicates, or to pick), a category for each of the bank's own categories, and every row as it would be added. Import
stays off until every question is answered; your answers are kept for that account's next file with the same columns.
Investment files work too: a broker's
activity (buys, sells, dividends, contributions; each kind of wording gets a picker, kept for next time) into an
investment account, and a holdings statement into a manual one (each holding's shares on the statement's date; it asks
for the date if the file doesn't say). Past imports are listed with **Undo**. The file isn't kept. On the Mac only (a phone shows a note); each account's page
links to it ("Import a file").

Transfers between your own accounts are not counted: by category, and as a safety net by pairing an outflow with an
equal inflow in another of your accounts within 3 days (undo with **Not a transfer**, or on **Settings › Transfers**).

## Sandbox demo data

With `PLAID_ENV=sandbox` you can fill the Sandbox database with a made-up household instead of Plaid's tiny test
accounts:

```sh
fintrack demo            # into an empty Sandbox database
fintrack demo --replace  # back up the existing Sandbox database first, then start fresh
```

You get a checking account, four credit cards, three investment accounts and three manual investment accounts, about 21
months of activity and a net worth of about $500,000. Every name is made up. **Sync now** and the scheduled sync work on
it without contacting Plaid; run it again with `--replace` to refresh the dates. It refuses to run against Production.
Restart `fintrack serve` afterwards.

## Commands

| Command | What it does |
|---|---|
| `fintrack init` | Create or repair the data folder, `.env`, Keychain key, and database (safe to re-run) |
| `fintrack certs` | Create or renew the HTTPS certificate |
| `fintrack serve` | Start the web app (applies migrations first, after a backup) |
| `fintrack migrate` | Apply database migrations (backs up first) |
| `fintrack backup` | Save a dated copy of the database to `~/.fintrack/backups/` |
| `fintrack items` | List Items and the Production Item budget |
| `fintrack sync` | Sync all connections (exit code 1 if any failed) |
| `fintrack schedule install\|uninstall\|status` | Manage the scheduled sync |
| `fintrack autostart install\|uninstall\|status\|restart` | Run the web server at login (launchd), for Chrome's installed app |
| `fintrack remote setup\|phone\|remove\|passcode\|enable\|disable\|status` | Phone access with no app on the phone (see "Phone access") |
| `fintrack preflight` | Check the Production setup before connecting real banks (creates no Item) |
| `fintrack demo [--replace]` | Sandbox only: fill the database with made-up demo data |
| `fintrack logos [--backfill [--dry-run]]` | Download missing logos; `--backfill` fills in merchant and transfer-bank logos for older transactions |
| `fintrack descriptions --backfill [--dry-run]` | Fill in the raw bank text (used by destination rules) for transactions synced before it was kept |
| `fintrack locations --backfill [--dry-run] [--item N]` | Fill in where transactions happened (Plaid's location, in-person purchases only) and which card was used (Plaid's `account_owner`) for transactions synced before they were kept; `--item` = one connection |
| `fintrack import FILE... --account ID [--apply]` | A bank's download (CSV of any layout, Excel `.xlsx`, OFX / QFX / QBO, QIF) into an account: connected, or a manual bank account, credit card or loan. Works out the header, columns, date format and which way round money in and out are, and shows what it would add; `--apply` imports it as one batch after a backup. If it can't tell month-first from day-first dates, or which sign is money out, it asks (`--dates mdy|dmy`, `--money-out positive|negative`); a wrong column guess is fixed with `--columns date=1,description=3,amount=5`. Your answers are kept for that account's files with the same columns. If the file's dates overlap the account's transactions it asks which you want: `--overlap outside` (only dates the account doesn't have), `skip` (every row but likely duplicates) or `review` (plus the duplicates you name with `--take ROW,ROW`). Importing the same rows again adds nothing. `--history` lists past imports; `--undo BATCH --apply` removes one. Investment files too: a broker's activity or a holdings statement (`--kind activity|positions`, `--as-of DATE` for a statement's date, `--holding SYMBOL=TICKER` to track a fund through another ticker, or `=new`, `--no-added-dividends`); `--primary-card LAST4` says which card in a file is the account's own. `import-csv` is the old name. Keep the downloads out of this folder (or delete them after) |
| `fintrack convert-positions [--apply]` | One-time (done 2026-11-14): manual investment accounts' typed contributions, withdrawals and fees into activity; checked on a copy of the database first (every value, holding and Savings figure to the cent), converted only with `--apply` when the copy matches |

## Switching to Production

Production connects your real accounts. Each bank **login** uses one of the Trial plan's 10 Items, permanently.

1. **Get the Production secret** in the Plaid dashboard under **Developers → Keys** (complete the Trial plan application
   first if Plaid asks). On the Trial plan you don't need the app profile, services agreement, or security
   questionnaire for OAuth banks. Some brokerages take longer to become available (up to 5 business days).
2. Put it in `~/.fintrack/.env` yourself (`open -e ~/.fintrack/.env`): `PLAID_SECRET_PRODUCTION=...`. Leave
   `PLAID_ENV=sandbox` for now.
3. Run **`fintrack preflight`**. It checks the Production keys, the Keychain key, the certificate, and the Item budget,
   and asks Plaid for one Production link token to prove the secret and redirect URI work. **It creates no Item.**
4. When every line shows ✓, set `PLAID_ENV=production` in `.env` and restart `fintrack serve`. Production uses its own
   database (`fintrack-production.db`), so none of your Sandbox data carries over.
5. **Connect one bank first** and check it fully: accounts, a sync, transactions. Then connect the rest, one at a time.
   Each connection asks you to type `CONNECT` after showing how many Items remain.
6. After connecting: wait for history to load, review **Recurring**, check **Categories** and **Budgets**, confirm the
   scheduled sync (`fintrack schedule status`), and run `fintrack backup`.

To go back to Sandbox for testing, set `PLAID_ENV=sandbox` and restart.

## Plaid Item budget

The Trial plan allows **10 Production Items**. One Item is one institution login, however many accounts it has.
**Removing an Item does not refund the slot.** The app therefore:

- keeps a permanent record of every Production Item it creates (the database refuses to delete them),
- shows used and remaining Items, and requires you to type `CONNECT` before any Production Link session; it refuses
  outright once the limit is reached,
- logs every Link session, including when you consented and how many Items were used at that moment,
- lists institutions you've already connected before Link opens, and stops Link if you pick one of them (unless you
  tick "second, separate login"),
- repairs broken connections and changes shared accounts with Link update mode, which never creates a new Item.

The Plaid dashboard is the real source of truth. If you restore an old backup or lose the database, check the dashboard
before connecting anything.

## Backups

```sh
fintrack backup
```

This uses SQLite's online backup API, so it's safe while the app is running. Copy `~/.fintrack/backups/` to external
storage now and then. A backup without the Keychain key still holds all your transactions, but its Plaid tokens can't
be decrypted. To restore onto a new Mac, re-link each institution with update mode.

## Security notes

- Plaid access tokens are encrypted (Fernet) with a key held in the macOS Keychain.
- Only the last 4 digits (mask) of account numbers are stored. The database rejects anything longer.
- Logs redact anything that looks like a Plaid token, plus your client ID, secrets and Alpaca keys.
- The web app rejects requests whose `Host` header isn't `localhost` or `127.0.0.1` (blocks DNS rebinding), and rejects
  state-changing requests from other origins (blocks cross-site form posts).
- The browser may load scripts and styles only from this app, plus exactly Plaid Link's script and frame
  (`cdn.plaid.com`) and the Plaid API host for the current environment (Content-Security-Policy). Plaid doesn't allow
  self-hosting the Link script, so it's the one file loaded from outside. Logos are downloaded by the server and served
  from the app.
- The data folder and its files are readable by your user account only.
- Phone access: the gate (sandboxed: no file, database or Keychain access) accepts only TLS 1.3 with a certificate
  issued to one of your phones, then hands the connection to fintrack on `127.0.0.1`; fintrack asks for the passcode (5
  wrong tries lock it for 15 minutes) and refuses bank connections. Every certificate authority here signed one
  certificate and its key was never saved, so none can vouch for anything else.
- With `fintrack autostart`, the server is always listening, on `127.0.0.1` only. Websites can't use it (the `Host` and
  origin checks above), but other programs running as you on this Mac could reach it, as they can while `fintrack
  serve` runs in a terminal.

## Development

```sh
~/.fintrack/venv/bin/pytest
```

Tests use a throwaway data folder and an in-memory keyring. They never touch `~/.fintrack` or your real Keychain.

To see how the pages fit a phone, with the server running: `node tools/phone_check.mjs` loads every page in headless
Chrome as a 390px phone and prints what doesn't fit (`--width 360,390,412` for several widths, `--shots <folder>` for
screenshots in a folder outside the repo, since they show your data). Plan and results: `docs/phone-plan.md`.
Working rules for changes (and for Claude Code) are in [`CLAUDE.md`](CLAUDE.md); notes per area are in
[`docs/`](docs/).
