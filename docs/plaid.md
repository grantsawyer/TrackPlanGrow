# Verified Plaid and Alpaca facts

Moved from CLAUDE.md on 2026-10-16 (it had grown to 151 KB). Dated notes say when and why something was decided,
usually at the user's request; they stay so a choice isn't reversed by accident. When you change this area, update
this file in the same change.

## Plaid (first checked 2026-10-02)

Source: <https://plaid.com/docs/account/billing/>, <https://plaid.com/docs/link/oauth/>.

- Trial plan includes: Assets, Auth, Balance, Identity, Investments (+Refresh),
  Liabilities, Transactions (+Refresh), Statements.
- **Recurring Transactions is NOT on Trial.** The user said to ignore this for
  now. At milestone 9, ask before choosing between local detection and dropping the feature.
- Removing a Trial Item does not allow creating more Items.
- Redirect URIs must be HTTPS in Production (HTTP localhost is allowed in
  Sandbox only), with no `#`, and must be registered in the dashboard. Ours is
  `https://localhost:8443/oauth-return`. The OAuth return page re-opens Link
  with `receivedRedirectUri = window.location.href`.
- **Trial plan + OAuth (re-verified 2026-10-03, https://plaid.com/docs/link/oauth/):**
  "If you are on a Trial plan, you do not need to complete these requirements until you
  upgrade to a paid plan." One large bank's security questionnaire applies "unless you are on a
  Trial plan." Fidelity and Schwab may take longer (Schwab: up to 5 business days after
  Production approval). Earlier notes saying compliance steps were required were wrong for
  Trial. support.plaid.com blocks automated fetching (403).
