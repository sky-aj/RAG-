"""评测集生成（步骤 6）。

评测集的质量决定评测结论值不值得信，所以这里不靠"编问题"，而是**从已入库的
真实片段反向出题**：拿一段正文，让模型出题并给标准答案，同时把
「该问题应该命中哪份文档的哪几行」记下来。

这样做的两个好处：
  1. `context_recall` 有客观口径——不是"看回答像不像"，而是"该召回的块有没有进上下文"
  2. 评测集随知识库自动更新，换一批文档就能重新出一套题

数据集落盘到 `data/eval/dataset.json`，跑评测时直接读，保证多次评测用的是同一套题。
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import settings
from ..llm import client as llm

logger = logging.getLogger(__name__)

# 出题时给模型的样例口径：问题要"像真用户问的"，答案要能从片段里逐字找到依据
_QUESTION_SYSTEM = """\
你在为一个检索系统造评测题。给定一段资料，出一道**用户真的会问**的问题，并给出标准答案。

要求：
1. 问题必须是自然的口语问法，不要出现"根据片段""上文提到"这类元话术。
2. 问题必须能**只靠这段资料**答出来，不能依赖外部常识。
3. 标准答案简短，只写事实（数字、名称、结论），不超过 60 字。
4. 严格输出 JSON，不要加解释、不要加代码块标记：
   {"question": "...", "answer": "..."}
5. 如果这段资料看起来只是标题、目录、代码骨架，没有可问的事实，返回：
   {"question": "", "answer": ""}
