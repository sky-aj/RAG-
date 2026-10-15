r"""步骤 3 的接口冒烟：确认 /api/chunks/* 真的能用。

用法：.venv\Scripts\python.exe scripts\smoke_step3_api.py
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

from app import create_app  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
LINE = "=" * 70


def main() -> int:
    app = create_app()
    client = app.test_client()

    print(LINE)
    print("步骤 3 接口冒烟")
    print(LINE)

    payload = client.get("/api/chunks/strategies").get_json()
    names = [item["name"] for item in payload["strategies"]]
    print(f"策略清单：{'、'.join(names)}")
    print(f"默认参数：{payload['defaults']}")

    cases = ["运维笔记.txt", "产品需求文档.md", "quota.py", "客服问答记录.csv", "运维 Runbook.docx", "API 参考手册.pdf"]
    failures = 0
    for name in cases:
        path = SAMPLES / name
        if not path.exists():
            print(f"  {name:22} !! 样例缺失")
            failures += 1
            continue
        with path.open("rb") as handle:
            response = client.post(
                "/api/chunks/preview?preview=2",
                data={"file": (handle, name)},
                content_type="multipart/form-data",
            )
        if response.status_code != 200:
            print(f"  {name:22} !! HTTP {response.status_code} {response.get_json()}")
            failures += 1
            continue
        body = response.get_json()
        stats = body["stats"]
        first = body["chunks"][0]
        symbol = f" [{first['symbol']}]" if first["symbol"] else ""
        print(
            f"  {name:22} 策略={body['strategy']:17} 块={body['chunk_total']:<3}"
            f" 字符 {stats['char_min']}~{stats['char_max']}(均{stats['char_avg']})"
            f" 首块行{first['line_start']}-{first['line_end']}{symbol}"
        )

    print()
    # 覆盖所有四条策略
    seen = set()
    for name in cases:
        path = SAMPLES / name
        if not path.exists():
            continue
        with path.open("rb") as handle:
            body = client.post(
                "/api/chunks/preview",
                data={"file": (handle, name)},
                content_type="multipart/form-data",
            ).get_json()
        seen.add(body["strategy"])
    print(f"覆盖策略：{'、'.join(sorted(seen))}")

    assert failures == 0, "有样例缺失或接口报错"
    assert seen == {"paragraph", "markdown", "code", "structure_aware"}, f"策略覆盖不全：{seen}"
    print("步骤 3 接口冒烟通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
