// Keep your place after a save (user's requests 2026-10-25). Loaded from base.html's <head> WITHOUT defer, so it runs
// before the page is drawn.
//
// A POST form inside [data-keep-scroll] reloads the page through a redirect, which would land at the top. On submit we
// remember the scroll position and where the area sat. When the page comes back, <html> gets .keep-scroll-pending
// (app.css hides the page) until we have scrolled back, shifted by however much the area moved, so the page never
// shows at the top first; the browser keeps showing the previous page meanwhile. An error message is left in view
// instead. app.css reveals the page after 1 s even if this script fails. sessionStorage is a convenience only:
// without it the page simply opens at the top.
(function () {
  const KEY = "ft_keep_scroll";
  const root = document.documentElement;
  const docTop = (el) => el.getBoundingClientRect().top + window.scrollY;

  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY);
  } catch (err) {
    saved = null;
  }
  if (saved && saved.path === location.pathname) {
    root.classList.add("keep-scroll-pending");
    document.addEventListener("DOMContentLoaded", function () {
      // the same area the save came from (its place among the page's areas; 2026-11-04: the Overview has two), else the first
      const areas = document.querySelectorAll("[data-keep-scroll]");
      const area = areas[saved.index || 0] || areas[0];
      if (area && !document.querySelector(".flash.bad")) window.scrollTo(0, saved.y + docTop(area) - saved.top);
      root.classList.remove("keep-scroll-pending");
    });
  }

  function remember(area) {
    try {
      const index = Array.prototype.indexOf.call(document.querySelectorAll("[data-keep-scroll]"), area);
      sessionStorage.setItem(KEY, JSON.stringify({ path: location.pathname, y: window.scrollY, top: docTop(area), index: index }));
    } catch (err) {
      /* storage unavailable: the page opens at the top */
    }
  }
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const area = form.closest && form.closest("[data-keep-scroll]");
    if (!area || (form.getAttribute("method") || "").toLowerCase() !== "post") return;
    remember(area);
  });
  // ...and a link marked data-keep-place there that reloads the same page another way (a tag card's pencil and Done,
  // which open and close its edit mode; user's request 2026-11-04)
  document.addEventListener("click", function (event) {
    const link = event.target.closest && event.target.closest("a[data-keep-place]");
    const area = link && link.closest("[data-keep-scroll]");
    if (area) remember(area);
  });
  // An edit panel's save in such an area comes back as a reload (the review card, user's request 2026-11-01).
  document.addEventListener("htmx:beforeRequest", function (event) {
    const elt = event.detail.elt;
    const area = elt.closest && elt.closest("[data-keep-scroll]");
    if (area && event.detail.requestConfig.verb === "post" && elt.closest("[data-close-panel], .rule-form")) remember(area);
  });
})();
