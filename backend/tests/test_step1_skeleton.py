"""pytest 版本，和 scripts/smoke_step1.py 覆盖同样的范围。"""
from __future__ import annotations

import io

import pytest

from app import create_app



def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["config"]["embedding_dimension"] == 1024
    assert "dependencies" in payload


def test_upload_and_delete(client):
    payload = io.BytesIO("测试内容".encode("utf-8"))
    response = client.post(
        "/api/documents",
        data={"file": (payload, "unit_test.md")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    doc_id = response.get_json()["document"]["doc_id"]

    assert any(item["doc_id"] == doc_id for item in client.get("/api/documents").get_json()["items"])
    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert client.delete(f"/api/documents/{doc_id}").status_code == 404


def test_rejects_unknown_suffix(client):
    payload = io.BytesIO(b"binary")
    response = client.post(
        "/api/documents",
        data={"file": (payload, "weird.exe")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_capabilities_match_installed_steps(client):
    """步骤 1-5 都已接好（步骤 5 的检索与生成依赖已装、Key 已配）。"""
    caps = client.get("/api/health").get_json()["capabilities"]
    assert caps["step1_skeleton"] is True
    assert caps["step2_documents"] is True
    assert caps["step3_chunking"] is True
    assert caps["step4_embedding"] is True
    assert caps["step4_qdrant"] is True
    # 步骤 5：jieba + rank_bm25 已装，检索能力必须为 True
    assert caps["step5_retrieval"] is True
    # 对话还要求智谱 Key 与向量化都就绪；开发环境 .env 已配，因此为 True
    assert caps["step5_llm_config"] is True
    assert caps["step5_chat"] is True
    # 旧字段已移除，避免前端误判
    assert "step5_generation" not in caps


def test_capabilities_gate_on_real_dependencies():
    """回归：能力探测不能把“配置填了”当成“依赖装了”。"""
    from app.api.health import _capabilities

    bare = {"httpx": {"installed": False}, "qdrant_client": {"installed": False}}
    caps = _capabilities(bare)
    assert caps["step4_embedding"] is False
    assert caps["step4_qdrant"] is False
    assert caps["step5_chat"] is False

    full = {
        "httpx": {"installed": True},
        "qdrant_client": {"installed": True},
        "jieba": {"installed": True},
        "rank_bm25": {"installed": True},
        "pypdf": {"installed": True},
        "docx": {"installed": True},
        "markdown_it": {"installed": True},
        "openpyxl": {"installed": True},
        "tree_sitter": {"installed": True},
    }
    assert _capabilities(full)["step4_embedding"] is True


def test_stage_label_inputs_are_consistent(client):
    """前端顶栏徽标靠这些字段推导进度，缺一个就会显示错误。"""
    caps = client.get("/api/health").get_json()["capabilities"]
    for step in (1, 2, 3, 4, 5):
        assert any(key.startswith("step%d_" % step) for key in caps), "step%d 缺能力字段" % step
