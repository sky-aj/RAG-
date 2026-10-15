export type ThemeId = 'minimalist' | 'soft' | 'brutalist' | 'stitch'
export type AccentId = 'ink' | 'indigo' | 'emerald' | 'rose' | 'amber' | 'hazard'
export type FontScaleId = 'sm' | 'md' | 'lg' | 'xl'
export type ViewId = 'chat' | 'kb'

export interface HealthConfig {
  embedding_backend: string
  embedding_model: string
  embedding_dimension: number
  chat_model: string
  llm_provider: string
  llm_configured: boolean
  fallback_configured: boolean
  zhipu_configured?: boolean
  qdrant_mode: string
  qdrant_collection: string
  langsmith_tracing: boolean
}

export interface HealthPayload {
  status: string
  service: string
  time: string
  python: { version: string; executable: string; platform: string }
  config: HealthConfig
  documents: { count: number }
  dependencies: Record<string, { installed: boolean; step: string }>
  missing_by_step: Record<string, string[]>
  capabilities: Record<string, boolean>
}

export interface OutlineEntry {
  title: string
  level: number
}

export interface OutlineNode {
  kind: string
  level: number
  text: string
  line_start: number
  line_end: number
  page: number | null
}

export interface ParsedElement {
  kind: string
  level: number
  language: string
  line_start: number
  line_end: number
  page: number | null
  section: string
  text: string
}

export interface DocumentRecord {
  doc_id: string
  kb_id?: string
  deletion_pending?: boolean
  name: string
  stored_name: string
  suffix: string
  size: number
  size_human: string
  status: string
  chunks: number
  uploaded_at: string
  step: number
  // 步骤 2 起：解析结果
  strategy?: string
  doc_type?: string
  parser?: string
  encoding?: string
  page_count?: number
  line_total?: number
  element_total?: number
  counts_by_kind?: Record<string, number>
  outline?: OutlineNode[]
  outline_entries?: OutlineEntry[]
  preview?: { kind: string; line_start: number; line_end: number; text: string }[]
  warnings?: string[]
  error?: string | null
}

export interface DocumentDetail {
  document: DocumentRecord
  summary: {
    parser: string
    doc_type: string
    strategy: string
    encoding: string
    page_count: number
    line_total: number
    element_total: number
    counts_by_kind: Record<string, number>
    outline: OutlineNode[]
    outline_entries: OutlineEntry[]
    warnings: string[]
  }
  elements: ParsedElement[]
}

export interface Citation {
  index: number
  doc_id?: string
  doc_name?: string
  location: string
  line_start?: number
  line_end?: number
  page?: number | null
  language?: string
  symbol?: string
  summary?: string
  heading_path?: string[]
  score: number
  chunk_ids?: string[]
  /* 早期占位字段，保留以兼容旧数据 */
  source?: string
  quote?: string
}

export interface RetrievalHit {
  chunk_id: string
  doc_name: string
  line_start: number
  line_end: number
  score: number
  rerank_score?: number
  routes?: string[]
  symbol?: string
}

export interface RetrievalDiagnostics {
  hybrid?: boolean
  vector_candidates?: number
  vector_kept?: number
  keyword_candidates?: number
  fused?: number
  overflow?: number
  returned?: number
  threshold?: number
  per_document?: number
  vector_top_n?: number
  keyword_top_n?: number
  rerank_requested?: boolean
  rerank_used?: boolean
  below_threshold?: number
  context_tokens?: number
  context_budget?: number
  context_used?: number
  context_dropped?: number
  merged_from_hits?: number
  notes?: string[]
}

export interface ModelAttempt {
  model: string
  provider?: string
  usage?: Record<string, number> | null
  finish_reason?: string | null
  error?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text?: string
  paragraphs?: string[]
  points?: string[]
  citations?: Citation[]
  trace?: string[]
  time: string
  pending?: boolean
  /* 步骤 5：真实流式的字段 */
  answer?: string
  reasoning?: string
  hits?: RetrievalHit[]
  retrieval?: RetrievalDiagnostics
  streaming?: boolean
  finishReason?: string
  usage?: Record<string, number> | null
  actualModel?: string
  actualProvider?: string
  attempts?: ModelAttempt[]
  notices?: string[]
  error?: string
  knowledgeBaseUsed?: boolean
  request?: RetrievalParams & { useKnowledgeBase: boolean; kbId: string; kbName: string }
}

export interface ThreadItem {
  id: string
  title: string
  count: number
  updated: string
  kbId?: string
  useKnowledgeBase?: boolean
}

export interface KnowledgeBaseItem {
  id: string
  name: string
  docs: number
  builtin?: boolean
  indexed?: number
  chunks?: number
  /* 显式分类中空数组表示没有文档。 */
  docIds?: string[]
}

export interface RetrievalParams {
  topK: number
  threshold: number
  rerank: boolean
  hybrid: boolean
}

export interface ChunkRecord {
  chunk_id: string
  doc_id: string
  doc_name: string
  text: string
  line_start: number
  line_end: number
  page: number | null
  section: string
  heading_path: string[]
  language: string
  symbol: string
  summary: string
  kind: string
  strategy: string
  index: number
  meta: Record<string, unknown>
  char_count: number
}

export interface ChunkingReport {
  doc_id: string
  doc_name: string
  strategy: string
  chunk_total: number
  notes: string[]
  stats: {
    target_tokens: number
    max_tokens: number
    overlap_tokens: number
    char_total: number
    char_min: number
    char_max: number
    char_avg: number
  }
  details: Record<string, unknown>
  chunks: ChunkRecord[]
}

export interface StrategyOption {
  name: string
  handles: string[]
  description: string
}

