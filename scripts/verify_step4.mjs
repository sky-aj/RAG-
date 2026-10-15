import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/07/01a1157a-2e88-7151-b7ee-6ffb00cc06ff/step4";
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

// 顶栏徽标应该已经走到步骤 4
const badge = await page.evaluate(() => document.querySelector(".topbar-actions .ghost-btn").textContent.trim());
console.log("[顶栏徽标] " + badge);

await page.click('.nav-item:has-text("知识库")');
await page.waitForSelector(".dropzone");

// 统计卡不能显示 0：后端入库后会把真实块数回写注册表
const stats = await page.evaluate(() =>
  Array.from(document.querySelectorAll(".stats .stat")).map(
    (s) => s.querySelector("dt").textContent.trim() + "=" + s.querySelector("dd").textContent.replace(/\s+/g, "").trim()
  )
);
console.log("\n[顶部统计] " + stats.join("  "));

// 切到向量库页签
await page.click('.panel--chunks .panel-tab:has-text("向量库")');
await page.waitForSelector(".panel--chunks .meta-grid", { timeout: 30000 });
await page.waitForTimeout(1200);

const store = await page.evaluate(() => {
  const meta = Array.from(document.querySelectorAll(".panel--chunks .meta-grid > div")).map(
    (d) => d.querySelector("span").textContent.trim() + "=" + d.querySelector("code").textContent.trim()
  );
  const desc = document.querySelector(".panel--chunks .strategy-desc");
  const controls = Array.from(document.querySelectorAll(".panel--chunks .chunk-controls button")).map((b) => b.textContent.trim());
  const pending = document.querySelector(".panel--chunks .chunk-controls .field-inline code");
  return { meta, desc: desc ? desc.textContent.replace(/\s+/g, " ").trim() : "", controls, pending: pending ? pending.textContent.trim() : "" };
});
console.log("\n[向量库状态]");
console.log("  " + store.meta.join("  "));
console.log("  待入库: " + store.pending);
console.log("  按钮: " + store.controls.join(" / "));
console.log("  说明: " + store.desc);
await page.screenshot({ path: OUT + "/01-index-status.png" });

// 选一份文档，只在它里面搜
await page.click('.table tbody tr:has-text("quota.py")');
await page.waitForTimeout(600);
await page.click('.panel--chunks .panel-tab:has-text("向量库")');
await page.waitForSelector(".panel--chunks .search-row");

/* 全库默认是勾上的，要测“仅当前文档”就得取消 */
async function setScope(all) {
  const checked = await page.isChecked(".panel--chunks .search-row input[type=checkbox]");
  if (checked !== all) {
    await page.click(".panel--chunks .search-row input[type=checkbox]");
    await page.waitForTimeout(200);
  }
}

async function searchFor(text) {
  // 记下旧结果的第一条：不能只等 .chunk 出现，
  // 上一次的结果还在 DOM 里，会把旧结果当成新的读走。
  const before = await page.evaluate(() => {
    const first = document.querySelector(".panel--chunks .chunk header");
    return first ? first.textContent.replace(/\s+/g, " ").trim() : "";
  });
  await page.fill(".panel--chunks .search-row input[type=text]", text);
  await page.click(".panel--chunks .search-row .solid-btn");
  await page.waitForFunction(
    (previous) => {
      const first = document.querySelector(".panel--chunks .chunk header");
      return Boolean(first) && first.textContent.replace(/\s+/g, " ").trim() !== previous;
    },
    before,
    { timeout: 90000 }
  );
  await page.waitForTimeout(300);
  return page.evaluate(() => {
    const note = document.querySelector(".panel--chunks .panel-note");
    const chips = Array.from(document.querySelectorAll(".panel--chunks .chunk")).map((c) => ({
      head: c.querySelector("header").textContent.replace(/\s+/g, " ").trim(),
      symbol: (c.querySelector(".chunk-symbol") || {}).textContent || "",
      body: (c.querySelector(".chunk-body") || c.querySelector("p")).textContent.replace(/\s+/g, " ").slice(0, 46)
    }));
    return { note: note ? note.textContent.replace(/\s+/g, " ").trim() : "", chips };
  });
}

await setScope(false);
const scoped = await searchFor("滑动窗口怎么统计请求量");
console.log("\n[仅当前文档检索 · 取消全库]");
console.log("  " + scoped.note);
scoped.chips.slice(0, 3).forEach((c) => {
  console.log("    " + c.head);
  if (c.symbol.trim()) console.log("      签名: " + c.symbol.trim());
  console.log("      内容: " + c.body);
});
await page.screenshot({ path: OUT + "/02-search-scoped.png" });

// 勾上全库再搜：这个问题只能在用户自己的其他.txt 里找到
await setScope(true);
const global = await searchFor("机械臂焊接的手动模式");
console.log("\n[全库检索]");
console.log("  " + global.note);
global.chips.slice(0, 3).forEach((c) => console.log("    " + c.head + "  ::  " + c.body));
await page.screenshot({ path: OUT + "/03-search-global.png" });

// 窄屏看一下
await page.setViewportSize({ width: 900, height: 1000 });
await page.waitForTimeout(500);
await page.screenshot({ path: OUT + "/04-narrow.png" });

await browser.close();

console.log("\n控制台错误: " + (errors.length ? errors.join(" | ") : "无"));
console.log("失败请求: " + (bad.length ? bad.join(" | ") : "无"));
console.log("截图: " + OUT);

if (errors.length || bad.length) throw new Error("有控制台错误或失败请求");
if (!badge.includes("4")) throw new Error("顶栏徽标没到步骤 4: " + badge);
if (!store.meta.some((m) => m.includes("1024"))) throw new Error("向量维度没读出来");
const chunkStat = stats.find((s) => s.startsWith("已入库块数"));
if (!chunkStat || chunkStat.endsWith("=0块")) throw new Error("分块统计卡还是 0: " + stats.join(" | "));

// 范围切换必须真的生效
const scopedDocs = new Set(scoped.chips.map((c) => c.head.split(" · ")[0]));
if (scopedDocs.size !== 1 || !scopedDocs.has("quota.py")) {
  throw new Error("“仅当前文档”检索没有限定文档: " + [...scopedDocs].join(", "));
}
const globalDocs = new Set(global.chips.map((c) => c.head.split(" · ")[0]));
if (global.chips.length && !globalDocs.has("其他.txt")) {
  throw new Error("全库检索没命中其他.txt: " + [...globalDocs].join(", "));
}
console.log("\n步骤 4 端到端校验通过。");
