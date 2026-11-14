#!/usr/bin/env node
// Phone check (docs/phone-plan.md, phase 0): loads fintrack's pages in headless Chrome with true phone emulation
// (DevTools protocol: a WIDTHx844 viewport, mobile, touch, an Android user agent; `--window-size` crops instead, see
// docs/ui.md) and measures each page:
//   - Page sideways: the page itself is wider than the screen (should never be)
//   - Above content: px from the top of the screen to the first card in <main> (target <= 120; the page's controls
//     are in a drop-down from its name since 2026-11-06, so they take no room)
//   - Tables sideways: elements in a sideways-scrolling box (main tables should fit; "Show data" tables are
//     opt-in and closed, so anything in a closed <details> doesn't count, nor does a swipe row)
//   - Boxes up/down: elements that scroll up and down inside the page (overflow-y auto/scroll and taller content) - a
//     scroll box within the page's own scroll. Should be none on a phone (Tags' cards were one until 2026-11-11).
//   - Small targets: links, buttons and inputs under 32px tall or wide (`--min 40`: phase 5's floor). A checkbox or
//     radio inside a <label> is measured by the label (tapping the label ticks it; the box itself can stay small).
// Read-only: GET requests only, sets no cookies, its own throwaway Chrome profile. Needs Node 18+ and Google Chrome.
//
// Usage (the server must be running, e.g. the launchd job com.fintrack.serve):
//   node tools/phone_check.mjs                         every page at 390px, table to stdout
//   node tools/phone_check.mjs --width 360,390,412     several widths
//   node tools/phone_check.mjs --min 40 --why /categories   targets under 40px, and which kinds they are
//   node tools/phone_check.mjs --shots ~/Desktop/phone /budgets /tags
//                                                      + screenshots in 1300px slices (they show your real data, so
//                                                        the folder must be outside this repo)
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
// Every tab in web/deps.NAV; /accounts/{id} is added from the first account link on /accounts.
const ALL_PAGES = ["/", "/transactions", "/spending", "/networth", "/accounts", "/investments", "/portfolio",
  "/savings", "/cash-flow", "/credit-cards", "/budgets", "/tags", "/recurring", "/projection", "/setup",
  "/import", "/account-types", "/categories", "/rules", "/transfers"];

// ---- arguments ----
const args = process.argv.slice(2);
let widths = [390], shots = null, base = "https://localhost:8443", min = 32, why = false;
const pages = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--width") widths = args[++i].split(",").map(Number);
  else if (args[i] === "--shots") shots = resolve(args[++i].replace(/^~/, process.env.HOME));
  else if (args[i] === "--base") base = args[++i];
  else if (args[i] === "--min") min = Number(args[++i]);
  else if (args[i] === "--why") why = true;
  else pages.push(args[i]);
}
if (shots && (shots === REPO || shots.startsWith(REPO + sep))) {
  console.error("--shots must be outside the repo: screenshots show your real data.");
  process.exit(2);
}

// ---- headless Chrome over the DevTools protocol ----
const profile = mkdtempSync(join(tmpdir(), "fintrack-phone-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--ignore-certificate-errors", "--no-first-run", "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let targets;
for (let i = 0; i < 50 && !targets; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); } catch { await sleep(200); }
}
if (!targets) { console.error("Chrome didn't start."); process.exit(1); }
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let nextId = 0;
const pending = new Map(), listeners = new Set();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } else listeners.forEach((f) => f(m));
});
const send = (method, params = {}) => new Promise((r) => {
  const id = ++nextId; pending.set(id, r); ws.send(JSON.stringify({ id, method, params }));
});
async function go(url) {
  const loaded = new Promise((r) => {
    const f = (m) => { if (m.method === "Page.loadEventFired") { listeners.delete(f); r(); } };
    listeners.add(f);
  });
  await send("Page.navigate", { url });
  await loaded;
  await sleep(1500);                                  // charts draw after load
}
const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true })).result.result.value;
const phone = (width, height = 844, scale = 2) =>
  send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: scale, mobile: true });

