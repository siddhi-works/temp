// Automated accessibility pass over every screen, driven like a real user (keyboard and forms).
// It complements, and does NOT replace, testing with TalkBack and NVDA on real devices.
//
// Usage: backend on :8000 with an EMPTY database, frontend on :3000, then
//   BASE=http://localhost:3000 node scripts/a11y-check.mjs
// It sets up a test college through the UI, so never point it at a real deployment.

import fs from "node:fs";
import { createRequire } from "node:module";

// CommonJS require also finds a globally installed Playwright (NODE_PATH), which ESM import does not.
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const AxeBuilder = require("@axe-core/playwright").default;

const BASE = process.env.BASE || "http://localhost:3000";
const OUT = process.env.OUT || "a11y-report";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
let failed = false;

async function audit(page, name) {
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
    const bad = r.violations.filter((v) => ["serious", "critical"].includes(v.impact));
    if (bad.length) failed = true;
    results.push({
      page: name,
      scheme,
      passes: r.passes.length,
      violations: r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target.join(" ")).slice(0, 5) })),
    });
    await page.screenshot({ path: `${OUT}/${name}-${scheme}.png`, fullPage: true });
  }
  await page.emulateMedia({ colorScheme: "light" });
}

/** Tab through the page; every stop must have a visible focus ring and an accessible name. */
async function keyboardWalk(page, name, stops = 25) {
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.mouse.click(1, 1);
  const seen = [];
  for (let i = 0; i < stops; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      // Chrome's own date/time picker button lives in a shadow root we cannot inspect; it draws its own ring.
      if (/^(date|time)$/.test(el.type || "") && !el.matches(":focus")) return { tag: "picker", name: "native picker", outline: true };
      const hidden = el.getBoundingClientRect().width <= 2;
      const cs = getComputedStyle(hidden && el.nextElementSibling ? el.nextElementSibling : el);
      const name = (el.getAttribute("aria-label") || el.innerText || el.getAttribute("value") || (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute("alt") || "").trim().slice(0, 60);
      return { tag: el.tagName.toLowerCase(), name, outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 2 };
    });
    if (!info) break;
    seen.push(info);
  }
  const noRing = seen.filter((s) => !s.outline);
  const noName = seen.filter((s) => !s.name && s.tag !== "input" && s.tag !== "select" && s.tag !== "textarea");
  if (noRing.length || noName.length) failed = true;
  results.push({ page: name, keyboard: { stops: seen.length, order: seen.map((s) => `${s.tag}: ${s.name}`), missingFocusRing: noRing, missingName: noName } });
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, permissions: [] });
const page = await ctx.newPage();
page.on("pageerror", (e) => {
  failed = true;
  results.push({ pageError: String(e) });
});

// 1. Landing
await page.goto(BASE + "/");
await audit(page, "home");
await keyboardWalk(page, "home", 8);

// 2. Desk: first-time setup through the UI
await page.goto(BASE + "/desk");
await audit(page, "desk-signin");
await page.getByText("Setting up Samyukta for the first time?").click();
await page.getByLabel("College code").fill("A11Y");
await page.getByLabel("College name").fill("Accessibility Test College");
await page.getByLabel("Your name").fill("Test Admin");
await page.getByRole("button", { name: "Set up college" }).click();
await page.getByRole("button", { name: "Continue to the desk" }).click();
await page.getByRole("heading", { name: "College desk" }).waitFor();
await audit(page, "desk-notices");

// Students tab: enrol a test student, get the app link
await page.getByRole("tab", { name: "Students" }).click();
await page.getByLabel("WhatsApp number").fill("9800000001");
await page.getByLabel("The student agreed").check();
await page.getByRole("button", { name: "Add student" }).click();
const link = await page.getByRole("link", { name: /\/me\?t=/ }).getAttribute("href");
await audit(page, "desk-students");

