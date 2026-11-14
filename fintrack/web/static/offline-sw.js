// The phone's read-only copy: its service worker (docs/phone-offline.md; fintrack/web/offline.py).
//
// Saving (phase 3): while fintrack answers, the worker keeps an encrypted copy of the pages GET /offline/pages lists.
// A refresh runs when a page asks (at most every 2 minutes) or when Chrome's periodic background sync fires, and only
// if the data changed (revision) or the copy is over 12 hours old. Each refresh writes a new generation beside the
// old one and switches to it in one transaction, so a dropped connection or a new login never leaves half a copy;
// the one exception is a phone with no complete copy yet, which gets the core pages as its copy as soon as they're
// saved (save(), 2026-11-11: the user's first test lost the Mac before a whole first copy was saved).
//
// Reading (phase 4): a page opened while fintrack doesn't answer within 4 s (or fails) comes from the copy, marked
// read-only with a banner; its CSS, scripts and logos come from the copy too, and anything else it asks for gets an
// immediate "read-only" answer. Pages fintrack answers in time are untouched.
"use strict";

importScripts("/static/offline-store.js");
const S = self.fintrackOffline;

const STATIC_CACHE = "fintrack-static";       // CSS, scripts, icons: no data in them, so not encrypted
const MAX_AGE_MS = 12 * 3600 * 1000;          // refresh at least this often, even when the data didn't change
const CHECK_EVERY_MS = 2 * 60 * 1000;         // pages ask on every load; look at the list at most this often
const MAX_BYTES = 60 * 1024 * 1024;           // stored (compressed, encrypted); over it the refresh gives up
const LOGO = /\/logos\/[0-9a-f]{64}\.png/g;   // merchant logos the pages show (web/transactions.py logo_file)
const LANES = 4;                              // requests at a time while saving
const FETCH_LIMIT_MS = 20000;                 // one request; a phone that pauses the app freezes requests mid-way

let running = null;     // the refresh in progress, if any (one at a time)
let lastCheck = 0;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => event.waitUntil((async () => {
  await self.clients.claim();
  await caches.delete("offline-spike-results");   // the phase 0 test worker's leftovers (2026-11-11)
})()));

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "refresh") event.waitUntil(refresh(false));
  if (event.data && event.data.type === "retry") retryNext = true;      // the banner's Try again (offline-copy.js)
  if (event.data && event.data.type === "neighbours") fetchAhead(event.data.urls);   // pages a swipe may open
  // A page shown from the copy asks on load whether fintrack is back (the probe may have answered before it was ready)
  if (event.data && event.data.type === "status" && !awaySince && event.source) event.source.postMessage({type: "back"});
});

self.addEventListener("periodicsync", (event) => {
  if (event.tag !== "fintrack-copy") return;
  lastCheck = 0;                       // Chrome's own schedule: not held back by the 2-minute limit
  event.waitUntil(refresh(false));
});

// The standard address a saved page is found by (docs/phone-offline.md, "What gets saved").
function pageKey(path, range, page) {
  return path + (range ? `?range=${range}` : "") + (page > 1 ? `&page=${page}` : "");
}

// The list -> every address to fetch, with its standard key (the fetch address is the key itself): `core` (each
// page as it opens: its default range, first page; every account's page) first, then `rest` (the other ranges and
// pages). A phone with no copy yet gets the core as its copy as soon as it's saved (save()).
function addresses(pages) {
  const core = [], rest = [];
  for (const entry of pages) {
    if (!entry.ranges) { core.push(pageKey(entry.path)); continue; }
    for (const [range, count] of Object.entries(entry.ranges)) {
      for (let page = 1; page <= count; page++) {
        (range === entry.default && page === 1 ? core : rest).push(pageKey(entry.path, range, page));
      }
    }
  }
  return {core, rest};
}

// Runs work(item) over items, LANES at a time; after a failure the lanes take nothing more.
async function inParallel(items, work) {
  let next = 0, failed = false;
  await Promise.all(Array.from({length: Math.min(LANES, items.length)}, async () => {
    while (!failed && next < items.length) {
      try { await work(items[next++]); } catch (err) { failed = true; throw err; }
    }
  }));
}

function getMeta(id) {
  return S.inTransaction(["meta"], "readonly", (s) => S.done(s.meta.get(id)));
}

