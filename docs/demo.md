# Sandbox demo data

`fintrack demo`: the made-up world in the Sandbox database.

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

- **Sandbox demo data (user's request 2026-10-09; the app moved to PLAID_ENV=sandbox):** `fintrack demo [--replace]` fills the
  SANDBOX database with a made-up world shaped like the real setup: 1 checking + 4 credit cards + 3 investment accounts
  (brokerage / IRA / Roth) through 4 "Demo ..." connections, 3 manual investment accounts with dated positions, ~1,800
  transactions since the start of the year before last, 647 days of net-worth snapshots, net worth ~$500k
  (`demo.TARGET_NET_WORTH_CENTS`; checking absorbs the rounding). Why generated rather than Plaid's custom user: a custom user
  can't set transaction categories and is capped (~250 transactions, 10 accounts per login). How: `build_dataset` (seed
  20261009) writes `FINTRACK_HOME/demo-sandbox.json` (mode 600, never in the repo); Items are inserted with tokens
  `demo-access-<key>`; the app's REAL `sync_all` then runs over a `DemoServer`, so categories, transfer matching, recurring
  detection, holdings, card statements and snapshots all come from the real code. `make_gateway(settings)` (web app default and
  CLI sync/logos) returns the plain `PlaidGateway` in Production and a `DemoAwareGateway` in the Sandbox, which answers demo
  tokens locally (no network; Sync now and the scheduled sync keep working, a re-sync adds nothing) and passes every other
  token to Plaid. Demo Items can't be re-linked or "simulate login required". Safety: `demo` refuses in Production, refuses a
  database that has data unless `--replace`, which first backs it up to `backups/` and starts a fresh database. Prices: live
  Alpaca quotes (through quotes.py, as always) size the holdings when keys exist (approximate fixed prices otherwise), and
  Alpaca daily history is backfilled so manual accounts follow the real market; manual accounts' values therefore drift
  slightly from $500k as markets move, Plaid-style balances stay fixed. Everything is fictional ("Riverside Market", "DEMO CORP
  PAYROLL"); holdings use real tickers with generic names. Investments start at 62% of today's value (`RAMP_START`) so the
  history grows by what saving + market explain (~$9k/month), checking hovers $10-19k. August 2026 is deliberately an overspent
  month (`demo.ONE_OFFS`: an air-conditioner replacement and a car repair on a card, so it spends ~$10.6k against $8.55k of
  income; user's request 2026-10-09) to exercise negative savings on the Savings page and Budgets; the other months stay within income. Re-run with `--replace` to refresh
  the dates. The original sandbox data (45 leftover Plaid test accounts, net worth about -$42k) was backed up on 2026-10-09
  (`backups/fintrack-sandbox-2026-10-09_074758.db`).
