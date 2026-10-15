import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/09/01a11f1f-b035-7f31-9f2a-41738af05ba7/step6";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1480, height: 1000 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
const bad = [];
page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) bad.push(r.status() + " " + r.url()); });

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForSelector(".status-pill.is-online", { timeout: 20000 });

const badge = await page.evaluate(() => document.querySelector(".topbar-actions .ghost-btn").textContent.trim());
console.log("[顶栏徽标] " + badge);

// 切到知识库 -> 评测页签
await page.click(".nav-item:has-text('知识库')");
await page.waitForSelector(".panel-tab:has-text('评测')", { timeout: 15000 });
await page.click(".panel-tab:has-text('评测')");
await page.waitForTimeout(1500);
await page.screenshot({ path: OUT + "/01-eval-panel.png" });

const metrics = await page.evaluate(() =>
  Array.from(document.querySelectorAll(".eval-metric")).map((m) => ({
    label: m.querySelector(".eval-metric-label").textContent.trim(),
    value: m.querySelector("strong").textContent.trim(),
    cls: m.querySelector("strong").className
  }))
);
console.log("\n[RAGAS 四项]");
metrics.forEach((m) => console.log("  " + m.label + " = " + m.value + "  (" + m.cls + ")"));

const hard = await page.evaluate(() =>
  Array.from(document.querySelectorAll(".eval-hard span")).map((s) => s.textContent.replace(/\s+/g, " ").trim())
);
console.log("\n[硬指标] " + hard.join("  |  "));

const cases = await page.evaluate(() =>
  Array.from(document.querySelectorAll(".eval-case header")).map((h) => h.textContent.replace(/\s+/g, " ").trim())
);
console.log("\n[样本] " + cases.length + " 条");
cases.forEach((c) => console.log("  " + c));

// 展开第一条样本
await page.click(".eval-case:first-of-type");
await page.waitForSelector(".eval-case-body", { timeout: 10000 });
const detail = await page.evaluate(() => {
  const body = document.querySelector(".eval-case-body");
  return Array.from(body.querySelectorAll("dl > div")).map((d) => ({
    key: d.querySelector("dt").textContent.trim(),
    value: d.querySelector("dd").textContent.replace(/\s+/g, " ").trim().slice(0, 120)
  }));
});
console.log("\n[样本详情]");
detail.forEach((d) => console.log("  " + d.key + "：" + d.value));
await page.screenshot({ path: OUT + "/02-case-detail.png" });

const historyRows = await page.evaluate(() =>
  Array.from(document.querySelectorAll(".eval-history tbody tr")).map((tr) =>
    Array.from(tr.querySelectorAll("td")).map((td) => td.textContent.trim()).join(" | ")
  )
);
console.log("\n[历史跑分] " + historyRows.length + " 行");
historyRows.slice(0, 4).forEach((r) => console.log("  " + r));
await page.screenshot({ path: OUT + "/03-history.png" });

// 窄屏：整页 + 只截评测面板
await page.setViewportSize({ width: 900, height: 1000 });
await page.waitForTimeout(500);
await page.screenshot({ path: OUT + "/04-narrow.png" });
const panelEl = await page.$(".panel--chunks");
if (panelEl) {
  await panelEl.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await panelEl.screenshot({ path: OUT + "/05-narrow-eval.png" });
}

await browser.close();

console.log("\n控制台错误: " + (errors.length ? errors.join(" | ") : "无"));
console.log("失败请求: " + (bad.length ? bad.join(" | ") : "无"));
console.log("截图: " + OUT);

if (errors.length || bad.length) throw new Error("有控制台错误或失败请求");
if (!badge.includes("6")) throw new Error("顶栏徽标没到步骤 6: " + badge);
if (metrics.length !== 4) throw new Error("四项指标没渲染全: " + metrics.length);
if (metrics.every((m) => m.value === "—")) throw new Error("四项指标全是空");
if (!hard.some((h) => h.includes("命中率"))) throw new Error("硬指标没显示命中率");
if (!cases.length) throw new Error("没有逐条样本");
if (!historyRows.length) throw new Error("历史跑分没渲染");
console.log("\n步骤 6 端到端校验通过。");