function putRecord(record) {
  return S.inTransaction(["pages"], "readwrite", (s) => S.done(s.pages.put(record)));
}

// Deletes the saved items of every generation but `keep` (keep = null: of generation `drop` only).
function prune(keep, drop = null) {
  return S.inTransaction(["pages"], "readwrite", (s) => new Promise((resolve, reject) => {
    const request = s.pages.openCursor();
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return resolve();
      const gen = cursor.value.gen;
      if (keep !== null ? gen !== keep : gen === drop) cursor.delete();
      cursor.continue();
    };
  }));
}

// A fetch from fintrack that must be the real thing: not the passcode page (session over), not an error.
async function fetchOk(url, type) {
  const response = await fetch(url, {cache: "no-store", credentials: "same-origin", priority: "low",
                                     signal: AbortSignal.timeout(FETCH_LIMIT_MS)});
  if (!response.ok || response.redirected || !(response.headers.get("content-type") || "").startsWith(type)) {
    throw new Error(`${url}: ${response.status}${response.redirected ? " (redirected)" : ""}`);
  }
  return response;
}

function refresh(force) {
  if (running) return running;
  if (!force && Date.now() - lastCheck < CHECK_EVERY_MS) return Promise.resolve("checked recently");
  lastCheck = Date.now();
  running = save(force).catch((err) => record("failed", String(err))).then((outcome) => {
    if (["stopped", "failed", "no list"].includes(outcome)) lastCheck = 0;   // try again at the next page or return
    return outcome;
  }).finally(() => { running = null; });
  return running;
}

// The last attempt's outcome, for phase 4's banner and for checking (never page content).
function record(outcome, detail = "") {
  return S.inTransaction(["meta"], "readwrite", (s) =>
    S.done(s.meta.put({id: "attempt", outcome, detail, when: Date.now()}))).then(() => outcome);
}

async function save(force) {
  const owner = await S.currentKey();
  if (!owner) return record("no key");
  let listing, csp;
  try {
    const probe = await fetch("/offline/pages", {cache: "no-store", credentials: "same-origin",
                                                 signal: AbortSignal.timeout(FETCH_LIMIT_MS)});
    // fintrack answered: the copy switched off (404) or the session over (sent to the passcode page) -> wipe it
    if (probe.status === 404 || (probe.redirected && new URL(probe.url).pathname === "/remote-login")) {
      await S.wipe();
      return record("wiped", probe.status === 404 ? "copy off" : "session over");
    }
    if (!probe.ok || probe.redirected || !(probe.headers.get("content-type") || "").startsWith("application/json")) {
      throw new Error(`/offline/pages: ${probe.status}`);
    }
    const response = probe;
    csp = response.headers.get("content-security-policy") || "default-src 'self'";   // served again with the copy
    listing = await response.json();
  } catch (err) {
    return record("no list", String(err));     // copy off, session over, or the Mac asleep
  }
  const copy = await getMeta("copy");
  if (!force && copy && !copy.partial && copy.revision === listing.revision && Date.now() - copy.saved < MAX_AGE_MS) {
    return record("unchanged");
  }

  const gen = (copy ? copy.gen : 0) + 1;
  // No copy at all yet (or only a first part of one): the core becomes the copy as soon as it's saved, so a phone
  // that loses the Mac a few seconds in still has the main pages. A complete copy is only ever replaced whole.
  const early = !copy || copy.partial;
  const pages = listing.pages.map(({path, default: fallback, ranges}) => ({path, default: fallback, ranges}));
  let bytes = 0, count = 0, published = false;
  const logos = new Set(), logosSaved = new Set();
  const keep = async (key, type, content) => {
    const sealed = await S.encrypt(owner.key, content);
    bytes += sealed.data.byteLength;
    if (bytes > MAX_BYTES) throw new Error(`over ${MAX_BYTES} bytes`);
    await putRecord({key: `${gen} ${key}`, gen, type, iv: sealed.iv, data: sealed.data});
    count++;
  };
  const savePage = async (key) => {
    const html = await (await fetchOk(key, "text/html")).text();
    for (const logo of html.matchAll(LOGO)) logos.add(logo[0]);
    await keep(key, "text/html", await S.gzip(html));              // gzip, then encrypt
  };
  const saveLogos = () => inParallel([...logos].filter((logo) => !logosSaved.has(logo)), async (logo) => {
    logosSaved.add(logo);
    const png = new Uint8Array(await (await fetchOk(logo, "image/png")).arrayBuffer());
    await keep(logo, "image/png", png);                            // PNGs are compressed already
  });
  // Point the copy at this generation, unless a new login made a new key meanwhile (that wiped everything already).
  const publish = async (partial) => {
    const still = await S.currentKey();
    if (!still || still.session !== owner.session) throw new Error("new session");
    await S.inTransaction(["meta"], "readwrite", (s) => S.done(s.meta.put({
      id: "copy", gen, revision: listing.revision, synced: listing.synced, saved: Date.now(), pages, bytes, count, csp,
      partial,
    })));
    published = true;
  };

  const {core, rest} = addresses(listing.pages);
  try {
    await inParallel(core, savePage);
    await saveLogos();
    const cache = await caches.open(STATIC_CACHE);
    await inParallel(listing.static, async (url) => cache.put(url, await fetchOk(url, "")));
    if (early) await publish(true);
    await inParallel(rest, savePage);
    await saveLogos();
    await publish(false);
  } catch (err) {
    if (String(err).includes("new session")) return record("new session");
    if (!published) await prune(null, gen);                        // drop the half-saved generation
    return record("stopped", String(err));                         // a first part already shown stays
  }
  await prune(gen);
  return record("saved", `${count} items, ${bytes} bytes`);
}