"""


@dataclass
class EvalCase:
    """一条评测样本。"""

    case_id: str
    question: str
    ground_truth: str
    # 期望被召回的来源：该问题的答案出在这些位置
    doc_id: str
    doc_name: str
    line_start: int
    line_end: int
    # 生成这条题用到的片段，方便人工复核题目质量
    source_text: str = ""
    difficulty: str = "normal"
    generation_model: str = ""

    @property
    def expected(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass
class EvalDataset:
    name: str
    created_at: str
    cases: list[EvalCase] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 出题时文库里的 doc_id。文档被删/新增后能立刻判断这套题是否还适用，
    # 否则会拿着一套引用已删文档的题反复跑，分数白白掉下来。
    source_doc_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "count": len(self.cases),
            "notes": self.notes,
            "source_doc_ids": self.source_doc_ids,
            "cases": [asdict(case) for case in self.cases],
        }


def dataset_path() -> Path:
    """每次读而不缓存，测试里改 settings 立刻生效。"""
    return settings.eval.dataset_path


def save_dataset(dataset: EvalDataset) -> Path:
    path = dataset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataset.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_dataset() -> EvalDataset | None:
    path = dataset_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    cases = []
    for item in raw.get("cases", []):
        try:
            cases.append(EvalCase(**item))
        except TypeError:  # 老版本字段对不上就跳过，不让整个数据集读不出来
            continue
    return EvalDataset(
        name=raw.get("name", "corpus-eval"),
        created_at=raw.get("created_at", ""),
        cases=cases,
        notes=list(raw.get("notes") or []),
        source_doc_ids=[str(item) for item in (raw.get("source_doc_ids") or [])],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_question(raw: str) -> tuple[str, str]:
    """从模型输出里抠出 question / answer。

    模型偶尔会包一层 ```json 代码块或多说一句，所以这里先剥壳再解析，
    解析不出来就当这条作废——宁缺毋滥，坏题会污染所有指标。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "", ""
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return "", ""
    question = str(payload.get("question") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not question or not answer:
        return "", ""
    return question, answer


def _candidates(doc_ids: list[str] | None, per_document: int, min_chars: int) -> list[dict]:
    """从向量库里挑"适合出题"的片段。

    只用向量库的 payload，不重新解析原文件：入库时已经把正文、行号、
    标题路径都存进去了，再解析一遍纯属浪费。
    """
    from ..retrieval import vector_store

    if not vector_store.collection_exists():
        return []

    wanted = set(doc_ids or [])
    records: list[dict] = []
    offset = None
    while True:
        points, offset = vector_store.get_client().scroll(
            collection_name=settings.qdrant.collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = dict(point.payload or {})
            if wanted and payload.get("doc_id") not in wanted:
                continue
            text = str(payload.get("text") or "").strip()
            # 太短的块（标题、目录、空行）问不出东西，直接排除
            if len(text) < min_chars:
                continue
            if payload.get("kind") in {"heading", "toc"}:
                continue
            payload["text"] = text
            records.append(payload)
        if offset is None:
            break

    # 每份文档各取前 N 条，避免一份长文档把题量全占了
    picked: list[dict] = []
    tally: dict[str, int] = {}
    for record in records:
        doc_id = str(record.get("doc_id") or "")
        if tally.get(doc_id, 0) >= per_document:
            continue
        tally[doc_id] = tally.get(doc_id, 0) + 1
        picked.append(record)
    return picked


def generate(
    doc_ids: list[str] | None = None,
    limit: int | None = None,
    per_document: int = 2,
    min_chars: int = 120,
    seed: int | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> EvalDataset:
    """生成评测集。`limit` 控制最多出多少题（一次出题要调一次模型，别贪多）。"""
    if not settings.llm.configured:
        raise RuntimeError("LLM_API_KEY 未配置，出题要调用模型")

    total = limit or settings.eval.max_cases
    candidates = _candidates(doc_ids, per_document, min_chars)
    if not candidates:
        raise RuntimeError("没有可出题的片段：向量库是空的，或文本都太短")

    if seed is not None:
        random.Random(seed).shuffle(candidates)

    def progress(event: dict) -> None:
        if on_progress is not None:
            on_progress(event)

    progress({"type": "progress", "phase": "question", "index": 0,
              "completed": 0, "total": total, "candidate_total": len(candidates),
              "candidate_completed": 0, "generated": 0, "status": "waiting"})

    dataset = EvalDataset(
        name="corpus-eval",
        created_at=_now(),
        source_doc_ids=sorted({str(item.get("doc_id") or "") for item in candidates if item.get("doc_id")}),
    )
    seen: set[str] = set()

    processed = 0
    for record in candidates:
        if len(dataset.cases) >= total:
            break
        processed += 1
        progress({"type": "progress", "phase": "question", "index": processed,
                  "completed": len(dataset.cases), "total": total,
                  "candidate_total": len(candidates), "candidate_completed": processed - 1,
                  "generated": len(dataset.cases), "chunk_id": record.get("chunk_id"),
                  "status": "started"})
        text = record["text"]
        prompt = f"资料（来自 {record.get('doc_name')} 第 {record.get('line_start')}-{record.get('line_end')} 行）：\n\n{text[:1500]}"
        notices: list[dict] = []
        try:
            result = llm.chat(
                [
                    {"role": "system", "content": _QUESTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                on_notice=notices.append,
                max_tokens=settings.eval.dataset_max_tokens,
            )
        except llm.LLMError as error:
            dataset.notes.extend(item["text"] for item in notices)
            logger.warning("出题失败，跳过这条：%s", error)
            dataset.notes.append(f"片段 {record.get('chunk_id')} 出题失败：{error}")
            progress({"type": "progress", "phase": "question", "index": processed,
                      "completed": len(dataset.cases), "total": total,
                      "candidate_total": len(candidates), "candidate_completed": processed,
                      "generated": len(dataset.cases), "status": "failed"})
            continue
        dataset.notes.extend(item["text"] for item in notices)

        question, answer = _parse_question(result.get("content", ""))
        if not question or not answer:
            dataset.notes.append(f"片段 {record.get('chunk_id')} 出题被跳过（模型没给出可用 JSON）")
            progress({"type": "progress", "phase": "question", "index": processed,
                      "completed": len(dataset.cases), "total": total,
                      "candidate_total": len(candidates), "candidate_completed": processed,
                      "generated": len(dataset.cases), "status": "skipped"})
            continue
        if question in seen:  # 不同块可能问出同一道题，去重
            progress({"type": "progress", "phase": "question", "index": processed,
                      "completed": len(dataset.cases), "total": total,
                      "candidate_total": len(candidates), "candidate_completed": processed,
                      "generated": len(dataset.cases), "status": "duplicate"})
            continue
        seen.add(question)

        case = EvalCase(
            case_id=f"case-{len(dataset.cases) + 1:03d}",
            question=question,
            ground_truth=answer,
            doc_id=str(record.get("doc_id") or ""),
            doc_name=str(record.get("doc_name") or ""),
            line_start=int(record.get("line_start") or 0),
            line_end=int(record.get("line_end") or 0),
            source_text=text[:400],
            generation_model=result.get("actual_model") or settings.llm.chat_model,
        )
        dataset.cases.append(case)
        progress({
            "type": "question",
            "case": {**asdict(case), "expected": case.expected},
            "index": processed,
            "completed": len(dataset.cases),
            "total": total,
            "candidate_total": len(candidates),
            "candidate_completed": processed,
            "generated": len(dataset.cases),
        })
        progress({"type": "progress", "phase": "question", "index": processed,
                  "completed": len(dataset.cases), "total": total,
                  "candidate_total": len(candidates), "candidate_completed": processed,
                  "generated": len(dataset.cases), "status": "generated"})

    if not dataset.cases:
        # 把前几条失败原因原样带出去：只说"一条题都没出出来"没法排查
        detail = "；".join(dataset.notes[:3]) or "模型对所有片段都返回了空 JSON"
        raise RuntimeError(f"一条题都没出出来（{detail}）")
    return dataset


def _current_doc_ids() -> set[str]:
    """当前注册表里的 doc_id。读不到就当作未知，不做过期判定。"""
    try:
        from ..api.documents import load_registry
    except Exception:  # noqa: BLE001
        return set()
    try:
        return {str(item.get("doc_id")) for item in load_registry() if item.get("doc_id")}
    except Exception:  # noqa: BLE001
        return set()


def staleness(saved: "EvalDataset | None") -> dict:
    """这套题相对当前文库是否过期。

    过期不等于不可用，只代表「分数不能和以前直接比」。三种情况：
      * 出过题的文档被删掉 -> 那些题注定召不回来，命中率虚低
      * 新增了文档但没重出题 -> 评测面没覆盖新内容
      * 老数据集没记来源（本字段是后加的）-> 无法判定，标 unknown
    """
    if saved is None or not saved.cases:
        return {"stale": False, "reason": "", "missing_doc_ids": [], "added_doc_ids": []}

    current = _current_doc_ids()
    if not saved.source_doc_ids or not current:
        return {"stale": False, "reason": "", "missing_doc_ids": [], "added_doc_ids": []}

    referenced = {case.doc_id for case in saved.cases if case.doc_id}
    missing = sorted(doc_id for doc_id in referenced if doc_id not in current)
    # 只有「真正出过题的文档」才要求被覆盖。
    # source_doc_ids 记的是「出题时抽取过的候选」，其中一部分可能因为模型
    # 返回空 JSON 而没产出题目；拿它判断覆盖会把正常情况误报成过期。
    covered = set(referenced) | (set(saved.source_doc_ids) & current)
    added = sorted(doc_id for doc_id in (current - covered))

    reasons = []
    if missing:
        reasons.append(f"{len(missing)} 份出题文档已从知识库删除")
    if added:
        reasons.append(f"知识库新增了 {len(added)} 份文档，本次评测未覆盖")

    return {
        "stale": bool(reasons),
        "reason": "；".join(reasons),
        "missing_doc_ids": missing,
        "added_doc_ids": added,
    }


def describe() -> dict:
    saved = load_dataset()
    return {
        "path": str(dataset_path()),
        "exists": saved is not None,
        "count": len(saved.cases) if saved else 0,
        "created_at": saved.created_at if saved else "",
        "max_cases": settings.eval.max_cases,
        "per_document": settings.eval.cases_per_document,
        "source_doc_ids": saved.source_doc_ids if saved else [],
        "staleness": staleness(saved),
    }
