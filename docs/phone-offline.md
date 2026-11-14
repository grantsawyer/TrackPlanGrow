# Phone: a read-only copy for when the Mac is asleep (plan)

**Status (2026-11-11): built (phases 0-6) and switched on for the user to try on their phone. The user's decisions are at the
end.** The user asked for it after learning that phone access (the gate, `remote.py`) needs the Mac awake. Nothing is
saved on a phone yet: `PHONE_OFFLINE_COPY` stays off until phase 5.

## What it is

While the phone can reach fintrack, it keeps an **encrypted copy** of the main pages. When the Mac doesn't answer
(asleep, off, home internet down), the phone shows that copy instead, read-only, under a banner:
"Copy · Tue 6:31 AM". No outside service, no app on the phone: a service worker (the browser's own
offline mechanism) inside Chrome's installed app.

What it can't do: sync, live prices, any edit, search, account / category filters or custom dates (the preset
ranges work: "What gets saved"), pages that were never saved. It's as fresh as the phone's last refresh while the Mac
was awake.

## How it works

```
online:   phone page --> gate --> fintrack (as today)
          then, in the background: service worker fetches the saved-page list --> encrypts each page --> IndexedDB
offline:  phone page --> service worker: no answer within ~4 s --> decrypted copy + banner (no passcode prompt)
```

### Locking: the same as the live app (user's decision 2026-11-11)

The live app asks for the passcode once per 30-day session; after that, opening it needs only the phone's client
certificate, which Chrome presents by itself. The copy matches that:

- **One key per passcode session** (`static/offline-store.js`, `ensureKey`): a phone's pages carry
  `data-offline-session`, the session's expiry in seconds (the cookie's public half; its signature stays in the
  HttpOnly cookie; `offline.session_id`). On each page, `offline-copy.js` compares it with the session stored beside the
  key; a different one (a new login) wipes the copy and makes a new AES-GCM 256 key with WebCrypto,
  **non-extractable** (code can use it but never read it out), stored in IndexedDB `fintrack-offline` with that expiry.
  Built this way instead of on the passcode page as first planned: there the key would be made before the server
  checked the passcode, so a wrong one would still wipe the copy. Now only a real login does, and the key's life is
  exactly the session's.
- **Saving and reading** use that key; offline the copy opens with **no passcode prompt**.
- **Forgetting is deleting the key:** the copy is unreadable at once, even before its pages are cleared. The key is
  deleted when the 30 days end (`currentKey` wipes everything past `expires`), at the next login (new key), and on the
  wipe signals below.
- Checked in headless Chrome (2026-11-11): the key can't be exported; a page round-trips; the same session keeps its
  key and pages; a new one wipes them and can't read the old data; an ended session leaves nothing.

The one difference from the live app: live pages also need the client certificate, which Android keeps in its own
key store; the copy's key sits in Chrome's storage. Someone who copies the phone's storage (unlocked or rooted phone)
could read the copy without the certificate. Otherwise the phone's lock screen protects both alike. The alternative,
a passcode to open the copy each time (key pair, private half wrapped with a PBKDF2 key from the passcode), was offered
and not chosen.

### The browser's own cache stays off

Pages keep `Cache-Control: no-store` (`web/app.py`): the only stored copy is the encrypted one in IndexedDB, which the
service worker writes itself. The rule "don't cache financial pages" becomes "no unencrypted copy on disk".

### Only phones, only when switched on

- `.env`: `PHONE_OFFLINE_COPY=on` (default off; `Settings.phone_offline_copy`).
- A phone's pages carry `data-offline-copy="on"` or `"off"` on `<body>` and load `static/offline-copy.js`
  (`offline.copy_state`, through `deps._page_context`; decided by the socket, `remote.via_phone`). "on": register the
  worker. "off": unregister every worker of the site and delete its caches and the `fintrack-offline` database: that
  is the wipe signal. The Mac's pages carry neither, so the Mac's browser never registers the worker.
- The worker script is served at `/offline-sw.js` (scope `/`, `Content-Type: text/javascript`). It holds no data, so it
  is reachable before the passcode like `/static/` (`web/remote.py` `OPEN_PREFIXES`). CSP needs no change
  (`default-src 'self'` covers workers).

### What gets saved