// ------------------------------------------------------------------------------------------------ reading (phase 4)

const WAIT_MS = 4000;                         // how long a page waits for fintrack before the copy is shown
const AWAY_MS = 10 * 60 * 1000;               // after fintrack didn't answer: pages come from the copy at once this long
let awaySince = 0;                            // when fintrack last didn't answer (0: it did)
let retryNext = false;                        // Try again: the next page waits for fintrack even while away
const TIMEOUT = Symbol("timeout"), FAILED = Symbol("failed");
const offlineClients = new Map();             // pages shown from the copy (client id -> when), newest last
const IGNORED = new Set(["range", "page", "from", "to"]);   // with a preset range, the pages ignore From / To too

// ------------------------------------------------------------- swiping between pages (user's requests 2026-11-11)
// A page asks the worker to fetch the pages beside it (offline-copy.js, from <body data-swipe-prev / -next>); a swipe
// to one then gets it at once from memory, so the slide starts as the finger lifts instead of after a load. Kept a
// minute; anything that changes data (a request that isn't a GET) drops them all, so a swipe never shows a page from
// before an edit. And the app's own CSS, scripts and icons (no data) come from the worker's store, refreshed in the
// background: pages are no-store, so without this a phone fetched them all again on every page.
const AHEAD_MS = 60 * 1000;
const ahead = new Map();                      // path + query -> {at, response: Promise<Response | null>}

function fetchAhead(urls) {
  const now = Date.now();
  for (const [key, item] of ahead) if (now - item.at > AHEAD_MS) ahead.delete(key);
  for (const url of urls || []) {
    if (!url || ahead.has(url)) continue;
    const response = fetch(url, {credentials: "same-origin", cache: "no-store", priority: "low"})
      .then((r) => (r.ok && !r.redirected && (r.headers.get("content-type") || "").startsWith("text/html") ? r : null))
      .catch(() => null);
    ahead.set(url, {at: now, response});
  }
}

// A copy each time: the peek of a page beside (ui.js) and then the page itself both use it within its minute.
async function takeAhead(url) {
  const key = url.pathname + url.search;
  const item = ahead.get(key);
  if (!item) return null;
  if (Date.now() - item.at > AHEAD_MS) { ahead.delete(key); return null; }
  const response = await item.response;
  return response ? response.clone() : null;
}

