"""步骤 5 的单元测试：融合、配额、上下文拼装、提示词、SSE 事件序列。

原则：不联网。
  * 检索层只测纯函数（RRF、配额、去重合并、预算）
  * /api/chat 用假 zhipu 客户端 + 假检索结果，验证 SSE 顺序与引用编号
"""
from __future__ import annotations

import json

import pytest

from app.retrieval import context as context_builder
from app.retrieval import hybrid


def hit(chunk_id: str, doc_id: str, start: int, end: int, text: str, score: float = 0.8, **extra):
    payload = {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "doc_name": extra.pop("doc_name", "产品需求文档.md"),
        "line_start": start,
        "line_end": end,
        "text": text,
        "score": score,
    }
    payload.update(extra)
    return payload


# ---------- RRF 融合 ----------

def test_rrf_rewards_items_found_in_both_routes():
    vector = [hit("a", "d1", 1, 2, "A"), hit("b", "d1", 3, 4, "B")]
    keyword = [hit("b", "d1", 3, 4, "B"), hit("c", "d2", 5, 6, "C")]

    fused = hybrid.reciprocal_rank_fusion([vector, keyword])
    order = [item["chunk_id"] for item in fused]

    assert order[0] == "b"
    assert fused[0]["routes"] == ["关键字", "语义"]
    assert set(order) == {"a", "b", "c"}


def test_rrf_keeps_first_seen_payload_and_tags_routes():
    vector = [hit("a", "d1", 1, 2, "A", score=0.7)]
    keyword = [hit("a", "d1", 1, 2, "A", score=9.9)]

    fused = hybrid.reciprocal_rank_fusion([vector, keyword])
    assert len(fused) == 1
    assert fused[0]["score"] == 0.7
    assert "rrf_score" in fused[0]


def test_rrf_weight_count_must_match_routes():
    with pytest.raises(ValueError):
        hybrid.reciprocal_rank_fusion([[hit("a", "d1", 1, 1, "A")]], weights=[1.0, 2.0])


# ---------- 每文档配额 ----------

def test_document_quota_caps_per_document_and_keeps_overflow():
    hits = [hit("c" + str(i), "d1", i, i, "t" + str(i)) for i in range(1, 6)] + [hit("x", "d2", 1, 1, "X")]
    picked, overflow = hybrid._apply_document_quota(hits, per_document=2)

    assert [item["chunk_id"] for item in picked] == ["c1", "c2", "x"]
    assert [item["chunk_id"] for item in overflow] == ["c3", "c4", "c5"]
    assert len(picked) + len(overflow) == len(hits)


# ---------- 去重与相邻合并 ----------

def test_merge_snippets_deduplicates_overlapping_lines():
    hits = [
        hit("p1", "d1", 1, 5, "第一段\n第二段"),
        hit("p2", "d1", 3, 8, "第二段\n第三段"),
    ]
    snippets = context_builder.merge_snippets(hits)

    assert len(snippets) == 1, "同文档重叠块必须合并成一段"
    merged = snippets[0]
    assert merged.line_start == 1
    assert merged.line_end == 8
    assert set(merged.chunk_ids) == {"p1", "p2"}
    assert merged.text.count("第二段") == 1


def test_merge_snippets_keeps_distant_blocks_separate():
    hits = [
        hit("p1", "d1", 1, 3, "前面"),
        hit("p2", "d1", 40, 45, "后面"),
    ]
    snippets = context_builder.merge_snippets(hits, gap=2)
    assert len(snippets) == 2


def test_merge_snippets_orders_by_score_after_merge():
    hits = [
        hit("p1", "d1", 1, 3, "低分块", score=0.2),
        hit("p2", "d2", 1, 3, "高分块", score=0.9, doc_name="其它.md"),
    ]
    snippets = context_builder.merge_snippets(hits)
    assert snippets[0].doc_name == "其它.md"


# ---------- 片段格式与预算 ----------

def test_snippet_render_follows_the_agreed_format():
    hits = [
        hit(
            "c1",
            "d1",
            27,
            61,
            "def sliding_window(): ...",
            language="python",
            symbol="def sliding_window",
            summary="滑动窗口统计请求量",
            heading_path=["限流策略"],
        )
    ]
    packed = context_builder.build_context(hits)
    text = packed["text"]

    assert "行 27-61" in text
    assert "语言 python" in text
    assert "签名：def sliding_window" in text
    assert "摘要：滑动窗口统计请求量" in text
    assert "def sliding_window" in text
    assert packed["citations"][0]["index"] == 1


def test_build_context_respects_budget_and_reports_dropped():
    hits = [hit("c" + str(i), "d" + str(i), 1, 2, "内容" * 200, score=1.0 - i * 0.01) for i in range(6)]
    packed = context_builder.build_context(hits, budget=120)

    assert packed["used"] >= 1
    assert packed["dropped"] >= 1
    assert packed["tokens"] <= 120 + 400
    assert len(packed["citations"]) == packed["used"]


# ---------- 提示词 ----------

