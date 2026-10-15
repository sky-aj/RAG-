import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/07/01a1157a-2e88-7151-b7ee-6ffb00cc06ff/step3";
const SAMPLES = "E:/Desktop/rag指标/backend/data/samples";
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
await page.click('.nav-item:has-text("知识库")');
await page.waitForSelector(".dropzone");

await page.setInputFiles('input[type="file"]', ["产品需求文档.md", "quota.py", "运维笔记.txt", "客服问答记录.csv", "运维 Runbook.docx", "API 参考手册.pdf"].map((n) => SAMPLES + "/" + n));
await page.waitForFunction(() => document.querySelectorAll(".table tbody tr").length >= 6, { timeout: 60000 });
await page.waitForTimeout(1500);

async function readPanel(label) {
  const info = await page.evaluate(() => {
    const meta = Array.from(document.querySelectorAll(".meta-grid > div")).map(
      (d) => d.querySelector("span").textContent + "=" + d.querySelector("code").textContent
    );
    const chunks = Array.from(document.querySelectorAll(".chunk")).slice(0, 3).map((c) => {
      const symbol = c.querySelector(".chunk-symbol");
      const path = c.querySelector(".chunk-path");
      return {
        head: c.querySelector("header").textContent.replace(/\s+/g, " ").trim(),
        symbol: symbol ? symbol.textContent.trim() : "",
        path: path ? path.textContent.trim() : "",
        body: (c.querySelector(".chunk-body") || c.querySelector("p")).textContent.replace(/\s+/g, " ").slice(0, 64)
      };
    });
    const notes = Array.from(document.querySelectorAll(".panel-block .panel-note")).map((n) => n.textContent.trim());
    return { meta, chunks, notes };
  });
  console.log("\n[" + label + "]");
  console.log("  " + info.meta.join("  "));
  info.notes.forEach((n) => console.log("  说明: " + n));
  info.chunks.forEach((c) => {
    console.log("    " + c.head);
    if (c.symbol) console.log("      签名: " + c.symbol);
    if (c.path) console.log("      路径: " + c.path);
    console.log("      内容: " + c.body);
  });
}

const rows = await page.$$(".table tbody tr");
for (let i = 0; i < rows.length; i += 1) {
  await (await page.$$(".table tbody tr"))[i].click();
  await page.waitForTimeout(900);
  const name = await page.evaluate(() => document.querySelector(".panel--chunks .panel-head h2").textContent.trim());
  await readPanel(name + " \u00b7 \u5206\u5757");
}

// \u6309\u6587\u4ef6\u540d\u9009\u4e2d\uff0c\u4e0d\u9760\u7d22\u5f15
async function pickDoc(name) {
  await page.click('.table tbody tr:has-text("' + name + '")');
  await page.waitForTimeout(900);
  const shown = await page.evaluate(() => document.querySelector(".panel--chunks .panel-head h2").textContent.trim());
  console.log("\n[\u622a\u56fe] " + shown);
  return shown;
}

await pickDoc("quota.py");
await page.screenshot({ path: OUT + "/01-chunks-code.png" });

await pickDoc("\u8fd0\u7ef4 Runbook.docx");
await page.screenshot({ path: OUT + "/02-chunks-docx.png" });

await pickDoc("API \u53c2\u8003\u624b\u518c.pdf");
await page.screenshot({ path: OUT + "/05-chunks-pdf.png" });

// 切换策略对比：同文件强制走 paragraph
await page.selectOption(".panel--chunks .chunk-controls select", "paragraph");
await page.waitForTimeout(900);
const forced = await page.evaluate(() => {
  const meta = Array.from(document.querySelectorAll(".meta-grid > div")).map((d) => d.textContent.replace(/\s+/g, " ").trim());
  return meta;
});
console.log("\n[强制 paragraph 策略后]");
console.log("  " + forced.join("  "));
await page.screenshot({ path: OUT + "/03-forced-paragraph.png" });

// 解析结构页签
await page.selectOption(".panel--chunks .chunk-controls select", "");
await page.waitForTimeout(700);
await page.click('.panel--chunks .panel-tab:has-text("解析结构")');
await page.waitForTimeout(600);
const outline = await page.evaluate(() => ({
  meta: Array.from(document.querySelectorAll(".meta-grid > div")).map((d) => d.textContent.replace(/\s+/g, " ").trim()),
  kinds: Array.from(document.querySelectorAll(".kind-chips .chip")).map((c) => c.textContent.trim()),
  entries: Array.from(document.querySelectorAll(".outline-entries .chip")).map((c) => c.textContent.trim())
}));
console.log("\n[解析结构页签]");
console.log("  " + outline.meta.join("  "));
console.log("  类型统计: " + outline.kinds.join(", "));
if (outline.entries.length) console.log("  文档自带目录: " + outline.entries.join(" / "));
await page.screenshot({ path: OUT + "/04-structure.png" });

// 顶栏徽标：应该显示“步骤 3 · 分块策略 进行中”，不能写死也不能跳到步骤 5
const badge = await page.evaluate(() => document.querySelector(".topbar-actions .ghost-btn").textContent.trim());
console.log("\n[顶栏徽标] " + badge);
if (!badge.includes("3")) throw new Error("顶栏徽标不对: " + badge);

await browser.close();
console.log("\n控制台错误: " + (errors.length ? errors.join(" | ") : "无"));
console.log("失败请求: " + (bad.length ? bad.join(" | ") : "无"));
console.log("截图: " + OUT);