async function staticFile(event) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(event.request);
  const fresh = fetch(event.request).then((response) => {
    if (response.ok) cache.put(event.request, response.clone()).catch(() => {});
    return response;
  });
  if (cached) {
    event.waitUntil(fresh.catch(() => {}));    // next time, the newer one
    return cached;
  }
  return fresh;
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (event.request.method !== "GET") ahead.clear();          // data may change: no page fetched before it
  if (event.request.mode === "navigate" && event.request.method === "GET" && ahead.has(url.pathname + url.search)) {
    event.respondWith(takeAhead(url).then((r) => r || navigate(event)));
    return;
  }
  if (event.request.method === "GET" && url.pathname.startsWith("/static/") && !offlineClients.has(event.clientId)) {
    event.respondWith(staticFile(event));
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(navigate(event));
  } else if (offlineClients.has(event.clientId)) {
    event.respondWith(forCopyPage(event.request, url));
  }
  // anything else: no answer from the worker, so the request goes to the network as without it
});

function shownFromCopy(clientId) {
  if (!clientId) return;
  offlineClients.set(clientId, Date.now());
  while (offlineClients.size > 50) offlineClients.delete(offlineClients.keys().next().value);
}

// Is fintrack answering again? A small request in the background while pages come from the copy at once.
async function probe() {
  try {
    const response = await fetch("/revision", {cache: "no-store", signal: AbortSignal.timeout(WAIT_MS)});
    if (!(response.ok || response.redirected)) return;
    awaySince = 0;
    // Pages showing the copy reload, now live (offline-copy.js)
    for (const client of await self.clients.matchAll({type: "window"})) {
      if (offlineClients.has(client.id)) client.postMessage({type: "back"});
    }
  } catch (err) { /* still away */ }
}

async function navigate(event) {
  const request = event.request;
  // Away (fintrack didn't answer a moment ago): tapping between pages comes from the copy at once, instead of
  // waiting 4 s each time (the user's test, 2026-11-11); a background probe notices when fintrack is back.
  const retry = retryNext;
  retryNext = false;
  if (request.method === "GET" && !retry && awaySince && Date.now() - awaySince < AWAY_MS) {
    const copy = await openCopy();
    if (copy) {
      event.waitUntil(probe());
      shownFromCopy(event.resultingClientId);
      return (await savedPage(copy, request.url)) || notInCopy(copy, request.url);
    }
  }
  const network = fetch(request);
  network.catch(() => {});                    // its failure is handled below; no "unhandled" noise
  if (request.method !== "GET") return network.catch(() => readOnly());   // a form post while the Mac is away
  const first = await Promise.race([network.then((r) => r, () => FAILED),
                                    new Promise((resolve) => setTimeout(() => resolve(TIMEOUT), WAIT_MS))]);
  if (first !== TIMEOUT && first !== FAILED) {                 // fintrack answered in time: the live page
    awaySince = 0;
    // The passcode page from fintrack itself means this phone's session is over (30 days, a new passcode on the Mac,
    // the phone removed): the copy goes with it. The next login makes a new key and a new copy anyway.
    if (new URL(first.url || request.url).pathname === "/remote-login") event.waitUntil(S.wipe().catch(() => {}));
    return first;
  }
  awaySince = Date.now();
  const copy = await openCopy();
  if (!copy) return network;                  // no copy: wait or fail exactly as without the worker
  const page = await savedPage(copy, request.url);
  if (page) {
    shownFromCopy(event.resultingClientId);
    return page;
  }
  // Not in the copy: a slow fintrack may still answer; a failed one won't.
  const notSaved = () => { shownFromCopy(event.resultingClientId); return notInCopy(copy, request.url); };
  return first === TIMEOUT ? network.catch(notSaved) : notSaved();
}

async function openCopy() {
  try {
    const owner = await S.currentKey();        // null once the session has ended (and then everything is wiped)
    const meta = owner && await getMeta("copy");
    return meta ? {owner, meta} : null;
  } catch (err) {
    return null;
  }
}

// An address -> the standard address it was saved under, or null (a search, a filter, Custom dates, a later page
// than saved, a page not in the list). Empty fields don't count: the filter forms send them.
function standardKey(href, pages) {
  const url = new URL(href);
  const entry = pages.find((p) => p.path === url.pathname);
  if (!entry) return null;
  const given = [...url.searchParams].filter(([, value]) => value !== "");
  if (!entry.ranges) return given.length ? null : entry.path;
  if (given.some(([name]) => !IGNORED.has(name))) return null;
  const params = url.searchParams;
  const range = params.get("range") || (params.get("from") || params.get("to") ? "custom" : entry.default);
  if (!Object.prototype.hasOwnProperty.call(entry.ranges, range)) return null;
  const page = Number(params.get("page") || 1);
  if (!Number.isInteger(page) || page < 1 || page > entry.ranges[range]) return null;
  return pageKey(entry.path, range, page);
}