`GET /offline/pages` (phone listener, passcode session) returns the list to save, the data revision and when the data
was last synced: each main page at its default view (Overview, Transactions, Spending, Budgets, Cash flow, Net worth,
Accounts, Investments, Portfolio, Savings, Credit cards, Tags, Recurring), each account's page, and **every preset range**
(`spending.RANGES` except Custom: This month, Last month, 90 days, YTD, Last year, 365 days) of the pages that have one
(Transactions with all its pages of 100, Spending, Credit cards, Savings, Transfers, Net worth, Accounts; an account's
own page has no range), so the range buttons work offline. A few MB; the list has a size cap. Settings' pages stay out.

The list (`offline.page_list`): `{"path"}` for a plain page; `{"path", "default", "ranges": {preset: pages}}` for a
page with a range (`offline.RANGE_PAGES`; pages is 1 except on Transactions, from the list's own count query), plus
`static` (every file in `static/` but `link.js` and the worker). **Standard address** for finding a saved page: the
path, `range` (a preset; with one, the pages ignore From / To, so the copy does too) and `page` after the first; a
bare path means its `default`. `tests/test_offline.py` checks each default against the page as rendered.

**Filtering offline is the presets only** (user asked 2026-11-11 whether 365 saved days could be filtered to shorter
ones). The pages are built on the Mac, where the filters run as SQL, so the copy is finished pages, not data. Real
offline filtering (search, accounts, categories, custom dates) would mean saving the rows as data and rebuilding the
filters, totals and charts in phone JS: a second copy of `transactions_effective`'s rules, which CLAUDE.md forbids.
Not planned. Static
files (CSS, JS, icons, logos) are saved too, unencrypted (no data in them).

**Refreshing (built 2026-11-11, `static/offline-sw.js`).** Every phone page asks the worker (a message from
`offline-copy.js`); the worker looks at the list at most every 2 minutes and saves only when the revision changed or the
copy is over 12 hours old (so "this month" and template changes catch up). Chrome's periodic background sync
(`fintrack-copy`, asked for every 12 hours, when Chrome grants it) does the same without the 2-minute limit. One refresh
at a time: pages one by one at low priority, each gzip-compressed then encrypted; then the merchant logos those pages
show (`/logos/<hash>.png`, encrypted; they say where you shop); then the static files into the Cache API
(`fintrack-static`, unencrypted: no data). Anything not a plain 200 of the expected type (the passcode page after the
session, an error) stops it.

**The user's first test (2026-11-11) and what changed.** The first save stalled about 16 s in: the user left the app
(Android pauses a background app, its worker and requests included) and then closed the Mac's lid, so no complete copy
existed and nothing could be shown. Since then: requests run 4 at a time (`LANES`) and each has a 20 s limit
(`FETCH_LIMIT_MS`, so a frozen one can't hold the refresh); the **core** (each page's default view, first page, and
every account's page; about 30) is saved first, and **a phone with no complete copy gets the core as its copy at once**
(`partial: true` in `meta.copy`; the rest follows and replaces it whole; a partial copy is never "unchanged"); a
stopped or failed save may retry at once; and the page asks for a refresh again when the app comes back on screen.
Checked in headless Chrome: the core was usable 0.7 s after opening (the harness, on the Mac), the server cut off right
then left it as the copy (Overview, Spending opened; YTD page 3 "Not in the copy"), and one page with the server back
finished the full copy 3 s later.

**Full sets only** (once a complete copy exists): a refresh writes generation N+1 beside N (records keyed `"<gen> <standard address>"`), then
switches `meta.copy` to N+1 and deletes N. A refresh that stops drops its half-saved generation; one that finds a new
key at the end (a new login meanwhile) drops it too. `meta.attempt` records the last outcome (saved / unchanged /
stopped / no list / new session, with counts or the error; never content). Limit: 60 MB stored.

**Sizes (2026-11-11, real data):** 109 pages (Transactions 51 of them: 17 for 365 days, 15 for YTD...), about 20 MB as
sent before; hence **gzip in `web/app.py`** (GZipMiddleware, every response over 1 KB): a refresh is now about 2 MB
over mobile data, and every phone page is about 10x smaller. BREACH needs a secret in the body and repeated requests
an attacker can cause: pages hold no secret tokens, the session cookie is SameSite=Lax, and the gate needs the phone's
certificate. Opening every page doesn't change the data revision (checked), so a refresh never causes another.

**Checked in headless Chrome (2026-11-11)** with a throwaway fintrack (temp data folder, the demo world, in-memory
keyring, its phone listener on loopback): the first refresh saved 90 items in one generation (about 1 MB stored, about
10 MB of pages); stored bytes don't contain the page; the Overview decrypts and unzips to the page; a periodic sync with
nothing changed did nothing ("unchanged"); after a data change it saved a new generation and left only that one. Found
on the way: opening the database without a version (as a debugging script did) makes an empty one that the upgrade never
fills; `offline-store.js` now deletes and remakes a database missing its stores.

### Where it lives and what you see

The copy is site storage (IndexedDB) of the installed app's origin (`REMOTE_HOST:REMOTE_PORT`) inside Chrome: no file
in Downloads or Files. Clearing Chrome's data for the site, or uninstalling the app, removes it. **Same home-screen
shortcut:** Mac awake -> live app as today, the copy refreshed in the background; no answer -> the saved pages with a
thin banner ("Copy · Tue 6:31 AM",
Try again); the bottom tabs and the preset range buttons work between saved pages.

### Offline behaviour

**Built 2026-11-11 (`offline-sw.js`, reading).** A navigation races fintrack against 4 s. An answer in time (any answer,
the passcode page included) is the live page. A failure or the 4 s -> the copy, looked up by its standard address
(`standardKey`: empty fields don't count, a bare path is its default range, From / To are ignored with a preset; any
other field, Custom, or a later page than saved -> not saved). Found: decrypted, unzipped, served with the CSP saved at
refresh time, `no-store`, and marked: `<body hx-disable data-offline-view>` (no HTMX requests at all) and the banner
first in the body. Not found: "Not in the copy" (404) with Back to the page's default view, unless fintrack was only
slow, in which case it keeps waiting. A form post that can't reach fintrack gets "Read-only copy" (503). The worker
remembers which pages it served from the copy (client ids, last 50); their static files come from the Cache API and
their logos from the copy at once, and everything else they ask for gets 503 at once (no 9 s waits). On such a page,
`offline-copy.js` disables every control in post forms and skips the refresh; `ui.js` skips its revision check (it
would reload on a 503).

**Tapping between pages while the Mac is away (user's second test, 2026-11-11).** Each tap waited the full 4 s for the
sleeping Mac with nothing on screen, and a second tap cancelled the first, so pages seemed not to switch (the log showed
the taps arriving in a burst when the Mac woke). Now, once fintrack hasn't answered (`awaySince`), pages come from the
copy at once for 10 minutes (`AWAY_MS`), each with a background probe (`/revision`, 4 s); when a probe gets an answer,
copy pages are told "back" and reload, live (a copy page also asks on load, `status`; at most 2 automatic reloads in
30 s). Try again (`retry` message from `offline-copy.js`) makes that one page wait for fintrack again. Opening the app
still waits up to 4 s, the first time. Checked: taps 0.3 s each (4.3 s before); Try again with the Mac away 4.3 s then
the copy; the server back -> the copy reloads live within about 0.4 s.

Checked in headless Chrome (2026-11-11, the harness of phase 3, then the server stopped, then a stand-in that accepts
connections and never answers, then the server back): refused -> copy in about 0.4 s; hanging -> copy in 4.4 s;
`/spending` -> This month, `/spending?range=90&from=..&to=..` -> 90 days, `/transactions?page=2` -> page 2 of 365 days;
a search and Custom -> Not in the copy; 0 enabled controls in post forms (10 live); the banner styled from the cached
CSS; `/revision` answered 503 in 2 ms; back online -> the live page. No JavaScript errors or CSP violations.

As planned:

- A page load waits about 4 s for fintrack. No answer (a sleeping Mac usually means no answer at all, not a refusal)
  -> the copy. The banner has "Try again" for a slow mobile connection.
- Forms, buttons that post and HTMX requests are disabled (`ui.js`, a class on `<body>`); a post that slips through
  gets "Read-only copy" from the worker.
- A link to a page that isn't saved (another filter, page 2) shows "Not in the copy" with a link back.

### Forgetting the copy

- A new passcode login makes a new key and wipes the old copy.
- When the 30-day session ends, the key and the copy are deleted (the next login, with the Mac awake, starts afresh).
- `PHONE_OFFLINE_COPY` off -> wiped at the next visit (the page script; the worker's refresh also wipes on a 404).
- **fintrack answers with the passcode page** (the session over: 30 days, `fintrack remote passcode`, the phone
  removed) -> wiped at once (`offline-sw.js`, navigate and refresh). Checked in headless Chrome: a new passcode on the
  harness, one page opened -> 0 keys and 0 pages left; with the server then stopped, no copy (Chrome's own error).
- No "Remove this phone's copy" button (it was planned): it would only empty the copy until the next refresh, and
  it's more text on screen; the Mac-side sign-out above does the useful part.
- **Limit:** a lost phone's copy can't be erased from the Mac (removing the phone cuts its access, not its storage).
  The phone's lock screen is what protects it, and the copy deletes itself when its 30 days end.

## Phases

0. **Check on the real phone first (go / no-go). Passed 2026-11-11** (throwaway worker, removed in phase 1): Chrome
   156 on Android registered it in the installed app through the gate; its own fetch of `/revision` got 200 in 60 ms
   (client certificate and session cookie both carried); with the Mac asleep (lid closed) a page failed after 8.9 s
   with "Failed to fetch", a clean error rather than a hang, so the 4 s fallback is what decides the wait. Found on
   the way: the session cookie was `SameSite=Strict`, which Chrome leaves off when the installed app is opened from
   the home screen, so every launch asked for the passcode; now `Lax` (`web/remote.py`).
   Plan as written: A throwaway worker on the phone listener: does Chrome on Android
   register it with the phone's own certificate authority (installed by the user, `fintrack-ca.crt`)? Do the worker's
   fetches carry the client certificate without asking again? How long does a page take to fail with the Mac asleep,
   off, and with the home internet down? If the worker can't register, stop here: the alternative is an always-on Mac.
1. **Server side (built 2026-11-11):** the setting, `data-offline-copy`, `/offline-sw.js` (for now registered and in
   control, with no fetch handler, so every request goes to the network as before), `/offline/pages`, the wipe signal;
   `tests/test_offline.py` (phone listener only, passcode session required, never on the Mac's listener, `no-store`
   unchanged, the defaults, Transactions' page count).
2. **The key (built 2026-11-11):** `offline-store.js` (storage, key per session, encrypt / decrypt; the worker loads
   it in phase 3), `data-offline-session`, `remote.MIN_PASSCODE` 6 -> 10 (a passcode already set keeps working).
3. **Saving (built 2026-11-11):** the worker's refresh, compression + encryption, logos, static files, generations,
   triggers (page message, periodic background sync), size limit; gzip on the server.
4. **Reading (built 2026-11-11):** the 4 s fallback, banner, read-only page state, preset ranges, "Not in the copy".
5. **Forgetting (built 2026-11-11):** 30-day expiry (phase 2), switched-off wipe (phase 1), and the wipe when fintrack
   answers with the passcode page; no button (see "Forgetting the copy").
6. **Docs (done 2026-11-11):** README (Phone access), `docs/ui.md` Phones, `docs/decisions.md`, `docs/history.md`,
   CLAUDE.md (security policies, architecture, docs index, status).

Checking it: the phone check tool reaches `localhost`, not the phone listener, so phases 3-4 need a test switch that
offers the worker on localhost in the Sandbox only, plus DevTools' offline mode (`Network.emulateNetworkConditions`).
The last check is always on the phone with the Mac asleep.

Size: about one milestone (phase 0 is an hour or two on the phone).

## Decisions (answered by the user 2026-11-11)

1. **Locking:** the same as the live app: no passcode to open the copy; it lasts the 30-day session ("Locking" above).
2. **Pages saved:** each page's default view, every account's page and every preset range of the pages with one.
   Offline filtering beyond the presets: not planned ("What gets saved").
3. **Refresh / freshness:** on every open while the Mac is awake, plus Chrome's periodic background sync (Chrome picks
   the time; usually under a day old with the Mac awake on power). **No Web Push:** it would time a refresh to the
   06:30 sync, but on Android it always goes through Google's push service, an outside service.
4. **Passcode:** minimum raised from 6 to 10 (`remote.MIN_PASSCODE`). With locking as in 1 it guards the live login
   rather than the copy. The current passcode keeps working; set a new one (`fintrack remote passcode`) when turning
   this on.