- **Institution logos (checked 2026-10-13, https://plaid.com/docs/api/institutions/ and /docs/account/billing/):**
  `/institutions/get_by_id` returns `logo`, "a base64 encoded 152x152 PNG. Not all institutions' logos are available";
  the docs' option is `include_logo`, SDK 45.0.0 has the older `include_optional_metadata` (logo, brand color, URL).
  Billing: "free-to-use endpoints include Institutions endpoints"; no Item involved.
- **Institution search for logos (checked 2026-10-13, https://plaid.com/docs/api/institutions/):** `/institutions/search`
  takes `query` + `country_codes` (`products` optional: "Provide null to get institutions regardless of supported
  products"), returns "up to a maximum of ten institutions per query", logo via `include_logo` (SDK:
  `include_optional_metadata`). Free (Institutions endpoints). **Alpaca's logo endpoint**
  (`data.alpaca.markets/v1beta1/logos/<symbol>`) answered 403 "Subscription does not permit querying logos" on the
  user's free plan (tested once 2026-10-13), so it isn't used.
- `/institutions/search` (Production, read-only, no Item; checked 2026-10-03): some 401(k)
  and HSA providers support only Transactions (no Investments), so they appear in Link only
  under the `banking` kind; some retirement providers are listed per employer plan. Check support this
  way before spending an Item. Never record the user's own institution list in the repo.
- Update mode with Account Select (`update.account_selection_enabled`) changes which accounts
  an Item shares without a new Item; OAuth banks with their own picker always allow it.
  `items.upsert_accounts` hides accounts Plaid stops returning and unhides re-shared ones
  (skipped on an empty list).
- No webhooks: the server is not reachable from the internet, so updates come
  from manual or scheduled syncs.
- `/link/token/create`: an institution must support **all** `products` to
  appear in Link, so the app has two kinds (`banking` requires transactions;
  `investments` requires investments; the rest are `optional_products`).
  `transactions.days_requested` (max 730) can only be set at Item creation;
  we use 730. Update mode = pass `access_token` and omit products.
- The Link script must load from `https://cdn.plaid.com/link/v2/stable/link-initialize.js`
  (self-hosting prohibited). CSP adds that script, `frame-src https://cdn.plaid.com/`,
  and `connect-src` for the current env's API host.
- Link `onEvent` ordering and timing are not guaranteed. In user testing (2026-10-02),
  SELECT_INSTITUTION fired too late to stop a duplicate, so it's only an early exit.
- `/transactions/sync` (verified in Sandbox 2026-10-03): count max 500; restart from
  the **first page's** cursor on TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION;
  pending->posted = removed pending + added posted with `pending_transaction_id`;
  dates are `datetime.date`; PFC defaults to v2 with `confidence_level`; first call
  right after Item creation returns NOT_READY with nothing; `next_cursor` can be
  "" with HISTORICAL_UPDATE_COMPLETE when an account has no data (Sandbox OAuth
  business card).
- Balance meanings (https://plaid.com/docs/api/accounts/): credit `current` positive =
  owed, negative = issuer owes holder; loan `current` = principal remaining (Sallie Mae
  student loans include interest); investment `current` = total value incl. cash;
  depository negative = overdraft.
- **Investment transactions and billing (checked 2026-10-09, https://plaid.com/docs/account/billing/,
  /docs/investments/, /docs/api/products/investments/):** Investments is a *subscription* product (per Item, not per
  request; only `/investments/refresh` is a per-request fee), and an Item can carry two separate Investments
  subscriptions, **Holdings** and **Transactions**. Verbatim: "Adding Investments to an Item via /link/token/create or by
  calling /investments/holdings/get adds the Investments Holdings subscription. Calling /investments/transactions/get on
  an Item adds both the Investments Transactions and Investments Holdings subscriptions." So the transactions endpoint is
  NOT covered by what our Items already have (answers the open question below: calling an endpoint for a product the
  Item lacks adds that product's subscription). Trial plan: "Investments (including Investments Refresh)" is listed as
  available, but the docs do not say whether that includes the Transactions subscription, and no price list is published
  ("To view pricing, apply for Production access"); "usage of Sandbox is always free". `/investments/transactions/get`:
  up to 24 months, `count` max 500 with `offset`, investment-type accounts only; types buy/sell/cancel/cash/fee/transfer;
  `cash` + subtype `contribution` or `deposit` = money into the account; no employer/employee distinction documented;
  institution coverage not documented. Not called by fintrack: never call it on a Production Item without the user's
  approval after confirming the price in the Plaid dashboard.
- **Transactions never covers investment accounts (checked 2026-10-11, https://plaid.com/docs/transactions/ and
  /docs/investments/):** "Plaid's Transactions product allows you to access a user's transaction history for `depository` …
  `credit` … and `loan` type accounts"; "For transaction history from investment accounts, use Plaid's Investments product."
  Seen in Production: an Item with transactions + investments and only investment accounts finishes `/transactions/sync`
  with HISTORICAL_UPDATE_COMPLETE, an empty cursor and 0 transactions (holdings still arrive). So contributions into
  connected brokerage/IRA accounts are visible only through `/investments/transactions/get` (INVESTMENT_TRANSACTIONS=on).
  The billing page lists "Investments (including Investments Refresh)" among Trial plan products; Plaid's support pages
  (403 to automated fetches) are summarized by search as describing the Trial plan as no cost up to 10 Items. Whether the
  Investments Transactions subscription is charged on Trial is still not stated verbatim: confirm in the dashboard first.
- `/investments/holdings/get`: cash appears as a security with `type: cash`,
  `is_cash_equivalent: true`. The docs don't say whether calling it on an Item without
  Investments adds the product, so it's only called when `items.products` has
  `investments`. In Sandbox, the 401k's holdings sum ($25,125.63) exceeds its balance
  ($23,631.98): Sandbox data is not internally consistent. Trust the balance.
- PFC v2 taxonomy (https://plaid.com/documents/pfc-taxonomy-all.csv, fetched 2026-10-03):
  18 primaries incl. new `LOAN_DISBURSEMENTS`. Credit card payment is the *detailed*
  `LOAN_PAYMENTS_CREDIT_CARD_PAYMENT` (same primary as mortgage/car/student payments).
  `TRANSFER_OUT` includes `_WITHDRAWAL` (ATM) and `_TRANSFER_OUT_FROM_APPS` (P2P apps).
  Sandbox: savings interest "INTRST PYMNT" comes back as `TRANSFER_IN_OTHER_TRANSFER_IN`
  (Plaid mis-categorization), so Sandbox shows $0 income. Sandbox months repeat identically.
- **Calling `/transactions/sync` on an Item without Transactions does not error.
  It starts initializing Transactions on that Item** (returned NOT_READY).
  Only sync Items whose `items.products` includes `transactions`.
- Duplicate Items (https://plaid.com/docs/link/duplicate-items/): "a duplicate Item
  will be created if … an access token is requested." So `items.check_duplicate`
  compares the onSuccess metadata (institution_id, account name + mask) with active
  Items and refuses **before** `/item/public_token/exchange`. The docs don't say
  explicitly how the Trial 10-Item counter treats an unexchanged Link success.
- **No rewards data (checked 2026-10-25, plaid.com/docs/api/products/liabilities/):** `liabilities.credit` has only
  `aprs` (percentage, type, balance subject to it, interest charged), `is_overdue`, `last_payment_amount/date`,
  `last_statement_issue_date/balance`, `minimum_payment_amount`, `next_payment_due_date` (the example also shows an
  undocumented `cash_advance_limit`). Nothing on points, cash back or reward categories, and transactions don't carry
  points earned. fintrack estimates rewards from rates you enter instead (rewards.py).
- **Production observations (2026-10-03):** `/liabilities/get` can return a *negative*
  `minimum_payment_amount` (new card, $0 statement); `loans._upsert_plaid` stores it as NULL.
  One issuer's card payments ("AUTOMATIC PAYMENT - THANK…") come back as `INCOME_SALARY`;
  fixed with a description rule → `CREDIT_CARD_PAYMENT` (a data fix, not code). History depth
  varies by institution (some ~18 months, one ~90 days) despite `days_requested=730`.
  Failed/cancelled Link logins (non-OAuth providers with Plaid health warnings) used no Item.
- **Production observation (2026-11-03): dropped connections.** The scheduled sync's log shows 20
  `RemoteDisconnected` ("Remote end closed connection without response", often on
  `/investments/transactions/get`) and a few DNS failures since the log began (2026-10-03). Raw urllib3 errors used to escape
  `sync_all`, so the Items left and every step after them (rules, transfers, recurring, snapshot) were skipped.
  `PlaidGateway._call` now turns network failures into `PlaidError("NETWORK_ERROR")` and retries a dropped
  connection once. A card's payment can also reach the checking side a day or more before the card side
  (`cards._paid_from_bank`).
- Verified against real Sandbox (2026-10-02): `/accounts/get` `type` comes back as a plain
  string; `/sandbox/item/reset_login` gives ITEM_LOGIN_REQUIRED; after
  `/item/remove`, `/item/get` gives ITEM_NOT_FOUND. The redirect URI is accepted.

**Alpaca (checked 2026-10-07: docs.alpaca.markets snapshots + bars references, alpaca.markets/data,
and a live test with the user's free Paper Only account; replaced Finnhub the same day):**
Free plan: 200 calls/min (confirmed by `X-RateLimit-Limit`); keys in headers `APCA-API-KEY-ID` /
`APCA-API-SECRET-KEY`; a Paper Only account needs only an email. `GET data.alpaca.markets/v2/stocks/
snapshots?symbols=A,B&feed=iex|delayed_sip` prices all symbols in one call (latestTrade, minuteBar,
dailyBar, prevDailyBar); `iex` = real time, one exchange (median 0.014% off Finnhub in the test);
`delayed_sip` = all exchanges, 15 min late; its dailyBar/prevDailyBar closes matched the official
closes exactly (19/19). `/v2/stocks/bars?timeframe=1Day&feed=sip` = daily history back to 2016, many
symbols per call (10,000 bars/page); the free plan refuses SIP from the last 15 minutes ("subscription
does not permit querying recent SIP data"), so `end` must be ≥ 15 min ago (we use 16). Mutual funds
and indexes aren't covered (the user's fund was missing, as it was at Finnhub). Not tested: intraday
freshness during market hours (test ran after the close).
**Alpaca's asset list (checked 2026-11-04: docs.alpaca.markets/reference/get-v2-assets-1, and one call with the user's
paper keys):** `GET https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity` (the trading API's paper
host; live keys use `api.alpaca.markets`), same two key headers. A JSON list: 14,395 assets, 6.5 MB, 0.7 s; each with
`symbol`, `name` ("NVIDIA Corporation Common Stock"), `exchange` (NASDAQ 5,609, NYSE 2,889, ARCA 2,752, BATS 1,673, OTC 1,171,
AMEX 291, TXSE 10), `tradable` (13,494 true), `status`, `class` and trading flags. Used only for the watchlist's search.
**Finnhub (used 2026-10-04 to 2026-10-07):** free `/quote` real time; `/stock/candle` and mutual-fund
endpoints "Premium Access Required" (swagger.json). Replaced by Alpaca.

Re-check the docs before relying on any of these in a later milestone. Plans change.

## Transaction location (checked 2026-10-28)

Source: <https://plaid.com/docs/api/products/transactions/> (`/transactions/sync` added/modified transaction object).

- Each transaction has a `location` object: `address`, `city`, `region` (state; `state` in API versions 2018-05-22 and
  earlier), `postal_code` (`zip` then), `country` (ISO 3166-1 alpha-2), `lat`, `lon` (doubles), `store_number`, every
  field nullable. Not deprecated. Plaid: "Location data is provided only for transactions at physical locations, not
  for online transactions", and it's most likely filled for large retail chains.
- fintrack stores it since 2026-10-28 (migration 0049, `sync.location_fields`); history via `fintrack locations
  --backfill` (`/transactions/get`, read-only). The docs page read didn't show `/transactions/get`'s object in full;
  the backfill assumes the same `location` object (it writes only what it finds, so a missing one changes nothing).

## Transaction account_owner (checked 2026-10-29)

Source: plaid.com/docs/api/products/transactions/ (the /transactions/sync transaction object).

- Transactions carry `account_owner` (string, nullable). Plaid: it's for a **sub-account**, which "most commonly exists
  in cases where a single account is linked to multiple cards, each with its own card number and card holder name"
  (an authorized user's card), and it "is not typically populated". No transaction field gives a card number or last 4:
  an account's `mask` is the only one, so an extra card's purchases come in under the main card's account.
- fintrack stores it since 2026-10-29 (migration 0051, in `sync._upsert_transaction`; blank = NULL, kept when an update
  leaves it out) to see whether a bank fills it. Not shown anywhere yet. History: `fintrack locations --backfill
  [--item N]` fills it in with the same read-only /transactions/get fetch.
- **Production observation (2026-10-29), one card issuer:** filled on every transaction (every one fetched), and the value
  is **the last 4 of the card used**, not a name: the main card's and an authorized user's card's each appear, and on
  every row also in the bank's CSV download the value equalled that row's "Card No.". Other banks not yet checked.
