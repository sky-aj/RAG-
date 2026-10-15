r"""步骤 4 的接口冒烟：切分 -> 向量化 -> 入库 -> 语义检索，真的走通一遍。

用法：.venv\Scripts\python.exe scripts\smoke_step4_api.py

为了让这个脚本可以反复跑、也不污染真实开发环境，向量库会临时指到
系统临时目录（在第 21 行 import app 之前就把 QDRANT_PATH 换掉）。
只读注册表里已有的样例文档，不新增、不删除任何文档。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# \u5fc5\u987b\u5728\u5bfc\u5165 app.config \u4e4b\u524d\uff0c\u628a\u6240\u6709\u201c\u4f1a\u88ab\u5199\u201d\u7684\u4e1c\u897f\u90fd\u6307\u5230\u4e34\u65f6\u76ee\u5f55\uff1a
# \u5411\u91cf\u5e93\u8def\u5f84\u3001\u5165\u5e93\u72b6\u6001\u6587\u4ef6\u3001\u6587\u6863\u6ce8\u518c\u8868\u3002
# \u53ea\u6362\u5411\u91cf\u5e93\u662f\u4e0d\u591f\u7684\uff1a\u811a\u672c\u91cc\u7684\u5220\u9664\u52a8\u4f5c\u4f1a\u987a\u624b\u628a\u771f\u5b9e\u7684
# documents.json \u548c index_state.json \u4e00\u8d77\u6539\u6389\uff0c\u4e0b\u6b21\u6253\u5f00\u9875\u9762\u5c31\u4f1a\u591a\u51fa\u201c\u5f85\u5165\u5e93\u201d\u7684\u6587\u6863\u3002
SMOKE_TMP = tempfile.mkdtemp(prefix="corpus-smoke4-")
os.environ["QDRANT_PATH"] = os.path.join(SMOKE_TMP, "qdrant")
os.environ["INDEX_STATE_PATH"] = os.path.join(SMOKE_TMP, "index_state.json")
# 注册表也拷一份到临时目录：脚本结尾会删一份文档的向量，
# 不能让它碰到真实注册表。
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
from app.retrieval import vector_store  # noqa: E402

LINE = "=" * 78
SAMPLE_NAMES = [
    "\u8fd0\u7ef4\u7b14\u8bb0.txt",
    "\u4ea7\u54c1\u9700\u6c42\u6587\u6863.md",
    "quota.py",
    "\u5ba2\u670d\u95ee\u7b54\u8bb0\u5f55.csv",
    "\u8fd0\u7ef4 Runbook.docx",
    "API \u53c2\u8003\u624b\u518c.pdf",
]

# \u95ee\u9898 -> \u671f\u671b\u547d\u4e2d\u7684\u6765\u6e90\u6587\u4ef6\uff08\u4eba\u5de5\u6807\u7684\uff0c\u7528\u6765\u770b\u53ec\u56de\u771f\u4e0d\u771f\uff09
QUESTIONS = [
    ("\u63a5\u53e3\u9ed8\u8ba4\u7684\u9650\u6d41\u989d\u5ea6\u662f\u591a\u5c11", "\u4ea7\u54c1\u9700\u6c42\u6587\u6863.md"),
    ("\u6ed1\u52a8\u7a97\u53e3\u600e\u4e48\u7edf\u8ba1\u8bf7\u6c42\u91cf", "quota.py"),
    ("\u7070\u5ea6\u53d1\u5e03\u5148\u5207\u591a\u5c11\u6d41\u91cf", "\u8fd0\u7ef4\u7b14\u8bb0.txt"),
    ("\u5bb9\u91cf\u6309\u5cf0\u503c QPS \u7684\u591a\u5c11\u500d\u9884\u7559", "\u8fd0\u7ef4 Runbook.docx"),
]


def main() -> int:
    app = create_app()
    client = app.test_client()

    print(LINE)
    print("\u6b65\u9aa4 4 \u63a5\u53e3\u5192\u70df\uff1a\u5411\u91cf\u5316 + \u5165\u5e93 + \u8bed\u4e49\u68c0\u7d22")
    print(LINE)

    from app.api.documents import load_registry

    registry = {record["name"]: record for record in load_registry()}
    targets = [registry[name]["doc_id"] for name in SAMPLE_NAMES if name in registry]
    missing = [name for name in SAMPLE_NAMES if name not in registry]
    if missing:
        print("!! \u6ce8\u518c\u8868\u91cc\u7f3a\u5c11\u6837\u4f8b\uff1a" + "\u3001".join(missing))
        print("   \u5148\u8dd1 .venv\\Scripts\\python.exe scripts\\make_samples.py \u5e76\u4e0a\u4f20")
        return 1

    print("\n[1] \u5165\u5e93\u524d\u7684\u5e93\u72b6\u6001")
    print("   ", client.get("/api/index").get_json()["store"])

    print("\n[2] POST /api/index \uff08\u5168\u91cf\u91cd\u704c\u8fd9 6 \u4efd\u6837\u4f8b\uff09")
    response = client.post("/api/index?force=true", json={"doc_id": targets})
    if response.status_code != 200:
        print("   !! HTTP", response.status_code, response.get_json())
        return 1
    body = response.get_json()
    print(f"    \u6a21\u5f0f={body['mode']} \u5199\u5165={body['written']} \u5757\u603b\u6570={body['chunk_total']}"
          f" \u5e93\u5185\u70b9\u6570={body['total_points']}")
    for item in body["documents"]:
        if item["ok"]:
            print(f"      {item['doc_name']:20} {item['strategy']:17} \u5757={item['chunks']:<3} \u5b57\u7b26={item['chars']}")
        else:
            print(f"      {item['doc_name']:20} !! {item['error']}")

    print("\n[3] \u589e\u91cf\u518d\u8dd1\u4e00\u6b21\uff08\u5e94\u8be5\u5168\u90e8\u8df3\u8fc7\uff0c\u4e0d\u91cd\u590d\u5411\u91cf\u5316\uff09")
    body2 = client.post("/api/index", json={"doc_id": targets}).get_json()
    print(f"    \u5199\u5165={body2['written']} \u8df3\u8fc7={len(body2['skipped'])} \u5e93\u5185\u70b9\u6570={body2['total_points']}")

    print("\n[4] POST /api/search\uff08\u7eaf\u8bed\u4e49\uff0c\u4e00\u6b21\u53d6\u56de 80 \u6761\u5019\u9009\uff09")
    hits_total = 0
    for question, expected in QUESTIONS:
        result = client.post("/api/search?top_n=80", json={"query": question}).get_json()
        hits = result.get("hits", [])
        hits_total += len(hits)
        if not hits:
            print(f"    {question:24} !! \u65e0\u547d\u4e2d  {result}")
            continue
        top = hits[0]
        mark = "\u2713" if top["doc_name"] == expected else "\u2717"
        print(f"    {mark} {question:24} \u8fd4\u56de={result['returned']:>3} \u8fc7\u9608={result['kept']:>3}"
              f"  \u6700\u9ad8={top['score']:.3f} [{top['doc_name']} \u884c{top['line_start']}-{top['line_end']}]")

    print("\n[5] \u6309\u6587\u6863\u8fc7\u6ee4\u68c0\u7d22")
    only_py = registry["quota.py"]["doc_id"]
    filtered = client.post("/api/search?top_n=80", json={"query": "\u9650\u6d41\u7b97\u6cd5", "doc_id": only_py}).get_json()
    names = {hit["doc_name"] for hit in filtered["hits"]}
    print(f"    \u9650\u5b9a doc_id=quota.py -> \u8fd4\u56de\u6587\u6863\u96c6\u5408={names or '\u7a7a'}")

    print("\n[6] DELETE /api/index?doc_id=... \u53ea\u5220\u4e00\u4efd")
    removed = client.delete("/api/index", json={"doc_id": [only_py]}).get_json()
    after = client.get("/api/index").get_json()
    print(f"    \u5220\u9664\u70b9\u6570={removed['removed']} \u5269\u4f59\u70b9\u6570={after['store']['points']}"
          f" \u5df2\u5165\u5e93\u6587\u6863={after['indexed_count']}")

    vector_store.close_client()
    problems = []
    if body["written"] != body["chunk_total"]:
        problems.append("\u5199\u5165\u6570\u4e0e\u5757\u6570\u4e0d\u7b26")
    if body2["written"] != 0:
        problems.append("\u589e\u91cf\u6a21\u5f0f\u91cd\u590d\u5199\u4e86")
    if names and names != {"quota.py"}:
        problems.append("\u6587\u6863\u8fc7\u6ee4\u5931\u6548")
    if after["store"]["points"] != body["total_points"] - removed["removed"]:
        problems.append("\u5220\u9664\u540e\u70b9\u6570\u5bf9\u4e0d\u4e0a")

    print("\n" + LINE)
    if problems:
        print("\u6b65\u9aa4 4 \u5192\u70df\u5931\u8d25\uff1a" + "\uff1b".join(problems))
        return 1
    print(f"\u6b65\u9aa4 4 \u5192\u70df\u901a\u8fc7\uff1a\u5165\u5e93 {body['written']} \u5757\uff0c"
          f"\u68c0\u7d22\u547d\u4e2d {hits_total} \u6761\uff0c\u5220\u9664\u540e\u5269 {after['store']['points']} \u70b9\u3002")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
