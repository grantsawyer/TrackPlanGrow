// Shared charts (milestone 13). Loaded from base.html after Chart.js and ui.js.
//
// Markup (see templates/_charts.html):
//   <canvas data-chart="bullet" data-source="x-data" aria-label="..."></canvas>
//   <script type="application/json" id="x-data">{...}</script>
// Each chart kind below reads its JSON, draws with colors from CSS custom
// properties (app.css: validated light and dark steps), and appends a
// "Show data" table built from the same data, so nothing is hover-only.
// Text is inserted with textContent (labels come from bank data).
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;

  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const moneyWhole = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  const fmt = window.fintrack;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function withAlpha(hex, alpha) {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  // Categorical slots in fixed order (never cycled); "Other" is always the gray.
  function slot(i) {
    return cssVar(`--series-${i + 1}`);
  }
  // A lighter shade of one hue (mixed toward the card surface): segments of one kind share its colour (Savings cards).
  const SHADES = [0, 0.4, 0.6, 0.75];
  function shade(hex, i) {
    const t = SHADES[Math.min(i, SHADES.length - 1)], to = parseInt(cssVar("--surface").replace("#", ""), 16);
    const from = parseInt(hex.replace("#", ""), 16);
    const mix = (shift) => Math.round(((from >> shift) & 255) * (1 - t) + ((to >> shift) & 255) * t);
    return "#" + [16, 8, 0].map((sh) => mix(sh).toString(16).padStart(2, "0")).join("");
  }

  // Vertical crosshair at the hovered x (line and area charts).
  const crosshair = {
    id: "ftCrosshair",
    afterDraw(chart) {
      const active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      const x = active[0].element.x;
      const { top, bottom } = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = cssVar("--chart-axis");
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  // Y range for the user's rule: data min..max plus 5% of the range each side.
  function paddedRange(values) {
    const v = values.filter((x) => x !== null && x !== undefined);
    if (!v.length) return {};
    const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo;
    const pad = span > 0 ? span * 0.05 : Math.max(Math.abs(hi) * 0.05, 100);
    return { min: lo - pad, max: hi + pad };
  }

  // A % axis's range: 5% of the span each side, or ±0.5 points when flat (paddedRange's $100 minimum is for money)
  function pctRange(values) {
    const v = values.filter((x) => x !== null && x !== undefined);
    if (!v.length) return {};
    const lo = Math.min(...v), hi = Math.max(...v), pad = hi > lo ? (hi - lo) * 0.05 : 0.5;
    return { min: lo - pad, max: hi + pad };
  }

  function baseOptions(extra) {
    const muted = cssVar("--muted");
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cssVar("--surface"), titleColor: muted, bodyColor: cssVar("--fg"),
          borderColor: cssVar("--line-strong"), borderWidth: 1, padding: 10, boxPadding: 4,
          usePointStyle: true,
        },
      },
      scales: {},
    }, extra);
  }

  // Phones (≤640px; docs/phone-plan.md phase 3): a compact legend line and at most PHONE_TICKS labels on a date or
  // category axis, so the shorter charts never crowd (Chart.js still skips any that would touch).
  const phone = window.matchMedia("(max-width: 640px)");
  const PHONE_TICKS = 4;

  function legend() {
    const muted = cssVar("--muted");
    return { display: true, position: "top", align: "start",
             labels: phone.matches
               ? { color: muted, boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 10, font: { size: 12 } }
               : { color: muted, boxWidth: 12, boxHeight: 12, usePointStyle: true, padding: 14 } };
  }

  function moneyAxis(extra) {
    return Object.assign({
      grid: { color: cssVar("--chart-grid") }, border: { display: false },
      afterBuildTicks: fmt.roundTicks,
      ticks: { color: cssVar("--muted"), callback: fmt.axisMoney },
    }, extra);
  }

  function categoryAxis(extra) {
    const axis = Object.assign({
      grid: { display: false }, border: { color: cssVar("--chart-axis") },
      ticks: { color: cssVar("--muted"), maxRotation: 0, autoSkip: true, autoSkipPadding: 16 },
    }, extra);
    // a phone: no more than PHONE_TICKS labels where they're skipped anyway (an axis that lists every label keeps them)
    if (phone.matches && axis.ticks && axis.ticks.autoSkip !== false) {
      axis.ticks = Object.assign({}, axis.ticks, { maxTicksLimit: Math.min(axis.ticks.maxTicksLimit || PHONE_TICKS, PHONE_TICKS) });
    }
    return axis;
  }

  function dateAxis(extra) {
    // A chart's own `ticks` are merged into the shared ones (flat labels, auto-skip), not swapped for them: a plain
    // Object.assign dropped maxRotation and tilted the labels on narrow charts (2026-10-24).
    const own = (extra && extra.ticks) || {};
    const axis = categoryAxis(Object.assign({}, extra, { ticks: Object.assign({}, categoryAxis().ticks, own) }));
    axis.ticks.callback = fmt.axisDate;
    return axis;
  }

  const BAR = { maxBarThickness: 24, borderRadius: 4, borderSkipped: "start" };

  // ---------------------------------------------------------------- chart kinds
  // Each returns { config, table: { headers, rows } }.

  // Comparison lines on the Investments performance chart: benchmark cards and table rows add
  // one each, up to MAX_COMPARE at once (palette slots 2-5; slot 1 is "Your stocks"). The canvas
  // keeps the chosen keys, oldest first, in data-compare ("SPY,sec:abc").
  const MAX_COMPARE = 4;
  function compareKeys(el) {
    return (el.dataset.compare || "").split(",").filter(Boolean);
  }

  // Each day's price: the latest on or before it (weekends repeat Friday); null before the first.
  function pricesOn(prices, days) {
    let next = 0, price = null;
    return days.map((d) => {                       // both oldest first
      while (next < prices.length && prices[next][0] <= d) { price = prices[next][1]; next += 1; }
      return price;
    });
  }
  // % change from the first day with a price (the range start, or a later first price).
  function changesFrom(prices, days) {
    const p = pricesOn(prices, days);
    const base = p.findIndex((x) => x);
    return { base, values: p.map((x, i) => (base === -1 || i < base || !x ? null : x / p[base] - 1)) };
  }
  const signedPct = (v, digits) => (v === null || v === undefined ? "—"
    : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(digits) + "%");

  function dailyLines(data, el) {
    const days = parseInt(el.dataset.days || "0", 10);
    // The range ends on the last trading day (data.end, the newest live quote's date), so
    // overnight / at weekends "1 day" is the last session, matching the cards' "today".
    const end = data.end ? new Date(data.end + "T12:00:00") : new Date();
    end.setHours(12, 0, 0, 0);                   // midday: no DST edge when stepping back
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const first = Object.values(data.series).reduce((m, s) => (s.prices.length && s.prices[0][0] < m ? s.prices[0][0] : m), iso(end));
    const labels = [];
    const day = days ? new Date(end.getTime() - (days - 1) * 86400000) : new Date(first + "T12:00:00");
    for (; iso(day) <= iso(end); day.setDate(day.getDate() + 1)) labels.push(iso(day));
    const last = labels.length - 1;

    // Your stocks: holdings with a change by today, weights = current values among them.
    const parts = data.mine.map((key) => ({ s: data.series[key], c: changesFrom(data.series[key].prices, labels) }))
      .filter((x) => x.c.base !== -1 && x.c.base < last && x.c.values[last] !== null && x.s.value_cents > 0);
    const weight = parts.reduce((t, x) => t + x.s.value_cents, 0);
    const start = parts.length ? Math.min(...parts.map((x) => x.c.base)) : -1;
    const mine = labels.map((d, i) => (start === -1 || i < start || !weight ? null
      : parts.reduce((t, x) => t + x.s.value_cents * (x.c.values[i] === null ? 0 : x.c.values[i]), 0) / weight));

    const lines = [{ label: "Your stocks", values: mine }].concat(compareKeys(el)
      .filter((key) => data.series[key])
      .map((key) => ({ label: key.startsWith("sec:") ? data.series[key].label : `${data.series[key].label} (${key})`,
                       values: changesFrom(data.series[key].prices, labels).values })));
    return { labels, lines };
  }

  // The "1 day" lines from minute prices ({series: {key: [[unix seconds, price], …]}, base: {key: previous
  // close}}): "Your stocks" = value-weighted like the daily line, over holdings with minute prices.
  function intradayLines(data, minutes, el) {
    const labels = Array.from(new Set(Object.values(minutes.series).flatMap((s) => s.map((p) => p[0]))))
      .sort((a, b) => a - b);
    const changes = (key) => pricesOn(minutes.series[key], labels).map((p) => (p ? p / minutes.base[key] - 1 : null));
    const parts = data.mine.filter((key) => minutes.series[key] && data.series[key].value_cents > 0)
      .map((key) => ({ weight: data.series[key].value_cents, values: changes(key) }));
    const weight = parts.reduce((t, x) => t + x.weight, 0);
    const mine = labels.map((t, i) => (!weight || parts.every((x) => x.values[i] === null) ? null
      : parts.reduce((t2, x) => t2 + x.weight * (x.values[i] === null ? 0 : x.values[i]), 0) / weight));
    const lines = [{ label: "Your stocks", values: mine }].concat(compareKeys(el)
      .filter((key) => data.series[key] && minutes.series[key])
      .map((key) => ({ label: key.startsWith("sec:") ? data.series[key].label : `${data.series[key].label} (${key})`,
                       values: changes(key) })));
    return { labels, lines };
  }

  // The chord diagram (KINDS.chord): SVG in the chart's box, the canvas hidden. Angles run clockwise from 12 o'clock.
  // Each node's arc holds its sent segments (in its receivers' order around the circle), then its received ones; a ribbon
  // joins a sent segment to the matching received one through the centre, ending in a point. Hovering an arc highlights
  // its ribbons; the tooltip (a div in the box) names an arc's sent / received or a ribbon's route.
  function drawChord(box, canvas, data) {
    const NS = "http://www.w3.org/2000/svg";
    const make = (tag, attrs) => {
      const el = document.createElementNS(NS, tag);
      Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
      return el;
    };
    const colorOf = (s) => (!s || s === "slot-other" ? cssVar("--series-other") : slot(parseInt(s.slice(5), 10) - 1));
    // nodes: "L:a3" and "R:a3" are one account
    const nodes = new Map();
    data.flows.forEach((f) => {
      [[f.from, "out"], [f.to, "in"]].forEach(([full, side]) => {
        const key = full.slice(2);
        if (!nodes.has(key)) nodes.set(key, { key, label: data.labels[full] || key, color: colorOf(data.slots[full]),
                                              account: key[0] === "a", out: 0, in: 0, slot: data.slots[full] || "" });
        nodes.get(key)[side] += f.flow;
      });
    });
    const slotNo = (n) => (n.slot.startsWith("slot-") && n.slot !== "slot-other" ? parseInt(n.slot.slice(5), 10) : 99);
    const list = [...nodes.values()].sort((a, b) => (b.account - a.account) || (slotNo(a) - slotNo(b))
                                                     || (b.out + b.in - a.out - a.in));
    const index = new Map(list.map((n, i) => [n.key, i]));
    const total = list.reduce((t, n) => t + n.out + n.in, 0);
    if (!total) return;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H) return;
    const narrow = W < 520, side = narrow ? 70 : 130;
    const r = Math.max(50, Math.min(H / 2 - 24, W / 2 - side)), band = 12, cx = W / 2, cy = H / 2;
    const pad = Math.min(0.04, (2 * Math.PI * 0.12) / list.length);           // gaps: at most 12% of the circle
    const perDollar = (2 * Math.PI - pad * list.length) / total;
    const P = (a, rad) => `${(cx + rad * Math.sin(a)).toFixed(1)},${(cy - rad * Math.cos(a)).toFixed(1)}`;
    const large = (span) => (span > Math.PI ? 1 : 0);
    let at = 0;
    list.forEach((n) => { n.a0 = at; n.a1 = at + (n.out + n.in) * perDollar; n.cursor = at; at = n.a1 + pad; });
    // sent segments first (receivers in circle order), then received ones (senders in circle order)
    const flows = data.flows.map((f) => ({ f, s: nodes.get(f.from.slice(2)), t: nodes.get(f.to.slice(2)) }));
    list.forEach((n) => {
      flows.filter((x) => x.s === n).sort((x, y) => index.get(x.t.key) - index.get(y.t.key)).forEach((x) => {
        x.s0 = n.cursor; x.s1 = n.cursor += x.f.flow * perDollar;
      });
      n.split = n.cursor;                                   // sent | received
      flows.filter((x) => x.t === n).sort((x, y) => index.get(x.s.key) - index.get(y.s.key)).forEach((x) => {
        x.t0 = n.cursor; x.t1 = n.cursor += x.f.flow * perDollar;
      });
    });

    const svg = make("svg", { class: "svg-chart chord", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
                              "aria-label": canvas.getAttribute("aria-label") || "Transfers between accounts" });
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    const show = (event, lines) => {
      tip.replaceChildren(...lines.map((t, i) => { const d = document.createElement("div"); d.textContent = t;
                                                   if (i === 0) d.className = "chart-tip-title"; return d; }));
      tip.hidden = false;
      const rect = box.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      tip.style.left = Math.min(x + 12, W - tip.offsetWidth - 4) + "px";
      tip.style.top = Math.max(4, y - tip.offsetHeight - 10) + "px";
    };
    const hide = () => { tip.hidden = true; svg.classList.remove("focus"); svg.querySelectorAll(".on").forEach((e) => e.classList.remove("on")); };

    // a ribbon picks its flow on the list (data.flows[].url, user's request 2026-10-31); data.picked = the picked one's
    // "<from>><to>": the rest dim
    const flowKey = (f) => f.from.slice(2) + ">" + f.to.slice(2);
    if (data.picked) svg.classList.add("picked");
    const ribbons = make("g", { class: "ribbons" });
    flows.forEach((x) => {
      const head = Math.min(10, r * 0.12), tm = (x.t0 + x.t1) / 2;
      const d = `M${P(x.s0, r)} A${r},${r} 0 ${large(x.s1 - x.s0)} 1 ${P(x.s1, r)} Q${cx},${cy} ${P(x.t0, r - head)}`
              + ` L${P(tm, r)} L${P(x.t1, r - head)} Q${cx},${cy} ${P(x.s0, r)} Z`;
      const path = make("path", { d, fill: x.s.color, "fill-opacity": "0.55", class: "ribbon" });
      path.dataset.s = x.s.key;
      path.dataset.t = x.t.key;
      const lines = [`${x.s.label} → ${x.t.label}`, `${money.format(x.f.flow)} (${x.f.count})`];
      path.addEventListener("mousemove", (e) => show(e, lines));
      path.addEventListener("mouseleave", hide);
      if (x.f.url) {
        path.classList.add("pickable");
        path.addEventListener("click", () => { window.location.href = x.f.url; });
      }
      if (data.picked && flowKey(x.f) === data.picked) path.classList.add("lit");
      ribbons.append(path);
    });
    svg.append(ribbons);

    const arcs = make("g", { class: "arcs" }), labels = [];
    const sector = (a0, a1, r0, r1) => `M${P(a0, r1)} A${r1},${r1} 0 ${large(a1 - a0)} 1 ${P(a1, r1)} L${P(a1, r0)}`
                                       + ` A${r0},${r0} 0 ${large(a1 - a0)} 0 ${P(a0, r0)} Z`;
    list.forEach((n) => {
      const g = make("g", { class: "node" });
      if (n.split > n.a0) g.append(make("path", { d: sector(n.a0, n.split, r + 2, r + 2 + band), fill: n.color }));
      if (n.a1 > n.split) g.append(make("path", { d: sector(n.split, n.a1, r + 2, r + 2 + band), fill: shade(n.color, 1) }));
      const lines = [n.label, `Sent ${money.format(n.out)}`, `Received ${money.format(n.in)}`];
      g.addEventListener("mousemove", (e) => {
        svg.classList.add("focus");
        ribbons.querySelectorAll(".ribbon").forEach((p) => p.classList.toggle("on", p.dataset.s === n.key || p.dataset.t === n.key));
        show(e, lines);
      });
      g.addEventListener("mouseleave", hide);
      arcs.append(g);
      // its name outside the arc, when the arc is long enough to own it (else: the tooltip and Show data)
      if (n.a1 - n.a0 >= 0.05) {
        const mid = (n.a0 + n.a1) / 2;
        const [x, y] = P(mid, r + band + 8).split(",").map(Number);
        labels.push({ n, mid, x, y, at: y, right: Math.sin(mid) >= 0 });
      }
    });
    svg.append(arcs);
    // labels: on each side, pushed apart to a line's height (top to bottom, then back up from the bottom edge), with a
    // thin leader to the arc when one had to move
    const LINE = 15, max = narrow ? 12 : 22;
    [true, false].forEach((right) => {
      const side = labels.filter((l) => l.right === right).sort((a, b) => a.y - b.y);
      side.forEach((l, i) => { if (i) l.at = Math.max(l.at, side[i - 1].at + LINE); });
      for (let i = side.length - 1; i >= 0; i--) {
        const limit = i === side.length - 1 ? H - 8 : side[i + 1].at - LINE;
        side[i].at = Math.max(8, Math.min(side[i].at, limit));
      }
    });
    labels.forEach((l) => {
      const dx = l.right ? 6 : -6;
      if (Math.abs(l.at - l.y) > 3) {
        arcs.append(make("path", { d: `M${l.x.toFixed(1)},${l.y.toFixed(1)} L${(l.x + dx).toFixed(1)},${l.at.toFixed(1)}`,
                                   stroke: cssVar("--muted"), "stroke-width": "1", fill: "none" }));
      }
      const text = make("text", { x: (l.x + dx).toFixed(1), y: l.at.toFixed(1), "text-anchor": l.right ? "start" : "end",
                                  "dominant-baseline": "middle", fill: cssVar("--fg"), class: "chord-label" });
      text.textContent = l.n.label.length > max ? l.n.label.slice(0, max - 1) + "…" : l.n.label;
      arcs.append(text);
    });
    box.append(svg, tip);
  }

  // SVG kinds (chord) are redrawn when their box changes size; Chart.js charts resize themselves.
  const svgResize = typeof ResizeObserver === "function" ? new ResizeObserver((entries) => {
    entries.forEach((entry) => {
      const canvas = entry.target.querySelector("canvas[data-chart]");
      const w = Math.round(entry.contentRect.width);
      if (canvas && w && w !== Number(entry.target.dataset.drawnWidth)) render(canvas);
    });
  }) : null;

  const KINDS = {
    // One series over time (net worth). Optional range buttons slice the points.
    // With a range (data-days), the x-axis always spans the whole range ending today;
    // days with no data stay empty (null) rather than stretching what exists.
    // data-unit="pct" (the Net worth page's % mode, user's request 2026-10-13): v = % change since the range's start,
    // on a % axis; the tooltip and table add net worth in dollars (p.net).
    area(data, el) {
      const days = parseInt(el.dataset.days || "0", 10);
      const isPct = el.dataset.unit === "pct";
      const pctText = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + (Math.round(v * 10) / 10 + 0).toFixed(1) + "%");
      const byDate = new Map(data.map((p) => [p.d, p]));
      let points = data;
      if (days) {
        points = [];
        const day = new Date();
        day.setHours(12, 0, 0, 0);                 // midday: no DST edge when stepping back
        day.setDate(day.getDate() - (days - 1));
        for (let k = 0; k < days; k += 1) {
          const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
          points.push(byDate.get(iso) || { d: iso, v: null, carried: false });
          day.setDate(day.getDate() + 1);
        }
      }
      const color = slot(0);
      const values = points.map((p) => p.v);
      const known = points.filter((p) => p.v !== null);
      const datasets = [{
        data: values, borderColor: color, borderWidth: 2, pointHoverRadius: 4, spanGaps: false,
        // A lone day with data would be invisible as a line: show it as a dot.
        pointRadius: known.length === 1 ? 4 : 0, pointBackgroundColor: color,
        fill: "origin", backgroundColor: withAlpha(color, 0.1), tension: 0,
        segment: { borderDash: (ctx) => (points[ctx.p1DataIndex] && points[ctx.p1DataIndex].carried ? [4, 4] : undefined) },
      }];
      return {
        config: {
          type: "line",
          data: { labels: points.map((p) => p.d), datasets },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: {
                  title: (items) => fmt.dateLabel(items[0].label) + (points[items[0].dataIndex].carried ? " (no sync)" : ""),
                  label: (item) => {
                    const p = points[item.dataIndex];
                    if (p.assets === undefined) return isPct ? pctText(item.raw) : money.format(item.raw);
                    return (isPct ? ["Change  " + pctText(p.v)] : []).concat(
                      ["Net worth  " + money.format(isPct ? p.net : p.v), "Assets  " + money.format(p.assets),
                       "Liabilities  " + money.format(p.liabilities)]);
                  },
                },
              }),
            }),
            scales: { x: dateAxis({ ticks: { maxTicksLimit: 7 } }),
                      y: isPct
                        ? Object.assign({ grid: { color: cssVar("--chart-grid") }, border: { display: false },
                                          afterBuildTicks: fmt.roundTicks,          // round labels, not the padded ends
                                          ticks: { color: cssVar("--muted"), callback: (v) => `${Number(v.toFixed(2))}%` } },
                                        pctRange(values))
                        : moneyAxis(paddedRange(values)) },
          }),
          plugins: [crosshair],
        },
        table: known.length && known[0].assets !== undefined
          ? { headers: ["Date"].concat(isPct ? ["Change"] : [], ["Assets", "Liabilities", "Net worth"]),
              rows: known.slice().reverse().map((p) => [fmt.dateLabel(p.d) + (p.carried ? " (no sync)" : "")].concat(
                isPct ? [pctText(p.v)] : [],
                [money.format(p.assets), money.format(p.liabilities), money.format(isPct ? p.net : p.v)])) }
          : { headers: ["Date", "Value"], rows: known.map((p) => [fmt.dateLabel(p.d), money.format(p.v)]) },
      };
    },

    // Investments performance (% change over the range; user's request 2026-10-07).
    // data = { series: {key: {label, prices: [[date, price], …], value_cents?}}, mine: [holding keys] }.
    // "Your stocks" = your holdings' % changes weighted by current value, each from its price at the
    // range start (or its first price, counting 0 before it) - the Your stocks card's formula, so the
    // last point equals the card. Toggled keys (data-compare) add each security's own % change.
    performance(data, el) {
      // "1 day" (data-range="1") with minute prices: the last session minute by minute, % change vs the
      // previous close (user's request 2026-10-27); otherwise daily prices, as below.
      const minutes = el.dataset.range === "1" && data.intraday && Object.keys(data.intraday.series).length
        ? data.intraday : null;
      const { labels, lines } = minutes ? intradayLines(data, minutes, el) : dailyLines(data, el);
      const timeOf = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      const xText = minutes ? timeOf : fmt.dateLabel;
      const datasets = lines.map((line, i) => ({
        label: line.label, data: line.values, borderColor: slot(i), backgroundColor: i === 0 ? withAlpha(slot(0), 0.1) : slot(i),
        borderWidth: 2, pointRadius: line.values.filter((v) => v !== null).length === 1 ? 4 : 0, pointHoverRadius: 4,
        fill: i === 0 ? "origin" : false, spanGaps: false, tension: 0,
      }));
      // Y range: the user's rule (data min..max plus 5% of the range each side); flat data: ±0.5%.
      const all = lines.flatMap((l) => l.values).filter((v) => v !== null);
      const lo = all.length ? Math.min(...all, 0) : -0.01, hi = all.length ? Math.max(...all, 0) : 0.01;
      const pad = hi > lo ? (hi - lo) * 0.05 : 0.005;
      return {
        config: {
          type: "line",
          data: { labels, datasets },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: lines.length > 1 ? legend() : { display: false },
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: { title: (items) => xText(items[0].label),
                             label: (item) => (item.raw === null ? null : `${signedPct(item.raw, minutes ? 2 : 1)}  ${item.dataset.label}`) },
              }),
            }),
            scales: {
              x: minutes
                // Minutes: a label on each hour (10 AM, 11 AM, …) rather than wherever auto-skip lands.
                ? categoryAxis({ ticks: Object.assign(categoryAxis().ticks, { autoSkip: false,
                    callback(value) {           // the first point of each hour after the first (4 PM can have two)
                      const at = new Date(this.getLabelForValue(value) * 1000);
                      const before = value > 0 ? new Date(this.getLabelForValue(value - 1) * 1000) : null;
                      const every = this.chart.width < 480 ? 2 : 1;          // phones: every other hour
                      return before && before.getHours() !== at.getHours() && at.getHours() % every === 0
                        ? at.toLocaleTimeString([], { hour: "numeric" }) : null;
                    } }) })
                : dateAxis({ ticks: { maxTicksLimit: 7 } }),
              y: { min: lo - pad, max: hi + pad, grid: { color: cssVar("--chart-grid") }, border: { display: false },
                   afterBuildTicks: fmt.roundTicks,
                   ticks: { color: cssVar("--muted"), callback: (v) => `${Number((v * 100).toFixed(1))}%` } },
            },
          }),
          plugins: [crosshair],
        },
        table: { headers: [minutes ? "Time" : "Date"].concat(lines.map((l) => l.label)),
                 rows: labels.map((d, i) => i).filter((i) => lines.some((l) => l.values[i] !== null))
                   .map((i) => [xText(labels[i])].concat(lines.map((l) => signedPct(l.values[i], minutes ? 2 : 1)))) },
      };
    },

    // This month (accent) vs last month (gray context): cumulative, by day of month.
    lines(data) {
      const accent = slot(0), context = cssVar("--series-other");
      const thisName = fmt.dateLabel(data.this_month), lastName = fmt.dateLabel(data.last_month);
      return {
        config: {
          type: "line",
          data: {
            labels: data.days,
            datasets: [
              { label: thisName, data: data.this, borderColor: accent, backgroundColor: accent, borderWidth: 2,
                pointRadius: (ctx) => (ctx.dataIndex === data.this.length - 1 ? 4 : 0), pointBorderColor: cssVar("--surface"),
                pointBorderWidth: 2, tension: 0 },
              { label: lastName, data: data.last, borderColor: context, backgroundColor: context, borderWidth: 2,
                pointRadius: 0, tension: 0 },
            ],
          },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: legend(),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: { title: (items) => "Day " + items[0].label,
                             label: (item) => `${money.format(item.raw)}  ${item.dataset.label}` },
              }),
            }),
            scales: { x: categoryAxis({ ticks: { maxTicksLimit: 8 } }), y: moneyAxis({ beginAtZero: true }) },
          }),
          plugins: [crosshair],
        },
        table: { headers: ["Day", thisName, lastName],
                 rows: data.days.map((d, i) => [d, i < data.this.length ? money.format(data.this[i]) : "—",
                                                i < data.last.length ? money.format(data.last[i]) : "—"]) },
      };
    },

    // One account's projected balance (today + N days) with the low-balance line. Days with
    // events (paychecks, bills, card statements) get a dot; the tooltip lists them.
    // data-pick selects the account (index into data.accounts).
    balance(data, el) {
      const picked = data.accounts[parseInt(el.dataset.pick || "0", 10)] || data.accounts[0];
      // Range buttons (the Cash flow page: 30 / 90 / 180) set data-days: today + that many days. Without them, all.
      const days = parseInt(el.dataset.days || "0", 10);
      const n = days > 0 ? days + 1 : picked.labels.length;
      const acct = { labels: picked.labels.slice(0, n), balance: picked.balance.slice(0, n), events: picked.events.slice(0, n) };
      const color = slot(0), lowColor = cssVar("--status-critical");
      const values = acct.balance;
      // Optional (the Cash flow page's Balance history, 2026-10-24): `label` for the line, `todayIndex` (the point
      // marked "today"; default the first), `dots: false` (no dot on each day with events: history has them most days),
      // `range` ([low, high] in dollars) so two charts share one y-axis.
      // `legend: false` hides the legend (the Cash flow page's charts, 2026-10-24)
      const todayIndex = data.todayIndex === undefined ? 0 : data.todayIndex;
      const dots = data.dots !== false;
      return {
        config: {
          type: "line",
          data: {
            labels: acct.labels,
            datasets: [
              { label: data.label || "Projected balance", data: values, borderColor: color, backgroundColor: color,
                borderWidth: 2, tension: 0,
                pointRadius: (ctx) => (dots && acct.events[ctx.dataIndex] && acct.events[ctx.dataIndex].length ? 4 : 0),
                pointBorderColor: cssVar("--surface"), pointBorderWidth: 2, pointHoverRadius: 5 },
              { label: "Low-balance line", data: values.map(() => data.threshold), borderColor: lowColor,
                backgroundColor: lowColor, borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 0 },
            ],
          },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: data.legend === false ? { display: false } : legend(),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                filter: (item) => item.datasetIndex === 0,
                callbacks: {
                  title: (items) => fmt.dateLabel(items[0].label) + (items[0].dataIndex === todayIndex ? " (today)" : ""),
                  label: (item) => `${money.format(item.raw)}  balance`,
                  footer: (items) => acct.events[items[0].dataIndex] || [],
                },
              }),
            }),
            scales: {
              x: dateAxis({ ticks: { maxTicksLimit: 7 } }),
              y: moneyAxis(paddedRange(values.concat([data.threshold], data.range || []))),
            },
          }),
          plugins: [crosshair],
        },
        table: { headers: ["Date", "Balance", "What happens"],
                 rows: acct.labels.map((d, i) => [fmt.dateLabel(d), money.format(values[i]), acct.events[i].join("; ")])
                   .filter((row, i) => i === 0 || acct.events[i].length) },
      };
    },

    // Horizontal bars (spent or projected) with a marker at the budget, and each
    // row's % of budget written just past whichever ends further right (bar or marker).
    bullet(data, el) {
      const valueName = el.dataset.valueLabel || "Spent";
      const ink = cssVar("--fg");
      const used = (r) => (r.budget > 0 ? r.spent / r.budget : null);
      const usedText = (r) => {
        if (r.budget === null || r.budget === undefined) return "";   // "Other": no budget to compare
        const u = used(r);
        if (u === null) return "—";
        const icon = r.spent > r.budget ? "✕ " : "";
        return icon + Math.round(u * 100) + "%";
      };
      const LABEL_FONT = "12px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
      // What's written past a row's bar or marker: its %, or "Other"'s total.
      const rowLabel = (r) => (r.status === "other" ? money.format(r.spent) : usedText(r));
      // Only the room on the right that the labels need, so the bars use the card (user's request 2026-11-02; a fixed 88px
      // took a quarter of a phone's chart). A row's label starts at f = value / peak of the plot's width P, and needs
      // f*P + width + gap <= P, with P = chart width - names - this room. Solved for the room, for the row needing most.
      const rightRoom = (chart) => {
        const ctx = chart.ctx;
        const peak = Math.max(1, ...data.map((r) => Math.max(r.spent, r.budget || 0)));
        const available = chart.width - ((chart.scales.y && chart.scales.y.width) || 102);
        ctx.save();
        ctx.font = LABEL_FONT;
        let room = 20;                  // half of the axis's last label
        data.forEach((r) => {
          const f = Math.max(r.spent, r.budget || 0, 0) / peak;
          if (f > 0) room = Math.max(room, (ctx.measureText(rowLabel(r)).width + 10 - (1 - f) * available) / f);
        });
        ctx.restore();
        return Math.ceil(room);
      };
      // half the budget marker's length: 0.65 of its bar's thickness, at most 9 (wider screens' thick bars: 18px, as before)
      const markerRadius = (c) => {
        const bar = c.chart.getDatasetMeta(0).data[c.dataIndex];
        return bar && bar.height ? Math.min(9, Math.max(3, bar.height * 0.65)) : 9;
      };
      // Phones (user's request 2026-11-11): 2px more between the categories, the bars as thick as before - once laid
      // out, the bars keep their thickness and the box grows 2px a row (charts are responsive: it redraws to fit).
      const rowGap = {
        id: "ftBulletGap",
        afterLayout(chart) {
          if (chart.$ftGap || !window.matchMedia("(max-width: 640px)").matches || !data.length) return;
          chart.$ftGap = true;
          const band = (chart.chartArea.bottom - chart.chartArea.top) / data.length;
          chart.data.datasets[0].barThickness = Math.min(BAR.maxBarThickness, band * 0.8 * 0.9);   // Chart.js's default shares
          const box = chart.canvas.parentElement;
          box.style.height = Math.round(box.clientHeight + data.length * 2) + "px";
        },
      };
      const percentLabels = {
        id: "ftPercentUsed",
        afterDatasetsDraw(chart) {
          const bars = chart.getDatasetMeta(0).data;
          const marks = chart.getDatasetMeta(1).data;
          const ctx = chart.ctx;
          ctx.save();
          ctx.font = LABEL_FONT;
          ctx.textBaseline = "middle";
          data.forEach((r, i) => {
            if (!bars[i]) return;
            // Text in text tokens; over also carries an icon (✕), never color alone.
            // (No "near" warning level any more: user removed it 2026-10-06.)
            const over = r.status !== "other" && r.spent > r.budget;      // "Other" has no budget (null), never over
            ctx.fillStyle = over ? cssVar("--status-critical-text") : cssVar("--muted");
            const x = Math.max(bars[i].x, marks[i] ? marks[i].x : 0) + 8;
            // "Other" (spending without a budget) shows its total there instead of a % (user's request 2026-10-23)
            ctx.fillText(rowLabel(r), x, bars[i].y);
          });
          ctx.restore();
        },
      };
      return {
        config: {
          type: "bar",
          data: {
            labels: data.map((r) => r.label),
            datasets: [
              Object.assign({ label: valueName, data: data.map((r) => r.spent), order: 1,
                              // "Other" (unbudgeted spending) in the neutral gray
                              backgroundColor: data.map((r) => (r.status === "other" ? cssVar("--series-other") : slot(0))) }, BAR),
              // order 0 draws the budget marker over the bars (lower = on top), so it stays visible when spending runs
              // past it (user's request 2026-10-23)
              // A budget overspent past zero by rollover is held at the axis's start, not drawn below it; the tooltip and
              // Show data keep the real amount (user's request 2026-11-02).
              { type: "line", label: "Budget", data: data.map((r) => (r.budget === null ? null : Math.max(r.budget, 0))), showLine: false, order: 0,
                // its length follows the bar it marks: a little past the bar, never into the rows beside it (a fixed
                // 18px ran over a phone's 10px bars, 13px apart; user's request 2026-11-11). 9 at most, as before.
                pointStyle: "line", pointRotation: 90, pointRadius: markerRadius, pointHoverRadius: (c) => markerRadius(c) + 1,
                borderColor: ink, borderWidth: 2, backgroundColor: ink },
            ],
          },
          options: baseOptions({
            indexAxis: "y",
            layout: { padding: (context) => ({ right: rightRoom(context.chart) }) },
            interaction: { mode: "index", intersect: false, axis: "y" },
            plugins: Object.assign(baseOptions().plugins, {
              // Spent, then Budget in the legend and tooltip (not the drawing order)
              legend: Object.assign(legend(), { labels: Object.assign(legend().labels,
                                                                      { sort: (a, b) => a.datasetIndex - b.datasetIndex }) }),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                itemSort: (a, b) => a.datasetIndex - b.datasetIndex,
                callbacks: {
                  label: (item) => `${money.format(item.datasetIndex === 1 ? data[item.dataIndex].budget : item.raw)}  ${item.dataset.label}`,
                  footer: (items) => {
                    const r = data[items[0].dataIndex];
                    return r.status === "other" ? "spending in categories without a budget" : usedText(r) + " of budget";
                  },
                },
              }),
            }),
            scales: {
              y: categoryAxis({ ticks: { autoSkip: false } }),
              x: moneyAxis({ min: 0 }),
            },
          }),
          plugins: [percentLabels, rowGap],
        },
        table: { headers: ["Category", valueName, "Budget", "% used", "Status"],
                 rows: data.map((r) => [r.label, money.format(r.spent), r.budget === null ? "—" : money.format(r.budget),
                                        r.status === "other" ? "" : usedText(r),
                                        r.status === "other" ? "no budget"
                                          : r.spent > r.budget ? "✕ over by " + money.format(r.spent - r.budget)
                                          : money.format(r.budget - r.spent) + " left"]) },
      };
    },

    // Money in (up, blue) and money out (down, red) per day or month.
    flow(data, el) {
      const isMonth = data.labels.length && data.labels[0].length === 7;
      const names = data.names || [];
      // Transactions page (user's request 2026-10-26): data.urls = a link per bar (picks its month / day, or clears it
      // when it's the picked one, data.picked: the other bars dim)
      const urls = data.urls || null, picked = data.picked === undefined ? null : data.picked;
      const fill = (color) => (picked === null ? color : data.labels.map((d, i) => (i === picked ? color : withAlpha(color, 0.3))));
      const pick = urls ? {
        onClick: (event, active) => { if (active.length && urls[active[0].index]) window.location.href = urls[active[0].index]; },
        onHover: (event, active) => { event.native.target.style.cursor = active.length ? "pointer" : "default"; },
      } : {};
      return {
        config: {
          type: "bar",
          data: {
            labels: data.labels,
            datasets: [
              Object.assign({ label: el.dataset.inLabel || "Money in", data: data.in, backgroundColor: fill(slot(0)) }, BAR),
              Object.assign({ label: el.dataset.outLabel || "Money out", data: data.out.map((v) => -v),
                              backgroundColor: fill(cssVar("--diverge-neg")) }, BAR),
            ],
          },
          options: baseOptions(Object.assign(pick, {
            plugins: Object.assign(baseOptions().plugins, {
              legend: legend(),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: {
                  title: (items) => fmt.dateLabel(items[0].label),
                  label: (item) => `${money.format(Math.abs(item.raw))}  ${item.dataset.label}`,
                  footer: (items) => (names[items[0].dataIndex] || []).join(", "),
                },
              }),
            }),
            scales: {
              x: dateAxis({ stacked: true, ticks: { maxTicksLimit: isMonth ? 12 : 8 } }),
              y: moneyAxis({ stacked: true }),
            },
          })),
        },
        table: { headers: [isMonth ? "Month" : "Date", el.dataset.inLabel || "In", el.dataset.outLabel || "Out"].concat(names.length ? ["What"] : []),
                 rows: data.labels.map((d, i) => [fmt.dateLabel(d), money.format(data.in[i]), money.format(data.out[i])]
                   .concat(names.length ? [(names[i] || []).join(", ")] : []))
                   .filter((row, i) => data.in[i] || data.out[i]) },
      };
    },

    // One total per day or month (Transactions' Spending / Income views, user's request 2026-10-30; visuals.period_sums):
    // red bars for spending (data-direction="out"), blue for income, a dashed line at the average (data.average, per
    // data.per; the card's corner says it too) in the text colour (a reference, not a series). Bars pick their period
    // like the flow kind's (data.urls / data.picked).
    totals(data, el) {
      const isMonth = data.labels.length && data.labels[0].length === 7;
      const name = el.dataset.valueLabel || "Spent";
      const color = el.dataset.direction === "in" ? slot(0) : cssVar("--diverge-neg");
      const urls = data.urls || null, picked = data.picked === undefined ? null : data.picked;
      const fill = picked === null ? color : data.labels.map((d, i) => (i === picked ? color : withAlpha(color, 0.3)));
      const avgName = data.per === "day" ? "Daily average" : "Monthly average";
      const hasAverage = data.average !== null && data.average !== undefined;
      const datasets = [Object.assign({ label: name, data: data.values, backgroundColor: fill }, BAR)];
      if (hasAverage) {
        const ink = cssVar("--fg");
        datasets.push({ type: "line", label: avgName, data: data.labels.map(() => data.average), borderColor: ink,
                        backgroundColor: ink, borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 0,
                        tension: 0 });
      }
      const pick = urls ? {
        onClick: (event, active) => {
          const bar = active.find((a) => a.datasetIndex === 0);
          if (bar && urls[bar.index]) window.location.href = urls[bar.index];
        },
        onHover: (event, active) => { event.native.target.style.cursor = active.some((a) => a.datasetIndex === 0) ? "pointer" : "default"; },
      } : {};
      return {
        config: {
          type: "bar",
          data: { labels: data.labels, datasets },
          options: baseOptions(Object.assign(pick, {
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: hasAverage ? legend() : { display: false },
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: { title: (items) => fmt.dateLabel(items[0].label),
                             label: (item) => `${money.format(item.raw)}  ${item.dataset.label}` },
              }),
            }),
            scales: { x: dateAxis({ ticks: { maxTicksLimit: isMonth ? 12 : 8 } }), y: moneyAxis({ beginAtZero: true }) },
          })),
        },
        table: { headers: [isMonth ? "Month" : "Date", name].concat(hasAverage ? [avgName] : []),
                 rows: data.labels.map((d, i) => [fmt.dateLabel(d), money.format(data.values[i])]
                   .concat(hasAverage ? [money.format(data.average)] : [])) },
      };
    },

    // Where transfers went (Transactions' Transfers view, user's request 2026-10-30; visuals.transfer_flows), a flow
    // (Sankey) chart from the vendored chartjs-chart-sankey 0.14.0: accounts money left on the left, accounts it reached
    // (or, unmatched, its category) on the right; width = amount. A node's colour = its account's slot (`slots`), the
    // same on both sides; categories gray. Labels in the text colour; the tooltip names both ends, amount and count.
    sankey(data) {
      const colorOf = (key) => {
        const s = data.slots[key] || "slot-other";
        return s === "slot-other" ? cssVar("--series-other") : slot(parseInt(s.slice(5), 10) - 1);
      };
      const label = (key) => data.labels[key] || key;
      // a flow picks itself on the list (data.flows[].url, user's request 2026-10-31); data.picked = the picked one's
      // "<from>><to>": it stays bright, the rest dim
      const flowKey = (f) => f.from.slice(2) + ">" + f.to.slice(2);
      const alpha = (c) => {
        const f = c.dataset && c.dataset.data[c.dataIndex];
        if (!data.picked || !f) return 0.45;
        return flowKey(f) === data.picked ? 0.75 : 0.12;
      };
      const urlAt = (active) => (active.length && data.flows[active[0].index] ? data.flows[active[0].index].url : null);
      return {
        config: {
          type: "sankey",
          data: { datasets: [{
            data: data.flows,
            labels: data.labels,
            colorFrom: (c) => colorOf(c.dataset.data[c.dataIndex].from),
            colorTo: (c) => colorOf(c.dataset.data[c.dataIndex].to),
            colorMode: "gradient", alpha, color: cssVar("--fg"), borderWidth: 0, nodeWidth: 10, nodePadding: 26,
            size: "max",
          }] },
          options: baseOptions({
            layout: { padding: { top: 8, bottom: 10, left: 3, right: 3 } },     // the end labels stay inside the card
            onClick: (event, active) => { const url = urlAt(active); if (url) window.location.href = url; },
            onHover: (event, active) => { event.native.target.style.cursor = urlAt(active) ? "pointer" : "default"; },
            plugins: Object.assign(baseOptions().plugins, {
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: {
                  title: () => "",
                  label: (item) => {
                    const f = item.dataset.data[item.dataIndex];
                    return `${label(f.from)} → ${label(f.to)}: ${money.format(f.flow)} (${f.count})`;
                  },
                },
              }),
            }),
          }),
        },
        table: { headers: ["From", "To", "Amount", "Transactions"],
                 rows: data.flows.map((f) => [label(f.from), label(f.to), money.format(f.flow), String(f.count)]) },
      };
    },

    // The same transfers as a circle (a chord diagram; Transactions' Transfers view, user's request 2026-10-31; the card's
    // gear picks it or the flow above): each account once, as an arc whose length is all it sent plus all it received
    // (sent in its colour, received in a lighter shade), and a ribbon per route from the sender's arc to the receiver's,
    // in the sender's colour and pointed at the receiver. Same data as "sankey": its L: / R: keys name one node. Drawn
    // as SVG (Chart.js has no chord type): returns `svg` (drawChord) instead of a Chart.js config.
    chord(data) {
      const label = (key) => data.labels[key] || key;
      return {
        svg: (box, canvas) => drawChord(box, canvas, data),
        table: { headers: ["From", "To", "Amount", "Transactions"],
                 rows: data.flows.map((f) => [label(f.from), label(f.to), money.format(f.flow), String(f.count)]) },
      };
    },

    // Parts of a month's spending as a treemap (user's request 2026-10-09; was a pie): one tile per slice, area =
    // amount, palette order by size, gray "Other". No text in the tiles and no legend box: the tooltip names a tile and
    // the table beside it carries the same swatches and exact amounts (so no "Show data" either). data-picked = the slice key picked on the page; the others dim.
    // (chartjs-chart-treemap sorts tiles by size and keeps each original item in raw._data.)
    treemap(data, el) {
      const total = data.slices.reduce((sum, p) => sum + p.value, 0);
      const fills = {};
      data.slices.forEach((p, i) => { fills[p.key] = p.key === "_other" ? cssVar("--series-other") : slot(i); });
      const picked = el.dataset.picked || "";
      // The plugin also calls these with a context that has no tile yet (dataset-level option resolution), so
      // every one of them must cope with a missing raw / _data.
      const item = (ctx) => (ctx && ctx.raw && ctx.raw._data) || null;
      const colorOf = (ctx) => (item(ctx) && fills[item(ctx).key]) || cssVar("--series-other");
      const fill = (ctx) => (picked && item(ctx) && item(ctx).key !== picked ? withAlpha(colorOf(ctx), 0.35) : colorOf(ctx));
      return {
        config: {
          type: "treemap",
          data: { datasets: [{
            tree: data.slices, key: "value", spacing: 1,
            backgroundColor: fill, hoverBackgroundColor: fill,
            borderColor: cssVar("--surface"), borderWidth: 2, hoverBorderColor: cssVar("--surface"),
            labels: { display: false },        // no text in the tiles (user's request 2026-10-09): tooltip + the table's swatches
          }] },
          options: baseOptions({
            plugins: Object.assign(baseOptions().plugins, {
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: {
                  title: () => "",
                  label: (ctx) => {
                    const p = item(ctx);
                    if (!p) return "";
                    return `${money.format(p.value)}  ${p.label} (${total ? Math.round((p.value / total) * 100) : 0}%)`;
                  },
                },
              }),
            }),
          }),
        },
        table: { headers: ["Category", "Spent"], rows: data.slices.map((p) => [p.label, money.format(p.value)]) },
      };
    },

    // Parts of a total as vertical bars, each coloured by the page (Credit cards, user's request 2026-10-26: was a
    // treemap). Same data as treemap ({slices: [{label, key, value, detail?}], slots: {key: "slot-N" | "slot-other"}}),
    // but the colour comes from `slots`, not the bar's position, so the server can give a key one colour on every chart
    // (a reward rate on every card); a key without a slot ("Other") is gray. The tooltip adds `detail` after the label
    // ("5x (Travel · Acme)"). Long labels (categories) may tilt up to 45° instead of being skipped on a narrow card.
    colorbars(data, el) {
      const total = data.slices.reduce((sum, p) => sum + p.value, 0);
      const colorOf = (key) => {
        const s = (data.slots || {})[key] || "slot-other";
        return s === "slot-other" ? cssVar("--series-other") : slot(parseInt(s.slice(5), 10) - 1);
      };
      const name = (p) => (p.detail ? `${p.label} (${p.detail})` : p.label);
      return {
        config: {
          type: "bar",
          data: {
            labels: data.slices.map((p) => p.label),
            datasets: [Object.assign({ label: el.dataset.valueLabel || "Spent", data: data.slices.map((p) => p.value),
                                       backgroundColor: data.slices.map((p) => colorOf(p.key)) }, BAR)],
          },
          options: baseOptions({
            plugins: Object.assign(baseOptions().plugins, {
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: {
                  title: (items) => name(data.slices[items[0].dataIndex]),
                  label: (item) => `${money.format(item.raw)}  ${total ? Math.round((item.raw / total) * 100) : 0}%`,
                },
              }),
            }),
            scales: {
              x: categoryAxis({ ticks: Object.assign(categoryAxis().ticks, { autoSkip: false, maxRotation: 45 }) }),
              y: moneyAxis({ beginAtZero: true }),
            },
          }),
        },
        table: { headers: ["", "Spent"], rows: data.slices.map((p) => [name(p), money.format(p.value)]) },
      };
    },

    // A two-ring pie (Transactions page, user's request 2026-10-26; visuals.category_rings): the inner ring is the
    // categories, coloured by `slot` (slot-N / slot-other); the outer ring their subcategories in lighter shades of the
    // same hue (cycling, so neighbours differ), then a blank for what's in the category itself, so each category's
    // subcategories sit right outside it. Chart.js draws the first dataset outermost. A legend names the categories;
    // the tooltip names a slice, its amount and share (Other: its categories too); clicking a slice opens its `url`
    // (the list narrowed to it, Other to all of its categories, or back out when it's the picked one).
    rings(data, el) {
      const colorOf = (s) => (s === "slot-other" ? cssVar("--series-other") : slot(parseInt(s.slice(5), 10) - 1));
      const inner = [], outer = [];
      // the picked categories (the list's category filter; user's requests 2026-10-26/29) keep their colour, the rest
      // dim; the server marks each slice `lit`, and `dim` only when something picked is on the chart
      const lit = (color, on) => (!data.dim || on ? color : withAlpha(color, 0.3));
      data.parents.forEach((g) => {
        const base = colorOf(g.slot);
        inner.push({ label: g.label, value: g.value, color: lit(base, g.lit), url: g.url,
                     members: (g.members || []).map((m) => m.label) });
        g.subs.forEach((x, i) => outer.push({ label: x.label, parent: g.label, value: x.value,
                                              color: lit(shade(base, i % 3), x.lit), url: x.url }));
        if (g.direct > 0) outer.push({ blank: true, value: g.direct });
      });
      const share = (v) => (data.total ? Math.round((v / data.total) * 100) : 0) + "%";
      const ring = (parts) => ({
        data: parts.map((p) => p.value),
        backgroundColor: parts.map((p) => (p.blank ? "transparent" : p.color)),
        hoverBackgroundColor: parts.map((p) => (p.blank ? "transparent" : p.color)),
        borderColor: cssVar("--surface"), borderWidth: 2, hoverBorderColor: cssVar("--surface"),
      });
      const partOf = (datasetIndex, index) => (datasetIndex === 0 ? outer : inner)[index];
      const valueLabel = el.dataset.valueLabel || "Spent";
      const rows = [];
      data.parents.forEach((g) => {
        rows.push([g.label, "", money.format(g.value), share(g.value)]);
        g.subs.forEach((x) => rows.push(["", x.label, money.format(x.value), share(x.value)]));
      });
      return {
        config: {
          type: "doughnut",
          data: { labels: inner.map((p) => p.label), datasets: [ring(outer), ring(inner)] },
          options: baseOptions({
            cutout: "30%",
            onClick: (event, active) => {
              const p = active.length && partOf(active[0].datasetIndex, active[0].index);
              if (p && p.url) window.location.href = p.url;
            },
            onHover: (event, active) => {
              const p = active.length && partOf(active[0].datasetIndex, active[0].index);
              event.native.target.style.cursor = p && p.url ? "pointer" : "default";
            },
            plugins: Object.assign(baseOptions().plugins, {
              // the legend is the page's list beside the canvas (.rings-legend), so long names never wrap
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                filter: (item) => !partOf(item.datasetIndex, item.dataIndex).blank,
                callbacks: {
                  title: (items) => {
                    const p = items.length && partOf(items[0].datasetIndex, items[0].dataIndex);
                    return p ? (p.parent ? `${p.parent} › ${p.label}` : p.label) : "";
                  },
                  label: (item) => `${money.format(item.raw)}  ${share(item.raw)}`,
                  // Other: the categories folded into it (user's request 2026-10-29)
                  afterBody: (items) => {
                    const p = items.length && partOf(items[0].datasetIndex, items[0].dataIndex);
                    return p && p.members && p.members.length ? p.members : [];
                  },
                },
              }),
            }),
          }),
        },
        table: { headers: ["Category", "Subcategory", valueLabel, "Share"], rows },
      };
    },

    // Money moved into OR out of retirement accounts and other investment accounts, by month (Portfolio and Savings).
    // The canvas's data-direction ("into" | "out") picks which; the page draws one chart per direction, each on its own
    // axis (user's request 2026-10-11), so a large withdrawal can't flatten the monthly contributions. Colour = the kind
    // of account (slot 1 retirement, slot 2 investment), the same in both charts; the two kinds stack.
    transfers(data, el) {
      const direction = el.dataset.direction === "out" ? "out" : "into";
      const verb = direction === "out" ? "Out of" : "Into";
      const groups = [["retirement", "Retirement accounts", slot(0)], ["investment", "Investment accounts", slot(1)]];
      const values = data[direction];
      const datasets = groups.map(([key, name, color]) => ({
        label: name, data: values[key], backgroundColor: color, borderColor: cssVar("--surface"), borderWidth: 1,
        maxBarThickness: 28, borderRadius: 3 }));
      return {
        config: {
          type: "bar",
          data: { labels: data.labels, datasets },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: legend(),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                filter: (item) => item.raw !== 0,
                callbacks: {
                  title: (items) => fmt.dateLabel(items[0].label),
                  label: (item) => `${money.format(item.raw)}  ${verb} ${item.dataset.label.toLowerCase()}`,
                },
              }),
            }),
            scales: { x: dateAxis({ stacked: true }), y: moneyAxis({ stacked: true, beginAtZero: true }) },
          }),
        },
        table: { headers: ["Month", `${verb} retirement`, `${verb} investment`],
                 rows: data.labels.map((m, i) => [fmt.dateLabel(m), money.format(values.retirement[i]), money.format(values.investment[i])])
                   .filter((row, i) => values.retirement[i] || values.investment[i]) },
      };
    },

    // Total per month, one column each (Spending's chart for ranges other than this/last month): the first and
    // last months count only the days inside the range. Hover/click (data-emit) picks a month.
    monthbars(data) {
      const color = slot(0), avgColor = cssVar("--series-2");
      const hasAverage = data.average !== null && data.average !== undefined;
      const datasets = [Object.assign({ label: "Spent", data: data.values, backgroundColor: color }, BAR)];
      // Monthly average over the whole range (some ranges only): a dashed line across the bars.
      if (hasAverage) {
        datasets.push({ type: "line", label: "Monthly average", data: data.months.map(() => data.average),
                        borderColor: avgColor, backgroundColor: avgColor, borderWidth: 2, borderDash: [6, 4],
                        pointRadius: 0, pointHoverRadius: 0, tension: 0 });
      }
      return {
        config: {
          type: "bar",
          data: { labels: data.months, datasets },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: hasAverage ? legend() : { display: false },
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: { title: (items) => fmt.dateLabel(items[0].label),
                             label: (item) => `${money.format(item.raw)}  ${item.dataset.label}` },
              }),
            }),
            scales: { x: dateAxis(), y: moneyAxis({ beginAtZero: true }) },
          }),
        },
        table: { headers: ["Month", "Spent"].concat(hasAverage ? ["Monthly average"] : []),
                 rows: data.months.map((m, i) => [fmt.dateLabel(m), money.format(data.values[i])]
                   .concat(hasAverage ? [money.format(data.average)] : [])) },
      };
    },

    // Recurring income or expenses on each day of the current month (Recurring, user's request 2026-10-16): one bar per
    // day = the confirmed items due that day (the server leaves annual items out). data: {days (ISO), values (dollars),
    // items: [[{name, amount}]] per day}. data-direction="out" draws money out in red, else money in in blue; no legend
    // (one series), so two cards side by side keep plots of the same size. The tooltip lists the day's items.
    daybars(data, el) {
      const name = el.dataset.valueLabel || "Amount";
      const color = el.dataset.direction === "out" ? cssVar("--diverge-neg") : slot(0);
      const dayOf = (iso) => String(Number(iso.slice(8, 10)));
      const xAxis = categoryAxis();
      xAxis.ticks = Object.assign(xAxis.ticks, { callback(value) { return dayOf(this.getLabelForValue(value)); } });
      return {
        config: {
          type: "bar",
          data: { labels: data.days,
                  datasets: [Object.assign({ label: name, data: data.values, backgroundColor: color }, BAR)] },
          options: baseOptions({
            plugins: Object.assign(baseOptions().plugins, {
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: {
                  title: (items) => fmt.dateLabel(items[0].label),
                  label: (item) => `${money.format(item.raw)}  ${name}`,
                  afterBody: (items) => (data.items[items[0].dataIndex] || []).map((i) => `${i.name}: ${money.format(i.amount)}`),
                },
              }),
            }),
            scales: { x: xAxis, y: moneyAxis({ beginAtZero: true }) },
          }),
        },
        // only the days with something due
        table: { headers: ["Day", name, "Items"],
                 rows: data.days.map((d, i) => [fmt.dateLabel(d), money.format(data.values[i]),
                                                 (data.items[i] || []).map((x) => x.name).join(", ")])
                                .filter((_row, i) => data.values[i]) },
      };
    },

    // Shares of each month's total income, as stacked columns by type (Savings: retirement savings by account type;
    // user's requests 2026-10-12, bars since the same day). data: {labels (months), partial, series: [{label, values
    // (% or null), dollars}]}. Types keep their slot in the order given (the server sorts them stably), so colours
    // don't move between ranges. The stack's top is the total; the tooltip adds it. The month in progress is solid (its
    // tooltip says "(so far)");
    // a month with no income has no column.
    // Colours (user's request 2026-10-12: the same kind is the same colour on every card): data.hue (a CSS variable) =
    // one kind's card, its segments in shades of that hue; data.colors = one CSS variable per series (the Net savings
    // card: each part in its kind's colour); neither = the categorical slots in order.
    // data.money (the Balances page's $ mode, user's request 2026-10-12): values are dollars, on a money axis that can go
    // below zero; tooltip and table show dollars only. data.decimals: % shown with that many decimals (Balances' % mode).
    sharebars(data) {
      const whole = data.money
        ? (v) => (v === null || v === undefined ? "—" : money.format(v))
        : (v) => {
          if (v === null || v === undefined) return "—";
          const step = 10 ** (data.decimals || 0);
          return (Math.round(v * step) / step + 0).toFixed(data.decimals || 0) + "%";   // + 0: -0 shows as 0
        };
      const colorOf = (i) => (data.hue ? shade(cssVar(data.hue), i) : data.colors ? cssVar(data.colors[i]) : slot(i));
      const datasets = data.series.map((s, i) => ({
        label: s.label, data: s.values, maxBarThickness: 28, borderRadius: 3,
        // the month in progress is drawn solid like the rest (user's request 2026-10-12); the tooltip says "(so far)"
        backgroundColor: colorOf(i),
        borderColor: cssVar("--surface"), borderWidth: 1,
      }));
      const title = (i) => fmt.dateLabel(data.labels[i]) + (data.partial[i] ? " (so far)" : "");
      const total = (i) => {
        const parts = data.series.map((s) => s.values[i]).filter((v) => v !== null && v !== undefined);
        return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
      };
      return {
        config: {
          type: "bar",
          data: { labels: data.labels, datasets },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              // A legend names the segments (e.g. an institution); data.legend === false hides it where the card's title is the
              // only name (the Net cash card)
              legend: data.legend === false ? { display: false } : legend(),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                filter: (item) => item.raw !== null && item.raw !== 0,
                callbacks: {
                  title: (items) => title(items[0].dataIndex),
                  label: (item) => (data.money ? `${whole(item.raw)}  ${item.dataset.label}`
                    : `${whole(item.raw)}  ${item.dataset.label} (${money.format(data.series[item.datasetIndex].dollars[item.dataIndex])})`),
                  footer: (items) => (data.series.length > 1 ? `${whole(total(items[0].dataIndex))}  Total` : ""),
                },
              }),
            }),
            scales: { x: dateAxis({ stacked: true }),
                      y: data.money ? moneyAxis({ stacked: true, beginAtZero: true })
                        : { stacked: true, beginAtZero: true, grid: { color: cssVar("--chart-grid") }, border: { display: false },
                            ticks: { color: cssVar("--muted"), callback: (v) => v + "%" } } },
          }),
        },
        table: { headers: ["Month"].concat(data.series.map((s) => s.label), data.series.length > 1 ? ["Total"] : []),
                 rows: data.labels.map((m, i) => [title(i)].concat(
                   data.series.map((s) => (data.money ? whole(s.values[i]) : `${whole(s.values[i])} (${money.format(s.dollars[i])})`)),
                   data.series.length > 1 ? [whole(total(i))] : [])) },
      };
    },

    // What you own by type, stacked over days; what you owe as a separate line.
    // Stacked areas over time. Optional: data.owed (a separate dashed line, Net worth page) and
    // range buttons (data-days: the x-axis spans the whole range ending today, like `area`;
    // days before the data stay empty).
    stackedarea(data, el) {
      const days = parseInt((el && el.dataset.days) || "0", 10);
      let labels = data.labels;
      let pick = (values) => values;
      if (days) {
        const index = new Map(data.labels.map((d, i) => [d, i]));
        labels = [];
        const day = new Date();
        day.setHours(12, 0, 0, 0);                 // midday: no DST edge when stepping back
        day.setDate(day.getDate() - (days - 1));
        for (let k = 0; k < days; k += 1) {
          labels.push(`${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`);
          day.setDate(day.getDate() + 1);
        }
        pick = (values) => labels.map((d) => (index.has(d) ? values[index.get(d)] : null));
      }
      const shown = labels.filter((d, i) => data.series.some((s) => pick(s.values)[i] !== null)).length;
      // Colours: data.hue = shades of one kind, data.colors = one CSS variable per series (Balances' $ mode, user's
      // request 2026-10-12), else the palette slots in order.
      const colorOf = (i) => (data.hue ? shade(cssVar(data.hue), i) : data.colors ? cssVar(data.colors[i]) : slot(i));
      // Each area fills down to the previous series of the same sign, the first to $0: Chart.js stacks values below
      // zero separately, so what's owed (credit cards, loans) stacks downward instead of on top of what you own. All
      // positive data (Net worth, Portfolio) = "origin" then the previous series, as before.
      const below = data.series.map((s) => {
        const known = s.values.filter((v) => v !== null && v !== undefined && v !== 0);
        return known.length > 0 && known.filter((v) => v < 0).length > known.length / 2;
      });
      const fillOf = (i) => {
        for (let j = i - 1; j >= 0; j -= 1) if (below[j] === below[i]) return j;
        return "origin";
      };
      const datasets = data.series.map((s, i) => ({
        label: s.label, data: pick(s.values), borderColor: colorOf(i), backgroundColor: withAlpha(colorOf(i), 0.35),
        borderWidth: 2, pointRadius: shown < 3 ? 4 : 0, fill: fillOf(i), stack: "own",
        tension: 0,
      }));
      if (data.owed) {
        datasets.push({ label: "Owed", data: pick(data.owed), borderColor: cssVar("--diverge-neg"),
                        backgroundColor: cssVar("--diverge-neg"), borderWidth: 2, pointRadius: shown < 3 ? 4 : 0,
                        borderDash: [6, 4], fill: false, stack: "owed", tension: 0 });
      }
      const rows = labels.map((d, i) => i).filter((i) => datasets.some((ds) => ds.data[i] !== null));
      return {
        config: {
          type: "line",
          data: { labels, datasets },
          options: baseOptions({
            interaction: { mode: "index", intersect: false },
            plugins: Object.assign(baseOptions().plugins, {
              legend: legend(),
              tooltip: Object.assign(baseOptions().plugins.tooltip, {
                callbacks: { title: (items) => fmt.dateLabel(items[0].label) + (data.partial && data.partial[items[0].dataIndex] ? " (so far)" : ""),
                             label: (item) => `${money.format(item.raw)}  ${item.dataset.label}`,
                             // data.total_footer (Balances): the stack's total under the parts
                             footer: (items) => (data.total_footer && data.series.length > 1
                               ? `${money.format(items.filter((it) => it.dataset.stack === "own")
                                   .reduce((sum, it) => sum + (it.raw || 0), 0))}  Total` : "") },
              }),
            }),
            scales: { x: dateAxis({ ticks: { maxTicksLimit: 7 } }), y: moneyAxis({ stacked: true, beginAtZero: true }) },
          }),
          plugins: [crosshair],
        },
        table: { headers: ["Date"].concat(datasets.map((ds) => ds.label)),
                 rows: rows.map((i) => [fmt.dateLabel(labels[i])].concat(datasets.map((ds) => money.format(ds.data[i])))) },
      };
    },
  };

  // ---------------------------------------------------------------- rendering

  // The "Show data" table: a <details> right after the chart box.
  function renderTable(box, table) {
    let details = box.nextElementSibling;
    if (!details || !details.classList.contains("data-view")) {
      details = document.createElement("details");
      details.className = "data-view";
      const summary = document.createElement("summary");
      summary.textContent = "Show data";
      details.appendChild(summary);
      box.after(details);
    }
    details.querySelectorAll(".table-wrap").forEach((n) => n.remove());
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const t = document.createElement("table");
    t.className = "data compact";
    const head = t.createTHead().insertRow();
    table.headers.forEach((h, i) => {
      const th = document.createElement("th");
      th.textContent = h;
      if (i > 0) th.className = "num";
      head.appendChild(th);
    });
    const body = t.createTBody();
    table.rows.forEach((row) => {
      const tr = body.insertRow();
      row.forEach((cell, i) => {
        const td = tr.insertCell();
        td.textContent = cell;
        if (i > 0) td.className = "num";
      });
    });
    wrap.appendChild(t);
    details.appendChild(wrap);
  }

  // Opt-in (data-emit): tells the page which point / slice the pointer is on, without this file knowing
  // what the page does with it. Fires "fintrack:pick" on the canvas (bubbles): detail {type: "hover" | "click",
  // index}; hover's index is null when the pointer leaves. Bound once per canvas (charts are redrawn on
  // theme changes); the handlers look up the current Chart each time.
  function bindEmit(canvas) {
    if (canvas.dataset.emitBound) return;
    canvas.dataset.emitBound = "1";
    const tiles = canvas.dataset.chart === "treemap";      // shapes you point AT, rather than an x position
    function indexAt(event) {
      const chart = Chart.getChart(canvas);
      if (!chart) return null;
      const hits = chart.getElementsAtEventForMode(event, tiles ? "nearest" : "index", { intersect: tiles }, false);
      if (!hits.length) return null;
      // A treemap draws its tiles sorted by size: report the index in the page's own list instead.
      return tiles ? chart.data.datasets[0].tree.indexOf(chart.data.datasets[0].data[hits[0].index]._data) : hits[0].index;
    }
    function send(type, index) {
      canvas.dispatchEvent(new CustomEvent("fintrack:pick", { bubbles: true, detail: { type, index } }));
    }
    canvas.addEventListener("mousemove", (event) => send("hover", indexAt(event)));
    canvas.addEventListener("mouseleave", () => send("hover", null));
    canvas.addEventListener("click", (event) => {
      const index = indexAt(event);
      if (index !== null) send("click", index);
    });
  }

  // Small cards' charts on a phone (inside [data-mini-charts]: Net worth's Compact cards, Savings' cards; user's
  // requests 2026-11-06): no legend (the card's title and the tooltip name the parts) and about 3 labels an axis.
  function mini(config) {
    const options = (config.options = config.options || {});
    options.plugins = Object.assign({}, options.plugins, { legend: { display: false } });
    Object.values(options.scales || {}).forEach((scale) => {
      scale.ticks = Object.assign({}, scale.ticks, { maxTicksLimit: 3 });
    });
  }

  function render(canvas) {
    const kind = KINDS[canvas.dataset.chart];
    const source = document.getElementById(canvas.dataset.source);
    if (!kind || !source) return;
    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();
    const data = JSON.parse(source.textContent);
    const built = kind(data, canvas);
    const box = canvas.parentElement;
    box.querySelectorAll(".svg-chart, .chart-tip").forEach((el) => el.remove());
    if (built.svg) {                                       // an SVG kind (chord): drawn into the box, canvas hidden
      canvas.hidden = true;
      built.svg(box, canvas);
      box.dataset.drawnWidth = String(box.clientWidth);
      if (svgResize && !box.dataset.svgObserved) { svgResize.observe(box); box.dataset.svgObserved = "1"; }
    } else {
      canvas.hidden = false;
      if (phone.matches && canvas.closest("[data-mini-charts]")) mini(built.config);
      new Chart(canvas, built.config);
    }
    if (canvas.dataset.emit) bindEmit(canvas);
    // data-no-table: the page already lists the same data right below the chart.
    if (canvas.dataset.noTable) return;
    renderTable(canvas.parentElement, built.table);
  }

  function renderAll(root) {
    root.querySelectorAll("canvas[data-chart]").forEach(render);
  }

  // Range buttons: <div class="seg" data-range-for="canvas-id"><button data-days="90">…
  // The choice is remembered per browser (a convenience; it's fine if storage is unavailable).
  function storedRange(id) {
    try { return window.localStorage.getItem("ft-range-" + id); } catch (e) { return null; }
  }
  // Other elements that follow a chart's range (e.g. the Investments performance tiles):
  // <div data-range-of="canvas-id" data-range-show="90"> is shown only for that range.
  function showRange(id, days) {
    document.querySelectorAll('[data-range-of="' + id + '"]').forEach((el) => {
      el.hidden = el.dataset.rangeShow !== days;
    });
    // Tables that sort by the range's values (ui.js) re-sort on this event.
    document.dispatchEvent(new CustomEvent("fintrack:range", { detail: { id: id, key: days } }));
  }
  function setupRanges(root) {
    root.querySelectorAll(".seg[data-range-for]").forEach((group) => {
      const canvas = document.getElementById(group.dataset.rangeFor);
      if (!canvas) return;
      const buttons = Array.from(group.querySelectorAll("button[data-days]"));
      // A button's key: its named range (data-range, e.g. "ytd", whose day count changes
      // daily) or else its day count. The key is what's remembered and shown elsewhere.
      const keyOf = (b) => b.dataset.range || b.dataset.days;
      function choose(key, remember) {
        const button = buttons.find((b) => keyOf(b) === key);
        canvas.dataset.days = button.dataset.days;
        canvas.dataset.range = key;            // the chosen key (the performance chart draws "1" by the minute)
        buttons.forEach((b) => b.setAttribute("aria-pressed", String(keyOf(b) === key)));
        if (remember) {
          try { window.localStorage.setItem("ft-range-" + canvas.id, key); } catch (e) { /* ignore */ }
        }
        render(canvas);
        showRange(canvas.id, key);
      }
      buttons.forEach((b) => b.addEventListener("click", () => choose(keyOf(b), true)));
      const initial = storedRange(canvas.id);
      if (initial && buttons.some((b) => keyOf(b) === initial)) choose(initial, false);
      else {
        const pressed = buttons.find((b) => b.getAttribute("aria-pressed") === "true");
        if (pressed) { canvas.dataset.days = pressed.dataset.days; canvas.dataset.range = keyOf(pressed); }
      }
    });
  }

  // Account picker for a chart with several accounts: <select data-chart-pick="canvas-id">.
  document.addEventListener("change", (event) => {
    const select = event.target.closest("select[data-chart-pick]");
    if (!select) return;
    const canvas = document.getElementById(select.dataset.chartPick);
    if (!canvas) return;
    canvas.dataset.pick = select.value;
    render(canvas);
  });

  // Comparison toggles: benchmark cards (<div role="button">) and Investments table rows (a
  // <button> in the row; clicking anywhere in a tr[data-row-toggle] counts), each with
  // data-compare-for="canvas-id" data-compare="key" aria-pressed. Click to add a line, again to
  // remove it; past MAX_COMPARE the oldest goes. Each active toggle gets data-slot (2-5) = its
  // line's palette slot, so CSS can mark it in the same color.
  function toggleCompare(toggle) {
    const canvas = document.getElementById(toggle.dataset.compareFor);
    if (!canvas) return;
    let keys = compareKeys(canvas);
    const key = toggle.dataset.compare;
    if (keys.includes(key)) keys = keys.filter((k) => k !== key);
    else keys = keys.concat(key).slice(-MAX_COMPARE);
    if (keys.length) canvas.dataset.compare = keys.join(",");
    else delete canvas.dataset.compare;
    document.querySelectorAll('[data-compare-for="' + canvas.id + '"]').forEach((t) => {
      const at = keys.indexOf(t.dataset.compare);
      t.setAttribute("aria-pressed", String(at !== -1));
      if (at !== -1) t.dataset.slot = String(at + 2);
      else delete t.dataset.slot;
    });
    render(canvas);
  }
  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-compare-for]")
      || (event.target.closest("tr[data-row-toggle]") || { querySelector: () => null }).querySelector("[data-compare-for]");
    if (toggle) toggleCompare(toggle);
  });
  document.addEventListener("keydown", (event) => {
    const tile = event.target.closest && event.target.closest("[data-compare-for]");
    // Real <button>s already turn Enter/Space into a click; only the card tiles need this.
    if (tile && tile.tagName !== "BUTTON" && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();                    // Space would otherwise scroll the page
      toggleCompare(tile);
    }
  });

  setupRanges(document);
  renderAll(document);
  // After HTMX swaps content in (e.g. new transaction filters), draw any canvas that has no
  // chart yet. With outerHTML swaps event.target may be the removed element, so look at the
  // whole page; canvases already drawn are left alone.
  // On afterSettle, not afterSwap: while settling, HTMX resets the class / style / width /
  // height of elements whose id was already on the page (htmx.config.attributesToSettle), which
  // wiped a chart drawn at afterSwap (it flashed and vanished; found 2026-10-08 on Transactions).
  document.addEventListener("htmx:afterSettle", () => {
    document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
      if (!Chart.getChart(canvas) && !canvas.parentElement.querySelector(".svg-chart")) render(canvas);
    });
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderAll(document));
  // the rail's light / dark switch (ui.js, 2026-10-28): colours come from CSS variables, read when a chart is drawn
  document.addEventListener("fintrack:theme", () => renderAll(document));
})();
