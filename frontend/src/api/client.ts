import type {
  ChunkingReport,
  DocumentDetail,
  DocumentRecord,
  EvalDatasetPayload,
  EvalHistoryItem,
  EvalMetric,
  EvalStreamProgress,
  EvalStreamQuestionEvent,
  EvalReportPayload,
  EvalStatus,
  HealthPayload,
  IndexReport,
  IndexStatus,
  KnowledgeBaseItem,
  ParsedElement,
  SearchResult,
  StrategyCatalog
} from '@/types'

import type { Citation, ModelAttempt, RetrievalDiagnostics, RetrievalHit } from '@/types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init)
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'error' in payload ? String(payload.error) : response.statusText
    throw new Error(detail || '请求失败')
  }
  return payload as T
}

export const api = {
  health: () => request<HealthPayload>('/health'),
  listKnowledgeBases: () => request<{ items: KnowledgeBaseItem[] }>('/knowledge-bases'),
  createKnowledgeBase: (name: string) => request<{ item: KnowledgeBaseItem }>('/knowledge-bases', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name })
  }),
  renameKnowledgeBase: (id: string, name: string) => request<{ item: KnowledgeBaseItem }>('/knowledge-bases/' + encodeURIComponent(id), {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name })
  }),
  deleteKnowledgeBase: (id: string) => request('/knowledge-bases/' + encodeURIComponent(id), { method: 'DELETE' }),
  moveDocument: (docId: string, kbId: string) => request<{ document: DocumentRecord }>('/documents/' + docId, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kb_id: kbId })
  }),

  listDocuments: () =>
    request<{ count: number; items: DocumentRecord[]; supported_suffixes: string[] }>('/documents'),

  documentDetail: (docId: string) => request<DocumentDetail>('/documents/' + docId),

  previewDocument: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<{ summary: DocumentDetail['summary'] & { elements: ParsedElement[] } }>(
      '/documents/preview',
      { method: 'POST', body }
    )
  },

  uploadDocument: (file: File, kbId = 'unclassified') => {
    const body = new FormData()
    body.append('file', file)
    body.append('kb_id', kbId)
    return request<{ document: DocumentRecord; duplicate?: boolean; notice?: string }>('/documents', { method: 'POST', body })
  },

  deleteDocument: (docId: string) => request<{ deleted: string }>('/documents/' + docId, { method: 'DELETE' }),

  strategyCatalog: () => request<StrategyCatalog>('/chunks/strategies'),

  chunksForDocument: (docId: string, options: { strategy?: string; targetTokens?: number } = {}) => {
    const query = new URLSearchParams()
    if (options.strategy) query.set('strategy', options.strategy)
    if (options.targetTokens) query.set('target_tokens', String(options.targetTokens))
    const suffix = query.toString() ? '?' + query.toString() : ''
    return request<ChunkingReport>('/chunks/' + docId + suffix)
  },

  indexStatus: (kbId?: string) => request<IndexStatus>('/index' + (kbId ? '?kb_id=' + encodeURIComponent(kbId) : '')),

  indexDocuments: (options: { docIds?: string[]; force?: boolean; kbId?: string } = {}) =>
    request<IndexReport>('/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: options.docIds ?? [], kb_id: options.kbId, force: Boolean(options.force) })
    }),

  clearIndex: (options: { docIds?: string[]; kbId?: string; resetAll?: boolean }) =>
    request<{ ok: boolean; removed?: number; total_points: number }>('/index', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: options.docIds, kb_id: options.kbId, reset_all: options.resetAll ?? false })
    }),

  search: (query: string, options: { topN?: number; docIds?: string[]; kbId?: string } = {}) =>
    request<SearchResult>('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, top_n: options.topN ?? 80, doc_id: options.docIds ?? [], kb_id: options.kbId })
    }),

  previewChunks: (file: File, options: { strategy?: string; targetTokens?: number } = {}) => {
    const query = new URLSearchParams()
    if (options.strategy) query.set('strategy', options.strategy)
    if (options.targetTokens) query.set('target_tokens', String(options.targetTokens))
    const suffix = query.toString() ? '?' + query.toString() : ''
    const body = new FormData()
    body.append('file', file)
    return request<ChunkingReport>('/chunks/preview' + suffix, { method: 'POST', body })
  },

  chunkDetail: (chunkIds: string[]) =>
    request<{ count: number; items: Record<string, unknown>[]; missing: number }>('/chunks/fetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chunk_ids: chunkIds })
    }),

  // ---------- 步骤 6：评测与追踪 ----------

  /** 评测集与最近一次跑分的状态。 */
  evalStatus: () => request<EvalStatus>('/eval'),

  /** 从已入库文档反向出题生成评测集（要调模型，慢）。 */
  buildEvalDataset: (options: { limit?: number; perDocument?: number; docIds?: string[] } = {}) =>
    request<{ ok: boolean; count: number; path: string; notes: string[]; cases: Record<string, unknown>[] }>(
      '/eval/dataset',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          limit: options.limit,
          per_document: options.perDocument,
          doc_id: options.docIds ?? []
        })
      }
    ),

  /**
   * 流式生成评测集。普通 JSON 接口仍保留给脚本和不支持流式的调用方。
   * 每个 question 事件都带已生成题目，done 事件带完整数据集结果。
   */
  buildEvalDatasetStream: (
    options: { limit?: number; perDocument?: number; docIds?: string[] } = {},
    handlers: EvalStreamHandlers = {},
    signal?: AbortSignal
  ) => streamEval('/eval/dataset/stream', {
    limit: options.limit,
    per_document: options.perDocument,
    doc_id: options.docIds ?? []
  }, handlers, signal),

  evalDataset: () => request<EvalDatasetPayload>('/eval/dataset'),

  /** 跑一轮评测：真实 RAG 链路 + RAGAS 打分。很慢（分钟级）。 */
  runEval: (options: { limit?: number; withRagas?: boolean; maxWorkers?: number } = {}) =>
    request<EvalReportPayload>('/eval/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        limit: options.limit,
        with_ragas: options.withRagas ?? true,
        max_workers: options.maxWorkers
      })
    }),

  /** 流式跑评测：按样本报告生成回答、判分和当前均值。 */
  runEvalStream: (
    options: { limit?: number; withRagas?: boolean; maxWorkers?: number } = {},
    handlers: EvalStreamHandlers = {},
    signal?: AbortSignal
  ) => streamEval('/eval/run/stream', {
    limit: options.limit,
    with_ragas: options.withRagas ?? true,
    max_workers: options.maxWorkers
  }, handlers, signal),

  evalLatest: () => request<EvalReportPayload>('/eval/latest'),

  evalHistory: (limit = 10) => request<{ items: EvalHistoryItem[] }>('/eval/history?limit=' + limit)
}