function getRecord(copy, key) {
  return S.inTransaction(["pages"], "readonly", (s) => S.done(s.pages.get(`${copy.meta.gen} ${key}`)));
}

async function savedPage(copy, href) {
  const key = standardKey(href, copy.meta.pages);
  const record = key && await getRecord(copy, key);
  if (!record) return null;
  try {
    const html = new TextDecoder().decode(await S.gunzip(await S.decryptBytes(copy.owner.key, record)));
    return htmlResponse(copy, markAsCopy(html, banner(copy, href)));
  } catch (err) {
    return null;                               // can't be read (another key): treat as not saved
  }
}

// When the data was last synced, as on the banner: "Tue 6:31 AM", with the date once it's a week old.
function copyDate(meta) {
  const when = meta.synced ? new Date(meta.synced.replace(" ", "T") + "Z") : new Date(meta.saved);
  const old = Date.now() - when.getTime() > 6 * 86400 * 1000;
  return when.toLocaleString("en-US", old ? {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}
                                          : {weekday: "short", hour: "numeric", minute: "2-digit"});
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function banner(copy, href) {
  const url = new URL(href);
  return `<div class="offline-banner" role="status"><span>Copy · ${escapeHtml(copyDate(copy.meta))}</span>` +
         `<a href="${escapeHtml(url.pathname + url.search)}">Try again</a></div>`;
}

// The saved page, marked: hx-disable switches off every HTMX request (edit panels, row actions), and
// data-offline-view tells the page scripts (offline-copy.js disables post forms; ui.js skips its revision check).
function markAsCopy(html, bannerHtml) {
  const start = html.indexOf("<body");
  const end = start < 0 ? -1 : html.indexOf(">", start);
  if (end < 0) return html;
  return html.slice(0, start) + "<body hx-disable data-offline-view" + html.slice(start + 5, end + 1) + bannerHtml +
         html.slice(end + 1);
}

function htmlResponse(copy, html, status = 200) {
  return new Response(html, {status, headers: {
    "content-type": "text/html; charset=utf-8",
    "content-security-policy": copy.meta.csp,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  }});
}

function notInCopy(copy, href) {
  const url = new URL(href);
  const entry = copy.meta.pages.find((p) => p.path === url.pathname);
  const back = entry ? entry.path : "/";
  return htmlResponse(copy, `<!doctype html><html lang="en"><head><meta charset="utf-8">` +
    `<meta name="viewport" content="width=device-width, initial-scale=1"><title>Track Plan Grow</title>` +
    `<link rel="stylesheet" href="/static/app.css"><link rel="icon" href="/static/icons/favicon.svg" type="image/svg+xml">` +
    `<script src="/static/offline-copy.js" defer></script>` +
    `</head><body data-offline-view>${banner(copy, href)}` +
    `<main class="offline-missing"><h1>Not in the copy</h1><p><a href="${escapeHtml(back)}">Back</a></p></main>` +
    `</body></html>`, 404);
}

function readOnly() {
  return new Response("Read-only copy", {status: 503, headers: {"content-type": "text/plain; charset=utf-8",
                                                                "cache-control": "no-store"}});
}

// What a page shown from the copy asks for: its static files and logos from the copy; anything else (an edit, the
// revision check, a chart's data) gets "read-only" at once instead of waiting on a Mac that doesn't answer.
async function forCopyPage(request, url) {
  if (request.method === "GET" && url.pathname.startsWith("/static/")) {
    const cached = await (await caches.open(STATIC_CACHE)).match(url.pathname);
    if (cached) return cached;
  }
  if (request.method === "GET" && /^\/logos\/[0-9a-f]{64}\.png$/.test(url.pathname)) {
    const copy = await openCopy();
    const record = copy && await getRecord(copy, url.pathname);
    if (record) {
      try {
        return new Response(await S.decryptBytes(copy.owner.key, record),
                            {headers: {"content-type": "image/png", "cache-control": "no-store"}});
      } catch (err) { /* fall through */ }
    }
  }
  return readOnly();
}
