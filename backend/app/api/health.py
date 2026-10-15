"""步骤 1 的验收点：一个能反映真实依赖状态的健康检查。

后续步骤每接一个新组件（解析器、embedding、Qdrant、智谱），就在这里加一条探测，
前端页脚会直接显示，方便判断哪一环没起来。
"""
from __future__ import annotations

import importlib.util
import platform
import sys
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from ..config import settings

bp = Blueprint("health", __name__)

MODULES = {
    "flask": "flask",
    "dotenv": "dotenv",
    "pypdf": "pypdf",
    "docx": "docx",
    "openpyxl": "openpyxl",
    "markdown_it": "markdown_it",
    "bs4": "bs4",
    "numpy": "numpy",
    "httpx": "httpx",
    "qdrant_client": "qdrant_client",
    "jieba": "jieba",
    "rank_bm25": "rank_bm25",
    "openai": "openai",
    "tree_sitter": "tree_sitter",
    "tree_sitter_language_pack": "tree_sitter_language_pack",
    "ragas": "ragas",
    "langsmith": "langsmith",
}

# 每项属于哪一步，前端可以照着步骤.md 显示进度
MODULE_STEP = {
    "pypdf": "step2",
    "docx": "step2",
    "openpyxl": "step2",
    "markdown_it": "step2",
    "bs4": "step2",
    "httpx": "step4",
    "qdrant_client": "step4",
    "jieba": "step5",
    "rank_bm25": "step5",
    "openai": "step5",
    "tree_sitter": "step3",
    "tree_sitter_language_pack": "step3",
    "ragas": "step6",
    "langsmith": "step6",
}


def _probe_modules() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for label, module_name in MODULES.items():
        present = importlib.util.find_spec(module_name) is not None
        result[label] = {"installed": present, "step": MODULE_STEP.get(label, "step1")}
    return result


def _capabilities(modules: dict[str, dict[str, object]]) -> dict[str, bool]:
    """每一步是否真的就绪。

    抽成纯函数是为了能用合成的 modules 直接测试：
    这里曾经犯过“智谱 Key 已配置”就当“步骤 5 已完成”的错。

    约定：一个步骤只有在「代码接好 + 依赖安装好 + 配置填好」
    三件事全部满足时才算 True。
    """
    installed = lambda name: bool(modules.get(name, {}).get("installed"))  # noqa: E731

    step4_embedding = settings.siliconflow.configured and installed("httpx")
    step4_qdrant = installed("qdrant_client")
    step5_retrieval = installed("jieba") and installed("rank_bm25")
    # 步骤 6：评测要能出题（需要模型）与打分（需要 ragas）
    step6_dataset = bool(settings.llm.configured and step4_embedding)
    step6_ragas = installed("ragas") and step4_embedding

    return {
        "step1_skeleton": True,
        "step2_documents": installed("pypdf") and installed("docx"),
        "step2_markdown": installed("markdown_it"),
        "step2_spreadsheet": installed("openpyxl"),
        "step3_chunking": True,
        "step3_tree_sitter": installed("tree_sitter"),
        "step4_embedding": step4_embedding,
        "step4_qdrant": step4_qdrant,
        # 注意：下面两项只表示“该步需要的依赖已就位”，
        # 不等于流水线已接好。
        "step5_llm_config": bool(settings.llm.configured),
        "step5_retrieval": step5_retrieval,
        "step5_chat": bool(
            settings.llm.configured
            and step4_embedding
            and step4_qdrant
            and step5_retrieval
        ),
        # 评测集生成与跑分分开记：出题只要模型，打分还要 ragas 装好
        "step6_dataset": bool(step6_dataset and step5_retrieval and step4_qdrant),
        "step6_ragas": bool(step6_ragas),
        "step6_tracing": installed("langsmith"),
    }


@bp.get("/health")
def health():
    modules = _probe_modules()
    missing_by_step: dict[str, list[str]] = {}
    for label, info in modules.items():
        if not info["installed"]:
            missing_by_step.setdefault(str(info["step"]), []).append(label)

    from .documents import document_count

    return jsonify(
        {
            "status": "ok",
            "service": "corpus-rag-backend",
            "time": datetime.now(timezone.utc).isoformat(),
            "python": {
                "version": sys.version.split()[0],
                "executable": sys.executable,
                "platform": platform.platform(),
            },
            "config": {
                "embedding_backend": settings.embedding.backend,
                "embedding_provider": "siliconflow",
                "embedding_model": settings.embedding.model_name,
                "embedding_dimension": settings.embedding.dimension,
                "embedding_configured": settings.siliconflow.configured,
                "chat_model": settings.llm.chat_model,
                "llm_provider": settings.llm.provider,
                "llm_configured": settings.llm.configured,
                "fallback_configured": settings.llm.fallback_configured,
                "zhipu_configured": settings.llm.fallback_configured,
                "qdrant_mode": settings.qdrant.mode,
                "qdrant_collection": settings.qdrant.collection,
                "langsmith_tracing": settings.eval.langsmith_tracing,
            },
            "documents": {"count": document_count()},
            "dependencies": modules,
            "missing_by_step": missing_by_step,
            "capabilities": _capabilities(modules),
        }
    )
