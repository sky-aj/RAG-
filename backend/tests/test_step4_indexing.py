"""步骤 4 \u7684\u5355\u5143\u6d4b\u8bd5\u3002

\u8bbe\u8ba1\u539f\u5219\uff1a\u4e0d\u8054\u7f51\u3001\u4e0d\u78b0\u771f\u5b9e\u5411\u91cf\u5e93\u3002
  * HTTP \u7528\u5047\u5ba2\u6237\u7aef\u66ff\u6362\uff0c\u6d4b\u8bd5\u91cd\u8bd5 / \u62c6\u6279 / \u987a\u5e8f
  * \u5411\u91cf\u5e93\u7528\u56fa\u5b9a\u5411\u91cf\u76f4\u63a5\u5199\uff0c\u6d4b\u8bd5\u5199\u5165 / \u68c0\u7d22 / \u8fc7\u6ee4 / \u5220\u9664
"""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.ingest import parse_file
from app.ingest.chunking import chunk_document
from app.retrieval import embedder as embedder_module
from app.retrieval import indexer, vector_store

SAMPLES = settings.backend_dir / "data" / "samples"


# ---------- 拼给嵌入模型的文本 ----------

def _sample_chunks(name: str):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"\u7f3a\u5c11\u6837\u4f8b {name}\uff0c\u5148\u8dd1 scripts/make_samples.py")
    # 每份样例用自己的 doc_id：都用同一个的话，按文档过滤就区分不了。
    document = parse_file(path, "doc-" + path.stem.replace(" ", "-"), name)
    return chunk_document(document).chunks


def test_embedding_text_keeps_locators():
    """\u6807\u9898\u8def\u5f84\u4e0e\u51fd\u6570\u7b7e\u540d\u662f\u5f88\u5f3a\u7684\u68c0\u7d22\u7ebf\u7d22\uff0c\u4e0d\u80fd\u4e22\u3002"""
    chunks = _sample_chunks("quota.py")
    target = next(chunk for chunk in chunks if chunk.symbol.startswith("class"))
    text = indexer.chunk_embedding_text(target)
    assert target.symbol in text
    assert target.text in text


def test_embedding_text_includes_heading_path():
    chunks = _sample_chunks("\u4ea7\u54c1\u9700\u6c42\u6587\u6863.md")
    target = next(chunk for chunk in chunks if chunk.heading_path)
    text = indexer.chunk_embedding_text(target)
    assert target.heading_path[-1] in text
    assert text.endswith(target.text)


# ---------- point id ----------

def test_point_id_is_stable_and_distinct():
    a = vector_store.point_id_for("doc-0001-abcdef")
    assert a == vector_store.point_id_for("doc-0001-abcdef")
    assert a != vector_store.point_id_for("doc-0002-abcdef")
    assert 0 <= a < 2 ** 63


# ---------- 假 HTTP：重试与拆批 ----------