export interface EvalStreamHandlers {
  onStage?: (event: EvalStreamProgress) => void
  onProgress?: (event: EvalStreamProgress) => void
  onQuestion?: (event: EvalStreamQuestionEvent) => void
  onDone?: (event: Record<string, unknown>) => void
  onError?: (message: string, event?: Record<string, unknown>) => void
}

/**
 * 评测 SSE 的通用 fetch 消费器。与聊天流分开，避免把评测的未知进度事件
 * 误当成聊天协议；同时保留分片 UTF-8、CRLF 和唯一终态校验。
 */
async function streamEval(
  path: string,
  body: Record<string, unknown>,
  handlers: EvalStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch('/api' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal
  })

  if (!response.ok) {
    const text = await response.text()
    let detail = text
    try {
      const parsed = JSON.parse(text)
      if (parsed && typeof parsed === 'object' && 'error' in parsed) detail = String(parsed.error)
    } catch {
      /* 保持原文 */
    }
    throw new Error(detail || `HTTP ${response.status}`)
  }

  const reader = response.body?.getReader()
  if (!reader) throw new Error('浏览器不支持流式响应')
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let terminal = false

  const dispatch = (raw: string) => {
    if (terminal) return
    const payload = raw.split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart()).join('\n').trim()
    if (!payload) return
    let event: Record<string, unknown>
    try {
      event = JSON.parse(payload) as Record<string, unknown>
    } catch {
      throw new Error('评测流式响应 JSON 无效')
    }
    if (!event || typeof event !== 'object') throw new Error('评测流式响应格式无效')

    const type = String(event.type ?? '')
    switch (type) {
      case 'stage':
        handlers.onStage?.(event as EvalStreamProgress)
        break
      case 'progress':
        handlers.onProgress?.(event as EvalStreamProgress)
        break
      case 'question':
        handlers.onQuestion?.(event as unknown as EvalStreamQuestionEvent)
        break
      case 'done':
        terminal = true
        handlers.onDone?.(event)
        break
      case 'error':
        terminal = true
        {
          const message = String(event.message ?? event.error ?? '评测失败')
          handlers.onError?.(message, event)
          throw new Error(message)
        }
      default:
        // 将未来新增的非终态事件交给通用进度回调，避免前端因扩展字段中断。
        handlers.onProgress?.(event as EvalStreamProgress)
        break
    }
  }

  try {
    while (!terminal) {
      const { value, done } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        if (buffer.trim()) dispatch(buffer)
        if (!terminal) throw new Error('评测流意外中断，已保留收到的进度，请重试')
        break
      }
      buffer += decoder.decode(value, { stream: true })
      let separator = /\r?\n\r?\n/.exec(buffer)
      while (separator && !terminal) {
        dispatch(buffer.slice(0, separator.index))
        buffer = buffer.slice(separator.index + separator[0].length)
        separator = /\r?\n\r?\n/.exec(buffer)
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

// ---------- 步骤 5：混合检索与流式对话 ----------

export interface ChatStreamOptions {
  question: string
  kbId?: string
  useKnowledgeBase?: boolean
  topK?: number
  threshold?: number
  hybrid?: boolean
  rerank?: boolean
  docIds?: string[]
  history?: { role: 'user' | 'assistant'; content: string }[]
}

export interface ChatStreamHandlers {
  onSources?: (payload: { citations: Citation[]; hits: RetrievalHit[]; diagnostics: RetrievalDiagnostics; knowledgeBaseUsed?: boolean }) => void
  onReasoning?: (text: string) => void
  onContent?: (text: string) => void
  onNotice?: (payload: { text: string; reason?: string; target_model?: string }) => void
  onDone?: (payload: ChatStreamResult) => void
  onError?: (message: string, payload?: ChatStreamResult) => void
}

interface ChatStreamResult {
  finish_reason?: string
  usage?: Record<string, number> | null
  actual_model?: string
  actual_provider?: string
  attempts?: ModelAttempt[]
}

export interface RetrieveResult {
  query: string
  hits: RetrievalHit[]
  citations: Citation[]
  context_text: string
  diagnostics: RetrievalDiagnostics
  error?: string
}

/** 逐行解析 SSE：一次网络分片可能包含多行，也可能把一行劈成两半。 */
async function readSse(response: Response, handlers: ChatStreamHandlers): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('浏览器不支持流式响应')

  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let terminal = false

  const dispatch = (raw: string) => {
    if (terminal) return
    const payload = raw.split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart()).join('\n').trim()
    if (!payload) return
    let event: Record<string, unknown>
    try {
      event = JSON.parse(payload) as Record<string, unknown>
    } catch {
      throw new Error('流式响应 JSON 无效，已保留收到的内容')
    }
    if (!event || typeof event !== 'object') throw new Error('流式响应格式无效')
    const result = (): ChatStreamResult => ({
      finish_reason: event.finish_reason as string | undefined,
      usage: event.usage as Record<string, number> | null | undefined,
      actual_model: event.actual_model as string | undefined,
      actual_provider: event.actual_provider as string | undefined,
      attempts: event.attempts as ModelAttempt[] | undefined
    })
    switch (event.type) {
      case 'sources':
        handlers.onSources?.({
          citations: (event.citations as Citation[]) ?? [],
          hits: (event.hits as RetrievalHit[]) ?? [],
          diagnostics: (event.diagnostics as RetrievalDiagnostics) ?? {},
          knowledgeBaseUsed: event.knowledge_base_used as boolean | undefined
        })
        break
      case 'reasoning':
        handlers.onReasoning?.(String(event.text ?? ''))
        break
      case 'content':
        handlers.onContent?.(String(event.text ?? ''))
        break
      case 'notice':
        handlers.onNotice?.({ text: String(event.text ?? ''), reason: event.reason as string | undefined,
          target_model: event.target_model as string | undefined })
        break
      case 'done':
        terminal = true
        handlers.onDone?.(result())
        break
      case 'error':
        terminal = true
        handlers.onError?.(String(event.message ?? '未知错误'), result())
        break
      default:
        break
    }
  }

  try {
    while (!terminal) {
      const { value, done } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        if (buffer.trim()) dispatch(buffer)
        if (!terminal) throw new Error('回答流意外中断，已保留收到的内容，请重试')
        break
      }
      buffer += decoder.decode(value, { stream: true })
      let separator = /\r?\n\r?\n/.exec(buffer)
      while (separator && !terminal) {
        dispatch(buffer.slice(0, separator.index))
        buffer = buffer.slice(separator.index + separator[0].length)
        separator = /\r?\n\r?\n/.exec(buffer)
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export const chatApi = {
  /** 后端当前的检索与生成参数，用来给前端面板定默认值。 */
  config: () =>
    request<{
      generation: Record<string, unknown>
      retrieval: {
        top_k: number
        vector_top_n: number
        keyword_top_n: number
        similarity_threshold: number
        rerank_enabled: boolean
        context_token_budget: number
      }
      bm25: Record<string, unknown>
      rerank: Record<string, unknown>
      context: Record<string, unknown>
      pipeline: Record<string, unknown>
    }>('/chat/config'),

  /** 只要检索结果，不生成回答。用来调参和排查召回。 */
  retrieve: (options: ChatStreamOptions) =>
    request<RetrieveResult>('/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: options.question,
        top_k: options.topK,
        threshold: options.threshold,
        hybrid: options.hybrid,
        rerank: options.rerank,
        doc_id: options.docIds ?? [],
        kb_id: options.kbId
      })
    }),

  /** 流式对话。用原生 fetch 读 SSE —— AI SDK 作为可选增强层放到步骤 7。 */
  async stream(options: ChatStreamOptions, handlers: ChatStreamHandlers, signal?: AbortSignal): Promise<void> {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: options.question,
        top_k: options.topK,
        threshold: options.threshold,
        hybrid: options.hybrid,
        rerank: options.rerank,
        doc_id: options.docIds ?? [],
        kb_id: options.kbId,
        use_knowledge_base: options.useKnowledgeBase ?? true,
        history: options.history ?? []
      }),
      signal
    })

    if (!response.ok) {
      const text = await response.text()
      let detail = text
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed === 'object' && 'error' in parsed) detail = String(parsed.error)
      } catch {
        /* 保持原文 */
      }
      throw new Error(detail || `HTTP ${response.status}`)
    }

    await readSse(response, handlers)
  },

}
