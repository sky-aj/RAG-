import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/07/01a1157a-2e88-7151-b7ee-6ffb00cc06ff/step1-fix";
const APP = "http://127.0.0.1:5173/";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));

await page.goto(APP, { waitUntil: "networkidle" });
await page.waitForSelector(".composer-input", { timeout: 15000 });

// ---------- 1) 输入框边框与贴合 ----------
const box = await page.evaluate(() => {
  const ta = document.querySelector(".composer-input");
  const shell = document.querySelector(".composer-shell");
  const cs = getComputedStyle(ta);
  const t = ta.getBoundingClientRect();
  const s = shell.getBoundingClientRect();
  return {
    borderWidth: cs.borderTopWidth + " " + cs.borderRightWidth + " " + cs.borderBottomWidth + " " + cs.borderLeftWidth,
    borderStyle: cs.borderTopStyle,
    outline: cs.outlineStyle + " " + cs.outlineWidth,
    boxShadow: cs.boxShadow,
    padding: cs.padding,
    taLeft: Math.round(t.left), taRight: Math.round(t.right), taWidth: Math.round(t.width),
    shellLeft: Math.round(s.left), shellRight: Math.round(s.right), shellWidth: Math.round(s.width)
  };
});
console.log("输入框边框:", JSON.stringify(box));

// 表格里所有输入框都不该有边框
const bordered = await page.evaluate(() =>
  Array.from(document.querySelectorAll("textarea, input[type=text]")).map((n) => {
    const cs = getComputedStyle(n);
    return { cls: n.className || n.type, border: cs.borderTopWidth + " " + cs.borderTopStyle };
  })
);
console.log("页面上所有输入框:", JSON.stringify(bordered));

// ---------- 2) 输入框高度自适应 ----------
const heights = [];
await page.click(".composer-input");
for (const lines of [1, 3, 8]) {
  await page.fill(".composer-input", Array.from({ length: lines }, (_, i) => "第 " + (i + 1) + " 行文字").join("\n"));
  await page.waitForTimeout(180);
  heights.push(await page.evaluate(() => Math.round(document.querySelector(".composer-input").getBoundingClientRect().height)));
}
console.log("输入框高度 1/3/8 行:", heights.join(" -> "));
await page.fill(".composer-input", "");
await page.waitForTimeout(150);

// ---------- 3) 字号档位 ----------
const sizes = {};
for (const fs_ of ["sm", "md", "lg", "xl"]) {
  await page.evaluate((id) => { document.documentElement.dataset.fs = id; }, fs_);
  await page.waitForTimeout(250);
  sizes[fs_] = await page.evaluate(() => ({
    root: getComputedStyle(document.documentElement).fontSize,
    title: getComputedStyle(document.querySelector(".page-title")).fontSize,
    body: getComputedStyle(document.querySelector(".msg-text")).fontSize,
    input: getComputedStyle(document.querySelector(".composer-input")).fontSize
  }));
}
console.log("字号档位:", JSON.stringify(sizes, null, 0));

// ---------- 4) 抽屉里的字号选择 + 持久化 ----------
await page.evaluate(() => { document.documentElement.dataset.fs = "md"; });
await page.click(".rail-foot .ghost-btn");
await page.waitForSelector(".drawer .scale-list");
const cards = await page.$$eval(".scale-card", (n) => n.map((c) => c.textContent.replace(/\s+/g, " ").trim()));
console.log("字号选项:", cards.join(" | "));
await page.click('.scale-card:nth-child(3)');   // 舒适
await page.waitForTimeout(350);
const rootAfter = await page.evaluate(() => getComputedStyle(document.documentElement).fontSize);
console.log("点「舒适」后根字号:", rootAfter);
await page.screenshot({ path: OUT + "/03-drawer-fontsize.png" });

// 持久化：刷新后应保持
const stored = await page.evaluate(() => localStorage.getItem("corpus.style"));
console.log("localStorage:", stored);
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(600);
const afterReload = await page.evaluate(() => ({
  root: getComputedStyle(document.documentElement).fontSize,
  fs: document.documentElement.dataset.fs,
  theme: document.documentElement.dataset.theme,
  accent: document.documentElement.dataset.accent
}));
console.log("刷新后:", JSON.stringify(afterReload));
await page.screenshot({ path: OUT + "/01-chat-xl.png" });

// 重置为默认并出图
await page.click(".rail-foot .ghost-btn");
await page.waitForSelector(".drawer .scale-list");
await page.click('.scale-card:nth-child(2)');
await page.waitForTimeout(250);
await page.click(".drawer-foot .solid-btn");
await page.waitForTimeout(400);
await page.screenshot({ path: OUT + "/02-chat-md.png" });

// 大屏（1920）看一眼桌面适配
await page.setViewportSize({ width: 1920, height: 1080 });
await page.waitForTimeout(500);
await page.screenshot({ path: OUT + "/04-wide-1920.png" });

await browser.close();
console.log("控制台错误:", errors.length ? errors.join(" | ") : "无");
console.log("截图:", OUT);
