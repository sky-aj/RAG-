"""评测接口（步骤 6）。

  GET  /api/eval            当前评测集与最近一次跑分的状态
  POST /api/eval/dataset    从已入库文档生成评测集（要调模型出题）
  POST /api/eval/run        跑一轮评测（走真实 RAG 链路 + RAGAS 打分）
  POST /api/eval/dataset/stream  SSE 出题进度与逐题结果
  POST /api/eval/run/stream      SSE 生成回答与判分进度
  GET  /api/eval/latest     最近一次跑分的完整报告
  GET  /api/eval/history    历次跑分的分数（画曲线用）

原 JSON 接口保留。流式接口用本轮线程和队列桥接业务进度，不引入持久化任务队列。
同一进程内仅允许一轮出题或评测，避免覆盖正在使用的数据集和报告。
"""
from __future__ import annotations

import json
import logging
import math
import threading
from queue import Empty, Queue
from typing import Callable

from flask import Blueprint, Response, jsonify, request

from ..config import settings
from ..eval import dataset as dataset_module
from ..eval import ragas_eval

bp = Blueprint("eval", __name__)
logger = logging.getLogger(__name__)
_operation_lock = threading.Lock()
_BUSY_MESSAGE = "已有出题或评测任务正在运行，请等待当前任务结束"


def _payload() -> dict:
    body = request.get_json(silent=True) or {}
    merged = dict(body)
    for key in ("limit", "with_ragas", "seed"):
        value = request.args.get(key)
        if value is not None:
            merged[key] = value
    return merged


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _truthy(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _doc_ids(payload: dict) -> list[str]:
    raw = payload.get("doc_id")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item]
    return [str(raw)]


def _dataset_result(built, path) -> dict:
    return {
        "ok": True,
        "count": len(built.cases),
        "path": str(path),
        "notes": built.notes,
        "cases": [
            {
                "case_id": case.case_id,
                "question": case.question,
                "ground_truth": case.ground_truth,
                "generation_model": case.generation_model,
                "expected": case.expected,
            }
            for case in built.cases
        ],
    }


def _generate_dataset(payload: dict, on_progress=None) -> dict:
    options = {
        "doc_ids": _doc_ids(payload) or None,
        "limit": _int_or_none(payload.get("limit")) or settings.eval.max_cases,
        "per_document": _int_or_none(payload.get("per_document")) or settings.eval.cases_per_document,
        "seed": _int_or_none(payload.get("seed")),
    }
    if on_progress is not None:
        options["on_progress"] = on_progress
    built = dataset_module.generate(**options)
    path = dataset_module.save_dataset(built)
    return _dataset_result(built, path)


def _run_report(payload: dict, on_progress=None) -> tuple[dict, str]:
    options = {
        "variants": payload.get("variants"),
        "limit": _int_or_none(payload.get("limit")),
        "with_ragas": _truthy(payload.get("with_ragas"), True),
        "max_workers": _int_or_none(payload.get("max_workers")),
    }
    if on_progress is not None:
        options["on_progress"] = on_progress
    report = ragas_eval.run(**options)
    path = ragas_eval.save_report(report)
    return report.to_dict(), str(path)


def _safe_message(error: Exception) -> str:
    message = str(error)
    for key in (settings.llm.api_key, settings.llm.fallback_api_key,
                settings.siliconflow.api_key, settings.qdrant.api_key,
                settings.eval.langsmith_api_key):
        if key:
            message = message.replace(key, "[已隐藏密钥]")
    return message


