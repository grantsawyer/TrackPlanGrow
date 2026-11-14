// Spending page: the Transactions card shows counted spending, filtered by
//   - the point on the chart: in "days" mode (This/Last month; the cumulative lines) every transaction THROUGH that
//     day of the month; in "months" mode (any other range; monthly bars) that calendar month. Hover follows the
//     pointer; a click locks the point until the next click on the chart,
//   - the category clicked in the treemap or on a category row (click again to clear).
// Rows are rendered by the server once; this only shows and hides them (no request per mouse move).
// Chart events come from charts.js (canvas data-emit -> "fintrack:pick"). No inline script (CSP).
(function () {
  "use strict";

  // ---- pure matching rules (also run directly in Node for testing) ----

  // The point that filters right now (a day number, or "YYYY-MM"): the locked one if there is one (hover is
  // ignored meanwhile), else the hovered one.
  function activePoint(state) {
    return state.pinned !== null ? state.pinned : state.hover;
  }

  // Does a row ({day, month, category}) pass? `mode` = "days" | "months". `otherKeys` = the categories folded
  // into the treemap's gray "Other".
  function matches(row, state, otherKeys, mode) {
    const point = activePoint(state);
    if (point !== null) {
      if (mode === "months" ? row.month !== point : row.day > point) return false;     // days: through that day
    }
    if (state.category === null) return true;
    if (state.category === "_other") return otherKeys.indexOf(row.category) !== -1;
    return row.category === state.category;
  }

  // Which treemap tile a picked category lives in (a category folded into "Other" is part of that slice).
  function sliceKey(category, slots) {
    return category !== null && slots[category] === "slot-other" ? "_other" : category;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { activePoint, matches, sliceKey };
    return;
  }

  // ---- page wiring ----

  const root = document.getElementById("sp-txns");
  const mapBox = document.getElementById("sp-share");      // the treemap
  const compareBox = document.getElementById("sp-compare");
  if (!root || !compareBox) return;

  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const mode = root.dataset.mode === "months" ? "months" : "days";
  const state = { hover: null, pinned: null, category: null };
  let drawnSlice;                   // the slice last highlighted in the treemap (redraw only when it changes)

  function json(id) {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
  }
  const compare = json("sp-compare-data");
  const share = mapBox ? json("sp-share-data") : null;
  const slots = share ? share.slots : {};
  const otherKeys = Object.keys(slots).filter((key) => slots[key] === "slot-other");

  function label(category) {
    if (category === "_other") return "Other";
    const row = document.querySelector('tr[data-pick-category="' + CSS.escape(category) + '"]');
    return row ? row.dataset.label : category;
  }

  // A chart point in words: "Through day 12" (days mode) or "Sep 2026" (months mode).
  function pointText(point) {
    if (mode === "days") return "Through day " + point;
    const parts = point.split("-");
    return new Date(Number(parts[0]), Number(parts[1]) - 1, 1).toLocaleString("en-US", { month: "short", year: "numeric" });
  }

  // Chips in the card header say what is filtering; clicking one clears just that filter.
  function chip(text, clear) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.textContent = text + " ✕";
    button.setAttribute("aria-label", "Clear filter: " + text);
    button.addEventListener("click", clear);
    return button;
  }

  function update() {
    // Rows and totals.
    root.querySelectorAll("[data-pane]").forEach(function (pane) {
      let total = 0, shown = 0;
      pane.querySelectorAll("tbody tr[data-day]").forEach(function (row) {
        const show = matches({ day: parseInt(row.dataset.day, 10), month: row.dataset.month, category: row.dataset.category },
                             state, otherKeys, mode);
        row.hidden = !show;
        if (show) { shown += 1; total += parseInt(row.dataset.amount, 10); }
      });
      // a date's heading (phones show them) goes with its last shown row
      pane.querySelectorAll("tr[data-date-head]").forEach(function (head) {
        head.hidden = !pane.querySelector('tbody tr[data-date="' + head.dataset.dateHead + '"]:not([hidden])');
      });
      pane.querySelector(".empty-row").hidden = shown > 0;
      pane.querySelector("[data-total]").textContent = money.format(total / 100);
    });

    // Chips: a locked point can be cleared from here; a hovered one (temporary) is only shown.
    const chips = root.querySelector("[data-chips]");
    chips.replaceChildren();
    const point = activePoint(state);
    if (point !== null) {
      const text = pointText(point);
      if (state.pinned === null) {
        const hint = document.createElement("span");
        hint.className = "chip hover-chip";
        hint.textContent = text;
        chips.appendChild(hint);
      } else {
        chips.appendChild(chip(text, function () { state.pinned = null; update(); }));
      }
    }
    if (state.category !== null) {
      chips.appendChild(chip(label(state.category), function () { state.category = null; update(); }));
    }

    // The picked category highlighted in the table, and its tile in the treemap (the other tiles dim).
    const picked = sliceKey(state.category, slots);
    document.querySelectorAll("tr[data-pick-category]").forEach(function (row) {
      const on = state.category !== null && sliceKey(row.dataset.pickCategory, slots) === picked &&
                 (state.category === "_other" || row.dataset.pickCategory === state.category);
      row.querySelector(".cat-pick").setAttribute("aria-pressed", on ? "true" : "false");
      row.classList.toggle("picked", on);
    });
    const chart = mapBox && window.Chart ? Chart.getChart(mapBox) : null;
    if (chart && share && picked !== drawnSlice) {
      drawnSlice = picked;
      mapBox.dataset.picked = picked || "";
      chart.update();
    }
  }

  function toggleCategory(key) {
    state.category = state.category === key ? null : key;
    update();
  }

  document.addEventListener("fintrack:pick", function (event) {
    const { type, index } = event.detail;
    if (event.target === compareBox) {
      const point = index === null ? null : (mode === "months" ? compare.months[index] : compare.days[index]);
      if (type === "hover") { if (state.hover !== point) { state.hover = point; update(); } }     // mousemove fires constantly
      else if (type === "click") {            // locks the hovered point; the next click unlocks (hover resumes)
        state.pinned = state.pinned !== null ? null : point;
        update();
      }
    } else if (event.target === mapBox && type === "click" && share) {
      toggleCategory(share.slices[index].key);
    }
  });

  document.addEventListener("click", function (event) {
    const row = event.target.closest("tr[data-pick-category]");     // anywhere on the row, name included
    if (row) toggleCategory(row.dataset.pickCategory);
  });

  update();
})();
