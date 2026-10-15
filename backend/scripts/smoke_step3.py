r"""步骤 3 冒烟测试：四条分块策略各跑一遍。

用法：.venvScriptspython.exe scriptssmoke_step3.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from app.ingest import parse_file  # noqa: E402
from app.ingest.chunking import chunk_document  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
LINE = "=" * 76

CASES = [
    ("运维笔记.txt", None, "长文 / 纯文本 -> 段落策略"),
    ("产品需求文档.md", None, "Markdown -> 标题树策略"),
    ("quota.py", None, "Python -> ast 按函数类"),
    ("客服问答记录.csv", None, "表格 -> 结构感知"),
    ("运维 Runbook.docx", None, "Word -> 结构感知"),
    ("API 参考手册.pdf", None, "PDF -> 结构感知"),
]


def main() -> int:
    print(LINE)
    print("步骤 3 冒烟测试：分块策略")
    print(LINE)

    failures = 0
    seen_strategies: set[str] = set()

    for name, override, label in CASES:
        path = SAMPLES / name
        print()
        print(LINE)
        print(f"{label}   文件：{name}")
        if not path.exists():
            print("  !! 样例缺失，先跑 scripts/make_samples.py")
            failures += 1
            continue

        document = parse_file(path, "smoke0001", name)
        report = chunk_document(document, strategy=override)
        seen_strategies.add(report.strategy)

        # 走 summary()，和接口返回的结构保持一致，
        # 免得「脚本里的字段名」和「接口里的字段名」对不上
        payload = report.summary()
        stats = payload["stats"]
        print(f"  策略：{report.strategy}")
        print(f"  块数：{payload['chunk_total']}   字符 {stats['char_min']}~{stats['char_max']} 平均 {stats['char_avg']}")
        print(f"  参数：目标 {stats['target_tokens']} / 上限 {stats['max_tokens']} / 重叠 {stats['overlap_tokens']} token")
        if payload["details"]:
            print(f"  细节：{payload['details']}")
        for note in report.notes:
            print(f"  说明：{note}")

        # 不变量：行号必须递增、区间合法、每块非空
        previous = 0
        for chunk in report.chunks:
            assert chunk.text.strip(), "空块"
            assert chunk.line_start <= chunk.line_end, chunk.chunk_id
            assert chunk.line_start >= previous, f"行号倒退：{chunk.chunk_id}"
            previous = chunk.line_start
        over = [c for c in report.chunks if len(c.text) / 3.5 > stats["max_tokens"] * 1.5]
        if over:
            print(f"  注意：{len(over)} 块明显超过上限")

        print("  前 4 块：")
        for chunk in report.chunks[:4]:
            symbol = f" [{chunk.symbol}]" if chunk.symbol else ""
            path_text = " > ".join(chunk.heading_path)
            head = f"行 {chunk.line_start}-{chunk.line_end}{symbol}"
            if path_text:
                head += f"  «{path_text[:38]}»"
            body = chunk.text.replace("\n", " / ")
            print(f"    {head}")
            print(f"        {body[:96]}")

    print()
    print(LINE)
    print(f"覆盖策略：{'、'.join(sorted(seen_strategies))}")
    assert failures == 0, "有样例缺失"
    expected = {"paragraph", "markdown", "code", "structure_aware"}
    missing = expected - seen_strategies
    assert not missing, f"这些策略没被覆盖：{missing}"
    print("步骤 3 通过：四条策略都能切分，且行号区间连续合法。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
