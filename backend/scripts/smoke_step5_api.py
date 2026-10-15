r"""步骤 5 的接口冒烟：混合检索 -> 上下文拼装 -> SSE 对话（真的叫一次智谱）。

用法：.venv\Scripts\python.exe scripts\smoke_step5_api.py
      .venv\Scripts\python.exe scripts\smoke_step5_api.py --no-llm   # 跳过联网生成

与步骤 4 的冒烟一样，向量库 / 入库状态 / 文档注册表全部指到临时目录，
反复跑不会污染开发环境，也不会改动真实注册表。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# 不用系统临时目录：沙箱里写不进去（PermissionError）。
# 放到工作区内的 data/tmp，跑完自动清掉。
SMOKE_TMP = str(
    Path(__file__).resolve().parent.parent / "data" / "tmp" / ("smoke5-" + str(os.getpid()))
)
shutil.rmtree(SMOKE_TMP, ignore_errors=True)
os.makedirs(SMOKE_TMP, exist_ok=True)
os.environ["QDRANT_PATH"] = os.path.join(SMOKE_TMP, "qdrant")
os.environ["INDEX_STATE_PATH"] = os.path.join(SMOKE_TMP, "index_state.json")
_REAL_REGISTRY = Path(__file__).resolve().parent.parent / "data" / "documents.json"
_SMOKE_REGISTRY = Path(SMOKE_TMP) / "documents.json"
if _REAL_REGISTRY.exists():
    _SMOKE_REGISTRY.write_text(_REAL_REGISTRY.read_text(encoding="utf-8"), encoding="utf-8")
os.environ["DOCUMENT_REGISTRY_PATH"] = str(_SMOKE_REGISTRY)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from app import create_app  # noqa: E402
from app.config import settings  # noqa: E402
from app.retrieval import vector_store  # noqa: E402

LINE = "=" * 78
SAMPLES = [
    "运维笔记.txt",
    "产品需求文档.md",
    "quota.py",
    "客服问答记录.csv",
    "运维 Runbook.docx",
    "API 参考手册.pdf",
]
QUESTIONS = [
    ("接口默认的限流额度是多少", "产品需求文档.md"),
    ("滑动窗口是怎么统计请求量的", "quota.py"),
    ("灰度发布先切多少流量", "运维笔记.txt"),
    ("容量按峰值 QPS 的多少倍预留", "运维 Runbook.docx"),
]


def run_retrieval(client) -> tuple[int, list[str]]:
    problems: list[str] = []
    print("\n[1] POST /api/retrieve?hybrid=true（两路召回 + RRF + rerank）")
    for question, expected in QUESTIONS:
        result = client.post("/api/retrieve", json={"question": question}).get_json()
        if "error" in result:
            problems.append(f"检索报错：{result['error']}")
            print("    !!", result["error"])
            continue
        diag = result["diagnostics"]
        hits = result["hits"]
        top = hits[0] if hits else None
        mark = "✓" if top and top["doc_name"] == expected else "✗"
        if mark == "✗":
            problems.append(f"{question} 首条不是 {expected}")
        print(
            f"    {mark} {question:22} 语义候选={diag['vector_candidates']:>3}"
            f" 过阈={diag['vector_kept']:>3} 关键字={diag['keyword_candidates']:>3}"
            f" 融合={diag['fused']:>3} 返回={diag['returned']:>2}"
            f" rerank={'是' if diag['rerank_used'] else '否'}"
        )
        if top:
            routes = "+".join(top.get("routes") or [])
            print(
                f"         首条 [{top['doc_name']} 行{top['line_start']}-{top['line_end']}]"
                f" score={top['score']:.3f} 路径={routes or '语义'}"
                f" rerank={top.get('rerank_score', '—')}"
            )
        print(
            f"         上下文：装入 {diag['context_used']} / 去重前 {diag['merged_from_hits']}"
            f" 放弃 {diag['context_dropped']}，估算 {diag['context_tokens']} / {diag['context_budget']} token"
        )
    return (1 if problems else 0), problems


def check_no_duplicate_context(client) -> list[str]:
    """同一文档重叠行号不能拼两遍，这正是需求第 3 条。"""
    problems: list[str] = []
    print("\n[2] 上下文去重：同一文档重叠块只出现一次")
    result = client.post("/api/retrieve", json={"question": "限流与配额怎么设置", "top_k": 12}).get_json()
    seen: dict[tuple[str, int, int], int] = {}
    for citation in result["citations"]:
        key = (citation["doc_id"], citation["line_start"], citation["line_end"])
        seen[key] = seen.get(key, 0) + 1
    duplicates = {key: count for key, count in seen.items() if count > 1}
    if duplicates:
        problems.append(f"上下文里出现重复片段：{duplicates}")
        print("    !! 重复：", duplicates)
    else:
        print(f"    ✓ {len(result['citations'])} 条引用区间互不重复")
    for citation in result["citations"][:3]:
        print(
            f"      [片段 {citation['index']}] {citation['doc_name']} {citation['location']}"
            f"  合并自 {len(citation['chunk_ids'])} 块"
        )
    return problems


def run_chat(client) -> list[str]:
    problems: list[str] = []
    print("\n[3] POST /api/chat（SSE：sources -> reasoning -> content -> done）")
    response = client.post(
        "/api/chat",
        json={"question": "接口默认的限流额度是多少？超出会返回什么？"},
    )
    if response.status_code != 200:
        print("    !! HTTP", response.status_code, response.get_data(as_text=True)[:200])
        return ["对话接口没有返回 200"]

    kinds: list[str] = []
    answer: list[str] = []
    sources: dict = {}
    usage: dict = {}
    error = ""
    reasoning_chars = 0

    for block in response.get_data(as_text=True).split("\n\n"):
        line = block.strip()
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:].strip())
        kinds.append(event["type"])
        if event["type"] == "sources":
            sources = event
        elif event["type"] == "content":
            answer.append(event["text"])
        elif event["type"] == "reasoning":
            reasoning_chars += len(event["text"])
        elif event["type"] == "done":
            usage = event.get("usage") or {}
        elif event["type"] == "error":
            error = event.get("message", "")

    # 流式事件有几百条，折叠连续重复后再打印，否则刷屏
    squeezed: list[str] = []
    for kind in kinds:
        if not squeezed or squeezed[-1] != kind:
            squeezed.append(kind)
        else:
            squeezed[-1] = kind
    counts: list[str] = []
    index = 0
    for kind in squeezed:
        run = 0
        while index < len(kinds) and kinds[index] == kind:
            run += 1
            index += 1
        counts.append(kind if run == 1 else f"{kind}×{run}")
    print("    事件序列：" + " -> ".join(counts))
    if error:
        problems.append(f"生成报错：{error}")
        print("    !! 生成报错：", error)

    citations = sources.get("citations") or []
    print(f"    引用 {len(citations)} 条，推理 {reasoning_chars} 字，usage={usage}")
    print("    回答：")
    text = "".join(answer)
    for line in text.splitlines()[:12]:
        if line.strip():
            print("      " + line)

    if citations and citations[0]["index"] != 1:
        problems.append("引用编号不是从 1 开始")
    if kinds and kinds[0] != "sources":
        problems.append("第一个事件不是 sources")
    if "content" not in kinds and not error:
        problems.append("没有收到正文")
    if error == "" and usage and not usage.get("completion_tokens"):
        problems.append("done 事件里没有 completion_tokens")
    return problems


def main() -> int:
    args = set(sys.argv[1:])
    app = create_app()
    client = app.test_client()

    print(LINE)
    print("步骤 5 接口冒烟：混合检索 + 上下文拼装 + 对话")
    print(LINE)

    from app.api.documents import load_registry

    registry = {record["name"]: record for record in load_registry()}
    targets = [registry[name]["doc_id"] for name in SAMPLES if name in registry]
    missing = [name for name in SAMPLES if name not in registry]
    if missing:
        print("!! 注册表里缺少样例：" + "、".join(missing))
        print("   先跑 scripts/make_samples.py 并上传，或直接跑 smoke_step4_api.py 看样例是否齐全")
        return 1

    print("\n[0] 先把 6 份样例灌进临时向量库")
    response = client.post("/api/index?force=true", json={"doc_id": targets})
    body = response.get_json() or {}
    if response.status_code != 200 or "written" not in body:
        print("    入库失败（HTTP %s）：%s" % (response.status_code, body.get("error") or body))
        print("    提示：这一步要联网调硅基流动向量化；沙箱里会 Connection refused。")
        return 1
    print(f"    写入={body['written']} 块，库内点数={body['total_points']}，模式={body['mode']}")
    if body["written"] == 0:
        print("    !! 一块都没写进去，先查 /api/index 的 store.error")
        return 1

    code, problems = run_retrieval(client)
    problems += check_no_duplicate_context(client)

    if "--no-llm" in args:
        print("\n[3] 跳过对话（--no-llm）")
    elif not settings.llm.configured:
        print("\n[3] 跳过对话：LLM_API_KEY 未配置")
    else:
        problems += run_chat(client)

    vector_store.close_client()
    print("\n" + LINE)
    if problems:
        print("步骤 5 冒烟失败：")
        for item in problems:
            print("  -", item)
        return 1
    print("步骤 5 冒烟通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