def test_build_messages_puts_context_in_system_and_limits_history():
    from app import rag

    history = [{"role": "user", "content": "旧问题" + str(i)} for i in range(10)]
    built = rag.build_messages("新问题", "片段正文", history)

    assert built[0]["role"] == "system"
    assert "片段正文" in built[0]["content"]
    assert built[-1] == {"role": "user", "content": "新问题"}
    body = [item for item in built[1:-1] if item["role"] == "user"]
    assert len(body) <= 4


# ---------- /api/chat 的 SSE 事件序列 ----------

@pytest.fixture()
def fake_chat(monkeypatch):
    from app.retrieval import vector_store
    from app import rag as rag_module
    from app.llm import client as zhipu

    monkeypatch.setattr(vector_store, "count", lambda: 60)

    found = {
        "query": "限流额度",
        "hits": [hit("c1", "d1", 9, 10, "默认 600 次/分钟")],
        "citations": [
            {
                "index": 1,
                "doc_id": "d1",
                "doc_name": "产品需求文档.md",
                "location": "行 9-10",
                "line_start": 9,
                "line_end": 10,
                "page": None,
                "language": "",
                "symbol": "",
                "summary": "",
                "heading_path": ["1.1 默认额度"],
                "score": 0.74,
                "chunk_ids": ["c1"],
            }
        ],
        "context_text": "[片段 1] 产品需求文档.md 行 9-10\n内容：\n默认 600 次/分钟",
        "diagnostics": {"hybrid": True, "returned": 1},
    }
    monkeypatch.setattr(rag_module, "retrieve", lambda *a, **k: found)

    def fake_stream(messages, **overrides):
        yield {"type": "reasoning", "text": "先看片段 1"}
        yield {"type": "content", "text": "默认额度是"}
        yield {"type": "content", "text": " 600 次/分钟【片段 1】"}
        yield {"type": "done", "finish_reason": "stop", "usage": {"total_tokens": 42}}

    monkeypatch.setattr(zhipu, "stream_chat", fake_stream)
    return found


def _events(response) -> list:
    body = response.get_data(as_text=True)
    parsed = []
    for block in body.split("\n\n"):
        line = block.strip()
        if line.startswith("data:"):
            parsed.append(json.loads(line[5:].strip()))
    return parsed


def test_chat_stream_emits_sources_then_reasoning_then_content_then_done(client, fake_chat):
    response = client.post("/api/chat", json={"question": "限流额度是多少"})
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    events = _events(response)
    kinds = [event["type"] for event in events]

    assert kinds[0] == "sources"
    assert kinds[-1] == "done"
    assert "content" in kinds
    assert kinds.index("reasoning") < kinds.index("content")

    sources = events[0]
    assert sources["citations"][0]["index"] == 1
    assert sources["citations"][0]["doc_name"] == "产品需求文档.md"
    assert sources["diagnostics"]["returned"] == 1

    answer = "".join(event["text"] for event in events if event["type"] == "content")
    assert "600 次/分钟" in answer
    assert "【片段 1】" in answer

    done = events[-1]
    assert done["finish_reason"] == "stop"
    assert done["usage"]["total_tokens"] == 42


def test_chat_requires_question(client, fake_chat):
    response = client.post("/api/chat", json={})
    assert response.status_code == 400
    assert "question" in response.get_json()["error"]


def test_chat_failure_emits_notice_and_error_without_done(client, fake_chat, monkeypatch):
    from app.llm import client as llm

    attempts = [{"model": "deepseek-v4-flash", "usage": {"total_tokens": 80}},
                {"model": "glm-4-flash", "error": "断流", "usage": None}]

    def stream(*args, **kwargs):
        yield {"type": "notice", "text": "已切换至 glm-4-flash"}
        yield {"type": "content", "text": "部分答案"}
        raise llm.LLMError("备用断流", attempts=attempts, actual_model="glm-4-flash", actual_provider="zhipu")

    monkeypatch.setattr(llm, "stream_chat_until_content", stream)
    events = _events(client.post("/api/chat", json={"question": "q"}))
    assert [e["type"] for e in events] == ["sources", "notice", "content", "error"]
    assert events[-1]["actual_model"] == "glm-4-flash"
    assert events[-1]["attempts"] == attempts


def test_chat_without_citations_skips_generation(client, monkeypatch):
    from app.retrieval import vector_store
    from app import rag as rag_module
    from app.llm import client as zhipu

    monkeypatch.setattr(vector_store, "count", lambda: 60)
    monkeypatch.setattr(
        rag_module,
        "retrieve",
        lambda *a, **k: {
            "query": "无关问题",
            "hits": [],
            "citations": [],
            "context_text": "",
            "diagnostics": {},
        },
    )

    def boom(messages, **overrides):
        raise AssertionError("没有引用时不该调用模型")

    monkeypatch.setattr(zhipu, "stream_chat", boom)

    events = _events(client.post("/api/chat", json={"question": "无关问题"}))
    kinds = [event["type"] for event in events]
    assert kinds == ["sources", "content", "done"]
    assert events[-1]["finish_reason"] == "no_context"


def test_retrieve_endpoint_reports_empty_store(client, monkeypatch):
    from app.retrieval import vector_store

    monkeypatch.setattr(vector_store, "count", lambda: 0)
    response = client.post("/api/retrieve", json={"question": "随便问问"})
    assert response.status_code == 409
    assert "向量库" in response.get_json()["error"]

