r"""步骤 1 冒烟测试：不依赖任何第三方重库，直接跑通路由。

用法：.venv\Scripts\python.exe scripts\smoke_step1.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402


def main() -> int:
    app = create_app()
    client = app.test_client()

    print("=" * 62)
    print("步骤 1 冒烟测试")
    print("=" * 62)

    response = client.get("/api/health")
    assert response.status_code == 200, response.data
    health = response.get_json()
    print("[健康检查] 状态:", health["status"])
    print("            Python:", health["python"]["version"])
    print("            向量模型:", health["config"]["embedding_model"], f"({health['config']['embedding_dimension']} 维)")
    print("            生成模型:", health["config"]["chat_model"], "| 模型 Key:", "已配置" if health["config"]["llm_configured"] else "未配置")
    print("            向量库:", health["config"]["qdrant_mode"], health["config"]["qdrant_collection"])
    print("            已接收文档:", health["documents"]["count"])

    installed = [name for name, info in health["dependencies"].items() if info["installed"]]
    missing = [name for name, info in health["dependencies"].items() if not info["installed"]]
    print("            依赖已装:", ", ".join(installed) or "无")
    print("            依赖未装:", ", ".join(missing) or "无")

    sample = io.BytesIO("这是一份用于冒烟测试的示例文档。\n\n第二段用来验证上传链路。".encode("utf-8"))
    response = client.post(
        "/api/documents",
        data={"file": (sample, "smoke_sample.md")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.data
    uploaded = response.get_json()["document"]
    print("[上传] 成功:", uploaded["name"], uploaded["size_human"], "-> doc_id =", uploaded["doc_id"])

    response = client.get("/api/documents")
    listing = response.get_json()
    print("[列表] 文档数:", listing["count"], "| 最新:", listing["items"][-1]["name"])

    response = client.delete(f"/api/documents/{uploaded['doc_id']}")
    assert response.status_code == 200, response.data
    print("[删除] 已清理测试文档")

    print("-" * 62)
    print("步骤 1 通过：后端骨架、健康检查、上传链路都可用。")
    print(json.dumps(health["capabilities"], ensure_ascii=False, indent=2))
    return 0


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


if __name__ == "__main__":
    raise SystemExit(main())
