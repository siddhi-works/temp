// Checks that a big phone photo is shrunk in the browser before upload. Needs the backend with an empty
// database and the app running. Usage: IMG=/path/big.jpg node scripts/upload-size-check.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
const { chromium } = createRequire(import.meta.url)("playwright");
const BASE = process.env.BASE || "http://localhost:3000";
const IMG = process.env.IMG;
const b = await chromium.launch();
const page = await (await b.newContext({ viewport: { width: 412, height: 915 } })).newPage();
await page.goto(BASE + "/desk");
await page.getByText("Setting up Samyukta for the first time?").click();
await page.getByLabel("College code").fill("PERF");
await page.getByLabel("College name").fill("Perf College");
await page.getByLabel("Your name").fill("Admin");
await page.getByRole("button", { name: "Set up college" }).click();
await page.getByRole("button", { name: "Continue to the desk" }).click();
await page.getByRole("tab", { name: "Students" }).click();
await page.getByLabel("WhatsApp number").fill("9800000009");
await page.getByLabel("The student agreed").check();
await page.getByRole("button", { name: "Add student" }).click();
const link = await page.getByRole("link", { name: /\/me\?t=/ }).getAttribute("href");
await page.goto(BASE + link);
await page.getByRole("button", { name: /right now/i }).waitFor();
// The backend stores the upload as received, so its size on disk is what travelled over the network.
const STORAGE = process.env.STORAGE_DIR || "./storage";
const stored = () => (fs.existsSync(`${STORAGE}/snaps`) ? fs.readdirSync(`${STORAGE}/snaps`, { recursive: true, withFileTypes: true }).filter((f) => f.isFile()).map((f) => fs.statSync(`${f.parentPath}/${f.name}`).size) : []);
await page.locator("#board-file").setInputFiles(IMG);
await page.getByText(/Saved\. Reading the board/).waitFor({ timeout: 20000 });
const sent = stored()[0] || 0;
const original = fs.statSync(IMG).size;
console.log(`original ${(original / 1e6).toFixed(2)} MB, uploaded ${(sent / 1e3).toFixed(0)} KB (${(100 - (100 * sent) / original).toFixed(0)}% smaller)`);
await b.close();
process.exit(sent && sent < original / 4 ? 0 : 1);
