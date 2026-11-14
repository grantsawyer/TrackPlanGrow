const fs = require("fs"), vm = require("vm"), assert = require("assert");
let src = fs.readFileSync(process.argv[2], "utf8");
const marker = "// ---------------------------------------------------------------- rendering";
assert(src.includes(marker));
src = src.replace(marker, "globalThis.__KINDS = KINDS;\n" + marker);
const css = { "--series-1": "#2a78d6", "--series-2": "#eb6834", "--series-3": "#1baf7a", "--series-4": "#eda100",
              "--series-5": "#e87ba4", "--diverge-neg": "#e34948", "--series-other": "#898781", "--surface": "#ffffff", "--muted": "#666666",
              "--fg": "#111111", "--line-strong": "#999999", "--chart-grid": "#eeeeee", "--chart-axis": "#aaaaaa" };
const sandbox = {
  Chart: { getChart() { return null; } },
  Intl, console, CustomEvent: class {}, parseInt, Math, JSON, Object, Array,
  document: { documentElement: {}, addEventListener() {}, querySelectorAll: () => [], getElementById: () => null },
  getComputedStyle: () => ({ getPropertyValue: (name) => css[name] || "" }),
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
// ui.js's helpers, as charts.js finds them on window.fintrack (identity labels keep the assertions readable)
sandbox.fintrack = { axisDate() {}, axisMoney() {}, roundTicks() {}, dateLabel: (d) => d };
sandbox.matchMedia = () => ({ addEventListener() {} });
vm.runInNewContext(src, sandbox);
const data = { slices: [{ label: "Rent", key: "RENT", value: 1200 }, { label: "Food", key: "FOOD", value: 300.5 },
                         { label: "Fun", key: "FUN", value: 50 }, { label: "Other", key: "_other", value: 20 }], slots: {} };
const build = (picked) => sandbox.__KINDS.treemap(data, { dataset: { picked } });
const ds = build("").config.data.datasets[0];
const tile = (key) => ({ raw: { _data: data.slices.find((s) => s.key === key) } });
assert.strictEqual(sandbox.__KINDS.treemap(data, { dataset: {} }).config.type, "treemap");
assert.strictEqual(JSON.stringify(ds.tree), JSON.stringify(data.slices));
// fills follow palette order by slice; Other is the gray
assert.strictEqual(ds.backgroundColor(tile("RENT")), "#2a78d6");
assert.strictEqual(ds.backgroundColor(tile("FUN")), "#1baf7a");
assert.strictEqual(ds.backgroundColor(tile("_other")), "#898781");
// no text inside the tiles (the tooltip and the table's swatches name them)
assert.strictEqual(ds.labels.display, false);
// picking dims every other tile, keeps the picked one solid
const picked = build("FOOD").config.data.datasets[0];
assert.ok(picked.backgroundColor(tile("RENT")).startsWith("rgba(42, 120, 214, 0.35"));
assert.ok(!picked.backgroundColor(tile("FOOD")).startsWith("rgba"));
// the plugin also calls the options with no tile at all (dataset-level resolution): must not throw
for (const empty of [{}, { raw: undefined }, { raw: {} }]) {
  assert.strictEqual(ds.backgroundColor(empty), "#898781");
  assert.strictEqual(build("FOOD").config.options.plugins.tooltip.callbacks.label(empty), "");
}
// tooltip: amount, name, share of the month
const tip = build("").config.options.plugins.tooltip.callbacks;
assert.strictEqual(tip.label(tile("RENT")), "$1,200.00  Rent (76%)");
assert.strictEqual(tip.title(), "");
// the "Show data" table is the slices (the page hides it, but it stays correct)
assert.strictEqual(JSON.stringify(build("").table.rows[1]), '["Food","$300.50"]');

// ---- monthbars: monthly totals, with the monthly-average line only when the page sends one
const bars = (average) => sandbox.__KINDS.monthbars({ months: ["2026-08", "2026-09"], values: [100, 50], average }, { dataset: {} });
const plain = bars(null);
assert.strictEqual(plain.config.data.datasets.length, 1);
assert.strictEqual(plain.config.options.plugins.legend.display, false);
assert.strictEqual(plain.table.headers.length, 2);
const withAvg = bars(75);
assert.strictEqual(withAvg.config.data.datasets.length, 2);
const line = withAvg.config.data.datasets[1];
assert.strictEqual(line.type, "line");
assert.strictEqual(line.label, "Monthly average");
assert.strictEqual(JSON.stringify(line.data), "[75,75]");               // flat across every month
assert.strictEqual(withAvg.config.options.plugins.legend.display, true);   // two series: a legend
assert.strictEqual(JSON.stringify(withAvg.table.headers), '["Month","Spent","Monthly average"]');
assert.strictEqual(JSON.stringify(withAvg.table.rows[0]), '["2026-08","$100.00","$75.00"]');
const tipLabel = withAvg.config.options.plugins.tooltip.callbacks.label;
assert.strictEqual(tipLabel({ raw: 75, dataset: { label: "Monthly average" } }), "$75.00  Monthly average");

// ---- flow: the "Show data" headers follow custom in/out labels (Portfolio's transfers chart), else In / Out
const flowData = { labels: ["2026-08", "2026-09"], in: [1850, 8850], out: [0, 3000] };
const labelled = sandbox.__KINDS.flow(flowData, { dataset: { inLabel: "Into investments", outLabel: "Out of investments" } });
assert.strictEqual(JSON.stringify(labelled.table.headers), '["Month","Into investments","Out of investments"]');
assert.strictEqual(JSON.stringify(labelled.config.data.datasets.map((d) => d.label)), '["Into investments","Out of investments"]');
assert.strictEqual(JSON.stringify(labelled.config.data.datasets[1].data), "[0,-3000]");        // withdrawals draw below the line
assert.strictEqual(JSON.stringify(sandbox.__KINDS.flow(flowData, { dataset: {} }).table.headers), '["Month","In","Out"]');

// ---- transfers: money into OR out of retirement / investment accounts, one chart per direction (data-direction)
const tData = { labels: ["2026-08", "2026-09"],
                into: { retirement: [400, 7400], investment: [1850, 1850] }, out: { retirement: [0, 0], investment: [3000, 0] } };
const trIn = sandbox.__KINDS.transfers(tData, { dataset: { direction: "into" } });
const trOut = sandbox.__KINDS.transfers(tData, { dataset: { direction: "out" } });
const inSets = trIn.config.data.datasets, outSets = trOut.config.data.datasets;
assert.strictEqual(JSON.stringify(inSets.map((d) => d.label)), '["Retirement accounts","Investment accounts"]');
assert.strictEqual(JSON.stringify(inSets[1].data), "[1850,1850]");
assert.strictEqual(JSON.stringify(outSets[1].data), "[3000,0]");                         // money out draws upward on its own axis
assert.strictEqual(inSets[0].backgroundColor, outSets[0].backgroundColor);              // one colour per kind of account, both charts
assert.notStrictEqual(inSets[0].backgroundColor, inSets[1].backgroundColor);
assert.strictEqual(trOut.config.options.scales.y.stacked, true);
assert.strictEqual(trOut.config.options.scales.y.beginAtZero, true);
assert.strictEqual(trIn.config.options.plugins.legend.display, true);
assert.strictEqual(trOut.config.options.plugins.tooltip.callbacks.label({ raw: 3000, dataset: outSets[1] }), "$3,000.00  Out of investment accounts");
assert.strictEqual(trIn.config.options.plugins.tooltip.callbacks.label({ raw: 7400, dataset: inSets[0] }), "$7,400.00  Into retirement accounts");
assert.strictEqual(trIn.config.options.plugins.tooltip.filter({ raw: 0 }), false);      // zero-height bars stay out of the tooltip
assert.strictEqual(JSON.stringify(trIn.table.headers), '["Month","Into retirement","Into investment"]');
assert.strictEqual(JSON.stringify(trOut.table.rows), '[["2026-08","$0.00","$3,000.00"]]');  // months with nothing moved are left out

// ---- sharebars: retirement savings by account type, % of total income, stacked columns
const sa = sandbox.__KINDS.sharebars({ labels: ["2026-08", "2026-09"], partial: [false, true],
  series: [{ label: "401(k)", values: [10.2, 12.0], dollars: [1000, 1200] }, { label: "HSA", values: [2.0, null], dollars: [200, 0] }] }, { dataset: {} });
const saSets = sa.config.data.datasets;
assert.strictEqual(sa.config.type, "bar");
assert.strictEqual(JSON.stringify(saSets.map((d) => d.label)), '["401(k)","HSA"]');
assert.strictEqual(sa.config.options.scales.x.stacked, true);
assert.strictEqual(sa.config.options.scales.y.stacked, true);
assert.strictEqual(saSets[0].backgroundColor, "#2a78d6");                             // slot order as given
assert.strictEqual(saSets[0].backgroundColor, "#2a78d6");                                 // month in progress: solid too
const saTip = sa.config.options.plugins.tooltip.callbacks;
assert.strictEqual(saTip.label({ raw: 10.2, datasetIndex: 0, dataIndex: 0, dataset: saSets[0] }), "10%  401(k) ($1,000.00)");
assert.strictEqual(saTip.footer([{ dataIndex: 0 }]), "12%  Total");
assert.strictEqual(saTip.title([{ dataIndex: 1 }]), "2026-09 (so far)");
assert.strictEqual(JSON.stringify(sa.table.headers), '["Month","401(k)","HSA","Total"]');
assert.strictEqual(JSON.stringify(sa.table.rows[1]), '["2026-09 (so far)","12% ($1,200.00)","— ($0.00)","12%"]');

// the Net cash card asks for no legend (legend: false); a one-series card that names its segment (an institution)
// keeps it
const noLegend = sandbox.__KINDS.sharebars({ labels: ["2026-09"], partial: [false], legend: false,
  series: [{ label: "Net cash", values: [-12.5], dollars: [-1200] }] }, { dataset: {} });
assert.strictEqual(noLegend.config.options.plugins.legend.display, false);
const oneNamed = sandbox.__KINDS.sharebars({ labels: ["2026-09"], partial: [false],
  series: [{ label: "Fake Broker", values: [-12.5], dollars: [-1200] }] }, { dataset: {} });
assert.strictEqual(oneNamed.config.options.plugins.legend.display, true);

// colours by kind (user's request 2026-10-12): a hue = shades of it per segment; colors = one variable per series
const hued = sandbox.__KINDS.sharebars({ labels: ["2026-09"], partial: [false], hue: "--series-2",
  series: [{ label: "A", values: [1], dollars: [1] }, { label: "B", values: [2], dollars: [2] }] }, { dataset: {} });
assert.strictEqual(hued.config.data.datasets[0].backgroundColor, "#eb6834");        // the hue itself first
assert.notStrictEqual(hued.config.data.datasets[1].backgroundColor, "#eb6834");     // then a lighter shade of it
assert.ok(/^#[0-9a-f]{6}$/.test(hued.config.data.datasets[1].backgroundColor));
const parts = sandbox.__KINDS.sharebars({ labels: ["2026-09"], partial: [false], colors: ["--series-3", "--series-1"],
  series: [{ label: "Net cash", values: [1], dollars: [1] }, { label: "Retirement", values: [2], dollars: [2] }] }, { dataset: {} });
assert.strictEqual(parts.config.data.datasets[0].backgroundColor, "#1baf7a");
assert.strictEqual(parts.config.data.datasets[1].backgroundColor, "#2a78d6");

// money mode (the Balances page's change in value, user's request 2026-10-12): dollars on a money axis, no % anywhere
const mo = sandbox.__KINDS.sharebars({ labels: ["2026-09"], partial: [false], money: true,
  series: [{ label: "Checking & savings", values: [1500], dollars: [1500] }, { label: "Credit cards", values: [-400], dollars: [-400] }] },
  { dataset: {} });
const moTip = mo.config.options.plugins.tooltip.callbacks;
assert.strictEqual(moTip.label({ raw: -400, datasetIndex: 1, dataIndex: 0, dataset: mo.config.data.datasets[1] }), "-$400.00  Credit cards");
assert.strictEqual(moTip.footer([{ dataIndex: 0 }]), "$1,100.00  Total");
assert.strictEqual(typeof mo.config.options.scales.y.ticks.callback, "function");
assert.notStrictEqual(mo.config.options.scales.y.ticks.callback(10), "10%");
assert.strictEqual(JSON.stringify(mo.table.rows[0]), '["2026-09","$1,500.00","-$400.00","$1,100.00"]');
// the month in progress is drawn solid (user's request 2026-10-12); the tooltip still says "(so far)"
const solid = sandbox.__KINDS.sharebars({ labels: ["2026-10"], partial: [true], money: true,
  series: [{ label: "A", values: [1], dollars: [1] }] }, { dataset: {} });
assert.strictEqual(solid.config.data.datasets[0].backgroundColor, "#2a78d6");
assert.strictEqual(solid.config.options.plugins.tooltip.callbacks.title([{ dataIndex: 0 }]), "2026-10 (so far)");

// stackedarea with colours by kind and a part below zero (Balances' $ mode, user's request 2026-10-12): owed stacks
// downward from $0, not on top of what you own; the tooltip ends with the Total
const area = sandbox.__KINDS.stackedarea({ labels: ["2026-09", "2026-10"], partial: [false, true], total_footer: true,
  colors: ["--series-1", "--series-3", "--series-7"],
  series: [{ label: "Retirement", values: [100, 110] }, { label: "Cash", values: [50, 40] },
           { label: "Credit cards", values: [-20, -30] }] }, { dataset: {} });
const areaSets = area.config.data.datasets;
assert.strictEqual(JSON.stringify(areaSets.map((d) => d.fill)), '["origin",0,"origin"]');
assert.strictEqual(areaSets[0].borderColor, "#2a78d6");
const areaTip = area.config.options.plugins.tooltip.callbacks;
assert.strictEqual(areaTip.footer(areaSets.map((d, i) => ({ dataset: d, raw: d.data[1] }))), "$120.00  Total");
assert.strictEqual(areaTip.title([{ label: "2026-10", dataIndex: 1 }]), "2026-10 (so far)");
// all-positive data (Net worth, Portfolio) keeps its old fills and palette slots
const allPositive = sandbox.__KINDS.stackedarea({ labels: ["2026-10"], series: [{ label: "A", values: [1] }, { label: "B", values: [2] }] }, { dataset: {} });
assert.strictEqual(JSON.stringify(allPositive.config.data.datasets.map((d) => d.fill)), '["origin",0]');
assert.strictEqual(allPositive.config.options.plugins.tooltip.callbacks.footer([]), "");

// area in % mode (Net worth's Over time, user's request 2026-10-13): % axis, the tooltip adds the dollars, flat data
// padded by ±0.5 points (not paddedRange's $100)
const nwPct = sandbox.__KINDS.area([
  { d: "2026-10-01", v: 0.0, net: 1000, assets: 1200, liabilities: 200, carried: false },
  { d: "2026-10-02", v: 0.0, net: 1000, assets: 1200, liabilities: 200, carried: true }], { dataset: { unit: "pct" } });
assert.strictEqual(nwPct.config.options.scales.y.min, -0.5);
assert.strictEqual(nwPct.config.options.scales.y.ticks.callback(1.25), "1.25%");
const nwLines = nwPct.config.options.plugins.tooltip.callbacks.label({ dataIndex: 0, raw: 0 });
assert.strictEqual(nwLines[0], "Change  0.0%");
assert.strictEqual(nwLines[1], "Net worth  $1,000.00");
assert.strictEqual(JSON.stringify(nwPct.table.headers), '["Date","Change","Assets","Liabilities","Net worth"]');


// ---- daybars: recurring income / expenses on each day of the month (Recurring; user's request 2026-10-16)
const month = { days: ["2026-10-01", "2026-10-02", "2026-10-03"], values: [60, 0, 15.5],
                items: [[{ name: "Fake Phone", amount: 60 }], [], [{ name: "Fake Gym", amount: 10 }, { name: "Fake App", amount: 5.5 }]] };
const out = sandbox.__KINDS.daybars(month, { dataset: { valueLabel: "Expenses", direction: "out" } });
assert.strictEqual(out.config.type, "bar");
assert.strictEqual(out.config.data.datasets[0].backgroundColor, "#e34948");                    // money out: red
assert.strictEqual(sandbox.__KINDS.daybars(month, { dataset: {} }).config.data.datasets[0].backgroundColor, "#2a78d6");
assert.strictEqual(out.config.options.plugins.legend.display, false);                          // one series, no legend
const tips = out.config.options.plugins.tooltip.callbacks;
assert.strictEqual(tips.label({ raw: 15.5 }), "$15.50  Expenses");
assert.strictEqual(JSON.stringify(tips.afterBody([{ dataIndex: 2 }])), '["Fake Gym: $10.00","Fake App: $5.50"]');
const tick = out.config.options.scales.x.ticks.callback;
assert.strictEqual(tick.call({ getLabelForValue: (i) => month.days[i] }, 2), "3");              // day of the month
assert.strictEqual(JSON.stringify(out.table.headers), '["Day","Expenses","Items"]');
assert.strictEqual(out.table.rows.length, 2);                                                  // days with nothing due left out
assert.strictEqual(JSON.stringify(out.table.rows[1]), '["2026-10-03","$15.50","Fake Gym, Fake App"]');
console.log("chart kinds: all assertions passed");
