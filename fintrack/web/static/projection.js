// Projection page: actual net worth history, then baseline (and scenario)
// projections, monthly (Chart.js).
//
// Series: actual = blue; baseline projection = the same blue, lighter (an
// estimate continuing the same line); scenario = orange. Colors from CSS
// variables (validated pair). Y-axis = data range + 5% each side (user's rule).
(function () {
  "use strict";

  const canvas = document.getElementById("projection-chart");
  const dataEl = document.getElementById("projection-data");
  if (!canvas || !dataEl || typeof Chart === "undefined") return;
  const data = JSON.parse(dataEl.textContent);

  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function withAlpha(hex, alpha) {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  function yRange(values) {
    const v = values.filter((x) => x !== null && x !== undefined);
    const lo = Math.min(...v);
    const hi = Math.max(...v);
    const span = hi - lo;
    const pad = span > 0 ? span * 0.05 : Math.max(Math.abs(hi) * 0.05, 100);
    return { min: lo - pad, max: hi + pad };
  }

  // Vertical line at "today", where actual history ends and projections begin.
  const todayLine = {
    id: "todayLine",
    afterDatasetsDraw(chart) {
      const index = data.months.indexOf(data.today);
      if (index < 0) return;
      const x = chart.scales.x.getPixelForValue(index);
      const { top, bottom } = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = cssVar("--chart-axis");
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.fillStyle = cssVar("--muted");
      ctx.font = "11px system-ui, -apple-system, sans-serif";
      ctx.fillText("today", x + 4, top + 12);
      ctx.restore();
    },
  };

  let chart;
  function render() {
    if (chart) chart.destroy();
    const blue = cssVar("--series-1");
    const orange = cssVar("--series-2");
    const line = { borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, spanGaps: false, tension: 0 };
    const datasets = [
      { label: "Actual", data: data.history, borderColor: blue, pointHoverBackgroundColor: blue, ...line },
      { label: "Baseline projection", data: data.baseline, borderColor: withAlpha(blue, 0.45),
        pointHoverBackgroundColor: blue, ...line },
    ];
    if (data.scenario) {
      datasets.push({ label: "Scenario", data: data.scenario, borderColor: orange, pointHoverBackgroundColor: orange, ...line });
    }
    const all = data.history.concat(data.baseline, data.scenario || []);

    chart = new Chart(canvas, {
      type: "line",
      data: { labels: data.months, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "top", align: "start", labels: { color: cssVar("--fg"), boxWidth: 12, boxHeight: 2 } },
          tooltip: {
            filter: (item) => item.raw !== null,
            callbacks: { label: (item) => `${item.dataset.label}  ${money.format(item.raw)}` },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: cssVar("--chart-axis") },
            ticks: { callback: window.fintrack.axisDate, color: cssVar("--muted"), maxTicksLimit: 8, maxRotation: 0 },
          },
          y: {
            ...yRange(all),
            grid: { color: cssVar("--chart-grid") },
            border: { display: false },
            afterBuildTicks: window.fintrack.roundTicks,
            ticks: { color: cssVar("--muted"), callback: window.fintrack.axisMoney },
          },
        },
      },
      plugins: [todayLine],
    });
  }

  render();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  document.addEventListener("fintrack:theme", render);      // the rail's light / dark switch (ui.js, 2026-10-28)
})();
