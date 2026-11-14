const assert = require("assert");
const { activePoint, matches, sliceKey } = require(process.argv[2]);
const none = { hover: null, pinned: null, category: null };
const row = (day, category, month = "2026-10") => ({ day, category, month });
const other = ["B", "C"];

// no filter: everything passes, in both modes
assert(matches(row(3, "A"), none, other, "days") && matches(row(3, "A"), none, other, "months"));

// DAYS mode: a point shows everything THROUGH that day (the chart is cumulative)
const hover5 = { ...none, hover: 5 };
assert(matches(row(1, "A"), hover5, other, "days") && matches(row(5, "A"), hover5, other, "days") && !matches(row(6, "A"), hover5, other, "days"));
// a locked point wins over hover (hover is ignored until unlocked)
const locked = { hover: 9, pinned: 5, category: null };
assert.strictEqual(activePoint(locked), 5);
assert(matches(row(5, "A"), locked, other, "days") && !matches(row(6, "A"), locked, other, "days"));
// unlocking returns to wherever the pointer is
assert.strictEqual(activePoint({ ...locked, pinned: null }), 9);
assert.strictEqual(activePoint(none), null);
// a falsy point must not be mistaken for "no point"
assert.strictEqual(activePoint({ ...none, pinned: 0 }), 0);

// MONTHS mode: a point is a calendar month, and only that month shows (bars are not cumulative)
const sep = { ...none, hover: "2026-09" };
assert(matches(row(30, "A", "2026-09"), sep, other, "months") && matches(row(1, "A", "2026-09"), sep, other, "months"));
assert(!matches(row(1, "A", "2026-10"), sep, other, "months") && !matches(row(1, "A", "2026-08"), sep, other, "months"));
const lockedSep = { hover: "2026-10", pinned: "2026-09", category: null };
assert(matches(row(2, "A", "2026-09"), lockedSep, other, "months") && !matches(row(2, "A", "2026-10"), lockedSep, other, "months"));

// category: exact, and "_other" = every folded category
assert(matches(row(1, "A"), { ...none, category: "A" }, other, "days") && !matches(row(1, "B"), { ...none, category: "A" }, other, "days"));
const o = { ...none, category: "_other" };
assert(matches(row(1, "B"), o, other, "days") && matches(row(1, "C"), o, other, "days") && !matches(row(1, "A"), o, other, "days"));
// both filters together
const both = { hover: null, pinned: 2, category: "A" };
assert(matches(row(2, "A"), both, other, "days") && matches(row(1, "A"), both, other, "days"));
assert(!matches(row(2, "B"), both, other, "days") && !matches(row(3, "A"), both, other, "days"));
const bothMonths = { hover: null, pinned: "2026-09", category: "A" };
assert(matches(row(2, "A", "2026-09"), bothMonths, other, "months") && !matches(row(2, "B", "2026-09"), bothMonths, other, "months"));

// slices
assert.strictEqual(sliceKey("A", { A: "slot-1", B: "slot-other" }), "A");
assert.strictEqual(sliceKey("B", { A: "slot-1", B: "slot-other" }), "_other");
assert.strictEqual(sliceKey(null, {}), null);
console.log("spending.js rules: all assertions passed");
