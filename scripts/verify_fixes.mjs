/* 校验本轮修复：会话隔离、知识库分组、检索诊断说明、评测过期提示。
   需要后端(5000)与前端(5173)都在跑。 */
import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/11/00000000-0000-4000-8000-000000000000/fixes";
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

// ---------- 1. 知识库分组不再是写死的「全部文档 · 0 份」 ----------
const kbs = await page.$$eval(".kb-list .row-btn", (nodes) =>
  nodes.map((n) => ({
    name: n.querySelector(".label").textContent.trim(),
    docs: n.querySelector(".count").textContent.trim()
  }))
);
console.log("[知识库分组]", JSON.stringify(kbs));
const counted = kbs.filter((k) => Number(k.docs) > 0);
console.log(counted.length > 1 ? "  PASS：已按真实文档分组，且条数非 0" : "  FAIL：分组仍是占位数据");
await page.screenshot({ path: OUT + "/01-sidebar.png" });

// ---------- 2. 会话隔离 ----------
async function ask(text) {
  await page.fill(".composer-input", text);
  await page.keyboard.press("Enter");
  await page.waitForSelector(".send-btn--stop", { timeout: 20000 });
  await page.waitForSelector(".send-btn--stop", { state: "detached", timeout: 180000 });
}
async function msgCount() {
  return page.$$eval(".messages .msg", (n) => n.length);
}

await ask("接口默认的限流额度是多少");
const firstCount = await msgCount();
const firstName = await page.$eval(".thread-list .row-btn.is-active .label", (n) => n.textContent.trim());
console.log(`\n[会话 A] 消息 ${firstCount} 条，标题「${firstName}」`);

await page.click(".rail-block .micro-btn");
await page.waitForTimeout(300);
const newCount = await msgCount();
console.log(`[新会话 B] 消息 ${newCount} 条` + (newCount === 0 ? "  PASS：新会话是干净的" : "  FAIL：串到旧会话了"));

await ask("滑动窗口怎么统计请求量");
const bCount = await msgCount();
console.log(`[会话 B 提问后] 消息 ${bCount} 条`);

// 切回 A，A 的历史必须还在
const rows = await page.$$(".thread-list .row-btn");
await rows[1].click();
await page.waitForTimeout(300);
const backCount = await msgCount();
console.log(`[切回会话 A] 消息 ${backCount} 条` + (backCount === firstCount ? "  PASS：历史完整保留" : `  FAIL：期望 ${firstCount} 条`));
await page.screenshot({ path: OUT + "/02-thread-a.png" });

// 刷新页面后会话与消息仍应在
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector(".status-pill.is-online", { timeout: 20000 });
const afterReload = await msgCount();
const threadsAfter = await page.$$eval(".thread-list .row-btn .label", (n) => n.map((x) => x.textContent.trim()));
console.log(`[刷新后] 会话数 ${threadsAfter.length}，当前消息 ${afterReload} 条` + (afterReload === firstCount ? "  PASS：持久化成功" : "  WARN：消息未恢复"));
console.log("[刷新后会话列表] " + JSON.stringify(threadsAfter));

// ---------- 3. 检索诊断说明可见 ----------
const hasNotes = await page.$$eval(".diag-note", (n) => n.map((x) => x.textContent.trim()));
const hasDiag = await page.$$eval(".diagnostics .diag-item", (n) => n.map((x) => x.textContent.trim()));
console.log("\n[诊断条] " + JSON.stringify(hasDiag));
console.log("[诊断说明] " + JSON.stringify(hasNotes));
console.log(hasNotes.length ? "  PASS：降级/回填原因在界面上可见（以前只在后端日志里）" : "  INFO：本次两路都正常，没有需要解释的降级");

// ---------- 4. 评测面板的过期提示 ----------
await page.click(".nav-item:nth-child(2)");
await page.waitForSelector(".panel-tabs");
await page.click(".panel-tabs button:last-child");
await page.waitForSelector(".eval-cases, .empty", { timeout: 20000 });
const evalText = await page.$eval(".panel--chunks", (n) => n.textContent.replace(/\s+/g, " ").trim());
console.log("\n[评测面板] " + evalText.slice(0, 220));
await page.screenshot({ path: OUT + "/03-eval.png" });

console.log("\n控制台错误：" + (errors.length ? JSON.stringify(errors) : "无"));
console.log("失败请求：" + (bad.length ? JSON.stringify(bad) : "无"));
await browser.close();
