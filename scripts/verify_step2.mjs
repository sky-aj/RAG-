import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/07/01a1157a-2e88-7151-b7ee-6ffb00cc06ff/step2";
const APP = "http://127.0.0.1:5173/";
const SAMPLES = "E:/Desktop/rag指标/backend/data/samples";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
const failed = [];
page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) failed.push(r.status() + " " + r.url()); });

await page.goto(APP, { waitUntil: "networkidle" });
await page.waitForSelector(".status-pill.is-online", { timeout: 20000 });
await page.click('.nav-item:has-text("知识库")');
await page.waitForSelector(".dropzone", { timeout: 10000 });

// 一次上传全部六种类型
const files = ["产品需求文档.md", "quota.py", "运维笔记.txt", "客服问答记录.csv", "运维 Runbook.docx", "API 参考手册.pdf"]
  .map((n) => SAMPLES + "/" + n);
await page.setInputFiles('input[type="file"]', files);
await page.waitForFunction(
  () => document.querySelectorAll(".table tbody tr").length >= 6,
  { timeout: 60000 }
);
await page.waitForTimeout(1200);

const rows = await page.$$eval(".table tbody tr", (nodes) =>
  nodes.map((n) => Array.from(n.querySelectorAll("td")).slice(0, 6).map((td) => td.textContent.trim()).join(" | "))
);
console.log("文档表：");
rows.forEach((r) => console.log("  " + r));
await page.screenshot({ path: OUT + "/01-kb-list.png" });

// 逐个点开，核对解析结构
const checks = [];
for (let i = 0; i < rows.length; i += 1) {
  const cells = await page.$$(".table tbody tr");
  if (!cells[i]) continue;
  await cells[i].click();
  await page.waitForTimeout(700);
  const info = await page.evaluate(() => {
    const meta = Array.from(document.querySelectorAll(".meta-grid > div")).map(
      (d) => d.querySelector("span").textContent + "=" + d.querySelector("code").textContent
    );
    const chunks = Array.from(document.querySelectorAll(".chunk")).slice(0, 4).map((c) => ({
      head: c.querySelector("header").textContent.replace(/\s+/g, " ").trim(),
      body: (c.querySelector("p").textContent || "").slice(0, 70)
    }));
    return { meta, chunks };
  });
  const name = rows[i].split(" | ")[0];
  checks.push(name);
  console.log("\n[" + name + "]");
  console.log("  " + info.meta.join("  "));
  info.chunks.forEach((c) => console.log("    " + c.head + "  ::  " + c.body));
  if (i === 4) await page.screenshot({ path: OUT + "/02-detail-docx.png" });
  if (i === 5) await page.screenshot({ path: OUT + "/03-detail-pdf.png" });
}

await page.screenshot({ path: OUT + "/04-final.png" });
await browser.close();
console.log("\n控制台错误: " + (errors.length ? errors.join(" | ") : "无"));
console.log("失败请求: " + (failed.length ? failed.join(" | ") : "无"));
console.log("截图: " + OUT);
