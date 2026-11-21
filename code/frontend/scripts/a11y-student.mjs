// Automated accessibility pass over the student screens (/me): axe WCAG 2.2 AA in every colour theme and text size,
// plus a keyboard walk (every stop needs a visible focus ring and a name) and a touch-target size check.
// It does NOT replace testing with TalkBack on a real phone.
//
// Usage: backend on :8001, frontend on :3000 (npm run build && npm start), then
//   BASE=http://localhost:3000 node scripts/a11y-student.mjs
// Uses the Chrome installed on this computer (channel "chrome"); set CHANNEL=msedge to use Edge.

import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core"); // installed with @axe-core/playwright; the browser is your own Chrome
const AxeBuilder = require("@axe-core/playwright").default;

const BASE = process.env.BASE || "http://localhost:3000";
const looks = ["daylight", "sepia", "dark", "contrast"];
const sizes = ["normal", "xlarge"];
const screens = ["", "#read", "#catch", "#deadlines", "#settings"];
const ABOUT = true;
let failed = false;
const rows = [];

const browser = await chromium.launch({ channel: process.env.CHANNEL || "chrome" });

async function audit(page, label) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "wcag2aaa", "best-practice"]).analyze();
  const bad = r.violations.filter((v) => ["serious", "critical"].includes(v.impact));
  if (bad.length) failed = true;
  rows.push({ label, serious: bad.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0].target.join(" ").slice(0, 70)}`), minor: r.violations.filter((v) => !bad.includes(v)).map((v) => v.id) });
}

async function targets(page, label) {
  const small = await page.evaluate(() =>
    [...document.querySelectorAll("a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button]")]
      .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden"; })
      .filter((e) => { const r = e.getBoundingClientRect(); return !e.classList.contains("sr-only") && (r.height < 44 || r.width < 44); })
      .map((e) => `${e.tagName.toLowerCase()} "${(e.innerText || e.getAttribute("aria-label") || e.id || "").trim().slice(0, 30)}" ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`),
  );
  if (small.length) { failed = true; rows.push({ label: label + " TOUCH TARGETS < 44px", serious: small, minor: [] }); }
}

async function keyboard(page, label, stops = 14) {
  await page.mouse.click(1, 1);
  const seen = [];
  for (let i = 0; i < stops; i++) {
    await page.keyboard.press("Tab");
    seen.push(await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const hidden = el.getBoundingClientRect().width <= 2;
      const cs = getComputedStyle(hidden && el.nextElementSibling ? el.nextElementSibling : el);
      const name = (el.getAttribute("aria-label") || el.innerText || (el.labels && el.labels[0] && el.labels[0].innerText) || "").trim().slice(0, 40);
      return { name, ring: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 2 };
    }));
  }
  const stops2 = seen.filter(Boolean);
  const bad = stops2.filter((s) => !s.ring || !s.name);
  if (bad.length) { failed = true; rows.push({ label: label + " KEYBOARD", serious: bad.map((s) => `${s.name || "(no name)"} ${s.ring ? "" : "no focus ring"}`), minor: [] }); }
  return stops2.map((s) => s.name);
}

for (const size of sizes) {
  for (const look of looks) {
    const ctx = await browser.newContext({ viewport: { width: 412, height: 915 } });
    await ctx.addInitScript(([l, s]) => localStorage.setItem("saathi_display", JSON.stringify({ look: l, size: s, space: "wide" })), [look, size]);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => { failed = true; rows.push({ label: "PAGE ERROR", serious: [String(e).slice(0, 120)], minor: [] }); });
    await page.goto(`${BASE}/me`);
    await page.waitForSelector("h1");
    await audit(page, `welcome / ${look} / ${size}`);
    await targets(page, `welcome / ${look} / ${size}`);
    await page.getByRole("button", { name: "Start" }).click();
    await page.waitForFunction(() => document.querySelector("h1")?.textContent?.includes("What would you like"));
    for (const hash of screens) {
      await page.evaluate((h) => (window.location.hash = h), hash);
      await page.waitForTimeout(500);
      const title = await page.locator("h1").first().innerText();
      const focused = await page.evaluate(() => document.activeElement?.tagName);
      if (focused !== "H1") { failed = true; rows.push({ label: `${hash || "home"} FOCUS`, serious: [`title not focused after navigating (focus is on ${focused})`], minor: [] }); }
      await audit(page, `${hash || "home"} "${title}" / ${look} / ${size}`);
      if (look === "daylight" && size === "normal") {
        await targets(page, hash || "home");
        const order = await keyboard(page, hash || "home");
        rows.push({ label: `${hash || "home"} tab order`, serious: [], minor: order });
      }
    }
    await page.goto(`${BASE}/about`);
    await page.waitForSelector("h1");
    await audit(page, `about / ${look} / ${size}`);
    if (look === "daylight" && size === "normal") await targets(page, "about");
    await ctx.close();
  }
}
await browser.close();

for (const r of rows) {
  const flag = r.serious.length ? "FAIL" : "ok  ";
  console.log(`${flag} ${r.label}`);
  for (const s of r.serious) console.log(`      ! ${s}`);
  if (r.label.endsWith("tab order")) console.log(`      ${r.minor.join(" > ")}`);
  else if (r.minor.length) console.log(`      (minor: ${r.minor.join(", ")})`);
}
console.log(failed ? "\nRESULT: problems found" : "\nRESULT: all checks passed");
process.exit(failed ? 1 : 0);
