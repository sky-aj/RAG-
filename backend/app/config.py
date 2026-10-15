"""集中读取 .env，其他模块只从这里拿配置。"""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        if key.startswith(("LLM_", "EVAL_GENERATION_", "EVAL_JUDGE_", "EVAL_DATASET_")):
            raise ValueError(f"{key} 必须为整数") from None
        return default


def _env_float(key: str, default: float) -> float:
    strict = key.startswith(("LLM_", "EVAL_JUDGE_")) or key == "EVAL_REQUEST_INTERVAL"
    try:
        value = float(_env(key) or default)
    except ValueError:
        if strict:
            raise ValueError(f"{key} 必须为有限数值") from None
        return default
    if strict and not math.isfinite(value):
        raise ValueError(f"{key} 必须为有限数值")
    return value


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(key: str, default: str = "") -> list[str]:
    raw = _env(key) or default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI-compatible generation connection.

    ``max_output_tokens`` records the provider capability.  ``max_tokens`` is
    the per-request budget and intentionally remains a separate setting.
    """

    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "sub-coco"))
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"), repr=False)
    base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://www.sub-coco.com/v1"))
    chat_model: str = field(default_factory=lambda: _env("LLM_CHAT_MODEL", "deepseek-v4-flash"))
    max_output_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_OUTPUT_TOKENS", 393216))
    max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 8192))
    summary_max_tokens: int = field(default_factory=lambda: _env_int("LLM_SUMMARY_MAX_TOKENS", 1024))
    rewrite_max_tokens: int = field(default_factory=lambda: _env_int("LLM_REWRITE_MAX_TOKENS", 2048))
    temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.3))
    timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 180.0))
    max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 3))
    # 请求间隔可配置；旧智谱账户的限流经验不代表主平台的限制。
    min_interval: float = field(default_factory=lambda: _env_float("LLM_MIN_INTERVAL", 0.0))
    # 备用模型：主模型重试到最后一次仍失败时改用它。
    # 实测这个 Key 上 glm-4.7-flash 会长时间 429（最长一次等了两分多钟才恢复），
    # 直接失败会让评测出现空回答、线上出现报错。留一个跑得动的备用模型，
    # 是一次「降级但可用」的选择。留空表示不要兜底。
    fallback_enabled: bool = field(default_factory=lambda: _env_bool("LLM_FALLBACK_ENABLED", True))
    fallback_provider: str = field(default_factory=lambda: _env("LLM_FALLBACK_PROVIDER", "zhipu"))
    fallback_api_key: str = field(default_factory=lambda: _env("LLM_FALLBACK_API_KEY", _env("ZHIPU_API_KEY")), repr=False)
    fallback_base_url: str = field(default_factory=lambda: _env("LLM_FALLBACK_BASE_URL", _env("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")))
    fallback_model: str = field(default_factory=lambda: _env("LLM_FALLBACK_MODEL", "glm-4-flash"))
    fallback_max_tokens: int = field(default_factory=lambda: _env_int("LLM_FALLBACK_MAX_TOKENS", 8192))
    fallback_temperature: float = field(default_factory=lambda: _env_float("LLM_FALLBACK_TEMPERATURE", 0.3))
    # 推理模型的思考过程是否发给前端（前端当“正在推理”提示用）
    emit_reasoning: bool = field(default_factory=lambda: _env_bool("LLM_EMIT_REASONING", True))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def fallback_configured(self) -> bool:
        return bool(self.fallback_enabled and self.fallback_api_key and self.fallback_base_url and self.fallback_model)

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("https://", "http://")) or not self.chat_model:
            raise ValueError("LLM_BASE_URL 或 LLM_CHAT_MODEL 无效")
        for name in ("max_output_tokens", "max_tokens", "summary_max_tokens", "rewrite_max_tokens", "fallback_max_tokens"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须为正整数")
        if self.max_tokens > self.max_output_tokens:
            raise ValueError("LLM_MAX_TOKENS 超过 LLM_MAX_OUTPUT_TOKENS")
        if self.summary_max_tokens > self.max_output_tokens or self.rewrite_max_tokens > self.max_output_tokens:
            raise ValueError("用途预算超过 LLM_MAX_OUTPUT_TOKENS")
        if self.max_retries <= 0 or self.timeout <= 0 or self.min_interval < 0:
            raise ValueError("LLM_MAX_RETRIES、LLM_TIMEOUT 或 LLM_MIN_INTERVAL 无效")
        for name in ("temperature", "fallback_temperature", "timeout", "min_interval"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"LLM_{name.upper()} 必须为非负有限数值")
        if self.fallback_temperature < 0.01:
            raise ValueError("LLM_FALLBACK_TEMPERATURE 不能小于智谱要求的 0.01")


# Temporary compatibility name for scripts and external callers.
ZhipuConfig = LLMConfig


@dataclass(frozen=True)
class SiliconFlowConfig:
    """硅基流动（SiliconFlow）——OpenAI 兼容接口。

    向量化用它的 BAAI/bge-m3，重排用 BAAI/bge-reranker-v2-m3。
    不在本机跑推理，免去 2 GB 权重与 CPU 推理的耗时。
    """

    api_key: str = field(default_factory=lambda: _env("SILICONFLOW_API_KEY"))
    base_url: str = field(default_factory=lambda: _env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"))
    embedding_model: str = field(default_factory=lambda: _env("SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-m3"))
    rerank_model: str = field(default_factory=lambda: _env("SILICONFLOW_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class EmbeddingConfig:
    """向量化配置。后端只保留远程 API 一种，避免两套代码路径。"""

    backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "siliconflow"))
    model_name: str = field(default_factory=lambda: _env("SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-m3"))
    dimension_override: int = field(default_factory=lambda: _env_int("EMBEDDING_DIMENSION", 0))
    batch_size: int = field(default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", 32))
    timeout: float = field(default_factory=lambda: _env_float("EMBEDDING_TIMEOUT", 60.0))
    max_retries: int = field(default_factory=lambda: _env_int("EMBEDDING_MAX_RETRIES", 3))

    @property
    def dimension(self) -> int:
        """bge-m3 的稠密向量维度固定 1024。"""
        return self.dimension_override or 1024


@dataclass(frozen=True)
class IndexConfig:
    """入库状态文件。

    单独拿出来是为了能被重定向：测试与冒烟脚本必须能
    把它指到临时目录，否则跑一次就会改掉真实开发环境的入库记录。
    """

    state_path: Path = field(
        default_factory=lambda: _resolve(_env("INDEX_STATE_PATH", "./data/index_state.json"))
    )
    registry_path: Path = field(
        default_factory=lambda: _resolve(_env("DOCUMENT_REGISTRY_PATH", "./data/documents.json"))
    )
    knowledge_base_path: Path | None = field(
        default_factory=lambda: _resolve(_env("KNOWLEDGE_BASE_REGISTRY_PATH"))
        if _env("KNOWLEDGE_BASE_REGISTRY_PATH") else None
    )


@dataclass(frozen=True)
class QdrantConfig:
    mode: str = field(default_factory=lambda: _env("QDRANT_MODE", "embedded"))
    path: Path = field(default_factory=lambda: _resolve(_env("QDRANT_PATH", "./data/qdrant")))
    url: str = field(default_factory=lambda: _env("QDRANT_URL", "http://127.0.0.1:6333"))
    api_key: str = field(default_factory=lambda: _env("QDRANT_API_KEY"))
    collection: str = field(default_factory=lambda: _env("QDRANT_COLLECTION", "corpus_chunks"))

    @property
    def is_embedded(self) -> bool:
        return self.mode != "server"


@dataclass(frozen=True)
class RetrievalConfig:
    vector_top_n: int = field(default_factory=lambda: _env_int("VECTOR_TOP_N", 80))
    bm25_top_n: int = field(default_factory=lambda: _env_int("BM25_TOP_N", 40))
    fusion_top_k: int = field(default_factory=lambda: _env_int("FUSION_TOP_K", 12))
    similarity_threshold: float = field(default_factory=lambda: _env_float("SIMILARITY_THRESHOLD", 0.35))
    rerank_enabled: bool = field(default_factory=lambda: _env_bool("RERANK_ENABLED", False))
    context_token_budget: int = field(default_factory=lambda: _env_int("CONTEXT_TOKEN_BUDGET", 6000))


@dataclass(frozen=True)
class QueryConfig:
    """查询改写（步骤 7）。

    rewrite_enabled 默认关：它是「按评测结论取舍」的增强项，
    打开前先看 scripts/compare_step7.py 的 A/B 结果，别凭感觉开。
    """

    rewrite_enabled: bool = field(default_factory=lambda: _env_bool("QUERY_REWRITE_ENABLED", False))
    max_subqueries: int = field(default_factory=lambda: _env_int("QUERY_REWRITE_MAX_SUBQUERIES", 3))


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = field(default_factory=lambda: _env_int("CHUNK_TARGET_TOKENS", 320))
    max_tokens: int = field(default_factory=lambda: _env_int("CHUNK_MAX_TOKENS", 800))
    overlap_tokens: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP_TOKENS", 40))


@dataclass(frozen=True)
class ServerConfig:
    host: str = field(default_factory=lambda: _env("FLASK_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("FLASK_PORT", 5000))
    cors_origins: list[str] = field(default_factory=lambda: _env_list("CORS_ORIGINS", "http://localhost:5173"))

    @property
    def upload_dir(self) -> Path:
        path = _resolve(_env("UPLOAD_DIR", "./data/uploads"))
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(frozen=True)
class EvalConfig:
    """评测与追踪（步骤 6）。

    路径全部走 IndexConfig 那一套思路：能重定向，否则跑一次评测
    会把真实数据集覆盖掉，测试也没法隔离。
    """

    langsmith_tracing: bool = field(default_factory=lambda: _env_bool("LANGSMITH_TRACING", False))
    langsmith_api_key: str = field(default_factory=lambda: _env("LANGSMITH_API_KEY"))
    langsmith_project: str = field(default_factory=lambda: _env("LANGSMITH_PROJECT", "corpus-rag"))

    @property
    def langsmith_enabled(self) -> bool:
        """开关打开**且**填了 Key 才算数。

        只看开关会得到一个必然失败的上报（缺 Key），
        那种"配了一半"的状态最难查，所以这里直接判否。
        """
        return bool(self.langsmith_tracing and self.langsmith_api_key)
    # 出题时最多生成多少道（一次出题调一次模型，控制成本）
    max_cases: int = field(default_factory=lambda: _env_int("EVAL_MAX_CASES", 12))
    # 每份文档最多出几道，避免长文档把题量占满
    cases_per_document: int = field(default_factory=lambda: _env_int("EVAL_CASES_PER_DOC", 2))
    # 评测集与跑分报告的落盘位置
    dataset_path: Path = field(
        default_factory=lambda: _resolve(_env("EVAL_DATASET_PATH", "./data/eval/dataset.json"))
    )
    report_dir: Path = field(
        default_factory=lambda: _resolve(_env("EVAL_REPORT_DIR", "./data/eval"))
    )
    # 跑评测时并发几路。
    # 实测这个 Key 的限流非常紧（429 码 1302 速率限制 / 1305 访问量过大），
    # 并发只会把两条请求同时撞死，所以评测默认串行。
    max_workers: int = field(default_factory=lambda: _env_int("EVAL_MAX_WORKERS", 1))
    # 两次模型调用之间的最小间隔（秒）。串行 + 节流是这台 Key 唯一稳的跑法。
    request_interval: float = field(default_factory=lambda: _env_float("EVAL_REQUEST_INTERVAL", 1.5))
    # 评测生成时用的 max_tokens，比线上宽松：
    # glm-4.7-flash 是推理模型，实测一道题的推理就吃掉 8000+ 字符，
    # 按线上 4096 给会导致「推理用完预算、正文空白」，评测直接失真。
    # 实测 8192 可用，16384 被拒。
    generation_max_tokens: int = field(
        default_factory=lambda: _env_int("EVAL_GENERATION_MAX_TOKENS", 8192)
    )
    dataset_max_tokens: int = field(default_factory=lambda: _env_int("EVAL_DATASET_MAX_TOKENS", 2048))
    # 裁判使用主 DeepSeek 连接；GLM 备用仍走独立连接和独立预算。
    judge_model: str = field(default_factory=lambda: _env("EVAL_JUDGE_MODEL", "deepseek-v4-flash"))
    # 首次判分预算；输出达到预算时最多用双倍预算重判一次，并保留两次用量。
    judge_max_tokens: int = field(default_factory=lambda: _env_int("EVAL_JUDGE_MAX_TOKENS", 8192))
    # judge 请求之间的间隔。判分调用比生成密集得多（每条样本要判十几次），
    # 间隔必须比 EVAL_REQUEST_INTERVAL 大，否则 account 级限流立刻打穿。
    judge_interval: float = field(default_factory=lambda: _env_float("EVAL_JUDGE_INTERVAL", 3.0))
    # judge 并发：RAGAS 内部一次跑多个指标，同样要压住
    judge_max_workers: int = field(default_factory=lambda: _env_int("EVAL_JUDGE_MAX_WORKERS", 2))
    # 评测时把上下文预算压低，避免单条样本的 prompt 无限膨胀（也顺带省 token）
    context_budget: int = field(default_factory=lambda: _env_int("EVAL_CONTEXT_BUDGET", 4000))

    def __post_init__(self) -> None:
        if min(self.dataset_max_tokens, self.generation_max_tokens, self.judge_max_tokens) <= 0:
            raise ValueError("评测输出预算必须为正整数")
        for name in ("request_interval", "judge_interval"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"EVAL_{name.upper()} 必须为非负有限数值")


@dataclass(frozen=True)
class Settings:
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    siliconflow: SiliconFlowConfig = field(default_factory=SiliconFlowConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    query: QueryConfig = field(default_factory=QueryConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)
    backend_dir: Path = BACKEND_DIR

    def __post_init__(self) -> None:
        if max(self.eval.dataset_max_tokens, self.eval.generation_max_tokens,
               self.eval.judge_max_tokens) > self.llm.max_output_tokens:
            raise ValueError("评测输出预算超过 LLM_MAX_OUTPUT_TOKENS")

    @property
    def zhipu(self) -> LLMConfig:
        """Compatibility alias for integrations that still read the old name."""
        return self.llm


settings = Settings()
