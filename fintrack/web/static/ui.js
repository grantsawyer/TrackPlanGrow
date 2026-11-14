// Small behaviors shared by every page. Loaded from base.html.
//
// The CSP forbids inline scripts, inline event handlers (onchange="..."), and
// inline style attributes, so those jobs are done here instead.
(function () {
  "use strict";

  // ---- Shared chart helpers (chart scripts load after this file) ----

  // A round step (1, 2, 2.5 or 5 x 10^n) giving about `target` intervals across [min, max].
  function niceStep(min, max, target) {
    const raw = (max - min) / (target || 5);
    if (!(raw > 0)) return 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    // The candidate whose interval count is closest to the target.
    let best = magnitude;
    [1, 2, 2.5, 5, 10].forEach(function (m) {
      const c = m * magnitude;
      if (Math.abs((max - min) / c - (target || 5)) < Math.abs((max - min) / best - (target || 5))) best = c;
    });
    return best;
  }

  // Chart.js `afterBuildTicks` for value axes with an explicit min/max (our 5%
  // padding rule): labels on round multiples of a nice step inside the range.
  // Chart.js alone either printed the odd padded ends ("$502.8") or, with
  // maxTicksLimit, chose a step so large that a narrow axis got a single label (measured).
  // On a phone (≤640px; docs/phone-plan.md phase 3) about 4 labels: the charts are shorter there.
  const phoneAxes = window.matchMedia("(max-width: 640px)");
  // A chart's own maxTicksLimit (e.g. a phone's small cards, 3) asks for fewer.
  function roundTicks(axis) {
    const limit = axis.options && axis.options.ticks && axis.options.ticks.maxTicksLimit;
    const step = niceStep(axis.min, axis.max, limit ? Math.max(limit - 1, 1) : phoneAxes.matches ? 4 : 5);
    const ticks = [];
    for (let v = Math.ceil(axis.min / step) * step; v <= axis.max + step * 1e-9; v += step) {
      ticks.push({ value: Math.abs(v) < step * 1e-9 ? 0 : v });
    }
    axis.ticks = ticks;
  }

  // Money axis labels with just enough precision to tell neighbors apart:
  // "$1.38M / $1.40M", "$2.5K / $5K", "$1,234.2K" on a very tight axis.
  // Use as Chart.js `ticks.callback`; it gets (value, index, ticks).
  function axisMoney(value, index, ticks) {
    if (value === 0) return "$0";
    const values = ticks.map(function (t) { return t.value; });
    const top = Math.max.apply(null, values.map(Math.abs));
    const step = values.length > 1 ? Math.abs(values[1] - values[0]) : top;
    // Largest unit (M, K, none) in which one step is still >= 0.001 of a unit.
    const unit = top >= 1e6 && step >= 1e3 ? 1e6 : top >= 1e3 && step >= 1 ? 1e3 : 1;
    const suffix = unit === 1e6 ? "M" : unit === 1e3 ? "K" : "";
    // Fewest decimals (0..3) that show every tick exactly in that unit.
    let digits = 0;
    while (digits < 3 && values.some(function (v) {
      const scaled = (v / unit) * Math.pow(10, digits);
      return Math.abs(scaled - Math.round(scaled)) > 1e-6;
    })) digits += 1;
    const number = (Math.abs(value) / unit).toLocaleString("en-US", {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    });
    return (value < 0 ? "-$" : "$") + number + suffix;
  }
  // Date axis labels: "2026-10-03" -> "Oct 3", "2026-10" -> "Oct 2026"; other
  // years keep the year ("Oct 3, 2025"). A Chart.js category-axis `ticks.callback`
  // (a plain function: Chart.js passes the axis as `this`).
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function dateLabel(text) {
    const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(String(text));
    if (!m) return text;
    const month = MONTHS[parseInt(m[2], 10) - 1];
    if (!m[3]) return month + " " + m[1];
    const day = month + " " + parseInt(m[3], 10);
    return parseInt(m[1], 10) === new Date().getFullYear() ? day : day + ", " + m[1];
  }
  function axisDate(value) {
    return dateLabel(this.getLabelForValue(value));
  }
  window.fintrack = { axisMoney: axisMoney, roundTicks: roundTicks, axisDate: axisDate, dateLabel: dateLabel };

  // ---- Sortable tables: <table data-sortable data-sort-col="pct" data-sort-dir="desc">
  // Header buttons <button class="sort" data-sort="pct"> sort by each row's data-sort-pct
  // (numbers, or text with data-sort-type="text"). A data-sort-ranged column reads the value
  // for the table's current range (data-sort-pct-90 ...): tables with data-range-table follow
  // a chart's range buttons (charts.js sends "fintrack:range"). Empty values always sort last.
  // Click a header to sort by it (text A-Z, numbers highest first); click again to reverse.
  function sortTable(table) {
    const col = table.dataset.sortCol, dir = table.dataset.sortDir === "asc" ? 1 : -1;
    const button = table.querySelector('button.sort[data-sort="' + col + '"]');
    if (!button) return;
    const attr = "data-sort-" + col + (button.hasAttribute("data-sort-ranged") ? "-" + table.dataset.range : "");
    const text = button.dataset.sortType === "text";
    const body = table.tBodies[0];
    const rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (a, b) {
      const x = a.getAttribute(attr), y = b.getAttribute(attr);
      if (!x || !y) return (!x) - (!y);             // empty last, either direction
      if (text) return dir * x.localeCompare(y, undefined, { sensitivity: "base" });
      return dir * (parseFloat(x) - parseFloat(y));
    });
    rows.forEach(function (row) { body.appendChild(row); });
    table.querySelectorAll("button.sort").forEach(function (b) {
      b.closest("th").setAttribute("aria-sort", b === button ? (dir === 1 ? "ascending" : "descending") : "none");
    });
  }
  document.addEventListener("click", function (event) {
    const button = event.target.closest("table[data-sortable] button.sort");
    if (!button) return;
    const table = button.closest("table");
    if (table.dataset.sortCol === button.dataset.sort) {
      table.dataset.sortDir = table.dataset.sortDir === "asc" ? "desc" : "asc";
    } else {
      table.dataset.sortCol = button.dataset.sort;
      table.dataset.sortDir = button.dataset.sortType === "text" ? "asc" : "desc";
    }
    sortTable(table);
  });
  // ---- Phones' Select mode (Transactions; user's design 2026-11-05): the list's Select button shows the boxes (app.css
  // .selecting) and tapping a row ticks it; Done hides them again and clears the selection.
  document.addEventListener("click", function (event) {
    const button = event.target.closest && event.target.closest("[data-select-mode]");
    if (!button) return;
    const wrap = button.closest(".txns-wrap");
    const on = !wrap.classList.contains("selecting");
    wrap.classList.toggle("selecting", on);
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Done" : "Select";
    const clear = !on && document.querySelector("[data-bulk-clear]");
    if (clear) clear.click();
  });

  // ---- "Select all" boxes for a set of row boxes: <input type="checkbox" data-check-all="<class>"> ticks or unticks
  // every box with that class (the Categories to review card, 2026-11-14).
  document.addEventListener("change", function (event) {
    const all = event.target.closest("[data-check-all]");
    if (!all) return;
    document.querySelectorAll("input." + all.dataset.checkAll).forEach(function (b) { b.checked = all.checked; });
  });

  // ---- Group edit on Transactions (milestone 14): row checkboxes (.row-select, form="bulk-form"),
  // a "select this page" box, and #bulk-form: shown with a count once anything is checked.
  // "Select all N matching" sets all=1 (the server re-runs the filters) and asks before applying.
  function bulkState() {
    const form = document.getElementById("bulk-form");
    if (!form) return;
    const boxes = Array.prototype.slice.call(document.querySelectorAll(".row-select"));
    const checked = boxes.filter(function (b) { return b.checked; }).length;
    const all = form.elements.all.value === "1";
    form.hidden = !all && checked === 0;
    form.querySelector(".bulk-count").textContent = all
      ? "All " + form.dataset.bulkTotal + " matching selected" : checked + " selected";
    const page = document.querySelector("[data-select-page]");
    if (page) {
      page.checked = checked > 0 && checked === boxes.length;
      page.indeterminate = checked > 0 && checked < boxes.length;
    }
  }
  document.addEventListener("change", function (event) {
    const form = document.getElementById("bulk-form");
    if (!form) return;
    if (event.target.matches("[data-select-page]")) {
      document.querySelectorAll(".row-select").forEach(function (b) { b.checked = event.target.checked; });
    }
    if (event.target.matches(".row-select, [data-select-page]")) {
      form.elements.all.value = "";               // a manual change leaves "all matching"
      bulkState();
    }
  });
  document.addEventListener("click", function (event) {
    const form = document.getElementById("bulk-form");
    if (!form) return;
    if (event.target.closest("[data-bulk-all]")) {
      form.elements.all.value = "1";
      document.querySelectorAll(".row-select").forEach(function (b) { b.checked = true; });
      bulkState();
    } else if (event.target.closest("[data-bulk-clear]")) {
      form.elements.all.value = "";
      document.querySelectorAll(".row-select, [data-select-page]").forEach(function (b) { b.checked = false; });
      bulkState();
    }
  });
  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (form.id !== "bulk-form" || form.elements.all.value !== "1") return;
    // The Link button (purpose accounts) posts elsewhere: say what it will do.
    const linking = event.submitter && (event.submitter.getAttribute("formaction") || "").indexOf("bulk-purpose") >= 0;
    const what = linking ? "Link all " + form.dataset.bulkTotal + " matching transactions to the chosen purpose?"
      : "Change the category of all " + form.dataset.bulkTotal + " matching transactions?";
    if (!window.confirm(what)) {
      event.preventDefault();
    }
  });
  document.body.addEventListener("htmx:afterSwap", bulkState);    // new results: selection starts over

  // ---- Custom dropdowns ([data-menu-select]: hidden input + button + role=listbox <ul>): the list
  // always opens BELOW the button (a native <select> on macOS opens with the chosen option under the
  // pointer, i.e. upward for lower options). Mouse and keyboard (arrows, Enter/Space, Escape, Tab);
  // choosing writes the hidden input and fires "change" on it, like a select would.
  function menuParts(menu) {
    return { input: menu.querySelector("input[type=hidden]"), button: menu.querySelector(".menu-select-button"),
             list: menu.querySelector("[role=listbox]"),
             options: Array.from(menu.querySelectorAll("[role=option]")).filter(function (o) { return !o.hidden; }) };
  }
  function openMenu(menu) {
    const p = menuParts(menu);
    p.list.hidden = false;
    p.button.setAttribute("aria-expanded", "true");
    (p.options.find(function (o) { return o.getAttribute("aria-selected") === "true"; }) || p.options[0]).focus();
  }
  function closeMenu(menu, focusButton) {
    const p = menuParts(menu);
    if (p.list.hidden) return;
    p.list.hidden = true;
    p.button.setAttribute("aria-expanded", "false");
    if (focusButton) p.button.focus();
  }
  function setMenuSelect(menu, value, fire) {
    if (!menu) return;
    // A hidden option (Transactions' "Custom") is shown once chosen.
    const option = menu.querySelector('[role=option][data-value="' + value + '"]');
    if (!option) return;
    option.hidden = false;
    const p = menuParts(menu);
    p.input.value = value;
    p.options.forEach(function (o) { o.setAttribute("aria-selected", String(o === option)); });
    p.button.textContent = option.textContent;
    p.button.setAttribute("aria-label", p.list.getAttribute("aria-label") + ": " + option.textContent);
    if (fire) p.input.dispatchEvent(new Event("change", { bubbles: true }));
  }
  document.addEventListener("click", function (event) {
    const button = event.target.closest(".menu-select-button");
    const option = event.target.closest("[data-menu-select] [role=option]");
    document.querySelectorAll("[data-menu-select]").forEach(function (menu) {
      if (!menu.contains(event.target)) closeMenu(menu, false);       // a click elsewhere closes it
    });
    if (button) {
      const menu = button.closest("[data-menu-select]");
      if (menuParts(menu).list.hidden) openMenu(menu); else closeMenu(menu, false);
    } else if (option) {
      const menu = option.closest("[data-menu-select]");
      closeMenu(menu, true);
      setMenuSelect(menu, option.dataset.value, true);
    }
  });
  document.addEventListener("keydown", function (event) {
    const menu = event.target.closest && event.target.closest("[data-menu-select]");
    if (!menu) return;
    const p = menuParts(menu);
    if (event.target === p.button && ["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      openMenu(menu);
      return;
    }
    const at = p.options.indexOf(event.target);
    if (at === -1) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      p.options[Math.min(p.options.length - 1, Math.max(0, at + (event.key === "ArrowDown" ? 1 : -1)))].focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      p.options[event.key === "Home" ? 0 : p.options.length - 1].focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      closeMenu(menu, true);
      setMenuSelect(menu, event.target.dataset.value, true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(menu, true);
    } else if (event.key === "Tab") {
      closeMenu(menu, false);
    }
  });

  // ---- Transactions' date range in the ribbon (#tx-range: dropdown + From/To, all form="txn-filters"
  // but outside the form). The dropdown refreshes at once. Typed dates apply when you press Enter or
  // leave the From/To pair (user's request 2026-10-08: Chrome reports each typed digit as a new date,
  // so typing 2023 loaded year 0002 first); moving From -> To doesn't apply yet (the refresh would
  // replace the box you just clicked into). A pick from the calendar (no keys typed) applies at once.
  // Editing a date makes the range Custom. Listened for on the document, because #tx-range is
  // replaced on every update (listeners on it would be lost); HTMX event filters need eval (CSP).
  const TYPING_MS = 1000;                          // a change this soon after a key press = still typing
  function submitRange(box, makeCustom) {
    // One submit per rendered ribbon: while HTMX swaps in the new one, Chrome reports the old From/To
    // as left (still attached), which would submit the same dates again.
    if (box.dataset.submitted) return;
    box.dataset.submitted = "1";
    if (makeCustom) setMenuSelect(box.querySelector("[data-menu-select]"), "custom", false);
    const form = document.getElementById("txn-filters");
    if (form) form.requestSubmit();                // HTMX handles the submit (hx-trigger "submit")
  }
  function commitDates(box) {
    // defaultValue = the date the page was rendered with: only a real change refreshes.
    const changed = Array.from(box.querySelectorAll("input[type=date]"))
      .some(function (input) { return input.value !== input.defaultValue; });
    if (changed) submitRange(box, true);
  }
  document.addEventListener("change", function (event) {
    const box = event.target.closest && event.target.closest("#tx-range");
    if (!box) return;
    if (event.target.type !== "date") return submitRange(box, false);       // the dropdown
    const typing = Date.now() - (Number(event.target.dataset.keyAt) || 0) < TYPING_MS;
    if (!typing) commitDates(box);                                            // calendar pick
  });
  document.addEventListener("keydown", function (event) {
    const input = event.target;
    if (!input.closest || input.type !== "date" || !input.closest("#tx-range")) return;
    input.dataset.keyAt = String(Date.now());
    if (event.key === "Enter") {
      event.preventDefault();                      // not the form's own submit: it wouldn't set Custom
      commitDates(input.closest("#tx-range"));
    }
  });
  document.addEventListener("focusout", function (event) {
    const input = event.target;
    if (!input.closest || input.type !== "date" || !input.closest("#tx-range")) return;
    const box = input.closest("#tx-range");
    const next = event.relatedTarget;
    if (next && next.type === "date" && box.contains(next)) return;          // From <-> To: not yet
    commitDates(box);
  });

  // ---- Transaction rows (milestone 14): clicking a row (not its checkbox or another control) toggles
  // the edit panel under it, fetched with htmx.ajax (HTMX click filters need eval, which the CSP
  // blocks). The description is a button, so the keyboard opens it too. The panel's own row (edit or
  // rule panel) is the row's next sibling; clicking the row again closes it. Cancel = [data-dismiss-row].
  function openPanelOf(row) {
    const next = row.nextElementSibling;
    return next && (next.classList.contains("edit-panel") || next.classList.contains("rule-panel")) ? next : null;
  }
  document.addEventListener("click", function (event) {
    const row = event.target.closest && event.target.closest("tr[data-edit-url]");
    if (!row) return;
    if (event.target.closest("input, select, a, label, button:not(.row-open)")) return;
    // phones' Select mode (below): a tap ticks the row instead of opening it
    if (row.closest(".selecting")) {
      const box = row.querySelector(".row-select");
      if (box) { box.checked = !box.checked; box.dispatchEvent(new Event("change", { bubbles: true })); }
      return;
    }
    const button = row.querySelector(".row-open");
    const open = openPanelOf(row);
    if (open) {
      open.remove();
      if (button) button.setAttribute("aria-expanded", "false");
      return;
    }
    // a transaction can be on the review card and in the list: one panel per transaction, so their ids stay unique
    if (button) {
      const id = button.getAttribute("aria-controls");
      [id, id.replace("edit-panel-", "rule-panel-")].forEach(function (other) {
        const already = document.getElementById(other);
        if (already) {
          const opener = already.previousElementSibling && already.previousElementSibling.querySelector(".row-open");
          if (opener) opener.setAttribute("aria-expanded", "false");
          already.remove();
        }
      });
    }
    htmx.ajax("GET", row.dataset.editUrl, { target: row, swap: "afterend" }).then(function () {
      if (button) button.setAttribute("aria-expanded", "true");
      const panel = openPanelOf(row);
      const first = panel && panel.querySelector("input[name=description]");
      // not on a phone, where the panel is a sheet (app.css) and focusing would bring the keyboard up over it
      if (first && !window.matchMedia("(max-width: 640px)").matches) first.focus();
    });
  });
  // A successful Save / transfer button inside a panel replaced the row: close the panel. A response the server
  // retargeted (HX-Retarget: an error shown inside the panel, e.g. a reward bonus it couldn't read) keeps it open.
  document.addEventListener("htmx:afterRequest", function (event) {
    const elt = event.detail.elt;
    if (!event.detail.successful || !elt.hasAttribute || !elt.hasAttribute("data-close-panel")) return;
    if (event.detail.xhr && event.detail.xhr.getResponseHeader("HX-Retarget")) return;
    const panel = elt.closest("tr.edit-panel");
    if (panel) panel.remove();
  });
  document.addEventListener("click", function (event) {
    const dismiss = event.target.closest("[data-dismiss-row]");
    if (!dismiss) return;
    const panel = dismiss.closest("tr");
    const opener = panel.previousElementSibling && panel.previousElementSibling.querySelector(".row-open");
    if (opener) opener.setAttribute("aria-expanded", "false");
    panel.remove();
  });

  // ---- The Transactions list and the review card above it (user's requests 2026-11-01): their columns line up. Each table is
  // measured at its natural widths (app.css: every column but the description hugs its content, unwrapped), and all take the
  // wider of each column. Category, Account and Amount also take a share of the width (COLUMN_SHARES), so they space out on
  // wider screens rather than all the extra going to the description, whose the rest is. It comes first when space is
  // short: it gets at least MIN_DESCRIPTION of the width, taken from Category,
  // which then ends in "…"; Account and Amount keep theirs, so they never abbreviate. Narrow screens keep the automatic layout.
  const MIN_DESCRIPTION = 0.38, MIN_CATEGORY = 125;
  const COLUMN_SHARES = { 4: 0.20, 5: 0.16, 6: 0.09 };      // column index: its share of the table's width
  function syncTxnColumns() {
    const wraps = Array.prototype.slice.call(document.querySelectorAll(".txn-columns"));
    const tables = wraps.map(function (w) { return w.querySelector("table.txns"); }).filter(Boolean);
    wraps.forEach(function (w) { w.classList.add("measuring"); });
    tables.forEach(function (t) {
      t.style.tableLayout = "";
      t.querySelectorAll("thead th").forEach(function (th) { th.style.width = ""; });
    });
    const shown = tables.filter(function (t) { return t.offsetWidth > 0; });
    if (shown.length && shown[0].closest(".txn-columns").clientWidth >= 700) {
      const widths = [];
      shown.forEach(function (t) {
        t.querySelectorAll("thead th").forEach(function (th, i) {
          widths[i] = Math.max(widths[i] || 0, th.getBoundingClientRect().width);
        });
      });
      const total = shown[0].closest(".txn-columns").clientWidth;
      Object.keys(COLUMN_SHARES).forEach(function (i) {
        widths[i] = Math.max(widths[i] || 0, total * COLUMN_SHARES[i]);
      });
      const taken = widths.reduce(function (sum, w, i) { return i === 3 ? sum : sum + w; }, 0);
      const short = total * MIN_DESCRIPTION - (total - taken);
      if (short > 0) widths[4] = Math.max(MIN_CATEGORY, widths[4] - short);
      shown.forEach(function (t) {
        t.querySelectorAll("thead th").forEach(function (th, i) {
          if (i !== 3) th.style.width = widths[i] + "px";          // the fourth is the description: the rest
        });
        t.style.tableLayout = "fixed";
      });
    }
    wraps.forEach(function (w) { w.classList.remove("measuring"); });
  }
  if (document.querySelector(".txn-columns")) syncTxnColumns();
  document.addEventListener("DOMContentLoaded", syncTxnColumns);
  window.addEventListener("load", syncTxnColumns);
  window.addEventListener("resize", syncTxnColumns);
  document.addEventListener("toggle", function (event) {
    if (event.target.classList && event.target.closest(".review-card")) syncTxnColumns();
  }, true);
  document.addEventListener("htmx:afterSettle", function (event) {
    const where = event.target;
    if (where.closest && (where.closest(".txn-columns") || where.querySelector(".txn-columns"))) syncTxnColumns();
  });

  // ---- The rule builder (rule_form.html; user's request 2026-11-02): rows added from <template>s; each row shows and enables
  // the fieldset of the field or action chosen (a disabled fieldset isn't sent), and "between" its second input.
  function syncRuleRow(row) {
    // a <select>, or a radio toggle (Add a transaction's type, 2026-11-04): the checked one
    const kind = (row.querySelector("[data-rule-kind]:checked") || row.querySelector("[data-rule-kind]")).value;
    row.querySelectorAll("fieldset[data-for]").forEach(function (set) {
      const on = set.dataset.for === kind;
      set.hidden = !on;
      set.disabled = !on;
      const op = set.querySelector("[data-rule-op]");
      const second = set.querySelector("[data-between]");
      if (second) second.hidden = !(op && op.value === "between");
    });
  }
  document.addEventListener("change", function (event) {
    const row = event.target.closest && event.target.closest("[data-rule-row]");
    if (row) syncRuleRow(row);
  });
  document.addEventListener("click", function (event) {
    if (!event.target.closest) return;
    const builder = event.target.closest("[data-rule-builder]");
    if (!builder) return;
    const add = event.target.closest("[data-rule-add]");
    const remove = event.target.closest("[data-rule-remove]");
    if (add) {
      const kind = add.dataset.ruleAdd;
      const rows = builder.querySelector('[data-rule-rows="' + kind + '"]');
      const taken = Array.prototype.map.call(rows.querySelectorAll("[name$='-kind'], [name$='-field']"), function (el) {
        return parseInt(el.name.split("-")[1], 10);
      });
      const n = taken.length ? Math.max.apply(null, taken) + 1 : 0;
      const holder = document.createElement("div");
      holder.innerHTML = document.querySelector('[data-rule-template="' + kind + '"]').innerHTML.replace(/__i__/g, n);
      rows.append.apply(rows, Array.prototype.slice.call(holder.children));
      syncRuleRow(rows.lastElementChild);
    } else if (remove) {
      remove.closest("[data-rule-row]").remove();
    } else {
      return;
    }
    builder.dispatchEvent(new Event("change", { bubbles: true }));     // the preview follows
  });

  // ---- The Rules page's filters (user's request 2026-11-02): every word in what a rule says, plus what it checks and does.
  // Kept for this tab (sessionStorage), so moving or editing a rule doesn't lose your place.
  (function () {
    const bar = document.querySelector("[data-rule-filters]");
    if (!bar) return;
    const search = bar.querySelector("[data-rule-search]"), fieldPick = bar.querySelector("[data-rule-field]");
    const actionPick = bar.querySelector("[data-rule-action]"), clear = bar.querySelector("[data-rule-clear]");
    const shown = bar.querySelector("[data-rule-shown]"), none = document.querySelector("[data-rule-none]");
    const KEY = "ft_rule_filters";
    const has = function (list, value) { return !value || list.split(" ").indexOf(value) !== -1; };
    function apply() {
      const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
      // looked up each time: a keywords row is swapped when it's edited (one being edited stays in view)
      const items = Array.prototype.slice.call(document.querySelectorAll("[data-rule-id]"));
      let count = 0;
      items.forEach(function (item) {
        const on = item.dataset.search === undefined || (words.every(function (w) { return item.dataset.search.indexOf(w) !== -1; })
          && has(item.dataset.fields, fieldPick.value) && has(item.dataset.kinds, actionPick.value));
        item.hidden = !on;
        if (on) count += 1;
      });
      const active = Boolean(words.length || fieldPick.value || actionPick.value);
      clear.hidden = !active;
      shown.textContent = active ? count + " of " + items.length : "";
      none.hidden = count > 0;
      // while filtering, a section with nothing to show steps aside
      document.querySelectorAll("[data-rule-section]").forEach(function (section) {
        section.hidden = active && !section.querySelector("[data-rule-id]:not([hidden])");
      });
      try {
        sessionStorage.setItem(KEY, JSON.stringify({ q: search.value, field: fieldPick.value, action: actionPick.value }));
      } catch (err) { /* storage unavailable: filters just aren't kept */ }
    }
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (saved) {
        search.value = saved.q || "";
        fieldPick.value = saved.field || "";
        actionPick.value = saved.action || "";
        if (fieldPick.selectedIndex < 0) fieldPick.value = "";
        if (actionPick.selectedIndex < 0) actionPick.value = "";
      }
    } catch (err) { /* nothing kept */ }
    bar.addEventListener("input", apply);
    bar.addEventListener("change", apply);
    clear.addEventListener("click", function () {
      search.value = "";
      fieldPick.value = "";
      actionPick.value = "";
      apply();
      search.focus();
    });
    apply();
  })();

  // ---- The Rules page's order: drag a rule, and the new order is saved.
  (function () {
    const list = document.querySelector("[data-rule-order]");
    if (!list) return;
    let dragged = null;
    list.addEventListener("dragstart", function (event) {
      dragged = event.target.closest("[data-rule-id]");
      if (dragged) event.dataTransfer.effectAllowed = "move";
    });
    list.addEventListener("dragover", function (event) {
      const over = event.target.closest("[data-rule-id]");
      if (!dragged || !over || over === dragged) return;
      event.preventDefault();
      const rect = over.getBoundingClientRect();
      list.insertBefore(dragged, event.clientY < rect.top + rect.height / 2 ? over : over.nextSibling);
    });
    list.addEventListener("dragend", function () {
      if (!dragged) return;
      dragged = null;
      const body = new URLSearchParams();
      list.querySelectorAll("[data-rule-id]").forEach(function (el) { body.append("ids", el.dataset.ruleId); });
      fetch("/rules/order", { method: "POST", body: body }).then(function () { window.location.reload(); });
    });
  })();

  // ---- Filterable tables: <table id="x" data-filterable> with controls elsewhere on the page:
  // <input|select data-filter-for="x" data-filter="text|account|kind">. A row (data-account,
  // data-kind, data-search, data-value in cents) shows if it matches every filter. Group rows
  // (tr.group[data-account]) hide when none of their rows show, and their [data-subtotal] cell
  // shows the total of the rows still visible (the server's figure again when unfiltered).
  const wholeDollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  function filterTable(table) {
    const value = function (name) {
      const control = document.querySelector('[data-filter-for="' + table.id + '"][data-filter="' + name + '"]');
      return control ? control.value.trim().toLowerCase() : "";
    };
    const text = value("text"), account = value("account"), kind = value("kind");
    const filtered = Boolean(text || account || kind);
    const totals = {};
    let shown = 0;
    table.querySelectorAll("tbody tr[data-kind]").forEach(function (row) {
      const match = (!text || row.dataset.search.indexOf(text) !== -1) &&
                    (!account || row.dataset.account === account) &&
                    (!kind || row.dataset.kind.toLowerCase() === kind);
      row.hidden = !match;
      if (match) {
        shown += 1;
        totals[row.dataset.account] = (totals[row.dataset.account] || 0) + parseInt(row.dataset.value || "0", 10);
      }
    });
    table.querySelectorAll("tbody tr.group[data-account]").forEach(function (group) {
      group.hidden = !(group.dataset.account in totals);
      const cell = group.querySelector("[data-subtotal]");
      if (!cell) return;
      if (cell.dataset.original === undefined) cell.dataset.original = cell.textContent;
      cell.textContent = filtered && totals[group.dataset.account] !== undefined
        ? wholeDollars.format(totals[group.dataset.account] / 100) : cell.dataset.original;
    });
    const empty = table.querySelector("tr.filter-empty");
    if (empty) empty.hidden = shown > 0;
  }
  ["input", "change"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      const id = event.target.dataset && event.target.dataset.filterFor;
      const table = id && document.getElementById(id);
      if (table && table.hasAttribute("data-filterable")) filterTable(table);
    });
  });

  document.addEventListener("fintrack:range", function (event) {
    document.querySelectorAll('table[data-range-table="' + event.detail.id + '"]').forEach(function (table) {
      table.dataset.range = event.detail.key;
      sortTable(table);
    });
  });

  // Clickable rows (Balances account cards, user's request 2026-10-13): <tr data-href="..."> goes there on a click
  // anywhere in the row except its own links and buttons (the name is a real link for the keyboard).
  document.addEventListener("click", function (event) {
    const row = event.target.closest("tr[data-href]");
    if (!row || event.target.closest("a, button, input, select, label")) return;
    window.location.href = row.dataset.href;
  });

  // Share bars: <span class="meter-fill" data-share="0.42"> -> width 42%.
  function sizeMeters(root) {
    root.querySelectorAll(".meter-fill[data-share]").forEach(function (el) {
      el.style.width = Math.min(100, Math.max(0, parseFloat(el.dataset.share) * 100)) + "%";
    });
  }

  // A tag's color (Tags page; user's request 2026-11-02): data-tag-color="#rrggbb" sets --tb, which its swatch, share bars and
  // calendar bars already use. From a script, because the CSP allows no inline styles.
  function paintTagColors() {
    document.querySelectorAll("[data-tag-color]").forEach(function (el) {
      el.style.setProperty("--tb", el.dataset.tagColor);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", paintTagColors);
  else paintTagColors();
  document.addEventListener("htmx:afterSwap", paintTagColors);     // Overview's calendar arrows

  // A day on the Tags calendar (user's requests 2026-11-02): opens #day-dialog to add a label, listing yours to remove. The
  // month and view fields come from the page, so a removal returns to the same calendar.
  document.addEventListener("click", function (event) {
    if (!event.target.closest) return;
    if (event.target.closest("[data-day-cancel]")) {
      document.getElementById("day-dialog").close();
      return;
    }
    const day = event.target.closest("[data-cal-day]");
    const dialog = document.getElementById("day-dialog");
    if (!day || !dialog) return;
    const form = dialog.querySelector("[data-day-form]");
    dialog.querySelector("[data-day-dialog-title]").textContent = day.dataset.dayTitle;
    form.elements.date.value = day.dataset.calDay;
    form.elements.label.value = "";
    form.elements.yearly.checked = false;
    const list = dialog.querySelector("[data-day-list]");
    list.replaceChildren();
    JSON.parse(day.dataset.labels).forEach(function (label) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = label.text + (label.yearly ? " (every year)" : "");
      const remove = document.createElement("form");
      remove.method = "post";
      remove.action = "/tags/labels/" + label.id + "/delete";
      ["month", "view", "back"].forEach(function (field) {
        const copy = document.createElement("input");
        copy.type = "hidden";
        copy.name = field;
        copy.value = form.elements[field].value;
        remove.appendChild(copy);
      });
      const button = document.createElement("button");
      button.type = "submit";
      button.className = "link-button small";
      button.textContent = "Remove";
      remove.appendChild(button);
      row.append(name, remove);
      list.appendChild(row);
    });
    dialog.showModal();
    form.elements.label.focus();
  });

  // No 1-year calendar on a phone (user's request 2026-11-02; its toggle is hidden in app.css): one opened from a link or
  // the back button shows the 3 months ending with the same month instead.
  (function () {
    const box = document.querySelector("[data-cal-views]");
    if (!box || box.dataset.view !== "year" || !window.matchMedia("(max-width: 640px)").matches) return;
    const url = new URL(location.href);
    url.searchParams.set("view", "quarter");
    location.replace(url.toString());
  })();

  // The Tags page's cards (user's request 2026-11-02): their list scrolls so the first on the calendar is at its top, with room
  // added below when it's too near the end to get there. After a save on a card, the list stays where you were.
  const TAG_LIST_TOP = "ft_tag_list_top";
  function placeTagCards() {
    const list = document.querySelector("[data-tag-cards]");
    if (!list) return;
    let saved = null;
    try {
      saved = sessionStorage.getItem(TAG_LIST_TOP);
      sessionStorage.removeItem(TAG_LIST_TOP);
    } catch (err) {
      saved = null;
    }
    // ?focus=<tag id>: a bar clicked on Overview's calendar (user's request 2026-11-02) puts that tag's card on top
    const focus = new URLSearchParams(location.search).get("focus");
    const card = (focus && document.getElementById("tag-card-" + focus)) || list.querySelector(".on-calendar");
    list.scrollTop = saved !== null ? Number(saved) : cardTop(list, card);
  }
  // Where the list scrolls to put `card` in its top row, adding room below when it's too near the end to get there.
  function cardTop(list, card) {
    if (!card) return 0;
    list.style.paddingBottom = "";
    const top = card.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop - 3;
    const below = list.scrollHeight - top;
    if (below < list.clientHeight) list.style.paddingBottom = (list.clientHeight - below + 3) + "px";
    return top;
  }
  // A bar on the Tags calendar: its tag's card to the top of the list, the page staying where it is.
  document.addEventListener("click", function (event) {
    const bar = event.target.closest && event.target.closest("a.cal-bar[data-focus-tag]");
    const list = document.querySelector("[data-tag-cards]");
    if (!bar || !list || bar.getAttribute("href").charAt(0) !== "#") return;
    const card = document.getElementById("tag-card-" + bar.dataset.focusTag);
    if (!card) return;
    event.preventDefault();
    list.scrollTo({ top: cardTop(list, card), behavior: "smooth" });
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", placeTagCards);
  else placeTagCards();
  // ---- Tabs in a card (the Investments card's Investments / Watchlist; user's request 2026-11-04): a [data-tab] button
  // shows the [data-tab-panel] of the same name in its [data-card-tabs] card and hides the others; the page's address
  // gets ?tab= so a reload (or the back button) comes back to it.
  document.addEventListener("click", function (event) {
    const button = event.target.closest && event.target.closest("[data-card-tabs] [data-tab]");
    if (!button) return;
    const card = button.closest("[data-card-tabs]");
    card.querySelectorAll("[data-tab]").forEach(function (b) { b.setAttribute("aria-selected", b === button ? "true" : "false"); });
    card.querySelectorAll("[data-tab-panel]").forEach(function (p) { p.hidden = p.dataset.tabPanel !== button.dataset.tab; });
    // controls for one tab only (the Watchlist's +)
    card.querySelectorAll("[data-tab-only]").forEach(function (el) {
      el.hidden = el.dataset.tabOnly !== button.dataset.tab;
      if (el.hidden && el.open) el.open = false;
    });
    const url = new URL(location.href);
    url.searchParams.set("tab", button.dataset.tab);
    url.searchParams.delete("msg");
    url.searchParams.delete("error");
    history.replaceState(null, "", url);
  });

  // ---- Tags page search (user's requests 2026-11-04): the ribbon's box hides the cards whose name, Start or End date
  // ("sep", "september", "2026") doesn't contain what you type (any case; data-tag-text). Kept for this tab (sessionStorage, a convenience) so a save or the pencil, which reload the page,
  // don't drop it.
  const TAG_SEARCH = "ft_tag_search";
  function filterTagCards(box) {
    const q = box.value.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll("[data-tag-text]").forEach(function (card) {
      const hit = !q || card.dataset.tagText.indexOf(q) !== -1;
      card.hidden = !hit;
      if (hit) shown += 1;
    });
    const none = document.querySelector("[data-tag-none]");
    if (none) none.hidden = shown > 0;
  }
  document.addEventListener("input", function (event) {
    const box = event.target.closest && event.target.closest("[data-tag-search]");
    if (!box) return;
    filterTagCards(box);
    try {
      sessionStorage.setItem(TAG_SEARCH, box.value);
    } catch (err) { /* without it the search just starts empty after a reload */ }
  });
  function restoreTagSearch() {
    const box = document.querySelector("[data-tag-search]");
    if (!box) return;
    try {
      box.value = sessionStorage.getItem(TAG_SEARCH) || "";
    } catch (err) {
      box.value = "";
    }
    if (box.value) filterTagCards(box);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", restoreTagSearch);
  else restoreTagSearch();

  // A save in the list, or a card's pencil / Done (a[data-keep-place], 2026-11-04): the list keeps its place too
  function rememberList(list) {
    try {
      sessionStorage.setItem(TAG_LIST_TOP, String(list.scrollTop));
    } catch (err) { /* a convenience: without it the list goes back to the first on the calendar */ }
  }
  document.addEventListener("submit", function (event) {
    const list = event.target.closest && event.target.closest("[data-tag-cards]");
    if (list) rememberList(list);
  });
  document.addEventListener("click", function (event) {
    const link = event.target.closest && event.target.closest("a[data-keep-place]");
    const list = link && link.closest("[data-tag-cards]");
    if (list) rememberList(list);
  });

  // ---- Light / dark switch (rail; user's request 2026-10-28) ----
  // The effective theme: yours (data-theme on <html>, from the ft_theme cookie) or else the system's. Flipping it
  // writes the cookie (the server renders data-theme from it, so the next page opens in it with no flash) and asks the
  // charts to redraw in the new colours (charts.js listens for fintrack:theme).
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function isDark() {
    const chosen = document.documentElement.dataset.theme;
    return chosen ? chosen === "dark" : systemDark.matches;
  }
  function syncThemeSwitch() {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.setAttribute("aria-checked", String(isDark()));
    });
  }
  document.addEventListener("click", function (event) {
    if (!event.target.closest("[data-theme-toggle]")) return;
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    document.cookie = "ft_theme=" + next + "; path=/; max-age=31536000; SameSite=Lax; Secure";
    // the app window's title bar and, on a phone, the status and gesture bars follow at once: each theme-color tag
    // takes its colour for the new theme (base.html's data-light / data-dark) and keeps only its phone-width condition
    document.querySelectorAll('meta[name="theme-color"]').forEach(function (meta) {
      if (meta.hasAttribute("data-phone")) meta.setAttribute("media", "(max-width: 640px)");
      else meta.removeAttribute("media");
      meta.setAttribute("content", meta.getAttribute(next === "dark" ? "data-dark" : "data-light"));
    });
    syncThemeSwitch();
    document.dispatchEvent(new CustomEvent("fintrack:theme"));
  });
  syncThemeSwitch();
  systemDark.addEventListener("change", syncThemeSwitch);

  // ---- Phones: elements marked [data-phone-controls] (Transactions' filters) live in the controls panel (#page-controls)
  // on a phone and go back to their place in the page on a wider screen. A comment marks each one's place.
  (function () {
    const panel = document.getElementById("page-controls");
    const items = document.querySelectorAll("[data-phone-controls]");
    if (!panel || !items.length) return;
    const phone = window.matchMedia("(max-width: 640px)");
    const homes = Array.prototype.map.call(items, function (el) {
      const mark = document.createComment(" its place off phones ");
      el.before(mark);
      return [el, mark];
    });
    function place() {
      homes.forEach(function (home) {
        if (phone.matches) panel.appendChild(home[0]); else home[1].after(home[0]);
      });
    }
    phone.addEventListener("change", place);
    place();
  })();

  // ---- Phones: the current tab (the line at the bottom) opens the page's controls in a panel above it (app.css
  // ≤640px; base.html [data-controls-toggle] -> #page-controls); a swipe up / down opens / closes it too (below). It
  // closes when you scroll the page (unless you're typing
  // in it: the keyboard opening scrolls too, or a pop-out in it is open), tap elsewhere or press Escape. On wider
  // screens the tab is a plain link and the controls are in the ribbon.
  (function () {
    const toggle = document.querySelector("[data-controls-toggle]");
    const panel = toggle && document.getElementById(toggle.getAttribute("aria-controls"));
    if (!panel) return;
    const phone = window.matchMedia("(max-width: 640px)");
    function syncRole() {
      if (phone.matches) toggle.setAttribute("aria-expanded", String(panel.classList.contains("open")));
      else { toggle.removeAttribute("aria-expanded"); panel.classList.remove("open"); }
    }
    phone.addEventListener("change", syncRole);
    let openedAt = 0;
    // the panel's handle sits where the tab's mark is, the name's width (app.css .tabbar-panel::before)
    function placeHandle() {
      const tab = toggle.getBoundingClientRect();
      panel.style.setProperty("--handle-left", tab.left + "px");
      panel.style.setProperty("--handle-width", tab.width + "px");
    }
    function setOpen(open) {
      if (open) placeHandle();
      panel.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", String(open));
      openedAt = window.scrollY;
    }
    toggle.addEventListener("click", function (event) {
      if (!phone.matches) return;                     // a link to this page, as on desktop
      event.preventDefault();
      setOpen(!panel.classList.contains("open"));
    });
    window.addEventListener("scroll", function () {
      if (!panel.classList.contains("open") || Math.abs(window.scrollY - openedAt) < 24) return;
      // stays open while you type in it or one of its pop-outs (+ Add a transaction) is open
      const typing = panel.contains(document.activeElement) &&
        document.activeElement.matches("input:not([type]), input[type=text], input[type=search], textarea");
      if (!typing && !panel.querySelector("details[open]")) setOpen(false);
    }, { passive: true });
    document.addEventListener("click", function (event) {
      if (panel.classList.contains("open") && !panel.contains(event.target) && !toggle.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && panel.classList.contains("open")) { setOpen(false); toggle.focus(); }
    });
    // Swipes (user's request 2026-11-06): up on the tabs line opens the panel, down on the tabs line or the panel closes
    // it. Mostly vertical and at least SWIPE px; such a drag doesn't scroll the page (that would close the panel again);
    // a sideways one still scrolls the tabs. Not from inside a list, a pop-out or a text box in the panel (they scroll
    // or take typing).
    const SWIPE = 30;
    function onSwipe(el, up, down, allowed) {
      let x0 = 0, y0 = 0, active = false;
      el.addEventListener("touchstart", function (event) {
        active = phone.matches && event.touches.length === 1 && (!allowed || allowed(event.target));
        if (active) { x0 = event.touches[0].clientX; y0 = event.touches[0].clientY; }
      }, { passive: true });
      el.addEventListener("touchmove", function (event) {
        if (!active) return;
        const dx = event.touches[0].clientX - x0, dy = event.touches[0].clientY - y0;
        if (Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) event.preventDefault();     // vertical: not a page scroll
      }, { passive: false });
      el.addEventListener("touchend", function (event) {
        if (!active) return;
        active = false;
        const dx = event.changedTouches[0].clientX - x0, dy = event.changedTouches[0].clientY - y0;
        if (Math.abs(dy) < SWIPE || Math.abs(dy) < Math.abs(dx) * 1.5) return;
        if (dy < 0 && up) up(); else if (dy > 0 && down) down();
      });
    }
    const tabsLine = toggle.closest(".tabs");
    if (tabsLine) onSwipe(tabsLine, function () { setOpen(true); }, function () { setOpen(false); });
    onSwipe(panel, null, function () { setOpen(false); }, function (target) {
      return !target.closest("details[open] > :not(summary), .menu-select-list, input, select, textarea");
    });
    syncRole();
  })();

  // ---- Phones: the tabs are one line that scrolls sideways (app.css, ≤640px); bring the current tab into view.
  // scrollLeft on the tab line only (scrollIntoView could also scroll the page).
  (function () {
    const tabs = document.querySelector(".tabbar .tabs");
    const current = tabs && tabs.querySelector("a.active");
    if (!current || tabs.scrollWidth <= tabs.clientWidth) return;
    tabs.scrollLeft = current.offsetLeft - tabs.offsetLeft - (tabs.clientWidth - current.offsetWidth) / 2;
  })();

  // ---- Collapsible section rail ----
  // Toggles icons-only mode and remembers it in a first-party cookie, which the server
  // reads to render the rail collapsed from the first paint (no flash on page load).
  // Mid widths (641-1100px) start icons-only until you open the rail; your choice (expanded / collapsed) is
  // remembered there too, so it stays as you left it from page to page (user's request 2026-11-13).
  const midWidth = window.matchMedia("(min-width: 641px) and (max-width: 1100px)");

  function railIsCollapsed(shell) {
    return shell.classList.contains("rail-collapsed") || (midWidth.matches && !shell.classList.contains("rail-expanded"));
  }

  // Keep the button's state and label in step with what's shown.
  function syncRailButton() {
    const shell = document.querySelector(".shell");
    const button = document.querySelector("[data-rail-toggle]");
    if (!shell || !button) return;
    const collapsed = railIsCollapsed(shell);
    const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
    button.setAttribute("aria-expanded", String(!collapsed));
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  function toggleRail() {
    const shell = document.querySelector(".shell");
    if (!shell) return;
    const collapsed = !railIsCollapsed(shell);
    shell.classList.toggle("rail-collapsed", collapsed);
    shell.classList.toggle("rail-expanded", !collapsed);
    document.cookie = "ft_rail=" + (collapsed ? "collapsed" : "expanded") +
      "; path=/; max-age=31536000; SameSite=Lax; Secure";
    syncRailButton();
    // Charts sized to their box redraw on resize; nudge them once the width settles.
    window.dispatchEvent(new Event("resize"));
  }

  syncRailButton();
  midWidth.addEventListener("change", syncRailButton);

  // The toggle button, or a click anywhere on the rail that isn't a link (empty space,
  // the environment badge). Links still just navigate. On narrow windows the rail is a
  // top row and the button is hidden, so the rail doesn't collapse there.
  document.addEventListener("click", function (event) {
    const rail = event.target.closest(".rail");
    if (!rail) return;
    if (event.target.closest("a, form, button:not([data-rail-toggle])")) return;   // links, Sync now
    const button = rail.querySelector("[data-rail-toggle]");
    if (!button || getComputedStyle(button).display === "none") return;
    toggleRail();
  });

  // ---- Expand/collapse rows: <button data-toggle-group="x"> shows/hides <tr class="more"
  // data-group="x">. Optional data-label-open / data-label-closed swap the button text.
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-toggle-group]");
    if (!button) return;
    const open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(open));
    document.querySelectorAll('tr.more[data-group="' + button.dataset.toggleGroup + '"]').forEach(function (row) {
      row.classList.toggle("shown", open);
    });
    const label = open ? button.dataset.labelOpen : button.dataset.labelClosed;
    if (label) button.textContent = label;
  });

  // <tr data-toggle-row>: a click anywhere on the row (not on a link or another control) opens/closes it like its
  // expander button (Credit cards rate groups, user's request 2026-10-26). The button keeps it keyboard-reachable.
  document.addEventListener("click", function (event) {
    const row = event.target.closest && event.target.closest("tr[data-toggle-row]");
    if (!row || event.target.closest("a, button, input, select, label")) return;
    const button = row.querySelector("[data-toggle-group]");
    if (button) button.click();
  });

  // Adding a subcategory or subtype: its + (data-toggle-panel="id"), or a click anywhere on its row that isn't a control
  // (data-panel-row="id"), shows or hides that panel, focusing its first field.
  function togglePanel(id) {
    const panel = document.getElementById(id);
    if (!panel) return;
    panel.hidden = !panel.hidden;
    document.querySelectorAll('[aria-controls="' + id + '"]').forEach(function (b) { b.setAttribute("aria-expanded", String(!panel.hidden)); });
    if (!panel.hidden) {
      const field = panel.querySelector("input:not([type=hidden])");
      if (field) field.focus();
    }
  }
  document.addEventListener("click", function (event) {
    if (!event.target.closest) return;
    const toggle = event.target.closest("[data-toggle-panel]");
    if (toggle) {
      togglePanel(toggle.dataset.togglePanel);
      return;
    }
    const row = event.target.closest("[data-panel-row]");
    if (row && !event.target.closest("input, select, textarea, a, button, label, summary, details")) togglePanel(row.dataset.panelRow);
  });
  // Categories and Account types on a phone: ⋯ (data-row-more) shows or hides the row's other controls (order, Move
  // to, delete) on a line under it (app.css .ar-row.more-open; user's choice 2026-11-11). Hidden on desktop.
  document.addEventListener("click", function (event) {
    const more = event.target.closest && event.target.closest("[data-row-more]");
    if (!more) return;
    const open = more.closest(".ar-row").classList.toggle("more-open");
    more.setAttribute("aria-expanded", String(open));
  });
  // A button inside one that just closes it (the Rules page's Cancel).
  document.addEventListener("click", function (event) {
    const close = event.target.closest && event.target.closest("[data-popover-close]");
    if (close) close.closest("details[data-popover]").open = false;
  });
  // <details data-popover> (e.g. Budgets' settings): a click outside it or Escape closes it.
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details[data-popover][open]").forEach(function (pop) {
      if (!pop.contains(event.target)) pop.open = false;
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll("details[data-popover][open]").forEach(function (pop) {
      pop.open = false;
      pop.querySelector("summary").focus();
    });
  });

  // <details data-checklist> (Transactions' accounts, categories and tags, user's requests 2026-10-29/30): checkboxes
  // named like one filter, none = all. A box with [data-parent] ticks everything under it ([data-child-of], any depth:
  // an account type, its accounts, their cards); ticking the ones below one by one ticks it when all are, and shows it
  // partly ticked when some are. [data-check-all] clears the rest. The summary names what's picked, topmost ticked box
  // only: one = its name, more = "N categories" / "N tags" (data-checklist-all / -many), or every name joined
  // (data-checklist-join, the accounts'). Runs in the capture phase, so the boxes are settled before the form's own
  // "change" (its HTMX request) reads them.
  function checklistKids(box, key) {
    return [...box.querySelectorAll('[data-child-of="' + key + '"]')];
  }
  function checklistSetBelow(box, el, on) {
    if (!el.dataset.parent) return;
    checklistKids(box, el.dataset.parent).forEach(function (kid) {
      kid.checked = on;
      kid.indeterminate = false;
      checklistSetBelow(box, kid, on);
    });
  }
  function checklistAncestorTicked(box, el) {
    for (let key = el.dataset.childOf; key; ) {
      const parent = box.querySelector('[data-parent="' + key + '"]');
      if (!parent) return false;
      if (parent.checked) return true;
      key = parent.dataset.childOf;
    }
    return false;
  }
  function checklistSync(box) {
    // deepest first: a box's children follow it in the list, so going backwards settles them before it
    [...box.querySelectorAll("[data-parent]")].reverse().forEach(function (parent) {
      const kids = checklistKids(box, parent.dataset.parent);
      const ticked = kids.filter(function (k) { return k.checked; }).length;
      parent.checked = kids.length > 0 && ticked === kids.length;
      parent.indeterminate = !parent.checked && (ticked > 0 || kids.some(function (k) { return k.indeterminate; }));
    });
    const labels = [];
    box.querySelectorAll('input[type="checkbox"]:checked:not([data-check-all])').forEach(function (input) {
      if (!checklistAncestorTicked(box, input)) labels.push(input.dataset.label);
    });
    const all = box.querySelector("[data-check-all]");
    if (all) all.checked = labels.length === 0;
    const summary = box.querySelector("[data-checklist-label]");
    if (!summary) return;
    const none = box.dataset.checklistAll || "All accounts", many = box.dataset.checklistMany || "accounts";
    summary.textContent = labels.length === 0 ? none : labels.length === 1 || box.hasAttribute("data-checklist-join")
      ? labels.join(", ") : labels.length + " " + many;
    summary.title = labels.join(", ") || none;
  }
  document.querySelectorAll("details[data-checklist]").forEach(checklistSync);
  document.addEventListener("change", function (event) {
    const box = event.target.closest && event.target.closest("details[data-checklist]");
    if (!box) return;
    const el = event.target;
    if (el.matches("[data-check-all]")) {
      box.querySelectorAll('input[type="checkbox"]:not([data-check-all])').forEach(function (input) {
        input.checked = false;
        input.indeterminate = false;
      });
    } else if (el.matches("[data-parent]")) {
      checklistSetBelow(box, el, el.checked);
    }
    checklistSync(box);
  }, true);

  // <input data-submit-on-leave> (typed dates, e.g. a tag's Start / End; user's reports 2026-10-28) submits its form when
  // focus leaves the FORM (not just the box: tabbing from Start to End mustn't reload the page) or on Enter, and only if
  // something in it changed: a date box fires "change" while you're still typing (a one-digit year is already a valid
  // date), so data-autosubmit would save after the first digit.
  document.addEventListener("change", function (event) {
    const el = event.target.closest("[data-submit-on-leave]");
    if (el && el.form) el.form.dataset.changed = "1";
  });
  document.addEventListener("focusout", function (event) {
    const el = event.target.closest("[data-submit-on-leave]");
    if (!el || !el.form) return;
    const form = el.form;
    if (event.relatedTarget && form.contains(event.relatedTarget)) return;   // still in the form (e.g. Start -> End)
    if (form.dataset.changed === "1") {
      delete form.dataset.changed;
      form.requestSubmit();
    }
  });
  document.addEventListener("keydown", function (event) {
    const el = event.target.closest("[data-submit-on-leave]");
    if (el && event.key === "Enter" && el.form) {
      event.preventDefault();
      delete el.form.dataset.changed;
      el.form.requestSubmit();
    }
  });

  // <select data-autosubmit> submits its form when changed.
  document.addEventListener("change", function (event) {
    const el = event.target.closest("[data-autosubmit]");
    if (el && el.form) el.form.requestSubmit();
  });

  // ---- "Saved" toast (user's request 2026-10-25) ----
  // app.css shows .flash.ok (role="status") fixed at the bottom of the screen and fades it out; here it's removed once
  // faded, and its ?msg= is dropped from the address so a refresh doesn't show it again.
  document.querySelectorAll(".flash.ok").forEach(function (toast) {
    toast.addEventListener("animationend", function () { toast.remove(); });
  });
  try {
    const url = new URL(location.href);
    if (url.searchParams.has("msg")) {
      url.searchParams.delete("msg");
      history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    }
  } catch (err) {
    /* leave the address as it is */
  }

  // Keeping your place after a save on [data-keep-scroll] pages is keep-scroll.js (it must run before the page draws).

  // ---- Budgets table (templates/_budget.html) ----
  // Each edit swaps the whole table, so the open line panel and collapsed sections are
  // remembered here and re-applied after the swap. Collapsed sections also survive page
  // loads (localStorage, a convenience only: the page works the same without it).
  const BUDGET_KEY = "ft_budget_collapsed";
  const GROUP_KEY = "ft_budget_grouped";
  let openPanel = null;
  let collapsed = [];
  let grouped = false;          // "Group lines without a budget"
  const openGroups = [];        // sections whose Non-budgeted items row is expanded (this page view)
  try { collapsed = JSON.parse(localStorage.getItem(BUDGET_KEY) || "[]"); } catch (err) { collapsed = []; }
  try { grouped = localStorage.getItem(GROUP_KEY) === "1"; } catch (err) { grouped = false; }

  function applyBudgetState() {
    document.querySelectorAll("table.budget-table, table.plan-table").forEach(function (table) {
      table.classList.toggle("grouped", grouped);
    });
    document.querySelectorAll("[data-group-toggle]").forEach(function (toggle) {
      if (toggle.type === "checkbox") toggle.checked = grouped;      // the settings popup's checkbox
      else toggle.setAttribute("aria-pressed", String(grouped));
    });
    document.querySelectorAll("tbody[data-section]").forEach(function (body) {
      const shut = collapsed.indexOf(body.dataset.section) !== -1;
      body.classList.toggle("collapsed", shut);
      const button = body.querySelector("[data-collapse]");
      if (button) button.setAttribute("aria-expanded", String(!shut));
      const open = openGroups.indexOf(body.dataset.section) !== -1;
      body.classList.toggle("group-open", open);
      const group = body.querySelector("[data-group-open]");
      if (group) group.setAttribute("aria-expanded", String(open));
    });
    document.querySelectorAll("tr.line-panel").forEach(function (panel) {
      panel.hidden = panel.dataset.panel !== openPanel;
    });
    document.querySelectorAll("tr.line").forEach(function (row) {
      row.classList.toggle("open", row.dataset.line === openPanel);
    });
  }

  document.addEventListener("click", function (event) {
    // Section header: collapse / expand its lines.
    const toggle = event.target.closest("[data-collapse]");
    if (toggle) {
      const key = toggle.dataset.collapse;
      const at = collapsed.indexOf(key);
      if (at === -1) collapsed.push(key); else collapsed.splice(at, 1);
      try { localStorage.setItem(BUDGET_KEY, JSON.stringify(collapsed)); } catch (err) { /* private window */ }
      applyBudgetState();
      return;
    }
    // "Group lines without a budget" on/off (remembered in this browser).
    if (event.target.closest("[data-group-toggle]")) {
      grouped = !grouped;
      try { localStorage.setItem(GROUP_KEY, grouped ? "1" : "0"); } catch (err) { /* private window */ }
      applyBudgetState();
      return;
    }
    // A section's Non-budgeted items row: show / hide the lines it stands for.
    const groupRow = event.target.closest("[data-group-open]");
    if (groupRow) {
      const key = groupRow.dataset.groupOpen;
      const at = openGroups.indexOf(key);
      if (at === -1) openGroups.push(key); else openGroups.splice(at, 1);
      applyBudgetState();
      return;
    }
    // Suggestion button: put the amount in the line's input and fire "change", which asks
    // how far it applies, then saves.
    const fill = event.target.closest("[data-fill]");
    if (fill) {
      const input = document.getElementById(fill.dataset.fill);
      if (input) {
        input.value = fill.dataset.value;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return;
    }
    // A line: open its panel (clicking it again closes it). Links and the amount
    // input don't toggle; the input opens the panel through focus below.
    const row = event.target.closest("tr.line");
    if (!row || event.target.closest("a, input, button")) return;
    openPanel = openPanel === row.dataset.line ? null : row.dataset.line;
    applyBudgetState();
  });

  // Budget boxes show "$1,234.50"; while you're in one it's plain "1234.50" for editing,
  // and the dollar format comes back when you leave (user's request 2026-10-06). Changing
  // the value from code isn't an edit, so clicking in and out never saves anything.
  const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const USD_WHOLE = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  function plainAmount(text) { return text.replace(/[$,\s]/g, ""); }
  // A box rounded for display (1-year view: data-exact) edits its exact stored amount.
  function exactAmount(input) {
    return input.dataset.exact !== undefined ? input.dataset.exact : plainAmount(input.dataset.original || "");
  }
  function sameAmount(a, b) {
    const x = parseFloat(a), y = parseFloat(b);
    return a === b || (!isNaN(x) && !isNaN(y) && /^[\d.]+$/.test(a) && /^[\d.]+$/.test(b) && x === y);
  }

  document.addEventListener("focusin", function (event) {
    const input = event.target;
    if (!input.matches || !input.matches("input.amount")) return;
    if (input.dataset.plain === undefined) {
      input.dataset.formatted = input.value;
      input.dataset.plain = input.dataset.exact !== undefined ? input.dataset.exact : plainAmount(input.value);
      input.value = input.dataset.plain;
    }
    // Focusing a line's amount opens that line's panel (keyboard access to suggestions).
    const row = input.closest("tr.line");
    if (row && openPanel !== row.dataset.line) {
      openPanel = row.dataset.line;
      applyBudgetState();
    }
  });

  document.addEventListener("focusout", function (event) {
    const input = event.target;
    if (!input.matches || !input.matches("input.amount") || input.dataset.plain === undefined) return;
    const typed = input.value.trim();
    if (typed === input.dataset.plain) {
      input.value = input.dataset.formatted;            // unchanged: put the format back
    } else if (/^\d+(\.\d*)?$|^\.\d+$/.test(plainAmount(typed))) {
      // A plain number: format it now (whole dollars in boxes that are shown rounded).
      input.value = (input.dataset.exact !== undefined ? USD_WHOLE : USD).format(parseFloat(plainAmount(typed)));
    }                                                    // a calculation: the saved result replaces it
    delete input.dataset.plain;
    delete input.dataset.formatted;
  });

  // ---- Budget boxes ask how far a change applies (Month and 1-year views) ----
  // A box with data-scope-form="<form id>" opens #scope-dialog (_scope_dialog.html) when
  // changed: "Only <month>" or "<month> and every month after". The choice fills that form
  // and submits it (htmx). Cancel or Escape puts the old value back.
  let pendingCell = null;

  function closeScopeDialog(restore) {
    const dialog = document.getElementById("scope-dialog");
    if (pendingCell && restore) pendingCell.value = pendingCell.dataset.original || "";
    pendingCell = null;
    if (dialog && dialog.open) dialog.close();
  }

  document.addEventListener("change", function (event) {
    const input = event.target;
    if (!input.matches || !input.matches("input[data-scope-form]")) return;
    if (sameAmount(plainAmount(input.value), exactAmount(input))) return;
    const dialog = document.getElementById("scope-dialog");
    if (!dialog) return;
    pendingCell = input;
    const label = input.dataset.monthLabel;
    const shown = input.value.trim() === "" ? "no budget" : input.value.trim();
    dialog.querySelector("[data-scope-what]").textContent = "Apply " + shown + " to:";
    dialog.querySelector('[data-scope="month"]').textContent = "Only " + label;
    dialog.querySelector('[data-scope="forward"]').textContent = label + " and every month after";
    dialog.showModal();
  });

  document.addEventListener("click", function (event) {
    const choice = event.target.closest("#scope-dialog [data-scope]");
    if (!choice) return;
    const form = pendingCell && document.getElementById(pendingCell.dataset.scopeForm);
    if (choice.dataset.scope === "cancel" || !pendingCell || !form) {
      closeScopeDialog(true);
      return;
    }
    form.elements.category.value = pendingCell.dataset.category;
    form.elements.month.value = pendingCell.dataset.month;
    form.elements.amount.value = pendingCell.value;
    form.elements.scope.value = choice.dataset.scope;
    closeScopeDialog(false);
    form.requestSubmit();          // htmx posts it and swaps the table
  });

  // A tag's line (data-direct-form="<form id>"; user's request 2026-11-02) sets that month only: no question to ask.
  document.addEventListener("change", function (event) {
    const input = event.target;
    if (!input.matches || !input.matches("input[data-direct-form]")) return;
    if (sameAmount(plainAmount(input.value), exactAmount(input))) return;
    const form = document.getElementById(input.dataset.directForm);
    form.elements.category.value = input.dataset.category;
    form.elements.month.value = input.dataset.month;
    form.elements.amount.value = input.value;
    form.elements.scope.value = "";
    form.requestSubmit();          // htmx posts it and swaps the table
  });

  // ---- Category icons (Categories page, categories.html; user's request 2026-10-20) ----
  // A button with data-icon-picker="<post url>" opens #icon-picker for its category: the form posts there, the
  // current choice is marked, and the icons the name matches (data-icon-suggest, best first) are copied into
  // "Matches the name". Picking an icon submits it; Cancel or Escape closes. The search box filters by name/keywords.
  // A category that doesn't exist yet has nothing to post to: with data-icon-target="<input id>" the pick goes in that
  // form field and shows on the button, and is saved with the new category.
  let iconChooser = null;
  function filterIcons(dialog, text) {
    const words = text.trim().toLowerCase();
    dialog.querySelectorAll("[data-icon-section]").forEach(function (section) {
      let shown = 0;
      section.querySelectorAll(".icon-choice").forEach(function (choice) {
        const match = !words || choice.dataset.iconKeywords.indexOf(words) !== -1;
        choice.hidden = !match;
        if (match) shown += 1;
      });
      section.hidden = shown === 0;
    });
  }

  document.addEventListener("click", function (event) {
    const opener = event.target.closest("[data-icon-picker]");
    const dialog = document.getElementById("icon-picker");
    if (!opener || !dialog) return;
    const form = dialog.querySelector("[data-icon-form]");
    form.action = opener.dataset.iconPicker;
    dialog.querySelector("[data-icon-title]").textContent = opener.dataset.iconFor;
    const target = opener.dataset.iconTarget && document.getElementById(opener.dataset.iconTarget);
    iconChooser = target ? opener : null;
    if (target && !opener.autoIcon) opener.autoIcon = opener.firstElementChild.cloneNode(true);
    const current = target ? target.value : opener.dataset.iconCurrent;
    const suggested = dialog.querySelector("[data-icon-suggested]");
    suggested.replaceChildren();
    (opener.dataset.iconSuggest || "").split(",").filter(Boolean).forEach(function (name) {
      const original = dialog.querySelector('[data-icon-section]:not([data-icon-section="suggested"]) .icon-choice[value="' + name + '"]');
      if (original) suggested.appendChild(original.cloneNode(true));
    });
    dialog.querySelectorAll(".icon-choice").forEach(function (choice) {
      choice.setAttribute("aria-pressed", String(choice.value === current));
    });
    dialog.querySelector("[data-icon-auto]").setAttribute("aria-pressed", String(current === ""));
    const search = dialog.querySelector("[data-icon-search]");
    search.value = "";
    filterIcons(dialog, "");
    dialog.showModal();
    search.focus();
  });

  document.addEventListener("click", function (event) {
    if (!iconChooser) return;
    const choice = event.target.closest("#icon-picker .icon-choice, #icon-picker [data-icon-auto]");
    if (!choice) return;
    event.preventDefault();
    document.getElementById(iconChooser.dataset.iconTarget).value = choice.value;
    const svg = choice.value ? choice.querySelector("svg").cloneNode(true) : iconChooser.autoIcon.cloneNode(true);
    svg.setAttribute("width", iconChooser.autoIcon.getAttribute("width"));
    svg.setAttribute("height", iconChooser.autoIcon.getAttribute("height"));
    iconChooser.replaceChildren(svg);
    document.getElementById("icon-picker").close();
  });

  document.addEventListener("input", function (event) {
    if (!event.target.matches || !event.target.matches("#icon-picker [data-icon-search]")) return;
    filterIcons(document.getElementById("icon-picker"), event.target.value);
  });

  // Enter in the search box would submit the first icon: it only searches.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && event.target.matches && event.target.matches("#icon-picker [data-icon-search]")) {
      event.preventDefault();
    }
  });

  document.addEventListener("click", function (event) {
    if (!event.target.closest("#icon-picker [data-icon-cancel]")) return;
    document.getElementById("icon-picker").close();
  });

  // Escape closes a <dialog> with a "cancel" event: treat it like Cancel.
  document.addEventListener("cancel", function (event) {
    if (event.target.id === "scope-dialog") closeScopeDialog(true);
  }, true);

  sizeMeters(document);
  applyBudgetState();
  // Re-size bars inside content HTMX swaps in, and restore the budget table's state.
  // (An outerHTML swap's event target can be the replaced element, so size the whole page.)
  document.addEventListener("htmx:afterSwap", function () {
    sizeMeters(document);
    applyBudgetState();
  });

  // ---- Keep open pages from showing stale data ----
  // <body data-revision> is the data revision the page was rendered from.
  // The Back button can restore a page from the browser's back/forward cache
  // without asking the server, and a page in another tab keeps its old content.
  // In both cases, ask the server for the current revision and reload if it moved.
  async function fetchRevision() {
    const response = await fetch("/revision", { cache: "no-store" });
    return (await response.text()).trim();
  }

  async function reloadIfStale() {
    const rendered = document.body.dataset.revision;
    if (rendered === undefined) return;
    if (document.body.dataset.offlineView !== undefined) return;   // the phone's read-only copy: nothing to compare
    try {
      if ((await fetchRevision()) !== rendered) window.location.reload();
    } catch (err) {
      /* server stopped: leave the page as is */
    }
  }

  window.addEventListener("pageshow", function (event) {
    if (event.persisted) reloadIfStale(); // restored from back/forward cache
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") reloadIfStale(); // returned to this tab
  });

  // Edits made on this page via HTMX (e.g. a row's category) already show their
  // result, so adopt the new revision instead of reloading for our own change.
  document.addEventListener("htmx:afterRequest", async function () {
    try {
      document.body.dataset.revision = await fetchRevision();
    } catch (err) {
      /* ignore */
    }
  });

  // Phones: a sideways swipe on a main page opens the page beside it, in the bottom bar's order (<body data-swipe-prev /
  // data-swipe-next>, deps.nav_for; user's requests 2026-11-11): left -> next, right -> previous. Like a carousel: the
  // page beside comes in with the finger (a "peek": that page, built in an untouchable frame behind the bars once this
  // one has loaded), and letting go past 40% of the screen or with a flick finishes the move, else both spring back.
  // After the move the real page loads behind the identical frame (offline-sw.js has it ready) with no second slide.
  // Without a peek yet, the page follows alone and the next one slides in (swipe-reveal.js + app.css, a View
  // Transition). A gallery or a table that scrolls sideways scrolls first; at its end, the swipe moves the page (charts
  // too: galleries are mostly charts). Not a swipe from the screen's edge (Android's own Back), nor on fields, pop-ups
  // or the fixed bars and sheets.
  (function () {
    const body = document.body;
    if (window.top !== window) return;                      // a peek itself: no swiping, no peeks of its own
    if (!body.dataset.swipePrev && !body.dataset.swipeNext) return;
    const phone = window.matchMedia("(max-width: 640px)");
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    const EDGE = 24, LOCK = 12, RATIO = 1.5, SHARE = 0.4, FLICK = 0.4;   // px, px, sideways vs up/down, of the width, px/ms (a fling, as on Android)
    const EASE = "cubic-bezier(.2, .7, .3, 1)";
    const page = document.querySelector("main");
    const peeks = {};                                       // "next" / "prev" -> its iframe, once built
    let start = null;

    // ---- peeks: the pages beside this one, built quietly once this page has loaded (the worker has them already)
    function buildPeeks() {
      if (!phone.matches || calm.matches || !(navigator.serviceWorker && navigator.serviceWorker.controller)) return;
      [["next", body.dataset.swipeNext], ["prev", body.dataset.swipePrev]].forEach(function (pair) {
        if (!pair[1] || peeks[pair[0]]) return;
        const frame = document.createElement("iframe");
        frame.className = "swipe-peek";
        frame.src = pair[1];
        frame.tabIndex = -1;
        frame.setAttribute("aria-hidden", "true");
        frame.title = "";
        document.body.appendChild(frame);
        peeks[pair[0]] = frame;
      });
    }
    function rebuildPeeks() {                               // after an edit: the pages beside may show it too
      Object.keys(peeks).forEach(function (way) { peeks[way].remove(); delete peeks[way]; });
      setTimeout(buildPeeks, 300);
    }
    // as soon as the page's own content is there (not after every image: a phone swiping on soon had none yet)
    setTimeout(buildPeeks, 50);
    document.addEventListener("htmx:afterRequest", function (event) {
      const verb = event.detail && event.detail.requestConfig && event.detail.requestConfig.verb;
      if (verb && verb !== "get") rebuildPeeks();
    });

    // ---- what a touch starts on
    function stays(el) {                                    // never a page swipe from these
      for (let e = el; e && e !== body; e = e.parentElement) {
        if (e.matches("input, select, textarea, dialog, details[open], [data-no-swipe]")) return true;
        if (getComputedStyle(e).position === "fixed") return true;
      }
      return false;
    }
    function scrollers(el) {                                // boxes under the finger that scroll sideways
      const out = [];
      for (let e = el; e && e !== body; e = e.parentElement) {
        const o = getComputedStyle(e).overflowX;
        if ((o === "auto" || o === "scroll") && e.scrollWidth > e.clientWidth + 1) out.push(e);
      }
      return out;
    }
    function canScroll(box, dx) {                           // dx < 0: the finger goes left, the box's content right
      return dx < 0 ? box.scrollLeft + box.clientWidth < box.scrollWidth - 1 : box.scrollLeft > 1;
    }

    // ---- the bars move with the swipe (user's request 2026-11-11): the current tab's mark slides toward the tab it's
    // going to; into another section, this tabs line slides out and that section's (copied from the peek) slides in,
    // and the section's highlight moves to its icon. All by the same share as the page, and on finishing they end where
    // the new page draws them, so the hand-over doesn't show.
    const tabsLine = document.querySelector(".tabs");
    const sections = [...document.querySelectorAll(".rail nav a")];
    let bars = null;                                        // what the current swipe moves, made on its first move

    function setUpBars(way) {
      const to = way === "next" ? body.dataset.swipeNext : body.dataset.swipePrev;
      const b = { way: way, marks: null, incoming: null, pill: null, from: null, toIcon: null };
      const here = tabsLine && tabsLine.querySelector("a.active");
      const there = tabsLine && [...tabsLine.querySelectorAll("a")].find(function (a) { return new URL(a.href).pathname === to; });
      if (here && there) {                                  // the same section: the mark slides along the line
        const mark = document.createElement("span");
        mark.className = "tab-mark";
        tabsLine.appendChild(mark);
        tabsLine.classList.add("marking");
        b.marks = { el: mark, a: { left: here.offsetLeft, width: here.offsetWidth }, b: { left: there.offsetLeft, width: there.offsetWidth } };
      } else {                                              // another section: its tabs line comes in beside this one
        // (its tabs from the server: <body data-swipe-next-tabs / -prev-tabs>, deps.nav_for)
        const json = way === "next" ? body.dataset.swipeNextTabs : body.dataset.swipePrevTabs;
        if (json && tabsLine) {
          const line = tabsLine.cloneNode(false);
          line.classList.remove("has-controls");
          line.classList.add("tabs-incoming");
          line.removeAttribute("aria-label");
          line.setAttribute("aria-hidden", "true");
          JSON.parse(json).forEach(function (t) {
            const a = document.createElement("a");
            a.href = t.href;
            a.textContent = t.label;
            a.tabIndex = -1;
            if (t.active) a.className = "active";
            line.appendChild(a);
          });
          line.classList.add("marking");                     // its own mark hides: one mark travels instead
          document.body.appendChild(line);
          b.incoming = line;
          // the travelling mark: from the current tab (going out with this line) to the new one (coming in with
          // that one), measured before either moves
          const here2 = tabsLine.querySelector("a.active");
          const there2 = line.querySelector("a.active");
          if (here2 && there2 && getComputedStyle(tabsLine).display !== "none") {
            const box = tabsLine.getBoundingClientRect(), r1 = here2.getBoundingClientRect(), r2 = there2.getBoundingClientRect();
            const mark = document.createElement("span");
            mark.className = "tab-mark travelling";
            mark.style.top = (box.top + 5) + "px";
            document.body.appendChild(mark);
            tabsLine.classList.add("marking");
            b.travel = { el: mark, from: { left: r1.left, width: r1.width }, to: { left: r2.left, width: r2.width } };
          }
        }
        const i = sections.findIndex(function (a) { return a.classList.contains("active"); });
        const j = way === "next" ? i + 1 : i - 1;
        if (i >= 0 && sections[j]) {
          const pill = document.createElement("span");
          pill.className = "section-mark";
          sections[i].parentElement.insertBefore(pill, sections[0]);
          sections[i].parentElement.classList.add("marking");
          b.pill = { el: pill, a: { left: sections[i].offsetLeft, width: sections[i].offsetWidth },
                     b: { left: sections[j].offsetLeft, width: sections[j].offsetWidth } };
          pill.style.top = sections[i].offsetTop + "px";    // the real highlight's own height, not a guess
          pill.style.height = sections[i].offsetHeight + "px";
          b.from = sections[i]; b.toIcon = sections[j];
          b.plain = getComputedStyle(sections[j]).color;   // an icon's colour when it isn't the current one
        }
      }
      return b;
    }
    function blend(a, b, p) { return Math.round(a + (b - a) * p); }
    function moveBars(p, animate) {                         // p: 0 (here) .. 1 (there)
      if (!bars) return;
      const width = window.innerWidth, motion = animate ? "0.25s " + EASE : "";
      const sign = bars.way === "next" ? -1 : 1;
      if (bars.marks) {
        const m = bars.marks;
        m.el.style.transition = animate ? "left " + motion + ", width " + motion : "none";
        m.el.style.left = blend(m.a.left, m.b.left, p) + "px";
        m.el.style.width = blend(m.a.width, m.b.width, p) + "px";
      }
      if (bars.pill || bars.incoming) {
        if (tabsLine) {
          tabsLine.style.transition = animate ? "transform " + motion : "none";
          tabsLine.style.transform = "translateX(" + Math.round(sign * p * width) + "px)";
        }
        if (bars.incoming) {
          bars.incoming.style.transition = animate ? "transform " + motion : "none";
          bars.incoming.style.transform = "translateX(" + Math.round(-sign * (1 - p) * width) + "px)";
        }
        if (bars.travel) {                                  // where the two tabs are now, and the mark between them
          const t = bars.travel;
          const fromLeft = t.from.left + sign * p * width, toLeft = t.to.left - sign * (1 - p) * width;
          t.el.style.transition = animate ? "left " + motion + ", width " + motion : "none";
          t.el.style.left = blend(fromLeft, toLeft, p) + "px";
          t.el.style.width = blend(t.from.width, t.to.width, p) + "px";
        }
      }
      if (bars.pill) {
        const m = bars.pill;
        m.el.style.transition = animate ? "left " + motion + ", width " + motion : "none";
        m.el.style.left = blend(m.a.left, m.b.left, p) + "px";
        m.el.style.width = blend(m.a.width, m.b.width, p) + "px";
        const pct = Math.round(p * 100);
        [[bars.from, 100 - pct], [bars.toIcon, pct]].forEach(function (pair) {
          pair[0].style.transition = animate ? "color " + motion : "none";
          pair[0].style.color = "color-mix(in srgb, var(--accent) " + pair[1] + "%, " + bars.plain + ")";
        });
      }
    }
    function clearBars() {
      if (!bars) return;
      if (bars.marks) bars.marks.el.remove();
      if (bars.pill) { bars.pill.el.remove(); bars.from.style.color = bars.toIcon.style.color = ""; }
      if (bars.incoming) bars.incoming.remove();
      if (bars.travel) bars.travel.el.remove();
      if (tabsLine) { tabsLine.classList.remove("marking"); tabsLine.style.transform = ""; tabsLine.style.transition = ""; }
      sections.forEach(function (a) { a.style.transition = ""; });
      if (sections[0]) sections[0].parentElement.classList.remove("marking");
      bars = null;
    }

    // ---- moving
    function place(dx, animate) {
      const width = window.innerWidth;
      const way = dx < 0 ? "next" : "prev";
      const there = dx < 0 ? body.dataset.swipeNext : body.dataset.swipePrev;
      const shift = there ? dx : dx * 0.15;                 // a little resistance where there's no page that way
      const motion = animate ? "transform 0.25s " + EASE : "none";
      if (page) { page.style.transition = motion; page.style.transform = "translateX(" + Math.round(shift) + "px)"; }
      if (there && dx !== 0 && (!bars || bars.way !== way)) { clearBars(); bars = setUpBars(way); }
      if (bars) moveBars(bars.way === way && there ? Math.min(1, Math.abs(shift) / width) : 0, animate);
      ["next", "prev"].forEach(function (w) {
        const frame = peeks[w];
        if (!frame) return;
        const shown = w === way && there;
        frame.classList.toggle("showing", Boolean(shown) || (animate && frame.classList.contains("showing")));
        frame.style.transition = motion;
        frame.style.transform = "translateX(" + Math.round((w === "next" ? width : -width) + (shown ? shift : 0)) + "px)";
      });
    }
    function settle(after) {
      const done = function () {
        if (page) { page.style.transition = ""; page.style.transform = ""; }
        Object.keys(peeks).forEach(function (w) { peeks[w].classList.remove("showing"); peeks[w].style.transition = ""; });
        clearBars();
        if (after) after();
      };
      if (page && page.style.transform) { place(0, true); setTimeout(done, 260); } else done();
    }

    document.addEventListener("touchstart", function (event) {
      start = null;
      if (!phone.matches || event.touches.length !== 1) return;
      const t = event.touches[0];
      if (t.clientX < EDGE || t.clientX > window.innerWidth - EDGE || stays(event.target)) return;
      start = { x: t.clientX, x0: t.clientX, y: t.clientY, sideways: false, boxes: scrollers(event.target), track: [] };
    }, { passive: true });
    document.addEventListener("touchmove", function (event) {
      if (!start) return;
      const t = event.touches[0];
      const dx = t.clientX - start.x, dy = t.clientY - start.y;
      if (!start.sideways) {
        if (Math.abs(dy) > LOCK && Math.abs(dy) > Math.abs(dx)) { start = null; return; }   // scrolling the page
        if (Math.abs(dx) < LOCK || Math.abs(dx) < RATIO * Math.abs(dy)) return;
        // a gallery or table under the finger that can still scroll this way does; at its end, the page moves
        if (start.boxes.some(function (box) { return canScroll(box, dx); })) { start = null; return; }
        start.sideways = true;
        start.x = t.clientX;                                // the page starts moving from here, no jump (x0 stays)
      }
      const moved = t.clientX - start.x;
      start.track.push({ x: t.clientX, at: event.timeStamp });
      if (start.track.length > 5) start.track.shift();
      if (!calm.matches) place(moved, false);
    }, { passive: true });
    document.addEventListener("touchcancel", function () { start = null; settle(); }, { passive: true });
    document.addEventListener("touchend", function (event) {
      if (!start || !start.sideways) { start = null; return; }
      const t = event.changedTouches[0];
      const dx = t.clientX - start.x0;                      // the whole swipe, from where the finger first touched
      const track = start.track;
      start = null;
      const first = track[0], last = track[track.length - 1];
      const speed = first && last && last.at > first.at ? (last.x - first.x) / (last.at - first.at) : 0;   // px/ms
      const to = dx < 0 ? body.dataset.swipeNext : body.dataset.swipePrev;
      const far = Math.abs(dx) > SHARE * window.innerWidth;
      const flick = Math.abs(speed) > FLICK && Math.sign(speed) === Math.sign(dx) && Math.abs(dx) > 30;
      if (!to || !(far || flick) || String(window.getSelection ? window.getSelection() : "")) { settle(); return; }
      const way = dx < 0 ? "next" : "prev";
      const frame = peeks[way];
      if (frame && !calm.matches) {
        // finish the carousel move, then load the page the frame already shows, with no second slide
        const width = window.innerWidth;
        page.style.transition = frame.style.transition = "transform 0.22s " + EASE;
        page.style.transform = "translateX(" + (dx < 0 ? -width : width) + "px)";
        frame.style.transform = "translateX(0px)";
        moveBars(1, true);
        try { sessionStorage.setItem("ft-swipe", "peek"); } catch (err) { /* fine */ }
        setTimeout(function () { window.location.href = to; }, 220);
        return;
      }
      // no peek: carry on out while the next page arrives, which then slides in (app.css)
      try { sessionStorage.setItem("ft-swipe", way); } catch (err) { /* no slide, still goes */ }
      // into another section: the tabs line that came in with the finger is what the new page's replaces (they're the
      // same), so it doesn't slide in a second time with the page
      if (bars && bars.incoming && tabsLine) {
        tabsLine.style.viewTransitionName = "none";
        bars.incoming.style.viewTransitionName = "ft-tabs";
      }
      if (page && !calm.matches) {
        page.style.transition = "transform 0.3s " + EASE;
        page.style.transform = "translateX(" + (dx < 0 ? "-100%" : "100%") + ")";
        moveBars(1, true);
      }
      window.location.href = to;
    }, { passive: true });
    // Back to this page from the history cache: nothing left moved
    window.addEventListener("pageshow", function () {
      if (page) { page.style.transform = ""; page.style.transition = ""; }
      Object.keys(peeks).forEach(function (w) { peeks[w].classList.remove("showing"); peeks[w].style.transform = ""; });
      clearBars();
    });
  })();

  // Phones, the Overview (docs/phone-plan.md phase 6; user's choice 2026-11-11): after the tiles, the cards in the order
  // of where each would sit in the two columns - each column's cards stacked, then all of them by their top, the left
  // one first on a tie - so the columns take turns rather than one following the other. Heights are the phone's own
  // (charts have fixed heights there), measured once the page is laid out.
  (function () {
    const grid = document.querySelector("[data-overview-order]");
    if (!grid || !window.matchMedia("(max-width: 640px)").matches) return;
    const cards = [];
    [...grid.children].forEach(function (col, column) {
      let top = 0;
      [...col.children].forEach(function (card) {
        cards.push({ card: card, top: top, column: column });
        const cs = getComputedStyle(card);
        top += card.offsetHeight + parseFloat(cs.marginTop) + parseFloat(cs.marginBottom);
      });
    });
    cards.sort(function (a, b) { return a.top - b.top || a.column - b.column; });
    cards.forEach(function (c, i) { c.card.style.order = String(i); });
  })();

  // Phones, an account's page (user's choice 2026-11-11): the first 25 transactions, then Show more for the rest (the
  // list stays one table; the later rows, and the date headings among them, just hide). Again after the list refreshes
  // (a filter or a sort swaps #acct-txns).
  (function () {
    const FIRST = 25;
    function limit() {
      const box = document.querySelector("#acct-txns");
      if (!box || box.dataset.showAll || !window.matchMedia("(max-width: 640px)").matches) return;
      const rows = [...box.querySelectorAll("table.txns > tbody > tr")];
      let seen = 0, later = false;
      rows.forEach(function (tr) {
        if (!tr.classList.contains("date-head") && !tr.classList.contains("edit-panel") && !tr.classList.contains("rule-panel")) seen++;
        if (seen > FIRST) later = true;
        if (later) tr.classList.add("phone-later");
      });
      if (!later || box.querySelector(".show-more")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary show-more phone-only";
      button.textContent = "Show more";
      button.addEventListener("click", function () {
        box.querySelectorAll("tr.phone-later").forEach(function (tr) { tr.classList.remove("phone-later"); });
        box.dataset.showAll = "1";                          // this list stays whole (an edit panel's swap won't re-hide)
        button.remove();
      });
      box.querySelector(".table-wrap").after(button);
    }
    limit();
    document.addEventListener("htmx:afterSwap", limit);   // a new list (filter, sort) is limited again; one shown whole isn't
  })();

  // Phones, Credit cards: a card's Redeemed / Cash rewards line up with its figures above (Spent, Earned): the row gets
  // two columns split where the card's right column starts (docs/phone-plan.md phase 6, 2026-11-11).
  (function () {
    if (!window.matchMedia("(max-width: 640px)").matches) return;
    document.querySelectorAll(".redeemed-row").forEach(function (row) {
      const end = row.parentElement && row.parentElement.querySelector(".card-measures .measure-col.end");
      if (!end || row.children.length < 2) return;
      const split = end.getBoundingClientRect().left - row.getBoundingClientRect().left;
      if (split <= 0) return;
      row.dataset.split = "";
      row.style.gridTemplateColumns = Math.round(split) + "px minmax(0, 1fr)";
    });
  })();
})();
