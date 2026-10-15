import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/07/01a1157a-2e88-7151-b7ee-6ffb00cc06ff";
const URL = "file:///E:/Desktop/rag%E6%8C%87%E6%A0%87/web/index.html";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));

await page.goto(URL);
await page.waitForTimeout(900);
await page.screenshot({ path: OUT + "/01-chat-minimalist.png" });

for (const theme of ["soft", "brutalist", "stitch"]) {
  await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
  await page.waitForTimeout(420);
  await page.screenshot({ path: OUT + "/02-chat-" + theme + ".png" });
}

await page.evaluate(() => {
  document.documentElement.dataset.theme = "minimalist";
  document.documentElement.dataset.accent = "indigo";
});
await page.waitForTimeout(400);
await page.screenshot({ path: OUT + "/03-chat-indigo.png" });

await page.evaluate(() => { document.documentElement.dataset.accent = "ink"; });
await page.click('[data-view="kb"]');
await page.waitForTimeout(700);
await page.screenshot({ path: OUT + "/04-kb.png" });

await page.click("#openStyle");
await page.waitForTimeout(700);
await page.screenshot({ path: OUT + "/05-drawer.png" });

await page.click("#closeStyle");
await page.click('[data-view="chat"]');
await page.waitForTimeout(400);
await page.setViewportSize({ width: 420, height: 880 });
await page.waitForTimeout(500);
await page.screenshot({ path: OUT + "/06-mobile.png" });

await browser.close();
console.log("shots written to " + OUT);
console.log("console errors: " + (errors.length ? errors.join(" | ") : "none"));
