r"""步骤 7 的 A/B 对比：查询改写到底有没有用。

步骤 7 的验收标准是「有可测量的提升才合入」，所以这个脚本只做一件事：
在**同一套评测题**上，分别关掉/打开查询改写各跑一轮，把两组分数并排打出来。

为什么不用 /api/eval/run 的 variants 参数：
那条路一次请求里跑完所有变体，中途撞限流会让后一组分数失真，
分两次跑、分开存报告，才能确认两组用的是同一套题与同一套链路。

用法：
    .venv\Scripts\python.exe scripts\compare_step7.py            # 默认各 3 条
    .venv\Scripts\python.exe scripts\compare_step7.py --limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import query  # noqa: E402
from app.eval import dataset as dataset_module  # noqa: E402
from app.eval import ragas_eval  # noqa: E402

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def run_variant(label: str, limit: int, use_rewrite: bool) -> dict:
    """跑一轮评测。用 variant 里的 rewrite 开关控制是否改写。"""
    print(f"\n=== {label}（rewrite={use_rewrite}）===", flush=True)
    started = time.time()
    report = ragas_eval.run(
        variants=[{"name": label, "hybrid": True, "rerank": None, "rewrite": use_rewrite}],
        limit=limit,
        with_ragas=True,
    )
    path = ragas_eval.save_report(report)
    elapsed = time.time() - started
    print(f"用时 {elapsed:.0f}s，报告 {path.name}", flush=True)
    return report.to_dict()


def summarize(payload: dict) -> dict:
    variant = (payload.get("variants") or [{}])[0]
    return {
        "scores": variant.get("scores") or {},
        "retrieval": variant.get("retrieval") or {},
        "note": variant.get("error") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3, help="每组跑几条样本")
    args = parser.parse_args()

    saved = dataset_module.load_dataset()
    if saved is None or not saved.cases:
        print("还没有评测集，先跑 POST /api/eval/dataset 或 UI 的「生成评测集」")
        return 2

    info = dataset_module.staleness(saved)
    print(f"评测集：{len(saved.cases)} 条，本次每组跑 {args.limit} 条")
    if info["stale"]:
        print(f"警告：评测集已过期（{info['reason']}），先重建再对比")

    print("查询改写能力：" + json.dumps(query.describe(), ensure_ascii=False))

    base = run_variant("改写关闭（基线）", args.limit, False)
    rew = run_variant("改写开启", args.limit, True)

    left, right = summarize(base), summarize(rew)
    print("\n" + "=" * 62)
    print(f"{'指标':<18}{'基线':>12}{'改写开启':>12}{'变化':>12}")
    print("-" * 62)
    for key in METRICS:
        a, b = left["scores"].get(key), right["scores"].get(key)
        delta = "—" if a is None or b is None else f"{(b - a) * 100:+.1f}pp"
        fmt = lambda v: "—" if v is None else f"{v * 100:.1f}%"
        print(f"{key:<18}{fmt(a):>12}{fmt(b):>12}{delta:>12}")

    a_hit, b_hit = left["retrieval"].get("hit_rate"), right["retrieval"].get("hit_rate")
    delta = "—" if a_hit is None or b_hit is None else f"{(b_hit - a_hit) * 100:+.1f}pp"
    fmt = lambda v: "—" if v is None else f"{v * 100:.1f}%"
    print(f"{'hit_rate(自算)':<18}{fmt(a_hit):>12}{fmt(b_hit):>12}{delta:>12}")
    print("-" * 62)
    for label, item in (("基线", left), ("改写开启", right)):
        if item["note"]:
            print(f"{label} 备注：{item['note']}")
    print("=" * 62)
    print("\n判定：任一核心指标提升且没有指标明显变差，才值得把")
    print("QUERY_REWRITE_ENABLED 打开；否则保持默认关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
