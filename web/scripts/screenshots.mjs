// Screenshots for the write-up, taken against a running dev setup (`make dev`).
// It creates a new stability review, then captures the grid mid-run, a done cell's drawer
// with its citation highlighted, a not-found cell's drawer, and the review list.
// Usage (from web/): npm run screenshots   -> web/screenshots/*.png
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const WEB = process.env.WEB_URL ?? "http://127.0.0.1:5173";
const API = process.env.API_URL ?? "http://127.0.0.1:8010";
const OUT = new URL("../screenshots/", import.meta.url);
const shot = (page, name) => page.screenshot({ path: new URL(name, OUT).pathname });

await mkdir(OUT, { recursive: true });
const name = `Stability review ${new Date().toISOString().slice(0, 16).replace("T", " ")}`;
const res = await fetch(`${API}/api/reviews`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name, template: "stability_protocol" }),
});
if (!res.ok) throw new Error(`creating the review failed: ${res.status} ${await res.text()}`);
const { id } = await res.json();
console.log(`created review ${id}: ${name}`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 940 }, deviceScaleFactor: 1.5 });

await page.goto(`${WEB}/#/reviews/${id}`);
await page.waitForFunction(() => document.querySelectorAll(".cell-done").length >= 12, null, { timeout: 120_000 });
await shot(page, "1-grid-mid-run.png");
console.log("grid mid-run");

await page.waitForFunction(
  () => document.querySelectorAll(".cell-pending, .cell-running").length === 0,
  null,
  { timeout: 300_000 },
);
await page.click('button[data-doc="STAB-013"][data-key="deviations"]');
await page.waitForSelector(".doc-viewer mark.cite");
await page.waitForTimeout(600);
await shot(page, "2-drawer-citation.png");
console.log("drawer with the appendix deviation highlighted");

await page.click('button[data-doc="STAB-011"][data-key="bracketing_justification"]');
await page.waitForSelector(".drawer .not-found");
await page.waitForTimeout(400);
await shot(page, "3-drawer-not-found.png");
console.log("drawer for a not-found cell");

await page.goto(`${WEB}/#/`);
await page.waitForSelector(".list-table tbody tr");
await shot(page, "4-review-list.png");
console.log("review list");

await browser.close();
console.log(`saved to ${OUT.pathname}`);
