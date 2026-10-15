r"""步骤 6 的接口冒烟：出题 -> 跑分 -> 报告落盘（真的调智谱与硅基流动）。

用法：.venv\Scripts\python.exe scripts\smoke_step6_api.py
      .venv\Scripts\python.exe scripts\smoke_step6_api.py --no-llm   # 跳过联网出题与打分

和前几步的冒烟一样，向量库 / 入库状态 / 文档注册表 / 评测目录全部指到临时目录，
反复跑不会污染开发环境。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

SMOKE_TMP = str(
    Path(__file__).resolve().parent.parent / "data" / "tmp" / ("smoke6-" + str(os.getpid()))
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
os.environ["EVAL_DATASET_PATH"] = os.path.join(SMOKE_TMP, "eval", "dataset.json")
os.environ["EVAL_REPORT_DIR"] = os.path.join(SMOKE_TMP, "eval")

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
SAMPLES = ["产品需求文档.md", "quota.py", "运维笔记.txt"]


def main() -> int:
    args = set(sys.argv[1:])
    app = create_app()
    client = app.test_client()
    problems: list[str] = []

    print(LINE)
    print("步骤 6 接口冒烟：评测集生成 + RAGAS 跑分 + LangSmith 追踪状态")
    print(LINE)

    from app.api.documents import load_registry

    registry = {record["name"]: record for record in load_registry()}
    targets = [registry[name]["doc_id"] for name in SAMPLES if name in registry]
    missing = [name for name in SAMPLES if name not in registry]
    if missing:
        print("!! 注册表里缺少样例：" + "、".join(missing))
        return 1

    offline = "--no-llm" in args or not settings.llm.configured

    if offline:
        print("\n[0] 跳过入库（--no-llm 或未配置 Key）")
    else:
        print("\n[0] 先把样例灌进临时向量库")
        body = (client.post("/api/index?force=true", json={"doc_id": targets}).get_json() or {})
        if body.get("written") is None:
            print("    入库失败：", body.get("error") or body)
            print("    提示：这一步要联网调硅基流动向量化。")
            vector_store.close_client()
            return 1
        print(f"    写入={body['written']} 块，库内点数={body['total_points']}")

    status = client.get("/api/eval").get_json()
    print("\n[1] GET /api/eval")
    print(f"    评测集：{status['dataset']['count']} 条  {status['dataset']['path']}")
    print(f"    指标：{', '.join(status['metrics'])}")
    print(f"    LangSmith：{'开启' if status['config']['langsmith_enabled'] else '未配置'}")
    print(f"    并发={status['config']['max_workers']}（默认串行，规避账户级限流）")

    if offline:
        print("\n[2] 跳过出题与打分（--no-llm 或未配置 Key）")
        vector_store.close_client()
        print("\n步骤 6 冒烟（离线部分）通过。")
        return 0

    print("\n[2] POST /api/eval/dataset?limit=3（从已入库片段反向出题）")
    built = client.post("/api/eval/dataset?limit=3", json={"limit": 3}).get_json()
    if "error" in built:
        problems.append("出题失败：" + built["error"])
        print("    !!", built["error"])
    else:
        print(f"    出题 {built['count']} 道，落盘到 {built['path']}")
        for case in built["cases"]:
            print(f"      {case['case_id']}  {case['question']}")
            print(f"          标准答案：{case['ground_truth']}")
        if built["notes"]:
            print(f"    （{len(built['notes'])} 条片段被跳过）")

    if not problems:
        print("\n[3] POST /api/eval/run?limit=3（真实 RAG 链路 + RAGAS 打分，可能几分钟）")
        report = client.post("/api/eval/run", json={"limit": 3}).get_json()
        if "error" in report:
            problems.append("跑分失败：" + report["error"])
            print("    !!", report["error"])
        else:
            variant = report["variants"][0]
            print(f"    样本数={variant['retrieval']['case_count']}"
                  f" 命中率={variant['retrieval']['hit_rate']}"
                  f" 平均上下文={variant['retrieval']['avg_contexts']}"
                  f" 空回答={variant['retrieval']['empty_answers']}"
                  f" 调用失败={variant['retrieval']['failed_answers']}")
            print("    RAGAS 四项：")
            for key, value in variant["scores"].items():
                print(f"      {key:18} {value}")
            missing_metrics = [k for k, v in variant["scores"].items() if v is None]
            if missing_metrics:
                problems.append("这些指标没打出分（多半是 judge 撞限流）：" + "、".join(missing_metrics))
            print(f"    报告：{report['path']}")

        print("\n[4] GET /api/eval/history（历史跑分）")
        history = client.get("/api/eval/history").get_json()["items"]
        print(f"    {len(history)} 份报告")
        for item in history[:3]:
            print(f"      {item['created_at']}  样本 {item['dataset_count']}")

    vector_store.close_client()
    print("\n" + LINE)
    if problems:
        print("步骤 6 冒烟失败：")
        for item in problems:
            print("  -", item)
        return 1
    print("步骤 6 冒烟通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
