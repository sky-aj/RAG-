import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire("C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js");
const { chromium } = require("playwright");

const OUT = "C:/Users/渝/.codex/visualizations/2026/10/09/00000000-0000-4000-8000-000000000000/step5";
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

// 空态：还没提问时应该给出引导，而不是假消息
const empty = await page.evaluate(() => {
  const node = document.querySelector(".empty-state");
  return node ? node.textContent.replace(/\s+/g, " ").trim() : "";
});
console.log("\n[空态] " + empty.slice(0, 90));
await page.screenshot({ path: OUT + "/01-empty.png" });

// 开检索参数面板，确认重排默认开启、混合默认开启
await page.click(".retrieval-panel .field-toggle");
await page.waitForSelector(".field-pop");
const toggles = await page.evaluate(() =>
  Array.from(document.querySelectorAll(".field-pop .field")).map((f) => ({
    label: f.querySelector("span").textContent.trim(),
    kind: f.querySelector("input").type,
    value: f.querySelector("input").type === "checkbox" ? f.querySelector("input").checked : f.querySelector("output").textContent.trim()
  }))
);
console.log("\n[检索参数面板]");
toggles.forEach((t) => console.log("  " + t.label + " = " + t.value + " (" + t.kind + ")"));
const rerankToggle = toggles.find((t) => t.label.includes("重排序"));
const topKField = toggles.find((t) => t.label.includes("top-k"));
await page.screenshot({ path: OUT + "/02-retrieval-panel.png" });
await page.click(".retrieval-panel .field-toggle");

// 真提问：SSE 流式，等 done 之后再看结果
const question = "接口默认的限流额度是多少？超出会返回什么？";
await page.fill(".composer-input", question);
await page.click(".composer-input");
await page.keyboard.press("Enter");

// 生成中应该出现「停止」按钮
await page.waitForSelector(".send-btn--stop", { timeout: 20000 });
console.log("\n[生成中] 出现停止按钮，可以打断");
await page.screenshot({ path: OUT + "/03-streaming.png" });

// 等流结束：停止按钮消失
await page.waitForSelector(".send-btn--stop", { state: "detached", timeout: 180000 });

/* 等正文落地再断言。
   glm-4.7-flash 偶尔把预算全烧在推理上、正文空白，后端会自动换备用模型
   重跑一次；那段时间里「停止」按钮已经消失、正文却还没到。写死 1.2 秒
   会把这种正常降级误判成「没有拿到正文」。 */
async function readAnswer() {
  return page.evaluate(() => {
  const msgs = Array.from(document.querySelectorAll(".msg--assistant"));
  const last = msgs[msgs.length - 1];
  if (!last) return null;
  const text = last.querySelector(".msg-text");
  const trace = Array.from(last.querySelectorAll(".msg-meta .trace span")).map((s) => s.textContent.trim());
  const diags = Array.from(last.querySelectorAll(".diagnostics .diag-item")).map((s) => s.textContent.trim());
  const cites = Array.from(last.querySelectorAll(".citation")).map((c) => ({
    index: c.querySelector(".idx").textContent.trim(),
    src: c.querySelector(".src").textContent.replace(/\s+/g, " ").trim(),
    path: (c.querySelector(".path") || {}).textContent || "",
    score: c.querySelector(".score span").textContent.trim()
  }));
  const reasoning = last.querySelector(".reasoning");
  return {
    text: text ? text.textContent.replace(/\s+/g, " ").trim() : "",
    trace,
    diags,
    cites,
    reasoning: reasoning ? reasoning.querySelector(".reasoning-toggle").textContent.replace(/\s+/g, " ").trim() : "",
    error: (last.querySelector(".msg-error") || {}).textContent || ""
  };
  });
}

let answer = await readAnswer();
const deadline = Date.now() + 120000;
while (answer && !answer.text && !answer.error && Date.now() < deadline) {
  await page.waitForTimeout(1000);
  answer = await readAnswer();
}

console.log("\n[回答] " + answer.text.slice(0, 160));
console.log("[trace] " + answer.trace.join(" · "));
console.log("[检索诊断] " + answer.diags.join("  "));
console.log("[思考过程] " + answer.reasoning);
console.log("[引用]");
answer.cites.slice(0, 4).forEach((c) => console.log("  [" + c.index + "] " + c.src + "  分数=" + c.score + (c.path ? "  " + c.path.replace(/\s+/g, " ").trim() : "")));
if (answer.error) console.log("[错误] " + answer.error);
await page.screenshot({ path: OUT + "/04-answer.png", fullPage: false });

// 展开思考过程，看推理文本有没有渲染
if (answer.reasoning) {
  const toggle = await page.$(".msg--assistant:last-of-type .reasoning-toggle");
  if (toggle) {
    await toggle.click();
    await page.waitForSelector(".reasoning-text", { timeout: 10000 });
    const len = await page.evaluate(() => document.querySelector(".reasoning-text").textContent.length);
    console.log("\n[思考过程展开] " + len + " 字");
    await page.screenshot({ path: OUT + "/05-reasoning.png" });
  }
}

// 引用编号必须与【片段 N】一一对应
const cited = [...answer.text.matchAll(/【片段\s*(\d+)】/g)].map((m) => Number(m[1]));
const available = answer.cites.map((c) => Number(c.index));
const missing = cited.filter((n) => !available.includes(n));
console.log("\n[引用一致性] 正文引用 " + JSON.stringify([...new Set(cited)]) + "，卡片编号 " + JSON.stringify(available));

// 窄屏再看一眼
await page.setViewportSize({ width: 900, height: 1000 });
await page.waitForTimeout(500);
await page.screenshot({ path: OUT + "/06-narrow.png" });

await browser.close();

console.log("\n控制台错误: " + (errors.length ? errors.join(" | ") : "无"));
console.log("失败请求: " + (bad.length ? bad.join(" | ") : "无"));
console.log("截图: " + OUT);

if (errors.length || bad.length) throw new Error("有控制台错误或失败请求");
// 徽标显示的是「最高已跑通步骤」，项目继续往后做时会变成 6、7……
// 所以这里断言「至少跑到步骤 5」而不是「等于 5」。
const reached = Number((badge.match(/步骤\s*(\d+)/) || [])[1] || 0);
if (!reached || reached < 5) throw new Error("顶栏徽标还没到步骤 5: " + badge);
if (!empty.includes("知识库")) throw new Error("空态引导没出现");
if (!answer || answer.error) throw new Error("回答出错: " + (answer && answer.error));
if (!answer.text) throw new Error("没有拿到正文");
if (!answer.cites.length) throw new Error("没有引用卡片");
if (answer.cites[0].index !== "1") throw new Error("引用编号不是从 1 开始");
if (!answer.diags.some((d) => d.includes("语义"))) throw new Error("检索诊断没显示");
if (missing.length) throw new Error("正文引用了不存在的片段编号: " + missing.join(","));
// 面板默认值必须跟后端 /api/chat/config 一致（后端 RERANK_ENABLED=true）
if (!rerankToggle || rerankToggle.value !== true) throw new Error("重排默认值没跟后端配置同步: " + JSON.stringify(rerankToggle));
if (!topKField) throw new Error("没找到 top-k 面板项");
if (!answer.trace.some((t) => t.includes("重排 开启"))) throw new Error("trace 里没反映重排开启: " + answer.trace.join(" · "));
if (!answer.diags.some((d) => d.includes("已生效"))) throw new Error("重排没有生效: " + answer.diags.join(" | "));
console.log("\n步骤 5 端到端校验通过。");