// Team tab (admin only): create a volunteer token
await page.getByRole("tab", { name: "Team" }).click();
await page.getByLabel("Name").fill("Asha Volunteer");
await page.getByRole("button", { name: "Create sign-in token" }).click();
await page.getByText(/Sign-in token for Asha Volunteer/).waitFor();
await audit(page, "desk-team");

// Walkthroughs tab: create and record (typed steps), publish
await page.getByRole("tab", { name: "Walkthroughs" }).click();
await page.getByLabel("Name").fill("Main gate to library");
await page.getByLabel("Starts at").fill("Main gate");
await page.getByLabel("Ends at").fill("Library");
await page.getByRole("button", { name: "Start recording" }).click();
await page.waitForURL(/\/desk\/routes\//);
const routeId = page.url().split("/").pop();
for (const t of ["Walk straight 30 steps with the wall on your left.", "Climb the three steps. The library door is on your right."]) {
  await page.getByLabel("What the clip says").fill(t);
  await page.getByRole("button", { name: "Add step" }).click();
  await page.getByText(t, { exact: true }).first().waitFor();
  await page.getByRole("button", { name: "Add step" }).and(page.locator(":enabled")).waitFor();
}
await audit(page, "route-recorder");
await page.getByRole("button", { name: "I walked it, publish" }).click();
await page.getByText("Live", { exact: true }).waitFor();

// 3. Walkthrough player, keyboard only
await page.goto(`${BASE}/r/${routeId}`);
await page.getByRole("button", { name: "Start walking" }).waitFor();
await audit(page, "route-player-start");
await page.getByRole("button", { name: "Start walking" }).focus();
await page.keyboard.press("Enter");
await page.waitForFunction(() => document.activeElement?.classList.contains("step-text"), null, { timeout: 5000 }).catch(() => null);
const focusedStep = await page.evaluate(() => document.activeElement?.textContent?.slice(0, 200));
results.push({ page: "route-player", focusAfterStart: focusedStep });
if (!focusedStep?.includes("Walk straight")) failed = true;
await audit(page, "route-player-step");

// 4. Student app
await page.goto(BASE + link);
await page.getByRole("button", { name: /right now/i }).waitFor();
await audit(page, "student-app");
await keyboardWalk(page, "student-app", 30);
await page.getByRole("button", { name: /right now/i }).click();
await page.locator("#now-answer").filter({ hasText: /./ }).waitFor();
await page.getByLabel("Day").selectOption({ label: "Monday" });
await page.getByLabel("Subject").fill("Financial Accounting");
await page.getByRole("button", { name: "Add class" }).click();
await page.getByText("Added Financial Accounting").waitFor();
await page.getByLabel("Or type it").fill("Submit the lab journal in 5 days");
await page.getByRole("button", { name: "Check the date" }).click();
await page.getByRole("button", { name: "Save reminder" }).waitFor();
await page.waitForFunction(() => document.activeElement?.tagName === "H3", null, { timeout: 5000 }).catch(() => null);
const focusAfterCatch = await page.evaluate(() => document.activeElement?.textContent?.slice(0, 200));
results.push({ page: "student-app", focusAfterCatch });
if (!focusAfterCatch?.includes("Due")) failed = true;
await audit(page, "student-catch-readback");
await page.getByRole("button", { name: "Save reminder" }).click();
await page.getByText(/^Saved:/).waitFor();
await audit(page, "student-after-save");

// 5. Student app without a link
await page.goto(BASE + "/me");
await audit(page, "student-no-link");

await browser.close();
fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(results, null, 2));
for (const r of results) {
  if (r.violations) console.log(`${r.page} [${r.scheme}]: ${r.violations.length ? r.violations.map((v) => `${v.impact} ${v.id} (${v.nodes.length})`).join(", ") : "no violations"} (${r.passes} rules passed)`);
  else console.log(JSON.stringify(r));
}
console.log(failed ? "\nFAILED: fix the serious issues above." : "\nPASSED automated checks. Now test with TalkBack and NVDA.");
process.exit(failed ? 1 : 0);