export interface StrategyCatalog {
  strategies: StrategyOption[]
  defaults: { target_tokens: number; max_tokens: number; overlap_tokens: number }
}

// ---------- 步骤 4：向量化与入库 ----------

export interface IndexedDocument {
  doc_id: string
  doc_name: string
  chunks: number
}

export interface VectorStoreInfo {
  mode: string
  collection: string
  location: string
  ready: boolean
  points: number
  documents: number
  dimension?: number
  error?: string
  embedder: {
    backend: string
    provider: string
    base_url: string
    model: string
    declared_dimension: number
    batch_size: number
    configured: boolean
  }
}

export interface IndexStatus {
  store: VectorStoreInfo
  indexed: [string, { doc_name: string; strategy: string; chunks: number; indexed_at: string }][]
  indexed_count: number
  last_run: string | null
  scope?: { kb_id: string; documents: number; indexed_count: number; points: number }
}

export interface IndexReport {
  ok: boolean
  mode: string
  written: number
  chunk_total?: number
  total_points: number
  skipped: { doc_id: string; doc_name: string; reason: string }[]
  failed?: { doc_id: string; doc_name: string; error: string }[]
  documents: {
    doc_id: string
    doc_name: string
    ok: boolean
    strategy?: string
    chunks?: number
    chars?: number
    error?: string
  }[]
  store: VectorStoreInfo
  error?: string
}

export interface SearchHit {
  chunk_id: string
  doc_id: string
  doc_name: string
  text: string
  line_start: number
  line_end: number
  page: number | null
  section: string
  heading_path: string[]
  language: string
  symbol: string
  summary: string
  kind: string
  strategy: string
  index: number
  score: number
  point_id: number
}

export interface SearchResult {
  query: string
  requested_top_n: number
  returned: number
  kept: number
  threshold: number
  hits: SearchHit[]
  error?: string
}

// ---------- 步骤 6：评测与追踪 ----------

export interface EvalDatasetInfo {
  path: string
  exists: boolean
  count: number
  created_at: string
  max_cases: number
  per_document: number
  source_doc_ids?: string[]
}

/* 评测集相对当前文库是否已过期：过期不代表跑不了，只代表分数没法跟历史比 */
export interface EvalStaleness {
  stale: boolean
  reason: string
  missing_doc_ids: string[]
  added_doc_ids: string[]
}

export interface EvalConfig {
  max_cases: number
  cases_per_document: number
  max_workers: number
  langsmith_enabled: boolean
  report_dir: string
}

export type EvalMetric = 'faithfulness' | 'answer_relevancy' | 'context_precision' | 'context_recall'

export interface EvalStatus {
  dataset: EvalDatasetInfo
  staleness?: EvalStaleness
  report: { has_report: boolean; last_report_at: string }
  config: EvalConfig
  metrics: EvalMetric[]
}

export interface EvalCasePayload {
  case_id: string
  question: string
  ground_truth: string
  expected: { doc_id: string; doc_name: string; line_start: number; line_end: number }
  source_text?: string
  generation_model?: string
}

export interface EvalDatasetPayload {
  name: string
  created_at: string
  count: number
  notes: string[]
  cases: EvalCasePayload[]
}

export interface EvalStreamProgress {
  type: 'progress' | 'stage' | string
  phase?: string
  message?: string
  case_id?: string
  index?: number
  completed?: number
  total?: number
  scores?: Partial<Record<EvalMetric, number | null>>
  running_scores?: Partial<Record<EvalMetric, number | null>>
  score_counts?: Partial<Record<EvalMetric, number>>
  [key: string]: unknown
}

export interface EvalStreamQuestionEvent {
  type: 'question'
  case?: EvalCasePayload | Record<string, unknown>
  completed?: number
  total?: number
  candidate_index?: number
  candidate_total?: number
  [key: string]: unknown
}

export interface EvalRetrievalMetrics {
  case_count?: number
  hit_rate?: number
  avg_contexts?: number
  empty_answers?: number
  failed_answers?: number
}

export interface EvalPerCase {
  case_id: string
  question: string
  ground_truth: string
  answer: string
  contexts: string[]
  expected: { doc_id: string; doc_name: string; line_start: number; line_end: number }
  hit: boolean
  top_doc: string
  usage: Record<string, number> & { error?: string; reasoning_chars?: number }
  actual_model?: string
  judge_model?: string
  judge_attempts?: { model: string; provider: string; max_tokens: number; case_ids: string[];
    usage?: Record<string, unknown> | null; finish_reasons: (string | null)[] }[]
  notices?: string[]
  scores?: Partial<Record<EvalMetric, number | null>>
}

export interface EvalVariantResult {
  name: string
  params: Record<string, unknown>
  scores: Partial<Record<EvalMetric, number | null>>
  score_counts?: Partial<Record<EvalMetric, number>>
  retrieval: EvalRetrievalMetrics
  error: string
  per_case: EvalPerCase[]
}

export interface EvalReportPayload {
  created_at: string
  dataset_count: number
  notes: string[]
  ok?: boolean
  path?: string
  variants: EvalVariantResult[]
}

export interface EvalHistoryItem {
  file: string
  created_at: string
  dataset_count: number
  variants: { name: string; scores: Record<string, number | null>; score_counts?: Partial<Record<EvalMetric, number>>; retrieval: EvalRetrievalMetrics }[]
}

export interface TraceSpan {
  name: string
  ms: number
  fields: Record<string, unknown>
  error: string
}

export interface TracePayload {
  name: string
  run_id: string
  started_at: string
  total_ms: number
  langsmith: boolean
  spans: TraceSpan[]
}
