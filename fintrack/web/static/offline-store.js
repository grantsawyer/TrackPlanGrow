// The phone's read-only copy: its storage and encryption (docs/phone-offline.md, "Locking"). Shared by the pages
// (offline-copy.js) and, from phase 3, the worker (offline-sw.js, importScripts). Exposes self.fintrackOffline.
//
// One IndexedDB database, "fintrack-offline":
//   keys   {id: "current", key, session, expires}: the AES-GCM key, NON-EXTRACTABLE (code can use it, never read it
//          out), made for one passcode session: `session` is that session's expiry (seconds, the cookie's public
//          half), so a new login makes a new key and wipes the old copy. Past `expires` the copy is wiped.
//   pages  {key, iv, data, ...}: saved pages, encrypted with that key (phase 3)
//   meta   {id, ...}: when the copy was saved, its revision (phase 3)
"use strict";
(function (scope) {
  const DB_NAME = "fintrack-offline";
  const DB_VERSION = 1;
  const STORES = ["keys", "pages", "meta"];

  // An IndexedDB request as a promise
  function done(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function openOnce() {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const name of STORES) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name, {keyPath: name === "pages" ? "key" : "id"});
      }
    };
    return done(request);
  }

  // A database left without its stores (opened elsewhere with no version: the browser makes an empty one, and the
  // upgrade above never runs on it) is deleted and made again, rather than failing for good.
  async function open() {
    const db = await openOnce();
    if (STORES.every((name) => db.objectStoreNames.contains(name))) return db;
    db.close();
    await done(indexedDB.deleteDatabase(DB_NAME));
    return openOnce();
  }

  // Runs `work(stores)` in one transaction; resolves with its result once the transaction has committed.
  async function inTransaction(names, mode, work) {
    const db = await open();
    try {
      const transaction = db.transaction(names, mode);
      const stores = Object.fromEntries(names.map((name) => [name, transaction.objectStore(name)]));
      const result = await work(stores);
      await new Promise((resolve, reject) => {
        transaction.oncomplete = resolve;
        transaction.onerror = transaction.onabort = () => reject(transaction.error);
      });
      return result;
    } finally {
      db.close();
    }
  }

  // Everything: the key, the pages and their dates.
  function wipe() {
    return inTransaction(STORES, "readwrite", (s) => Promise.all(STORES.map((name) => done(s[name].clear()))));
  }

  // The current key's record, or null (none yet, or its session has ended: then the copy is wiped).
  async function currentKey(now = Date.now()) {
    const record = await inTransaction(["keys"], "readonly", (s) => done(s.keys.get("current")));
    if (!record) return null;
    if (record.expires <= now) {
      await wipe();
      return null;
    }
    return record;
  }

  // At each page load with the copy on: a key for this passcode session. A different session (a new login) wipes
  // the old copy and makes a new key, so a copy never outlives the session it was saved in.
  async function ensureKey(session, now = Date.now()) {
    const expires = Number(session) * 1000;
    if (!session || !(expires > now)) return null;
    const current = await currentKey(now);
    if (current && current.session === String(session)) return current;
    const key = await crypto.subtle.generateKey({name: "AES-GCM", length: 256}, false, ["encrypt", "decrypt"]);
    const record = {id: "current", key, session: String(session), expires, made: now};
    await inTransaction(STORES, "readwrite", async (s) => {
      await Promise.all(STORES.map((name) => done(s[name].clear())));
      await done(s.keys.put(record));
    });
    return record;
  }

  // Text or bytes -> {iv, data}, and back. A fresh 96-bit IV per item, as AES-GCM requires.
  async function encrypt(key, content) {
    const bytes = typeof content === "string" ? new TextEncoder().encode(content) : content;
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = await crypto.subtle.encrypt({name: "AES-GCM", iv}, key, bytes);
    return {iv, data};
  }

  async function decryptBytes(key, sealed) {
    return new Uint8Array(await crypto.subtle.decrypt({name: "AES-GCM", iv: sealed.iv}, key, sealed.data));
  }

  async function decrypt(key, sealed) {
    return new TextDecoder().decode(await decryptBytes(key, sealed));
  }

  // gzip before encrypting (pages shrink about 10x; encrypted data doesn't compress), with the browser's own streams.
  async function pipe(bytes, transform) {
    return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(transform)).arrayBuffer());
  }
  const gzip = (bytes) => pipe(typeof bytes === "string" ? new TextEncoder().encode(bytes) : bytes,
                               new CompressionStream("gzip"));
  const gunzip = (bytes) => pipe(bytes, new DecompressionStream("gzip"));

  scope.fintrackOffline = {DB_NAME, open, inTransaction, done, wipe, currentKey, ensureKey, encrypt, decrypt,
                           decryptBytes, gzip, gunzip};
})(self);