class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """记录每次请求，并按预设脚本返回。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, _url, json=None, headers=None):  # noqa: A002 - \u5bf9\u9f50 httpx \u7b7e\u540d
        inputs = list((json or {}).get("input", []))
        self.calls.append(inputs)
        return self._handler(len(inputs), inputs)


def _install_fake_client(monkeypatch, handler):
    """每次 httpx.Client(...) 都返回同一个假客户端，
    这样 calls 里才能看到跨请求的完整调用序列。"""
    client = _FakeClient(handler)
    monkeypatch.setattr(embedder_module.httpx, "Client", lambda *_a, **_k: client)
    return client


def test_embed_texts_preserves_order(monkeypatch):
    """\u8fdc\u7aef\u53ef\u80fd\u4e0d\u6309\u987a\u5e8f\u8fd4\u56de\uff0c\u5fc5\u987b\u6309 index \u91cd\u6392\u3002"""

    def handler(count, inputs):
        rows = [
            {"index": index, "embedding": [float(index)] * 4}
            for index in reversed(range(count))
        ]
        return _FakeResponse(200, {"data": rows})

    _install_fake_client(monkeypatch, handler)
    vectors = embedder_module.embed_texts(["a", "b", "c"])
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


def test_embed_texts_splits_batch_on_failure(monkeypatch):
    """\u6574\u6279\u88ab\u62d2\u5c31\u62c6\u534a\uff0c\u6700\u7ec8\u4ecd\u7136\u62ff\u5230\u5168\u90e8\u5411\u91cf\u3002"""

    def handler(count, inputs):
        if count > 2:
            return _FakeResponse(400, text="batch too large")
        return _FakeResponse(200, {"data": [
            {"index": index, "embedding": [float(index)] * 4} for index in range(count)
        ]})

    client = _install_fake_client(monkeypatch, handler)
    vectors = embedder_module.embed_texts(["a", "b", "c", "d"], batch_size=4)
    assert len(vectors) == 4
    # \u7b2c\u4e00\u6b21\u6574\u6279\uff084 \u6761\uff09\uff0c\u7136\u540e\u62c6\u6210 2 + 2
    assert [len(call) for call in client.calls] == [4, 2, 2]


def test_embed_texts_blank_becomes_space(monkeypatch):
    """\u7a7a\u4e32\u7edf\u4e00\u6362\u6210\u7a7a\u683c\uff0c\u907f\u514d\u8fdc\u7aef\u5bf9\u7a7a\u4e32\u884c\u4e3a\u4e0d\u4e00\u81f4\u3002"""

    def handler(count, inputs):
        assert all(text.strip() == "" or text == " " for text in inputs)
        return _FakeResponse(200, {"data": [{"index": i, "embedding": [0.0] * 4} for i in range(count)]})

    client = _install_fake_client(monkeypatch, handler)
    embedder_module.embed_texts(["", "   "])
    assert client.calls == [[" ", " "]]


def test_embed_texts_raises_without_key(monkeypatch):
    """没配 Key 时要报人能看懂的错，不是厄尔死在底层。

    这里不能用 monkeypatch.setattr：frozen dataclass 不允许赋值，
    直接换一个配置对象更直接。
    """
    from dataclasses import replace

    stripped = replace(settings, siliconflow=replace(settings.siliconflow, api_key=""))
    monkeypatch.setattr(embedder_module, "settings", stripped)
    with pytest.raises(embedder_module.SiliconFlowError, match="SILICONFLOW_API_KEY"):
        embedder_module.embed_texts(["x"])


# ---------- 向量库往返（固定向量，不联网） ----------

def _fake_vector(seed: float, dimension: int = 8) -> list[float]:
    vector = [0.0] * dimension
    vector[int(seed) % dimension] = 1.0
    return vector


def test_vector_store_round_trip():
    chunks = _sample_chunks("\u8fd0\u7ef4\u7b14\u8bb0.txt")
    assert chunks, "\u6837\u4f8b\u5e94\u8be5\u80fd\u5207\u51fa\u5757"

    vector_store.reset()
    vectors = [_fake_vector(float(index)) for index in range(len(chunks))]
    written = vector_store.upsert_chunks(chunks, vectors)
    assert written["written"] == len(chunks)
    assert vector_store.count() == len(chunks)

    # \u7528\u7b2c\u4e00\u5757\u7684\u5411\u91cf\u53bb\u67e5\uff0c\u5b83\u5e94\u8be5\u6392\u7b2c\u4e00
    hits = vector_store.search(_fake_vector(0.0), top_n=5)
    assert hits, "\u5e94\u8be5\u80fd\u68c0\u7d22\u5230"
    assert hits[0]["chunk_id"] == chunks[0].chunk_id
    assert hits[0]["doc_name"] == chunks[0].doc_name
    assert hits[0]["line_start"] == chunks[0].line_start


def test_payload_keeps_display_fields():
    """\u6b65\u9aa4 5 \u62fc\u4e0a\u4e0b\u6587\u53ea\u9760 payload\uff0c\u5b57\u6bb5\u7f3a\u4e00\u4e2a\u5c31\u8981\u56de\u5934\u89e3\u6790\u539f\u6587\u4ef6\u3002"""
    chunks = _sample_chunks("quota.py")
    vector_store.reset()
    vector_store.upsert_chunks(chunks, [_fake_vector(float(index)) for index in range(len(chunks))])

    hit = vector_store.search(_fake_vector(0.0), top_n=1)[0]
    for field in (
        "chunk_id", "doc_id", "doc_name", "text", "line_start", "line_end",
        "language", "symbol", "summary", "kind", "strategy", "index", "heading_path",
    ):
        assert field in hit, f"payload \u7f3a\u5b57\u6bb5\uff1a{field}"


def test_search_filter_and_delete_by_document():
    notes = _sample_chunks("\u8fd0\u7ef4\u7b14\u8bb0.txt")
    code = _sample_chunks("quota.py")

    vector_store.reset()
    vector_store.upsert_chunks(notes, [_fake_vector(0.0)] * len(notes))
    vector_store.upsert_chunks(code, [_fake_vector(0.0)] * len(code))
    assert vector_store.count() == len(notes) + len(code)

    # 只看代码那份：过滤后笔记一条都不该出来
    only_code = vector_store.search(_fake_vector(0.0), top_n=50, doc_ids=[code[0].doc_id])
    assert len(only_code) == len(code)
    assert {hit["doc_name"] for hit in only_code} == {"quota.py"}

    # 两份一起过滤，两份都能出来
    both = vector_store.search(_fake_vector(0.0), top_n=50, doc_ids=[code[0].doc_id, notes[0].doc_id])
    assert {hit["doc_name"] for hit in both} == {"quota.py", "\u8fd0\u7ef4\u7b14\u8bb0.txt"}

    removed = vector_store.delete_document(code[0].doc_id)
    assert removed == len(code)
    assert vector_store.count() == len(notes)


def test_dimension_mismatch_is_reported():
    chunks = _sample_chunks("\u8fd0\u7ef4\u7b14\u8bb0.txt")
    vector_store.reset()
    vector_store.upsert_chunks(chunks, [_fake_vector(0.0, 8)] * len(chunks))
    with pytest.raises(vector_store.VectorStoreError, match="\u7ef4\u5ea6"):
        vector_store.upsert_chunks(chunks, [_fake_vector(0.0, 16)] * len(chunks))


def test_upsert_rejects_mismatched_lengths():
    chunks = _sample_chunks("\u8fd0\u7ef4\u7b14\u8bb0.txt")
    with pytest.raises(vector_store.VectorStoreError, match="\u4e0d\u4e00\u81f4"):
        vector_store.upsert_chunks(chunks, [_fake_vector(0.0)])


# ---------- 入库编排 ----------

def test_build_chunks_reports_missing_file(scratch_dir, monkeypatch):
    """\u4e00\u4efd\u6587\u4ef6\u4e22\u4e86\u4e0d\u80fd\u8ba9\u6574\u6279\u5165\u5e93\u505c\u4e0b\u3002"""
    # upload_dir 是 ServerConfig 上的计算属性（每次读环境变量），
    # 所以控制它的办法是改 UPLOAD_DIR，而不是改属性。
    monkeypatch.setenv("UPLOAD_DIR", str(scratch_dir))
    records = [
        {"doc_id": "missing", "name": "\u4e0d\u5b58\u5728.txt", "stored_name": "nope.txt"},
    ]
    chunks, per_document = indexer.build_chunks(records)
    assert chunks == []
    assert per_document[0]["ok"] is False
    assert "\u4e22\u5931" in per_document[0]["error"]


def test_index_state_round_trip(isolated_state):
    path = isolated_state.index.state_path
    indexer.save_state({"documents": {"d1": {"doc_name": "示例"}}, "last_run": "now"})
    assert path.exists(), "状态文件应该写到配置指定的位置"
    assert json.loads(path.read_text(encoding="utf-8"))["documents"]["d1"]["doc_name"] == "示例"
    assert indexer.load_state()["documents"]["d1"]["doc_name"] == "示例"


def test_index_state_survives_corrupt_file(isolated_state):
    path = isolated_state.index.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是 json", encoding="utf-8")
    assert indexer.load_state() == {"documents": {}, "last_run": None}


def test_real_data_files_are_not_the_default_in_tests(isolated_state):
    """回归：测试不能碰真实的 data/documents.json 与 data/index_state.json。

    这两个文件一旦被测试改掉，页面上就会凭空多出“待入库”的文档，
    而且很难查到是谁改的。
    """
    real_dir = settings.backend_dir / "data"
    real_files = {real_dir / "documents.json", real_dir / "index_state.json"}
    for path in (isolated_state.index.state_path, isolated_state.index.registry_path):
        assert path not in real_files, f"测试不能直接写真实文件：{path}"
        # 必须落在 tmp/ 下，不能是 data/ 直接下面的文件
        assert "tmp" in path.parts, f"应该放在临时目录，而不是：{path}"


def test_index_writes_chunk_count_back_to_registry(scratch_dir, monkeypatch):
    """回归：入库后前端列表里的“块”列不能一直是 0。

    检查的是真实路径：切分 -> 写入向量库 -> 把块数回写注册表。
    向量化那一步用假向量替换，不联网。
    """
    from app.api import documents as documents_api

    note = SAMPLES / "运维笔记.txt"
    if not note.exists():
        pytest.skip("缺少样例，先跑 scripts/make_samples.py")

    upload = scratch_dir / "note.txt"
    upload.write_text(note.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("DOCUMENT_REGISTRY_PATH", str(scratch_dir / "documents.json"))
    documents_api.save_registry([{
        "doc_id": "doc-note",
        "name": "运维笔记.txt",
        "stored_name": "note.txt",
        "suffix": ".txt",
    }])
    monkeypatch.setenv("UPLOAD_DIR", str(scratch_dir))

    monkeypatch.setattr(indexer, "embed_chunks", lambda chunks: [_fake_vector(0.0)] * len(chunks))
    monkeypatch.setenv("INDEX_STATE_PATH", str(scratch_dir / "index_state.json"))

    vector_store.reset()
    record = documents_api.load_registry()[0]
    report = indexer.index_documents([record], force=True)

    assert report["written"] > 0
    assert documents_api.load_registry()[0]["chunks"] == report["written"]


def test_remove_document_clears_registry_field(scratch_dir, monkeypatch):
    """删文档时，已入库的那份记录要一并清掉。"""
    from dataclasses import replace as _replace

    store_dir = scratch_dir / "state"
    store_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INDEX_STATE_PATH", str(store_dir / "index_state.json"))

    vector_store.reset()
    chunks = _sample_chunks("运维笔记.txt")
    vector_store.upsert_chunks(chunks, [_fake_vector(0.0)] * len(chunks))
    indexer.save_state({"documents": {chunks[0].doc_id: {"doc_name": "运维笔记.txt"}}, "last_run": None})

    removed = indexer.remove_document(chunks[0].doc_id)
    assert removed == len(chunks)
    assert chunks[0].doc_id not in indexer.load_state()["documents"]
