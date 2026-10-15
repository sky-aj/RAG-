import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/07/01a1157a-2e88-7151-b7ee-6ffb00cc06ff/step1";
const APP = "http://127.0.0.1:5173/";
const SAMPLE = "E:/Desktop/rag指标/backend/data/samples/限流策略说明.md";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));

await page.goto(APP, { waitUntil: "networkidle" });

// 1. 后端连接状态
await page.waitForSelector(".status-pill.is-online", { timeout: 20000 });
const pill = (await page.textContent(".status-pill"))?.trim();
console.log("连接状态:", pill);
await page.screenshot({ path: OUT + "/01-chat.png" });

// 2. 知识库视图 + 链路就绪
await page.click('.nav-item:has-text("知识库")');
await page.waitForSelector(".kb-grid", { timeout: 10000 });
await page.waitForTimeout(600);
const readiness = await page.$$eval(".health-item", (nodes) =>
  nodes.map((n) => n.textContent.replace(/\s+/g, " ").trim())
);
console.log("链路就绪:", readiness.join(" | "));
await page.screenshot({ path: OUT + "/02-knowledge.png" });

// 3. 真实上传
await page.setInputFiles('input[type="file"]', SAMPLE);
await page.waitForFunction(
  () => Array.from(document.querySelectorAll(".table tbody tr")).some((tr) => tr.textContent.includes("限流策略说明.md")),
  { timeout: 20000 }
);
const rows = await page.$$eval(".table tbody tr", (nodes) =>
  nodes.map((n) => n.textContent.replace(/\s+/g, " ").trim())
);
console.log("文档表:", rows.join(" || "));
await page.screenshot({ path: OUT + "/03-uploaded.png" });

// 4. 主题切换
await page.click(".rail-foot .ghost-btn");
await page.waitForSelector(".drawer", { timeout: 10000 });
await page.click('.theme-card[data-preview="brutalist"]');
await page.waitForTimeout(500);
await page.screenshot({ path: OUT + "/04-drawer-brutalist.png" });
await page.click('.theme-card[data-preview="minimalist"]');
await page.click(".drawer-foot .solid-btn");
await page.waitForTimeout(400);

// 5. 清理：删除刚上传的文档
const deleteButtons = await page.$$('.table tbody tr .micro-btn');
for (const button of deleteButtons) {
  const row = await button.evaluateHandle((node) => node.closest("tr"));
  const text = await row.evaluate((node) => node.textContent || "");
  if (text.includes("限流策略说明.md")) {
    await button.click();
    break;
  }
}
await page.waitForFunction(
  () => !Array.from(document.querySelectorAll(".table tbody tr")).some((tr) => tr.textContent.includes("限流策略说明.md")),
  { timeout: 20000 }
);
console.log("清理完成，文档已删除");
await page.screenshot({ path: OUT + "/05-cleanup.png" });

await browser.close();
console.log("控制台错误:", errors.length ? errors.join(" | ") : "无");
console.log("截图目录:", OUT);
