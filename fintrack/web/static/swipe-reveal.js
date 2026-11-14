// Phones: the slide between pages after a sideways swipe (ui.js sets the direction; user's request 2026-11-11). Loaded
// before the page draws (not deferred): `pagereveal` fires at the first frame, and the slide is a cross-page View
// Transition (app.css, phones only) that has to be told which way to go, or skipped. Only swipes slide; a tap on a tab
// or link arrives without one, and so does anyone who asked their phone to reduce motion.
"use strict";
window.addEventListener("pagereveal", function (event) {
  if (!event.viewTransition) return;
  event.viewTransition.ready.catch(function () { /* skipped on purpose below: not an error */ });
  event.viewTransition.finished.catch(function () {});
  var way = null;
  try { way = sessionStorage.getItem("ft-swipe"); sessionStorage.removeItem("ft-swipe"); } catch (err) { /* none */ }
  if ((way !== "next" && way !== "prev") || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    event.viewTransition.skipTransition();
    return;
  }
  event.viewTransition.types.add(way);
});