await send("Page.enable");
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
await send("Emulation.setUserAgentOverride", { userAgent:
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36" });

// What's measured on each page (runs in the page).
const PROBE = `(() => {
  const W = document.documentElement.clientWidth;
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
  const main = document.querySelector("main");
  const first = main && [...main.children].find(visible);
  let sideways = 0, small = 0, updown = 0;
  const updownNames = [];
  for (const box of document.querySelectorAll("main *")) {
    const o = getComputedStyle(box).overflowX;
    // a swipe row (scroll-snap-type: x, e.g. Transactions' two cards) is meant to scroll sideways
    if ((o === "auto" || o === "scroll") && box.scrollWidth > box.clientWidth + 1 && visible(box) &&
        !getComputedStyle(box).scrollSnapType.startsWith("x") &&
        !box.closest("details:not([open])")) sideways++;      // a closed "Show data" (Chrome still lays it out)
    const oy = getComputedStyle(box).overflowY;
    // A box whose height is set on purpose scrolls on purpose: it, or something it's in, has a max-height (Tags'
    // calendar card, 40% of the screen) or fills a box of fixed size (position: absolute, Spending's By category).
    const sized = (el) => { for (let e = el; e && e !== main; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.maxHeight !== "none" || cs.position === "absolute") return true;
    } return false; };
    if ((oy === "auto" || oy === "scroll") && box.scrollHeight > box.clientHeight + 1 && visible(box) && !sized(box) &&
        !box.closest("details:not([open])")) {
      updown++;
      updownNames.push(box.tagName.toLowerCase() + (box.classList[0] ? "." + box.classList[0] : ""));
    }
  }
  const kinds = {};
  // inside a closed <details> (a menu, a confirm, "Show data"): not on screen, though Chrome still lays it out; its own
  // <summary> is what you see
  const shut = (el) => { for (let d = el.closest("details:not([open])"); d; d = d.parentElement?.closest("details:not([open])")) {
    const summary = d.querySelector(":scope > summary");
    if (!(summary && summary.contains(el))) return true;
  } return false; };
  for (const el of document.querySelectorAll("a, button, input, select, summary, [role=switch]")) {
    if (!visible(el) || el.type === "hidden" || shut(el)) continue;
    // a checkbox / radio in a label: the label is what you tap
    const target = (el.type === "checkbox" || el.type === "radio") && el.closest("label") ? el.closest("label") : el;
    const r = target.getBoundingClientRect();
    if (r.height < ${min} || r.width < ${min}) {
      small++;
      if (${why}) {
        const name = (e) => e ? e.tagName.toLowerCase() + (e.classList[0] ? "." + e.classList[0] : "") +
                                (e.type && e.tagName === "INPUT" ? "[" + e.type + "]" : "") : "";
        const key = name(target) + " in " + name(target.parentElement?.closest("[class]"));
        const k = kinds[key] ||= { n: 0, w: Math.round(r.width), h: Math.round(r.height) };
        k.n++;
      }
    }
  }
  return { pageWide: document.documentElement.scrollWidth - W, above: first ? Math.round(first.getBoundingClientRect().top + scrollY) : null,
           height: document.documentElement.scrollHeight, sideways, small, kinds, updown, updownNames,
           account: document.querySelector('a[href^="/accounts/"]')?.getAttribute("href") };
})()`;

// ---- run ----
let list = pages.length ? pages : ALL_PAGES;
const rows = [];
for (const width of widths) {
  await phone(width);
  for (let i = 0; i < list.length; i++) {
    const path = list[i];
    await go(base + path);
    const m = await evaluate(PROBE);
    // the first account's page goes after /accounts
    if (!pages.length && path === "/accounts" && m.account && /^\/accounts\/\d+$/.test(m.account) && !list.includes(m.account))
      list = [...list.slice(0, i + 1), m.account, ...list.slice(i + 1)];
    const ok = m.pageWide <= 0 && m.above !== null && m.above <= 120 && m.sideways === 0 && m.updown === 0;
    rows.push({ width, path, ...m, ok });
    if (shots) {
      mkdirSync(shots, { recursive: true });
      const h = Math.min(m.height, 14000);
      await phone(width, h, 1);
      await sleep(600);
      const name = `${width}-${path === "/" ? "overview" : path.slice(1).replace(/[/?=&]/g, "_")}`;
      for (let y = 0, n = 0; y < h; y += 1300, n++) {
        const shot = await send("Page.captureScreenshot", { format: "png", clip: { x: 0, y, width, height: Math.min(1300, h - y), scale: 1 } });
        writeFileSync(join(shots, `${name}-${n}.png`), Buffer.from(shot.result.data, "base64"));
      }
      await phone(width);
    }
  }
}
ws.close();
chrome.kill();
await sleep(300);
rmSync(profile, { recursive: true, force: true });

// ---- report ----
console.log(`| Width | Page | Pass | Screens | Above content | Page sideways | Tables sideways | Boxes up/down | Small targets (<${min}px) |`);
console.log("|---|---|---|---|---|---|---|---|---|");
for (const r of rows)
  console.log(`| ${r.width} | ${r.path} | ${r.ok ? "yes" : "no"} | ${(r.height / 844).toFixed(1)} | ${r.above ?? "?"} | ` +
    `${r.pageWide > 0 ? r.pageWide + "px" : "—"} | ${r.sideways || "—"} | ` +
    `${r.updown ? r.updown + " (" + [...new Set(r.updownNames)].join(", ") + ")" : "—"} | ${r.small} |`);
console.log(`\n${rows.filter((r) => r.ok).length} of ${rows.length} pass (no page or table sideways, no box scrolling up/down; content within 120px).`);
if (why) {
  console.log(`\nSmall targets (under ${min}px) by kind, most first (count, a typical width x height):`);
  for (const r of rows) {
    const top = Object.entries(r.kinds || {}).sort((a, b) => b[1].n - a[1].n).slice(0, 8);
    if (!top.length) continue;
    console.log(`\n${r.width} ${r.path}`);
    for (const [k, v] of top) console.log(`  ${String(v.n).padStart(5)}  ${k}  (${v.w}x${v.h})`);
  }
}
if (shots) console.log(`Screenshots: ${shots}`);
