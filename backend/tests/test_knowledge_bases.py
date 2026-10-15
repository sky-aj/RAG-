"""分类与普通问答回归：真实隔离 Qdrant，假 embedding 和生成。"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app import knowledge, rag
from app.api import documents
from app.llm import client as llm
from app.retrieval import bm25, embedder, indexer, vector_store


@pytest.fixture()
def kb_state(isolated_state, scratch_dir, monkeypatch):
    uploads = scratch_dir / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    assert "tmp" in vector_store.settings.qdrant.path.parts
    vector_store.reset()
    bm25.invalidate()
    monkeypatch.setattr(indexer, "embed_chunks", lambda chunks: [[1.0] + [0.0] * 1023 for _ in chunks])
    monkeypatch.setattr(embedder, "embed_query", lambda _: [1.0] + [0.0] * 1023)
    yield scratch_dir
    bm25.invalidate()


def label(client, name):
    response = client.post("/api/knowledge-bases", json={"name": name})
    assert response.status_code == 201
    return response.get_json()["item"]["id"]


def upload(client, kb_id, text, name="焊接.txt"):
    response = client.post("/api/documents", data={"kb_id": kb_id, "file": (io.BytesIO(text.encode()), name)})
    assert response.status_code in (200, 201)
    return response.get_json()


def events(response):
    return [json.loads(block[5:]) for block in response.get_data(as_text=True).split("\n\n") if block.startswith("data:")]


def test_labels_persist_and_validate_names(client, kb_state):
    a = label(client, "焊接培训")
    b = label(client, "生产工艺")
    assert knowledge.registry_path().parent == kb_state
    assert {a, b} <= {item["id"] for item in client.get("/api/knowledge-bases").get_json()["items"]}
    assert client.post("/api/knowledge-bases", json={"name": " 焊接培训 "}).status_code == 409
    assert client.post("/api/knowledge-bases", json={"name": "全部文档"}).status_code == 409
    assert client.post("/api/knowledge-bases", json={"name": " "}).status_code == 400
    assert client.patch("/api/knowledge-bases/" + a, json={"name": "教育"}).status_code == 200
    assert next(item for item in knowledge.load_labels() if item["id"] == a)["name"] == "教育"
    assert client.delete("/api/knowledge-bases/" + b).status_code == 200
    assert client.delete("/api/knowledge-bases/all").status_code == 400


def test_corrupt_label_file_is_reported_without_overwrite(client, kb_state):
    path = knowledge.registry_path()
    path.write_text("broken", encoding="utf-8")
    response = client.post("/api/knowledge-bases", json={"name": "new"})
    assert response.status_code == 500
    assert "无法读取" in response.get_json()["error"]
    assert path.read_text() == "broken"


def test_classification_filters_both_routes_and_move_reuses_vectors(client, kb_state):
    a, b = label(client, "A"), label(client, "B")
    first = upload(client, a, "焊接培训通过虚拟焊接帮助学生学习操作技能。\n焊接培训包括安全教育和练习。")
    second = upload(client, b, "焊接生产工艺采用焊接电流参数控制。\n焊接工艺应用于产品生产。")
    upload(client, "unclassified", "财务预算包括收入支出、资金管理和报表核算。", "财务.txt")
    d1, d2 = first["document"]["doc_id"], second["document"]["doc_id"]
    assert client.post("/api/index", json={"kb_id": a}).get_json()["written"] > 0
    assert client.post("/api/index", json={"kb_id": b}).get_json()["written"] > 0
    assert client.post("/api/index", json={"kb_id": "unclassified"}).get_json()["written"] > 0
    total = vector_store.count()
    for kb_id, expected, keyword in ((a, d1, "学生"), (b, d2, "生产")):
        semantic = client.post("/api/search", json={"kb_id": kb_id, "query": "焊接"}).get_json()
        assert semantic["hits"] and {item["doc_id"] for item in semantic["hits"]} == {expected}
        ids = knowledge.resolve_scope({"kb_id": kb_id})
        assert {item["doc_id"] for item in bm25.search(keyword, doc_ids=ids)} == {expected}
        mixed = client.post("/api/retrieve", json={"kb_id": kb_id, "question": "焊接", "rerank": False}).get_json()
        assert {item["doc_id"] for item in mixed["citations"]} == {expected}
    assert client.delete("/api/knowledge-bases/" + a).status_code == 409
    assert client.patch("/api/documents/" + d1, json={"kb_id": b}).status_code == 200
    assert vector_store.count() == total
    assert client.get("/api/documents?kb_id=" + a).get_json()["items"] == []
    assert set(knowledge.resolve_scope({"kb_id": b})) == {d1, d2}
    assert next(item for item in knowledge.list_labels() if item["id"] == a)["docs"] == 0
    moved = client.post("/api/retrieve", json={"kb_id": b, "question": "学生", "rerank": False}).get_json()
    assert d1 in {item["doc_id"] for item in moved["citations"]}
    assert client.post("/api/retrieve", json={"kb_id": a, "question": "学生"}).get_json()["citations"] == []


def test_heading_only_word_can_be_indexed_and_retrieved_after_move(client, kb_state, monkeypatch):
    from docx import Document
    doc = Document()
    headings = ["创新创业大赛省赛银奖", "挑战杯省赛铜奖", "机器人开发者大赛三等奖"]
    for heading in headings:
        doc.add_heading(heading, level=1)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    response = client.post("/api/documents", data={"kb_id": "unclassified", "file": (buffer, "奖.docx")})
    assert response.status_code == 201
    doc_id = response.get_json()["document"]["doc_id"]
    destination = label(client, "简历")
    assert client.patch("/api/documents/" + doc_id, json={"kb_id": destination}).status_code == 200
    before = events(client.post("/api/chat", json={"kb_id": destination, "question": "获得哪些奖项？"}))
    assert "有 1 份文件" in before[1]["text"]
    assert "增量入库" in before[1]["text"]
    report = client.post("/api/index", json={"kb_id": destination}).get_json()
    assert report["ok"] and report["written"] == len(headings)
    chunks, _ = indexer.build_chunks(documents.load_registry())
    assert [chunk.text for chunk in chunks] == headings
    assert [chunk.line_start for chunk in chunks] == sorted(chunk.line_start for chunk in chunks)
    assert all(chunk.heading_path[-1] == chunk.text for chunk in chunks)
    def stream(messages, **kwargs):
        assert "创新创业大赛省赛银奖" in messages[0]["content"]
        yield {"type": "content", "text": "获得省赛银奖【片段 1】"}
        yield {"type": "done", "finish_reason": "stop"}
    monkeypatch.setattr(llm, "stream_chat_until_content", stream)
    answer = events(client.post("/api/chat", json={"kb_id": destination, "question": "创新创业奖项", "rerank": False}))
    assert answer[0]["citations"]
    assert {item["doc_name"] for item in answer[0]["citations"]} == {"奖.docx"}
    assert answer[-1]["finish_reason"] == "stop"


def test_zero_chunk_state_is_not_reported_as_indexed(client, kb_state):
    record = upload(client, "unclassified", "空白文档")["document"]
    indexer.save_state({"documents": {record["doc_id"]: {"doc_name": record["name"], "chunks": 0}}})
    assert next(item for item in knowledge.list_labels() if item["id"] == "unclassified")["indexed"] == 0
    status = client.get("/api/index?kb_id=unclassified").get_json()
    assert status["indexed_count"] == status["scope"]["indexed_count"] == 0
    (documents.settings.server.upload_dir / record["stored_name"]).write_text("", encoding="utf-8")
    result = client.post("/api/index", json={"kb_id": "unclassified"}).get_json()
    assert result["ok"] is False
    assert result["written"] == 0
    assert "未生成可检索片段" in result["failed"][0]["error"]
    assert record["doc_id"] not in indexer.load_state()["documents"]


def test_duplicate_upload_keeps_owner_and_index_state(client, kb_state):
    a, b = label(client, "A"), label(client, "B")
    text = "焊接培训基础材料，帮助学习安全操作。"
    first = upload(client, a, text)
    assert client.post("/api/index", json={"kb_id": a}).status_code == 200
    total = vector_store.count()
    duplicate = upload(client, b, text, "改名.txt")
    assert duplicate["duplicate"] is True
    assert duplicate["document"]["kb_id"] == a
    assert duplicate["document"]["chunks"] > 0
    assert vector_store.count() == total
    assert duplicate["document"]["doc_id"] == first["document"]["doc_id"]


def test_empty_classification_never_calls_models_or_resets_store(client, kb_state, monkeypatch):
    a, empty = label(client, "A"), label(client, "空分类")
    upload(client, a, "已入库的焊接技术培训资料。")
    client.post("/api/index", json={"kb_id": a})
    total = vector_store.count()
    def forbidden(*args, **kwargs):
        raise AssertionError("空分类不能调用模型或重建")
    monkeypatch.setattr(embedder, "embed_query", forbidden)
    monkeypatch.setattr(indexer, "embed_chunks", forbidden)
    monkeypatch.setattr(llm, "stream_chat_until_content", forbidden)
    assert client.post("/api/search", json={"kb_id": empty, "query": "焊接"}).get_json()["hits"] == []
    assert client.post("/api/retrieve", json={"kb_id": empty, "question": "焊接", "rewrite": True}).get_json()["hits"] == []
    answer = events(client.post("/api/chat", json={"kb_id": empty, "question": "焊接"}))
    assert [item["type"] for item in answer] == ["sources", "content", "done"]
    assert answer[-1]["finish_reason"] == "no_context"
    assert client.post("/api/index", json={"kb_id": empty}).get_json()["written"] == 0
    assert client.delete("/api/index", json={"kb_id": empty}).get_json()["removed"] == 0
    assert client.delete("/api/index", json={"doc_id": []}).status_code == 400
    assert vector_store.count() == total
    assert client.post("/api/chat", json={"kb_id": "missing", "question": "q"}).status_code == 404


def test_unindexed_classification_skips_paid_retrieval_calls(client, kb_state, monkeypatch):
    a, b = label(client, "尚未入库"), label(client, "已有向量")
    upload(client, a, "培训资料还没有向量。")
    upload(client, b, "生产资料已经入库。")
    client.post("/api/index", json={"kb_id": b})
    def forbidden(*args, **kwargs):
        raise AssertionError("未入库分类不能调用 embedding 或生成")
    monkeypatch.setattr(embedder, "embed_query", forbidden)
    monkeypatch.setattr(llm, "stream_chat_until_content", forbidden)
    assert client.post("/api/search", json={"kb_id": a, "query": "培训"}).get_json()["hits"] == []
    assert client.post("/api/retrieve", json={"kb_id": a, "question": "培训", "rewrite": True}).get_json()["hits"] == []
    assert events(client.post("/api/chat", json={"kb_id": a, "question": "培训"}))[-1]["finish_reason"] == "no_context"


def test_clear_classification_keeps_other_vectors_and_resets_registry(client, kb_state):
    a, b = label(client, "A"), label(client, "B")
    first = upload(client, a, "焊接培训入门。")["document"]
    second = upload(client, b, "焊接工艺生产。")["document"]
    client.post("/api/index", json={"kb_id": "all"})
    response = client.delete("/api/index", json={"kb_id": a})
    assert response.status_code == 200
    assert vector_store.count_document(first["doc_id"]) == 0
    assert vector_store.count_document(second["doc_id"]) > 0
    rows = {item["doc_id"]: item for item in documents.load_registry()}
    assert rows[first["doc_id"]]["chunks"] == 0
    assert rows[second["doc_id"]]["chunks"] > 0
    assert client.delete("/api/index", json={"reset_all": True, "kb_id": a}).status_code == 400
    assert client.delete("/api/index", json={"reset_all": True}).status_code == 200
    assert vector_store.count() == 0


def test_ordinary_chat_has_no_retrieval_dependency(client, kb_state, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("普通问答不能访问检索")
    monkeypatch.setattr(vector_store, "count", forbidden)
    monkeypatch.setattr(rag, "retrieve", forbidden)
    captured = []
    def stream(messages, **kwargs):
        captured.extend(messages)
        yield {"type": "notice", "text": "已切换至 GLM"}
        yield {"type": "content", "text": "**普通回答**"}
        yield {"type": "done", "actual_model": "glm-4-flash", "finish_reason": "stop", "usage": None}
    monkeypatch.setattr(llm, "stream_chat_until_content", stream)
    response = client.post("/api/chat", json={"question": "你好", "kb_id": "deleted",
                           "use_knowledge_base": False, "history": [{"role": "user", "content": "本会话历史"}]})
    answer = events(response)
    assert answer[0]["knowledge_base_used"] is False
    assert answer[0]["citations"] == []
    assert [item["type"] for item in answer] == ["sources", "notice", "content", "done"]
    assert "本次没有检索" in captured[0]["content"]
    assert captured[1]["content"] == "本会话历史"
    assert answer[-1]["actual_model"] == "glm-4-flash"


def test_cleanup_failure_can_be_retried_and_is_excluded(client, kb_state, monkeypatch):
    a = label(client, "A")
    record = upload(client, a, "需要删除的文档。")["document"]
    remove = indexer.remove_document
    monkeypatch.setattr(indexer, "remove_document", lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
    assert client.delete("/api/documents/" + record["doc_id"]).status_code == 503
    assert documents.load_registry()[0]["deletion_pending"] is True
    assert knowledge.resolve_scope({"kb_id": "all"}) == []
    monkeypatch.setattr(indexer, "remove_document", remove)
    assert client.delete("/api/documents/" + record["doc_id"]).status_code == 200
    assert documents.load_registry() == []


def test_forced_reindex_removes_old_points_only_in_scope(client, kb_state):
    a, b = label(client, "A"), label(client, "B")
    first = upload(client, a, "焊接原始内容。" * 80)["document"]
    second = upload(client, b, "保留的生产资料。")["document"]
    client.post("/api/index", json={"kb_id": "all"})
    other_count = vector_store.count_document(second["doc_id"])
    (documents.settings.server.upload_dir / first["stored_name"]).write_text("新内容，仅一个块。", encoding="utf-8")
    assert client.post("/api/index", json={"kb_id": a, "force": True}).status_code == 200
    assert vector_store.count_document(first["doc_id"]) == 1
    assert vector_store.count_document(second["doc_id"]) == other_count


def test_full_reset_stays_empty_after_reopening_embedded_store(client, kb_state):
    record = upload(client, "unclassified", "验证全库清空持久化的资料。")["document"]
    client.post("/api/index", json={"kb_id": "all"})
    assert vector_store.count() > 0
    chunks, _ = indexer.build_chunks([record])
    vector_store.reset()
    vector_store.close_client()
    assert vector_store.count() == 0
    vector_store.upsert_chunks(chunks, [[1.0] + [0.0] * 7 for _ in chunks])
    assert vector_store.count() == len(chunks)
    assert vector_store.info()["dimension"] == 8


def test_old_records_are_unclassified_and_empty_low_level_scope_is_empty(client, kb_state, monkeypatch):
    documents.save_registry([{"doc_id": "old", "name": "旧文档"}])
    assert knowledge.resolve_scope({"kb_id": "unclassified"}) == ["old"]
    assert knowledge.resolve_scope({"doc_id": []}) is None
    def forbidden(*args, **kwargs):
        raise AssertionError("空范围不应打开索引")
    monkeypatch.setattr(bm25, "_ensure_index", forbidden)
    monkeypatch.setattr(vector_store, "get_client", forbidden)
    assert bm25.search("test", doc_ids=[]) == []
    assert vector_store.search([1.0], doc_ids=[]) == []