def _json_values(value):
    """SSE uses strict JSON; unavailable floating scores become null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_values(item) for item in value]
    return value


def _stream_job(operation: Callable[[Callable[[dict], None]], dict], failure_message: str):
    if not _operation_lock.acquire(blocking=False):
        return jsonify({"error": _BUSY_MESSAGE}), 409

    events: Queue[tuple[bool, str]] = Queue()
    disconnected = threading.Event()

    def emit(event: dict) -> None:
        if disconnected.is_set():
            return
        encoded = json.dumps(_json_values(event), ensure_ascii=False, allow_nan=False)
        events.put((event.get("type") in {"done", "error"}, f"data: {encoded}\n\n"))

    def emit_progress(event: dict) -> None:
        if event.get("type") in {"done", "error"}:
            raise ValueError("终态由评测接口在保存结果后发送")
        emit(event)

    def work() -> None:
        try:
            result = operation(emit_progress)
            terminal = {"type": "done", **result}
            # Validate before reporting success so a bad progress payload cannot emit done.
            json.dumps(_json_values(terminal), ensure_ascii=False, allow_nan=False)
        except RuntimeError as error:
            terminal = {"type": "error", "message": _safe_message(error), "error": _safe_message(error)}
        except Exception as error:  # noqa: BLE001
            logger.error("%s（%s）", failure_message, type(error).__name__)
            terminal = {"type": "error", "message": failure_message, "error": failure_message}
        finally:
            _operation_lock.release()
        emit(terminal)

    worker = threading.Thread(target=work, name="eval-stream", daemon=True)
    try:
        worker.start()
    except Exception:
        _operation_lock.release()
        raise

    def stream():
        try:
            while True:
                try:
                    terminal, frame = events.get(timeout=15)
                except Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield frame
                if terminal:
                    break
        finally:
            # An existing synchronous operation finishes and saves its result even if
            # the browser disconnects. Stop buffering events while it releases its lock.
            disconnected.set()

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.call_on_close(disconnected.set)
    return response


@bp.get("/eval")
def eval_status():
    dataset_info = dataset_module.describe()
    return jsonify(
        {
            "dataset": dataset_info,
            "staleness": dataset_info["staleness"],
            "report": {
                "has_report": ragas_eval.load_latest() is not None,
                "last_report_at": (ragas_eval.load_latest() or {}).get("created_at", ""),
            },
            "config": {
                "max_cases": settings.eval.max_cases,
                "cases_per_document": settings.eval.cases_per_document,
                "max_workers": settings.eval.max_workers,
                "langsmith_enabled": settings.eval.langsmith_enabled,
                "report_dir": str(settings.eval.report_dir),
            },
            "metrics": ragas_eval.describe()["metrics"],
        }
    )


@bp.post("/eval/dataset")
def build_dataset():
    payload = _payload()
    if not _operation_lock.acquire(blocking=False):
        return jsonify({"error": _BUSY_MESSAGE}), 409
    try:
        result = _generate_dataset(payload)
    except RuntimeError as error:
        return jsonify({"error": _safe_message(error)}), 409
    except Exception as error:  # noqa: BLE001
        logger.error("生成评测集失败（%s）", type(error).__name__)
        return jsonify({"error": "生成评测集失败"}), 502
    finally:
        _operation_lock.release()
    return jsonify(result)


@bp.post("/eval/dataset/stream")
def build_dataset_stream():
    payload = _payload()
    return _stream_job(lambda emit: _generate_dataset(payload, emit), "生成评测集失败")


@bp.get("/eval/dataset")
def show_dataset():
    saved = dataset_module.load_dataset()
    if saved is None:
        return jsonify({"error": "还没有评测集，先跑 POST /api/eval/dataset"}), 404
    return jsonify(saved.to_dict())


@bp.post("/eval/run")
def run_eval():
    payload = _payload()
    variants = payload.get("variants")
    if variants is not None and not isinstance(variants, list):
        return jsonify({"error": "variants 必须是数组"}), 400
    if not _operation_lock.acquire(blocking=False):
        return jsonify({"error": _BUSY_MESSAGE}), 409
    try:
        report, path = _run_report(payload)
    except RuntimeError as error:
        return jsonify({"error": _safe_message(error)}), 409
    except Exception as error:  # noqa: BLE001
        logger.error("评测失败（%s）", type(error).__name__)
        return jsonify({"error": "评测失败"}), 502
    finally:
        _operation_lock.release()
    return jsonify({**report, "ok": True, "path": path})


@bp.post("/eval/run/stream")
def run_eval_stream():
    payload = _payload()
    variants = payload.get("variants")
    if variants is not None and not isinstance(variants, list):
        return jsonify({"error": "variants 必须是数组"}), 400

    def operation(emit):
        report, path = _run_report(payload, emit)
        return {"ok": True, "report": report, "path": path}

    return _stream_job(operation, "评测失败")


@bp.get("/eval/latest")
def latest_report():
    report = ragas_eval.load_latest()
    if report is None:
        return jsonify({"error": "还没有跑分结果，先跑 POST /api/eval/run"}), 404
    return jsonify(report)


@bp.get("/eval/history")
def eval_history():
    limit = _int_or_none(request.args.get("limit")) or 10
    return jsonify({"items": ragas_eval.history(limit=limit)})
