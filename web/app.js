/* CORPUS · RAG 控制台 — 前端演示逻辑
   纯静态：数据为本地示例，接口位置已用 TODO 标出，接后端时替换即可。 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const icon = (name, cls) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", cls || "ico");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  };

  const THEMES = [
    { id: "minimalist", name: "极简编辑", skill: "minimalist-skill", desc: "暖色单色系，1px 浅边框，极小阴影，Notion / Linear 式克制。" },
    { id: "soft", name: "柔和高级", skill: "soft-skill", desc: "大留白，双层容器，漫射阴影，衬线标题的高端品牌感。" },
    { id: "brutalist", name: "工业粗野", skill: "brutalist-skill", desc: "直角零圆角，等宽大写标签，强边框分隔的终端工艺感。" },
    { id: "stitch", name: "Taste 标准", skill: "stitch-skill", desc: "冷调灰白，宽柔漫射阴影，2.5rem 圆角与重排式排版。" }
  ];

  const ACCENTS = [
    { id: "ink", name: "墨黑", hex: "#1F1F1E" },
    { id: "indigo", name: "靛蓝", hex: "#4F46E5" },
    { id: "emerald", name: "翡翠", hex: "#0E9F6E" },
    { id: "rose", name: "深玫", hex: "#E11D48" },
    { id: "amber", name: "琥珀", hex: "#C2760A" },
    { id: "hazard", name: "警示红", hex: "#E61919" }
  ];

  const THREADS = [
    { id: "t1", title: "产品文档问答", updated: "刚刚", count: 38 },
    { id: "t2", title: "API 限流策略", updated: "2 小时前", count: 12 },
    { id: "t3", title: "部署与回滚流程", updated: "昨天", count: 21 },
    { id: "t4", title: "向量检索调参", updated: "3 天前", count: 9 },
    { id: "t5", title: "计费口径核对", updated: "上周", count: 27 }
  ];

  const DOCS = [
    { name: "产品需求文档 v4.2.pdf", type: "PDF", chunks: 128, status: "已索引", size: "3.4 MB", updated: "2 小时前" },
    { name: "API 参考手册.md", type: "MD", chunks: 64, status: "已索引", size: "412 KB", updated: "2 小时前" },
    { name: "客服问答记录.csv", type: "CSV", chunks: 312, status: "解析中", size: "8.1 MB", updated: "8 分钟前" },
    { name: "运维 Runbook.docx", type: "DOCX", chunks: 87, status: "已索引", size: "1.2 MB", updated: "昨天" },
    { name: "2026 路线图.txt", type: "TXT", chunks: 0, status: "失败", size: "61 KB", updated: "3 天前" }
  ];

  const CHUNKS = {
    "产品需求文档 v4.2.pdf": [
      { id: "c-041", tokens: 384, text: "对外接口按租户维度限流，默认额度为每分钟 600 次请求、并发 20。额度以滑动窗口统计，超出后返回 429 并附 Retry-After 响应头，提示最早可重试的秒数。" },
      { id: "c-042", tokens: 296, text: "突发流量允许短暂超出配额，上限为默认值的 1.5 倍，持续不超过 10 秒。超出突发额度后立即拒绝，不做排队。" },
      { id: "c-057", tokens: 341, text: "企业版租户可在控制台自助申请提额，审批通过后额度在下一次心跳（最长 60 秒）内生效，无需重启服务。" }
    ],
    "API 参考手册.md": [
      { id: "a-012", tokens: 212, text: "GET /v1/search 支持 top_k 与 score_threshold 两个参数；score_threshold 低于 0.35 的片段默认被丢弃，可通过 query 参数覆盖。" },
      { id: "a-018", tokens: 188, text: "所有写操作接口要求 Idempotency-Key 请求头，重复的 key 在 24 小时内返回同一结果，避免网络重试造成重复扣费。" }
    ],
    "客服问答记录.csv": [
      { id: "s-204", tokens: 96, text: "客户反馈 429 报错频繁，排查发现是同一 API Key 被三个环境共用，建议按环境拆分 Key。" },
      { id: "s-311", tokens: 88, text: "关于额度恢复时间，客服话术应说明：滑动窗口意味着额度随请求时间推移连续释放，而非整点重置。" }
    ]
  };

  const KB_DOCS = ["产品需求文档 v4.2.pdf", "API 参考手册.md", "客服问答记录.csv", "运维 Runbook.docx", "2026 路线图.txt"];
  const KB_LIST = [
    { id: "k1", name: "产品文档", docs: 3 },
    { id: "k2", name: "客服知识库", docs: 1 },
    { id: "k3", name: "运维手册", docs: 2 }
  ];

  const ANSWERS = {
    limit: {
      text: [
        "对外接口按租户维度限流，默认额度是每分钟 600 次请求、并发 20；额度按滑动窗口统计，不是整点重置。",
        "超出限额会返回 429，并在 Retry-After 头里给出最早可重试的秒数。突发流量允许短时超出到默认额度的 1.5 倍，持续不超过 10 秒，之后立即拒绝而不排队。"
      ],
      points: ["企业版可在控制台自助申请提额，审批后最长 60 秒内生效。", "同一 API Key 被多个环境共用是 429 频繁出现的常见原因。"],
      citations: [
        { src: "产品需求文档 v4.2.pdf", page: "p.18 · c-041", score: 0.87, quote: "对外接口按租户维度限流，默认额度为每分钟 600 次请求、并发 20。额度以滑动窗口统计，超出后返回 429 并附 Retry-After 响应头。" },
        { src: "产品需求文档 v4.2.pdf", page: "p.19 · c-042", score: 0.74, quote: "突发流量允许短暂超出配额，上限为默认值的 1.5 倍，持续不超过 10 秒。" },
        { src: "客服问答记录.csv", page: "row 311", score: 0.66, quote: "滑动窗口意味着额度随请求时间推移连续释放，而非整点重置。" }
      ],
      trace: ["hybrid · BM25 + 向量", "top_k=5", "阈值 0.35", "重排 关闭", "312 ms"]
    },
    default: {
      text: [
        "我检索了当前知识库里的相关内容。下面几条是最接近的回答依据，可以在引用卡片里查看原始片段与相似度分数。",
        "如果这里没有覆盖你的问题，可以换一种问法，或把阈值调低到 0.2 看召回范围更大的结果。"
      ],
      points: ["必要时开启重排序，轻微提升前几条的准确度。", "补充文档后重新解析，命中率会明显改善。"],
      citations: [
        { src: "API 参考手册.md", page: "a-012", score: 0.71, quote: "GET /v1/search 支持 top_k 与 score_threshold 两个参数；score_threshold 低于 0.35 的片段默认被丢弃。" },
        { src: "运维 Runbook.docx", page: "p.7", score: 0.58, quote: "索引重建期间建议先将流量切到只读副本，重建完成后再切换回主节点。" }
      ],
      trace: ["hybrid · BM25 + 向量", "top_k=5", "阈值 0.35", "重排 关闭", "286 ms"]
    }
  };

  const SEED_MESSAGES = [
    { role: "user", text: "我们对外 API 的限流策略是什么？超出限额会返回什么？", time: "14:02" },
    {
      role: "assistant",
      answer: ANSWERS.limit,
      time: "14:02"
    },
    { role: "user", text: "额度恢复是滑动窗口还是固定窗口？", time: "14:03" },
    {
      role: "assistant",
      answer: {
        text: ["是滑动窗口。额度随着请求的时间推移连续释放，不会在整点整批重置；这也就意味着同一秒内密集重试会持续命中限流。"],
        points: ["Retry-After 给出的秒数就是当前窗口下最早可重试的时间。"],
        citations: [
          { src: "产品需求文档 v4.2.pdf", page: "p.18 · c-041", score: 0.83, quote: "额度以滑动窗口统计，超出后返回 429 并附 Retry-After 响应头，提示最早可重试的秒数。" },
          { src: "客服问答记录.csv", page: "row 311", score: 0.69, quote: "滑动窗口意味着额度随请求时间推移连续释放，而非整点重置。" }
        ],
        trace: ["hybrid · BM25 + 向量", "top_k=5", "阈值 0.35", "重排 关闭", "241 ms"]
      }
    }
  ];

  const state = {
    theme: "minimalist",
    accent: "ink",
    view: "chat",
    thread: "t1",
    attachments: [],
    fileTarget: "chat"
  };

  try {
    const saved = JSON.parse(localStorage.getItem("corpus.style") || "null");
    if (saved && saved.theme) state.theme = saved.theme;
    if (saved && saved.accent) state.accent = saved.accent;
  } catch (error) {
    /* file:// 下 localStorage 可能不可用，忽略即可 */
  }

  const persist = () => {
    try { localStorage.setItem("corpus.style", JSON.stringify({ theme: state.theme, accent: state.accent })); } catch (error) { /* 忽略 */ }
  };

  /* ---------- 主题 ---------- */
  function applyStyle() {
    document.documentElement.dataset.theme = state.theme;
    document.documentElement.dataset.accent = state.accent;
    document.querySelectorAll("#themeGrid .theme-card").forEach((card) => {
      card.classList.toggle("is-active", card.dataset.theme === state.theme);
      card.setAttribute("aria-pressed", String(card.dataset.theme === state.theme));
    });
    document.querySelectorAll("#swatches .swatch").forEach((sw) => {
      sw.classList.toggle("is-active", sw.dataset.accent === state.accent);
      sw.setAttribute("aria-pressed", String(sw.dataset.accent === state.accent));
    });
    renderTokens();
    persist();
  }

  function renderTokenList() {
    const cs = getComputedStyle(document.documentElement);
    const tokenMap = [
      ["背景 canvas", "--bg"],
      ["卡片 surface", "--surface"],
      ["边框 border", "--border"],
      ["正文 text", "--text"],
      ["强调 accent", "--accent"],
      ["圆角 radius-lg", "--radius-lg"],
      ["卡片阴影", "--shadow-card"]
    ];
    return tokenMap.map(([label, token]) => [label, cs.getPropertyValue(token).trim()]);
  }

  function renderTokens() {
    const list = $("#tokenList");
    if (!list) return;
    list.replaceChildren();
    renderTokenList().forEach(([label, value]) => {
      const row = el("div");
      row.append(el("dt", null, label));
      const dd = el("dd");
      const isColor = value.startsWith("#") || value.startsWith("rgb");
      if (isColor) {
        const dot = el("span", "swatch-dot");
        dot.style.background = value;
        dd.append(dot);
      }
      dd.append(el("span", null, value.length > 26 ? value.slice(0, 24) + "…" : value));
      row.append(dd);
      list.append(row);
    });
  }

  function buildStyleDrawer() {
    const grid = $("#themeGrid");
    THEMES.forEach((theme) => {
      const card = el("button", "theme-card");
      card.type = "button";
      card.dataset.theme = theme.id;
      card.dataset.preview = theme.id;
      const preview = el("span", "theme-preview");
      preview.append(el("i"), el("i"), el("i"));
      card.append(preview, el("strong", null, theme.name), el("small", null, theme.desc));
      card.addEventListener("click", () => { state.theme = theme.id; applyStyle(); });
      grid.append(card);
    });

    const wrap = $("#swatches");
    ACCENTS.forEach((accent) => {
      const sw = el("button", "swatch");
      sw.type = "button";
      sw.dataset.accent = accent.id;
      const dot = el("i");
      dot.style.background = accent.hex;
      sw.append(dot, el("span", null, accent.name));
      sw.addEventListener("click", () => { state.accent = accent.id; applyStyle(); });
      wrap.append(sw);
    });
  }

  /* ---------- 侧栏 ---------- */
  function renderRail() {
    const threadList = $("#threadList");
    threadList.replaceChildren();
    THREADS.forEach((thread) => {
      const li = el("li");
      const btn = el("button", "row-btn" + (thread.id === state.thread ? " is-active" : ""));
      btn.type = "button";
      if (thread.id === state.thread) btn.append(el("span", "dot"));
      btn.append(el("span", "label", thread.title), el("span", "count", String(thread.count)));
      btn.addEventListener("click", () => {
        state.thread = thread.id;
        $("#chatTitle").textContent = thread.title;
        renderRail();
        switchView("chat");
      });
      li.append(btn);
      threadList.append(li);
    });

    const kbList = $("#kbList");
    kbList.replaceChildren();
    KB_LIST.forEach((kb, index) => {
      const li = el("li");
      const btn = el("button", "row-btn" + (index === 0 ? " is-active" : ""));
      btn.type = "button";
      btn.append(icon("doc"), el("span", "label", kb.name), el("span", "count", String(kb.docs)));
      li.append(btn);
      kbList.append(li);
    });
  }

  /* ---------- 视图 ---------- */
  function switchView(view) {
    state.view = view;
    document.querySelectorAll(".nav-item").forEach((btn) => {
      const on = btn.dataset.view === view;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", String(on));
    });
    $("#view-chat").hidden = view !== "chat";
    $("#view-chat").classList.toggle("is-active", view === "chat");
    $("#view-kb").hidden = view !== "kb";
    $("#view-kb").classList.toggle("is-active", view === "kb");
    $("#crumbView").textContent = view === "chat" ? "对话" : "知识库";
    $("#composer").hidden = view !== "chat";
  }

  /* ---------- 对话 ---------- */
  function citationNode(cite, index) {
    const node = el("article", "citation");
    node.append(el("span", "idx", String(index + 1)));
    const body = el("div");
    body.append(el("div", "src", cite.src + " · " + cite.page));
    body.append(el("p", "quote", cite.quote));
    node.append(body);
    const score = el("div", "score");
    const meter = el("span", "meter");
    const fill = el("i");
    fill.style.width = Math.round(cite.score * 100) + "%";
    meter.append(fill);
    score.append(el("span", null, cite.score.toFixed(2)), meter);
    node.append(score);
    return node;
  }

  function answerBody(answer) {
    const wrap = el("div", "msg-text");
    answer.text.forEach((para) => wrap.append(el("p", null, para)));
    if (answer.points && answer.points.length) {
      const list = el("ul");
      answer.points.forEach((point) => list.append(el("li", null, point)));
      wrap.append(list);
    }
    return wrap;
  }

  function messageNode(message) {
    const node = el("li", "msg msg--" + message.role);
    const avatar = el("div", "avatar");
    avatar.textContent = message.role === "user" ? "你" : "RAG";
    const body = el("div", "msg-body");

    const meta = el("div", "msg-meta");
    meta.append(el("span", null, message.role === "user" ? "提问" : "回答"));
    if (message.time) meta.append(el("span", null, message.time));
    if (message.answer && message.answer.trace) {
      const trace = el("span", "trace");
      message.answer.trace.forEach((item) => trace.append(el("span", null, item)));
      meta.append(trace);
    }
    body.append(meta);

    if (message.role === "user") {
      body.append(el("p", "msg-text", message.text));
    } else if (message.thinking) {
      const typing = el("span", "typing");
      typing.append(el("i"), el("i"), el("i"));
      body.append(typing);
    } else {
      body.append(answerBody(message.answer));
      const cites = el("div", "citations");
      message.answer.citations.forEach((cite, index) => cites.append(citationNode(cite, index)));
      body.append(cites);
    }

    node.append(avatar, body);
    return node;
  }

  function renderMessages() {
    const list = $("#messages");
    list.replaceChildren();
    SEED_MESSAGES.forEach((message) => list.append(messageNode(message)));
    scrollToEnd();
  }

  function scrollToEnd() {
    const scroll = $("#scroll");
    requestAnimationFrame(() => {
      const anchor = $("#messagesEnd") || $("#messages").lastElementChild;
      if (anchor) anchor.scrollIntoView({ block: "end", behavior: "instant" });
      else scroll.scrollTop = scroll.scrollHeight;
    });
  }

  function pickAnswer(question) {
    const q = question.toLowerCase();
    if (q.includes("限流") || q.includes("429") || q.includes("额度") || q.includes("限额")) return ANSWERS.limit;
    return ANSWERS.default;
  }

  function send(text) {
    const list = $("#messages");
    const now = new Date();
    const time = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    list.append(messageNode({ role: "user", text, time }));

    const placeholder = messageNode({ role: "assistant", thinking: true, time });
    list.append(placeholder);
    scrollToEnd();

    // TODO: 接后端时改为 POST /api/chat（含 knowledge_base_id、top_k、threshold）。
    window.setTimeout(() => {
      const answer = pickAnswer(text);
      placeholder.replaceWith(messageNode({ role: "assistant", answer, time }));
      scrollToEnd();
    }, 900);
  }

  /* ---------- 附件 ---------- */
  function renderAttachments() {
    const row = $("#attachRow");
    row.replaceChildren();
    state.attachments.forEach((file, index) => {
      const chip = el("span", "attach");
      chip.append(icon("doc"));
      chip.append(el("span", null, file.name + " · " + formatSize(file.size)));
      const remove = el("button");
      remove.type = "button";
      remove.title = "移除";
      remove.append(icon("close"));
      remove.addEventListener("click", () => {
        state.attachments.splice(index, 1);
        renderAttachments();
      });
      chip.append(remove);
      row.append(chip);
    });
    const indexed = DOCS.filter((doc) => doc.status === "已索引").length;
    $("#composerHint").textContent = state.attachments.length
      ? state.attachments.length + " 个附件待解析"
      : indexed + " 份文档已索引";
  }

  function formatSize(bytes) {
    if (!bytes) return "0 KB";
    if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function acceptFiles(files) {
    Array.from(files).forEach((file) => state.attachments.push({ name: file.name, size: file.size }));
    renderAttachments();
    if (state.view !== "chat") switchView("chat");
    toast(state.attachments.length + " 个文件已加入解析队列");
  }

  function toast(text) {
    const node = el("div", "attach", text);
    node.style.position = "fixed";
    node.style.bottom = "26px";
    node.style.left = "50%";
    node.style.transform = "translateX(-50%)";
    node.style.zIndex = "60";
    node.style.boxShadow = "var(--shadow-pop)";
    document.body.append(node);
    window.setTimeout(() => node.remove(), 2200);
  }

  /* ---------- 知识库 ---------- */
  function statusBadge(status) {
    const badge = el("span", "badge");
    if (status === "已索引") badge.append(icon("check"));
    if (status !== "已索引") badge.classList.add("badge--muted");
    badge.append(el("span", null, status));
    return badge;
  }

  function renderDocs() {
    const body = $("#docRows");
    body.replaceChildren();
    DOCS.forEach((doc) => {
      const tr = el("tr");
      const nameCell = el("td");
      const name = el("span", "name");
      name.append(icon("doc"), el("span", null, doc.name));
      nameCell.append(name);

      const statusCell = el("td");
      statusCell.append(statusBadge(doc.status));

      tr.append(nameCell, el("td", null, doc.type), el("td", "num", String(doc.chunks)), statusCell, el("td", null, doc.updated));
      body.append(tr);
    });
    $("#docsMeta").textContent = DOCS.length + " 个文件 · " + DOCS.filter((d) => d.status === "已索引").length + " 个已索引";
  }

  function renderStats() {
    const totalChunks = DOCS.reduce((sum, doc) => sum + doc.chunks, 0);
    const items = [
      ["文档总数", String(DOCS.length), "份"],
      ["分块总数", totalChunks.toLocaleString("en-US"), "块"],
      ["向量维度", "1536", "dims"],
      ["平均检索延迟", "268", "ms"]
    ];
    const wrap = $("#stats");
    wrap.replaceChildren();
    items.forEach(([label, value, unit]) => {
      const card = el("dl", "stat");
      const dd = el("dd", null, value);
      dd.append(el("small", null, unit));
      card.append(el("dt", null, label), dd);
      wrap.append(card);
    });
  }

  function renderChunks(docName) {
    const box = $("#chunkBox");
    const chunks = CHUNKS[docName] || [];
    box.replaceChildren();
    $("#chunkMeta").textContent = docName ? docName + " · " + chunks.length + " 块" : "";
    if (!chunks.length) {
      box.append(el("p", "empty", "这份文档还没有可预览的分块。"));
      return;
    }
    chunks.forEach((chunk) => {
      const node = el("article", "chunk");
      const head = el("header");
      head.append(el("span", null, chunk.id), el("span", null, chunk.tokens + " tokens"));
      node.append(head, el("p", null, chunk.text));
      box.append(node);
    });
  }

  function scoreChunk(query, chunk) {
    const chars = Array.from(new Set(query.replace(/\s/g, "")));
    if (!chars.length) return 0;
    const hits = chars.filter((ch) => chunk.text.includes(ch)).length;
    return Math.min(0.95, 0.32 + (hits / chars.length) * 0.62);
  }

  function runSearch(query) {
    const hits = $("#hits");
    hits.replaceChildren();
    if (!query.trim()) {
      hits.append(el("p", "empty", "输入查询后查看命中的分块与相似度。"));
      return;
    }
    const scored = [];
    Object.keys(CHUNKS).forEach((docName) => {
      CHUNKS[docName].forEach((chunk) => {
        scored.push({ docName, chunk, score: scoreChunk(query, chunk) });
      });
    });
    scored.sort((a, b) => b.score - a.score);
    scored.slice(0, 4).forEach((hit) => {
      const node = el("li", "hit");
      const left = el("div");
      left.append(el("div", "src", hit.docName + " · " + hit.chunk.id));
      left.append(el("p", null, hit.chunk.text));
      const score = el("div", "score");
      const meter = el("span", "meter");
      const fill = el("i");
      fill.style.width = Math.round(hit.score * 100) + "%";
      meter.append(fill);
      score.append(el("span", null, hit.score.toFixed(2)), meter);
      node.append(left, score);
      hits.append(node);
    });
    // TODO: 接后端时改为 POST /api/search（返回分块、来源、分数）。
  }

  /* ---------- 交互绑定 ---------- */
  function bind() {
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });

    const prompt = $("#prompt");
    const autosize = () => {
      prompt.style.height = "auto";
      prompt.style.height = Math.min(prompt.scrollHeight, 180) + "px";
    };
    prompt.addEventListener("input", autosize);
    prompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        $("#composer").requestSubmit();
      }
    });

    $("#composer").addEventListener("submit", (event) => {
      event.preventDefault();
      const text = prompt.value.trim();
      if (!text) return;
      prompt.value = "";
      autosize();
      if (state.attachments.length) {
        state.attachments = [];
        renderAttachments();
      }
      send(text);
    });

    $("#topAsk").addEventListener("click", () => {
      switchView("chat");
      prompt.focus();
    });

    $("#newThread").addEventListener("click", () => {
      const id = "t" + (THREADS.length + 1);
      THREADS.unshift({ id, title: "新会话", updated: "刚刚", count: 0 });
      state.thread = id;
      $("#chatTitle").textContent = "新会话";
      renderRail();
      switchView("chat");
      prompt.focus();
    });

    const fileInput = $("#fileInput");
    const openPicker = (target) => { state.fileTarget = target; fileInput.click(); };
    $("#topUpload").addEventListener("click", () => openPicker("chat"));
    $("#kbUpload").addEventListener("click", () => openPicker("kb"));
    $("#attachBtn").addEventListener("click", () => openPicker("chat"));
    fileInput.addEventListener("change", () => { acceptFiles(fileInput.files); fileInput.value = ""; });

    const dropzone = $("#dropzone");
    ["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-over");
    }));
    ["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-over");
    }));
    dropzone.addEventListener("drop", (event) => { if (event.dataTransfer) acceptFiles(event.dataTransfer.files); });
    dropzone.addEventListener("click", () => openPicker("kb"));
    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openPicker("kb"); }
    });

    $("#searchForm").addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch($("#searchInput").value);
    });

    const pop = $("#retrievalPop");
    $("#retrievalToggle").addEventListener("click", () => {
      const open = pop.hidden;
      pop.hidden = !open;
      $("#retrievalToggle").setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (event) => {
      if (pop.hidden) return;
      if (!pop.contains(event.target) && !$("#retrievalToggle").contains(event.target)) {
        pop.hidden = true;
        $("#retrievalToggle").setAttribute("aria-expanded", "false");
      }
    });

    const syncParams = () => {
      const topK = $("#paramTopK").value;
      const score = Number($("#paramScore").value).toFixed(2);
      $("#outTopK").textContent = topK;
      $("#outScore").textContent = score;
      $("#retrievalSummary").textContent = "top " + topK + " · " + score;
    };
    ["#paramTopK", "#paramScore", "#paramRerank", "#paramHybrid"].forEach((sel) => {
      $(sel).addEventListener("input", syncParams);
    });
    syncParams();

    const drawer = $("#styleDrawer");
    const scrim = $("#scrim");
    const openDrawer = () => { drawer.hidden = false; scrim.hidden = false; $("#closeStyle").focus(); };
    const closeDrawer = () => { drawer.hidden = true; scrim.hidden = true; $("#openStyle").focus(); };
    $("#openStyle").addEventListener("click", openDrawer);
    $("#closeStyle").addEventListener("click", closeDrawer);
    $("#doneStyle").addEventListener("click", closeDrawer);
    scrim.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !drawer.hidden) closeDrawer(); });

    $("#resetStyle").addEventListener("click", () => {
      state.theme = "minimalist";
      state.accent = "ink";
      applyStyle();
    });
  }

  function init() {
    buildStyleDrawer();
    applyStyle();
    renderRail();
    renderMessages();
    renderAttachments();
    renderStats();
    renderDocs();
    renderChunks("产品需求文档 v4.2.pdf");
    runSearch("限流 429 返回");
    switchView("chat");
    bind();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
