r"""步骤 2 冒烟测试：全部加载器各跑一遍，并打一份结构报告。

用法：.venvScriptspython.exe scriptssmoke_step2.py [可选：你自己的文件]
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

from app.ingest import SUPPORTED_SUFFIXES, parse_file, resolve_strategy  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
LINE = "=" * 74


def report(path: Path) -> dict:
    print(LINE)
    print(f"文件：{path.name}（{path.stat().st_size / 1024:.1f} KB）")
    try:
        document = parse_file(path, path.stem[:12], path.name)
    except Exception as error:  # noqa: BLE001
        print(f"  !! 解析失败：{type(error).__name__}: {error}")
        return {"ok": False}

    print(f"  加载器：{document.parser}  文档类型：{document.doc_type}  分块策略：{document.strategy}")
    print(f"  编码：{document.encoding or '—'}  页数：{document.page_count or '—'}  行总数：{document.line_total}")
    print(f"  结构块：{len(document.elements)} 个 -> {document.counts_by_kind()}")
    if document.warnings:
        for warning in document.warnings:
            print(f"  警告：{warning}")

    print("  前 6 个块：")
    shown = 0
    for element in document.elements:
        if shown >= 6:
            break
        if not element.text.strip():
            continue
        label = f"{element.kind.value}"
        if element.level:
            label += f"(h{element.level})"
        if element.language:
            label += f"[{element.language}]"
        text = element.text.replace("\n", " / ")
        if len(text) > 64:
            text = text[:64] + "…"
        print(f"    行 {element.line_start:>4}-{element.line_end:<4} {label:<18} {text}")
        shown += 1

    if document.outline_entries:
        print(f"  嵌入目录：{len(document.outline_entries)} 条，例如「{document.outline_entries[0]['title']}」")

    return {"ok": True, "doc_type": document.doc_type, "strategy": document.strategy}


def main() -> int:
    print(LINE)
    print("步骤 2 冒烟测试：解析层")
    print(LINE)
    print(f"支持 {len(SUPPORTED_SUFFIXES)} 种扩展名")
    print()

    extra = [Path(arg) for arg in sys.argv[1:]]
    targets = sorted(SAMPLES.glob("*")) + extra
    results = [report(path) for path in targets if path.is_file()]

    print(LINE)
    ok = sum(1 for item in results if item.get("ok"))
    print(f"完成：{ok}/{len(results)} 份解析成功")

    strategies = sorted({item["strategy"] for item in results if item.get("ok")})
    print("步骤 3 会用到这些策略：" + "、".join(strategies))
    assert ok == len(results), "有文件解析失败"
    print("步骤 2 通过：解析层可用，每份文件都能给出带行号与结构的块序列。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
