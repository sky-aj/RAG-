r"""按顺序跑完当前已完成的步骤，每步失败就停。

用法：.venvScriptspython.exe scriptssmoke_all.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PYTHON = sys.executable

STEPS = [
    ("步骤 1 · 骨架与健康检查", "smoke_step1.py"),
    ("步骤 2 · 解析层", "smoke_step2.py"),
    ("步骤 3 · 分块策略", "smoke_step3.py"),
    ("步骤 3 · 分块接口", "smoke_step3_api.py"),
    ("步骤 4 · 向量化与入库", "smoke_step4_api.py"),
    ("步骤 5 · 混合检索与对话", "smoke_step5_api.py"),
    ("步骤 6 · 评测与追踪", "smoke_step6_api.py"),
]


def main() -> int:
    for label, script in STEPS:
        print()
        print("#" * 74)
        print(f"# {label}")
        print("#" * 74)
        result = subprocess.run([PYTHON, str(SCRIPTS / script)], cwd=SCRIPTS.parent)
        if result.returncode != 0:
            print(f"\n!! {label} 未通过，已停在这一步（退出码 {result.returncode}）")
            return result.returncode
    print()
    print("=" * 74)
    print("已完成步骤全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

