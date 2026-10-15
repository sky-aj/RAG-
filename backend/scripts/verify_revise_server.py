"""仅本地隔离验证：真实 Flask/Qdrant，固定假向量与生成，无付费请求。"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRATCH = BACKEND / "data" / "tmp" / ("revise-ui-20261013-" + uuid.uuid4().hex[:8])
SCRATCH.mkdir(parents=True)
for key, value in {
    "QDRANT_PATH": SCRATCH / "qdrant", "DOCUMENT_REGISTRY_PATH": SCRATCH / "documents.json",
    "INDEX_STATE_PATH": SCRATCH / "index_state.json", "UPLOAD_DIR": SCRATCH / "uploads",
    "KNOWLEDGE_BASE_REGISTRY_PATH": SCRATCH / "knowledge_bases.json",
    "EVAL_DATASET_PATH": SCRATCH / "eval/dataset.json", "EVAL_REPORT_DIR": SCRATCH / "eval",
    "QDRANT_MODE": "embedded", "QDRANT_COLLECTION": "revise_validation",
    "LANGSMITH_TRACING": "false", "QUERY_REWRITE_ENABLED": "false",
    "LLM_API_KEY": "offline-validation", "LLM_EMIT_REASONING": "true",
    "LLM_MIN_INTERVAL": "0", "EMBEDDING_MIN_INTERVAL": "0",
}.items():
    os.environ[key] = str(value)
sys.path.insert(0, str(BACKEND))

import httpx
from flask import jsonify, send_from_directory
from app import create_app
from app.llm import client as llm
from app.retrieval import embedder, indexer, rerank, vector_store

def blocked(*args, **kwargs):
    raise AssertionError("隔离验证禁止外部 HTTP 请求")
httpx.Client.send = blocked
httpx.AsyncClient.send = blocked
indexer.embed_chunks = lambda chunks: [[1.0] + [0.0] * 1023 for _ in chunks]
embedder.embed_query = lambda _: [1.0] + [0.0] * 1023
rerank.rerank = lambda _query, texts, top_n=None: [{"index": i, "score": 0.95 - i * 0.02} for i in range(len(texts))]

def offline_stream(messages, **kwargs):
    knowledge = "【参考片段】" in messages[0]["content"]
    fallback = "回退" in messages[-1]["content"]
    if fallback:
        yield {"type": "notice", "text": "模拟主模型暂不可用，已切换至 glm-4-flash", "target_model": "glm-4-flash"}
    text = ("- **教育与培训**：作为焊接技术培训工具，帮助学生学习焊接操作技能【片段 1】\n\n"
            "1. 操作训练\n2. 安全教育\n\n```text\n【片段 1】应当保持为代码\n```\n\n"
            "| 工具 | 用途 |\n| --- | --- |\n| 焊接模拟 | 学生培训 |\n\n"
            "<img src=x onerror=alert(1)>\n\n[危险链接](javascript:alert(1))") if knowledge else "**普通回答**：本次没有检索知识库。"
    for start in range(0, len(text), 18):
        yield {"type": "content", "text": text[start:start + 18]}
    yield {"type": "done", "finish_reason": "stop", "usage": None,
           "actual_model": "glm-4-flash" if fallback else "deepseek-v4-flash", "actual_provider": "offline-validation",
           "attempts": [{"model": "glm-4-flash" if fallback else "deepseek-v4-flash", "usage": None}]}
llm.stream_chat_until_content = offline_stream

app = create_app()
DIST = BACKEND.parent / "frontend" / "dist"
@app.get("/")
def frontend():
    return send_from_directory(DIST, "index.html")
@app.get("/assets/<path:filename>")
def asset(filename):
    return send_from_directory(DIST / "assets", filename)
@app.get("/__validation/status")
def status():
    return jsonify({"isolated": True, "scratch": str(SCRATCH), "points": vector_store.count(), "fake_models": True})

if __name__ == "__main__":
    print("ISOLATED_REVISION_VALIDATION " + str(SCRATCH), flush=True)
    app.run(host="127.0.0.1", port=5002, threaded=True, use_reloader=False)
