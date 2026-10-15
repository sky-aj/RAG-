"""Authorized live checks, isolated data, controlled HTTP faults and real fallback.

Usage: python scripts/verify_live_models.py --phase long --budget 16384
       python scripts/verify_live_models.py --phase flow --run-dir data/tmp/live-...
       python scripts/verify_live_models.py --phase fallback --run-dir data/tmp/live-...
       python scripts/verify_live_models.py --phase serve --run-dir data/tmp/live-... --port 5001
No production data or .env changes. Every output is redacted before saving.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--phase", choices=("long", "flow", "evaluate", "judge-budget", "fallback", "business", "serve"), required=True)
parser.add_argument("--run-dir")
parser.add_argument("--budget", type=int, default=16384)
parser.add_argument("--port", type=int, default=5001)
args = parser.parse_args()
run_dir = Path(args.run_dir).resolve() if args.run_dir else BACKEND / "data" / "tmp" / (
    "live-validation-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
allowed_root = (BACKEND / "data" / "tmp").resolve()
if not run_dir.is_relative_to(allowed_root):
    raise SystemExit("run-dir must stay inside backend/data/tmp")
run_dir.mkdir(parents=True, exist_ok=True)
for key, value in {"QDRANT_MODE": "embedded", "QDRANT_PATH": str(run_dir / "qdrant"),
                   "DOCUMENT_REGISTRY_PATH": str(run_dir / "documents.json"),
                   "INDEX_STATE_PATH": str(run_dir / "index_state.json"), "UPLOAD_DIR": str(run_dir / "uploads"),
                   "EVAL_DATASET_PATH": str(run_dir / "eval" / "dataset.json"),
                   "EVAL_REPORT_DIR": str(run_dir / "eval"), "LANGSMITH_TRACING": "false"}.items():
    os.environ[key] = value

import httpx
from app import create_app
from app.config import settings
from app.llm import client as llm
from app.eval import ragas_eval
from app.retrieval import vector_store

logging.basicConfig(level=logging.WARNING, format="%(name)s: %(levelname)s: %(message)s")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
secrets = [settings.llm.api_key, settings.llm.fallback_api_key, settings.siliconflow.api_key,
           settings.eval.langsmith_api_key]


def safe(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return json.loads(text)


def record(name, payload, summary=None):
    payload = safe({"at": datetime.now(timezone.utc).isoformat(), **payload})
    (run_dir / (name + ".json")).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"check": name, **(safe(summary) if summary is not None else payload)}, ensure_ascii=False), flush=True)
    return payload


def live_data_hashes():
    return {name: hashlib.sha256((BACKEND / "data" / name).read_bytes()).hexdigest()
            if (BACKEND / "data" / name).exists() else None
            for name in ("documents.json", "index_state.json", "eval/latest.json")}


baseline = live_data_hashes()
print(json.dumps({"run_dir": str(run_dir), "phase": args.phase, "primary": llm.describe(),
                  "judge_model": settings.eval.judge_model}, ensure_ascii=False), flush=True)


def stream_summary(events):
    kinds = [e["type"] for e in events]
    content = "".join(e.get("text", "") for e in events if e["type"] == "content")
    terminal = [e for e in events if e["type"] in ("done", "error")]
    return {"characters": len(content), "excerpt": content[:240], "sequence": list(dict.fromkeys(kinds)),
            "done_count": kinds.count("done"), "error_count": kinds.count("error"),
            "notices": [e for e in events if e["type"] == "notice"], "terminal": terminal}


class FaultGateway:
    """Actual local HTTP gateway. Only failure mode is synthetic; providers are real."""

    def __init__(self):
        self.mode = "pass"
        self.calls = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                requested = json.loads(body)
                entry = {"mode": owner.mode, "model": requested.get("model"),
                         "max_tokens": requested.get("max_tokens", requested.get("max_completion_tokens")),
                         "stream": requested.get("stream", False)}
                owner.calls.append(entry)
                if owner.mode == "503":
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", "1")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"code":"controlled_gateway_unavailable"}}')
                    entry["status"] = 503
                    return
                if owner.mode == "disconnect":
                    self.close_connection = True
                    self.connection.close()
                    entry["status"] = "closed_without_response"
                    return
                upstream = settings.llm.base_url.rstrip("/") + "/chat/completions"
                headers = {"Authorization": "Bearer " + settings.llm.api_key, "Content-Type": "application/json"}
                try:
                    with httpx.Client(timeout=settings.llm.timeout) as http:
                        with http.stream("POST", upstream, headers=headers, content=body) as response:
                            entry["status"] = response.status_code
                            self.send_response(response.status_code)
                            self.send_header("Content-Type", response.headers.get("content-type", "application/json"))
                            self.end_headers()
                            for chunk in response.iter_bytes():
                                self.wfile.write(chunk)
                                self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    entry["client_cancelled"] = True
                except Exception as error:
                    entry["error"] = type(error).__name__

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    @contextmanager
    def active(self, mode="503"):
        self.mode = mode
        patched = replace(settings, llm=replace(settings.llm, base_url=self.url, max_retries=2))
        previous = llm.settings, ragas_eval.settings
        llm.settings = ragas_eval.settings = patched
        try:
            yield
        finally:
            llm.settings, ragas_eval.settings = previous


def long_output():
    # Verify capability-parameter acceptance independently of actual long generation.
    main = replace(settings, llm=replace(settings.llm, fallback_enabled=False, max_retries=1))
    llm.settings = main
    started = time.monotonic()
    result = llm.chat([{"role": "user", "content": "Reply with exactly READY."}],
                      max_tokens=settings.llm.max_output_tokens)
    record("capability-parameter", {"requested_budget": settings.llm.max_output_tokens,
        "seconds": round(time.monotonic() - started, 2), "actual_model": result["actual_model"],
        "usage": result["usage"], "finish_reason": result["finish_reason"],
        "has_content": bool(result["content"]), "meaning": "Parameter accepted; does not prove output at this limit."})
    prompt = ("This is an authorized output length test with synthetic data. Output 5000 distinct numbered records, "
              "one per line, starting from 00001. Each line must contain: the number, then the words "
              "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu. "
              "Do not summarize, do not abbreviate, do not use ellipses, and do not stop early. "
              "Produce all 5000 lines sequentially until the output limit. No introduction or conclusion.")
    output = run_dir / f"long-output-{args.budget}.txt"
    count = reasoning = 0
    terminal = None
    started = time.monotonic()
    last_progress = started
    stopped = None
    with output.open("w", encoding="utf-8") as saved:
        with closing(llm.stream_chat_until_content([{"role": "user", "content": prompt}],
                                                   max_tokens=args.budget, temperature=0.2)) as upstream:
            for event in upstream:
                if event["type"] == "content":
                    saved.write(event["text"])
                    count += len(event["text"])
                elif event["type"] == "reasoning":
                    reasoning += len(event["text"])
                elif event["type"] == "done":
                    terminal = event
                now = time.monotonic()
                if now - last_progress > 25:
                    print(json.dumps({"progress": "long-output", "seconds": round(now - started),
                                      "characters": count, "reasoning_characters": reasoning}), flush=True)
                    last_progress = now
                if count > 300000 or now - started > 600:
                    stopped = "Local safety bound reached; no claim of successful completion."
                    break
    record(f"long-output-{args.budget}", {"requested_budget": args.budget,
           "seconds": round(time.monotonic() - started, 2), "characters": count,
           "reasoning_characters": reasoning, "lines": output.read_text(encoding="utf-8").count("\n"),
           "terminal": terminal, "local_stop": stopped, "output_path": str(output),
           "full_capability_proven": False})
    assert terminal and not stopped and count > 0, "Long generation did not finish normally"
    assert (terminal.get("usage") or {}).get("completion_tokens", 0) > 8192, "Output did not exceed 8192-token budget"


def json_api(http, method, path, **kwargs):
    response = http.request(method, path, **kwargs)
    data = response.json()
    if not 200 <= response.status_code < 300 or data.get("error"):
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {safe(data)}")
    return data


def api_chat(http, **body):
    events = []
    with http.stream("POST", "/api/chat", json=body) as response:
        if response.status_code != 200:
            raise RuntimeError(f"chat HTTP {response.status_code}")
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    summary = stream_summary(events)
    assert summary["done_count"] == 1 and summary["error_count"] == 0 and summary["characters"] > 0, summary
    return summary, events


@contextmanager
def app_server(port=0):
    from flask import send_from_directory
    from werkzeug.serving import make_server

    app = create_app()
    app.config["TESTING"] = False
    dist = BACKEND.parent / "frontend" / "dist"

    @app.get("/")
    def ui():
        return send_from_directory(dist, "index.html")

    @app.get("/assets/<path:name>")
    def asset(name):
        return send_from_directory(dist / "assets", name)

    gateway = FaultGateway().__enter__()
    if args.phase == "serve":
        @app.post("/__validation/fault")
        def fault():
            from flask import request, jsonify
            mode = (request.get_json() or {}).get("mode", "off")
            if mode not in ("off", "503", "disconnect"):
                return jsonify({"error": "invalid validation mode"}), 400
            gateway.mode = mode
            patched = settings if mode == "off" else replace(settings, llm=replace(
                settings.llm, base_url=gateway.url, max_retries=2))
            llm.settings = ragas_eval.settings = patched
            return jsonify({"mode": mode, "local_fault_injection": True})

    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        gateway.__exit__()
        if args.phase == "serve":
            record("browser-fault-gateway", {"calls": gateway.calls,
                   "production_hashes_unchanged": live_data_hashes() == baseline})
        vector_store.close_client()


def flow():
    with app_server() as url, httpx.Client(base_url=url, timeout=900) as http:
        health = json_api(http, "GET", "/api/health")
        record("health", {"config": health["config"], "capabilities": health["capabilities"]})
        names = ["产品需求文档.md", "quota.py", "运维笔记.txt", "客服问答记录.csv",
                 "运维 Runbook.docx", "API 参考手册.pdf"]
        uploaded = []
        for name in names:
            source = BACKEND / "data" / "samples" / name
            data = json_api(http, "POST", "/api/documents", files={"file": (name, source.read_bytes())})
            record_data = data.get("document") or data
            assert record_data["status"] == "已解析", record_data
            doc_id = record_data["doc_id"]
            chunks = json_api(http, "GET", "/api/chunks/" + doc_id)
            assert chunks["chunk_total"] > 0, chunks
            uploaded.append({"name": name, "doc_id": doc_id, "type": record_data.get("doc_type"),
                             "strategy": chunks["strategy"], "chunks": chunks["chunk_total"]})
        record("upload-parse-chunk", {"documents": uploaded})
        indexed = json_api(http, "POST", "/api/index", json={"doc_id": [d["doc_id"] for d in uploaded]})
        assert indexed.get("written", 0) > 0 and not indexed.get("failed"), indexed
        record("embedding-index", {k: indexed.get(k) for k in ("written", "total_points", "failed", "mode")})
        question = "接口默认的限流额度是多少？超出会返回什么？"
        retrieved = json_api(http, "POST", "/api/retrieve", json={"question": question, "rerank": True})
        assert retrieved["citations"] and retrieved["diagnostics"]["rerank_used"], retrieved["diagnostics"]
        record("hybrid-rerank", {"citations": len(retrieved["citations"]), "diagnostics": retrieved["diagnostics"]})
        summary, events = api_chat(http, question=question, rerank=True)
        assert summary["terminal"][0].get("actual_model") == settings.llm.chat_model, summary
        assert "600" in "".join(e.get("text", "") for e in events if e["type"] == "content")
        record("primary-chat", summary)
        with FaultGateway() as gateway, gateway.active("503"):
            summary, _ = api_chat(http, question=question)
            assert summary["notices"] and summary["terminal"][0]["actual_model"] == settings.llm.fallback_model
            record("fallback-chat-api", {**summary, "gateway_calls": gateway.calls,
                                          "fault_source": "controlled local HTTP 503; GLM output is real"})
        from app import query
        from app.eval import dataset
        primary_rewrites = query.rewrite("默认限流额度和超额处理规则是什么？")
        assert len(primary_rewrites) > 1, primary_rewrites
        record("primary-rewrite", {"queries": primary_rewrites, "global_switch_enabled": settings.query.rewrite_enabled})
        summary_text = llm.summarize("默认额度为每分钟 600 次请求，超出时返回 HTTP 429 和 Retry-After 响应头。")
        assert summary_text
        record("primary-summary", {"text": summary_text})
        built = json_api(http, "POST", "/api/eval/dataset", json={"limit": 2, "per_document": 1})
        assert built["count"] == 2 and all(c["generation_model"] == settings.llm.chat_model for c in built["cases"])
        record("primary-dataset", {"count": built["count"], "cases": built["cases"], "notes": built["notes"]})
        report = json_api(http, "POST", "/api/eval/run", json={"limit": 2, "with_ragas": True, "max_workers": 1})
        variant = report["variants"][0]
        assert variant["retrieval"]["failed_answers"] == 0 and variant["retrieval"]["empty_answers"] == 0
        assert all(variant["scores"].get(key) is not None for key in (
            "faithfulness", "answer_relevancy", "context_precision", "context_recall")), variant
        assert all(all(row.get("scores", {}).get(key) is not None for key in (
            "faithfulness", "answer_relevancy", "context_precision", "context_recall"))
            for row in variant["per_case"]), "A per-case RAGAS score is missing"
        record("primary-ragas", {"scores": variant["scores"], "retrieval": variant["retrieval"],
             "models": [{"case_id": row["case_id"], "actual_model": row.get("actual_model"),
                         "judge_model": row.get("judge_model"), "scores": row.get("scores")}
                        for row in variant["per_case"]], "notes": report["notes"]})
        assert json_api(http, "GET", "/api/eval/latest")["created_at"] == report["created_at"]
        assert json_api(http, "GET", "/api/eval/history")["items"]
        record("report-persistence", {"latest": True, "history": True, "path": report["path"]})


def fallback():
    prompt = [{"role": "user", "content": "只回答：备用连接可用"}]
    with FaultGateway() as gateway:
        for mode in ("503", "disconnect"):
            with gateway.active(mode):
                started = time.monotonic()
                notices = []
                result = llm.chat(prompt, max_tokens=64, on_notice=notices.append)
                assert result["actual_model"] == settings.llm.fallback_model and notices
                record("fallback-normal-" + mode, {"seconds": round(time.monotonic() - started, 2),
                    "actual_model": result["actual_model"], "usage": result["usage"], "notices": notices,
                    "attempts": result["attempts"], "fault_source": "controlled local HTTP fault; real GLM request"})
                events = list(llm.stream_chat_until_content(prompt, max_tokens=64))
                summary = stream_summary(events)
                assert summary["done_count"] == 1 and summary["notices"]
                assert summary["terminal"][0]["actual_model"] == settings.llm.fallback_model
                assert [e["type"] for e in events].index("notice") < [e["type"] for e in events].index("content")
                record("fallback-stream-" + mode, summary)
        with gateway.active("503"):
            from langchain_core.messages import HumanMessage
            notices = []
            Judge = ragas_eval._make_throttled_chat_openai(0, notices)
            judge = Judge(model=settings.eval.judge_model, api_key=settings.llm.api_key,
                          base_url=gateway.url, max_retries=0, max_tokens=64, temperature=settings.llm.temperature)
            for name, invoke in [("sync", lambda: judge._generate([HumanMessage(content='只输出 JSON：{"ok":true}')])) ,
                                 ("async", lambda: asyncio.run(judge._agenerate([HumanMessage(content='只输出 JSON：{"ok":true}')])) )]:
                result = invoke()
                assert result.generations and result.generations[0].message.content
                record("fallback-judge-" + name, {"has_content": True, "notices": list(notices),
                    "model": settings.llm.fallback_model, "usage": (result.llm_output or {}).get("token_usage")})
        record("fault-gateway", {"calls": gateway.calls, "primary_natural_outage_observed": False})


def fallback_business():
    with app_server() as url, httpx.Client(base_url=url, timeout=300) as http:
        with FaultGateway() as gateway, gateway.active("503"):
            result = json_api(http, "POST", "/api/retrieve", json={
                "question": "默认限流额度和超额处理规则是什么？", "rewrite": True, "rerank": False})
            diagnostics = result["diagnostics"]
            assert diagnostics["subqueries"] > 1 and diagnostics["notes"], diagnostics
            assert any("glm-4-flash" in note for note in diagnostics["notes"])
            record("fallback-rewrite-retrieval", {"diagnostics": diagnostics,
                   "citations": len(result["citations"]), "global_switch_enabled": settings.query.rewrite_enabled})
            notices = []
            text = llm.summarize("默认额度每分钟 600 次，超出返回 429 与 Retry-After。", on_notice=notices.append)
            assert text and notices
            record("fallback-summary", {"text": text, "notices": notices, "gateway_calls": gateway.calls})


def evaluate_existing():
    with app_server() as url, httpx.Client(base_url=url, timeout=900) as http:
        report = json_api(http, "POST", "/api/eval/run", json={"limit": 2, "with_ragas": True, "max_workers": 1})
        variant = report["variants"][0]
        assert variant["retrieval"]["failed_answers"] == 0 and variant["retrieval"]["empty_answers"] == 0
        record("primary-ragas-recheck", report, summary={"scores": variant["scores"],
               "retrieval": variant["retrieval"], "per_case": [{"case_id": row["case_id"], "scores": row["scores"]}
               for row in variant["per_case"]], "notes": report["notes"]})
        assert all(all(row.get("scores", {}).get(key) is not None for key in (
            "faithfulness", "answer_relevancy", "context_precision", "context_recall"))
            for row in variant["per_case"]), "A per-case RAGAS score is missing"


def judge_budget():
    from langchain_core.messages import HumanMessage
    notices, attempts = [], []
    Judge = ragas_eval._make_throttled_chat_openai(0, notices, judge_attempts=attempts)
    judge = Judge(model=settings.eval.judge_model, api_key=settings.llm.api_key,
                  base_url=settings.llm.base_url, temperature=0.1, max_retries=0, max_tokens=256)
    result = judge._generate([HumanMessage(content=(
        'Output exactly 100 numbered records, one per line, starting from 001. '
        'Each line must contain its number and the words alpha beta gamma delta epsilon zeta. '
        'No introduction, no summary, no abbreviations.'))])
    record("judge-budget-retry", {"notices": notices, "attempts": attempts,
           "finish_reason": result.generations[0].generation_info.get("finish_reason"),
           "characters": len(result.generations[0].message.content), "meaning":
           "Deliberately insufficient small budget; exactly one real expanded retry, no infinite retry or fake completed score."})
    assert len(attempts) == 2 and [attempt["max_tokens"] for attempt in attempts] == [256, 512]
    assert len(notices) == 1


try:
    if args.phase == "long":
        long_output()
    elif args.phase == "flow":
        flow()
    elif args.phase == "evaluate":
        evaluate_existing()
    elif args.phase == "judge-budget":
        judge_budget()
    elif args.phase == "fallback":
        fallback()
    elif args.phase == "business":
        fallback_business()
    else:
        with app_server(args.port) as url:
            print(json.dumps({"ready": url, "run_dir": str(run_dir), "isolated": True}), flush=True)
            threading.Event().wait()
    assert live_data_hashes() == baseline, "Production registry/state/report changed"
    record(args.phase + "-complete", {"passed": True, "production_hashes_unchanged": True})
except Exception as error:
    record(args.phase + "-failure", {"error_type": type(error).__name__, "message": str(error),
                                     "production_hashes_unchanged": live_data_hashes() == baseline})
    raise SystemExit(1)
finally:
    vector_store.close_client()
