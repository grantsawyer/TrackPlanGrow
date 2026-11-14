// The phone's read-only copy (docs/phone-offline.md): on a phone's pages, <body data-offline-copy> is "on" (register
// the worker; a key for this passcode session, offline-store.js) or "off" (PHONE_OFFLINE_COPY off on the Mac: remove
// the worker and wipe the copy). The Mac's own pages don't load this script.
"use strict";
(function () {
  if (window.top !== window) return;        // a swipe's peek of the page beside (ui.js): the page itself does all this
  var body = document.body;
  // A page shown from the copy (offline-sw.js marks it): read-only. Forms that post can't reach the Mac, so they're
  // switched off (HTMX is off already, hx-disable); the range menu and links are plain GETs and keep working.
  if (body && body.dataset.offlineView !== undefined) {
    document.querySelectorAll('form[method="post" i]').forEach(function (form) {
      Array.prototype.forEach.call(form.elements, function (element) { element.disabled = true; });
    });
    // Try again: tell the worker to wait for the Mac on this one page (otherwise, while the Mac is away, pages come
    // from the copy at once), then open it.
    var again = document.querySelector(".offline-banner a");
    var worker = navigator.serviceWorker && navigator.serviceWorker.controller;
    if (again && worker) {
      again.addEventListener("click", function (event) {
        event.preventDefault();
        worker.postMessage({type: "retry"});
        window.location.href = again.href;
      });
    }
    // The Mac is back (the worker's background check got an answer): reload, which now comes live. At most twice in
    // 30 s, so a connection that comes and goes can't keep reloading the page.
    if (worker) {
      navigator.serviceWorker.addEventListener("message", function (event) {
        if (!event.data || event.data.type !== "back") return;
        var now = Date.now(), recent = [];
        try { recent = JSON.parse(sessionStorage.getItem("offline-reloads") || "[]"); } catch (err) { /* none */ }
        recent = recent.filter(function (t) { return now - t < 30000; });
        if (recent.length >= 2) return;
        recent.push(now);
        try { sessionStorage.setItem("offline-reloads", JSON.stringify(recent)); } catch (err) { /* fine */ }
        window.location.reload();
      });
      worker.postMessage({type: "status"});       // in case the answer came before this page was ready
    }
    return;
  }
  var state = body && body.dataset.offlineCopy;
  if (!("serviceWorker" in navigator) || !state) return;

  if (state === "on") {
    var registered = navigator.serviceWorker.register("/offline-sw.js", {scope: "/"});
    registered.catch(function () { /* no worker: the app works as before, just without a copy */ });
    // data-offline-session: this login's session expiry (the cookie's public half). A new login -> new key, old
    // copy wiped; storage blocked (private window) -> no copy, the app works as before.
    if (!(window.fintrackOffline && window.crypto && crypto.subtle)) return;
    fintrackOffline.ensureKey(body.dataset.offlineSession).then(function (owner) {
      if (!owner) return;
      return registered.then(function () { return navigator.serviceWorker.ready; }).then(function (registration) {
        // The worker refreshes the copy if the data changed or it's over 12 hours old (offline-sw.js); asked again
        // when the app comes back on screen (a phone pauses the app in the background, cutting a save short).
        var ask = function () { if (registration.active) registration.active.postMessage({type: "refresh"}); };
        ask();
        // the pages a sideways swipe opens (ui.js): fetched ahead by the worker, so the slide starts at once
        var neighbours = [body.dataset.swipePrev, body.dataset.swipeNext].filter(Boolean);
        if (neighbours.length && registration.active) {
          registration.active.postMessage({type: "neighbours", urls: neighbours});
        }
        document.addEventListener("visibilitychange", function () {
          if (document.visibilityState === "visible") ask();
        });
        // Chrome's periodic background sync, when Chrome allows it (installed app, used often): Chrome picks the
        // time, about daily at best, and it only finds fintrack when the Mac is awake.
        if (!("periodicSync" in registration) || !navigator.permissions) return;
        return navigator.permissions.query({name: "periodic-background-sync"}).then(function (status) {
          if (status.state === "granted") {
            return registration.periodicSync.register("fintrack-copy", {minInterval: 12 * 3600 * 1000});
          }
        });
      });
    }).catch(function () { /* no worker or no storage: the app works as before, just without a copy */ });
    return;
  }
  // "off": every worker of this site, the saved pages and their key
  navigator.serviceWorker.getRegistrations().then(function (registrations) {
    registrations.forEach(function (registration) { registration.unregister(); });
  });
  if (window.caches) {
    caches.keys().then(function (names) { names.forEach(function (name) { caches.delete(name); }); });
  }
  if (window.indexedDB) {
    try { indexedDB.deleteDatabase("fintrack-offline"); } catch (err) { /* nothing saved */ }
  }
})();
