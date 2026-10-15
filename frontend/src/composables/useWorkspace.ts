import { computed, reactive, ref, watch } from 'vue'
import { chatApi } from '@/api/client'
import { THREADS } from '@/data/demo'
import { useBackendState } from '@/composables/useBackend'
import type { ChatMessage, DocumentRecord, RetrievalParams, ThreadItem, ViewId } from '@/types'

/* 会话与会话内容分开放：
   threads 只存侧栏要用的标题/条数，messagesByThread 才是每个会话真正的消息。
   以前 messages 是一个全局 ref，切会话只换标题、消息不动，新建会话还会把上一段清掉。 */
const view = ref<ViewId>('chat')
const threads = ref<ThreadItem[]>([])
const activeThreadId = ref('')
const messagesByThread = reactive<Record<string, ChatMessage[]>>({})
const params = reactive<RetrievalParams>({ topK: 5, threshold: 0.35, rerank: false, hybrid: true })
const attachments = ref<File[]>([])
const styleOpen = ref(false)
const streaming = ref(false)
let controller: AbortController | null = null

const STORAGE_KEY = 'corpus.threads.v1'

const drafts = reactive<Record<string, string>>({})

const messages = computed<ChatMessage[]>({
  get: () => messagesFor(activeThreadId.value),
  set: (next) => {
    messagesByThread[activeThreadId.value] = next
  }
})

const activeThread = computed(
  () => threads.value.find((item) => item.id === activeThreadId.value) ?? threads.value[0]
)
const activeKbId = computed({
  get: () => activeThread.value?.kbId ?? 'all',
  set: (id: string) => { activeThread.value.kbId = id; persist() }
})
const useKnowledgeBase = computed({
  get: () => activeThread.value?.useKnowledgeBase ?? true,
  set: (enabled: boolean) => { activeThread.value.useKnowledgeBase = enabled; persist() }
})
const draft = computed({
  get: () => drafts[activeThreadId.value] ?? '',
  set: (text: string) => { drafts[activeThreadId.value] = text }
})
const historyQuestions = computed(() => {
  const seen = new Set<string>()
  return [...messages.value].reverse().filter((message) => {
    if (message.role !== 'user' || !message.text?.trim() || message.pending) return false
    const key = message.text.trim().replace(/\s+/g, ' ')
    if (seen.has(key)) return false
    seen.add(key)
    return true
  }).slice(0, 8).map((message) => message.text!)
})

/* 取某个会话的消息数组。
   注意不能写 `messagesByThread[id] ?? (messagesByThread[id] = [])`：
   赋值表达式返回的是**原始数组**，不是 reactive 代理，往上面 push 不会触发更新，
   界面就会一直卡在「生成中」。必须赋值后再读一次属性拿回代理。 */
function messagesFor(id: string): ChatMessage[] {
  if (!messagesByThread[id]) messagesByThread[id] = []
  return messagesByThread[id]
}

function clock(): string {
  const now = new Date()
  return String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0')
}

/* 刷新页面后会话不该凭空消失。只落盘轻量字段：
   File 附件、流式中的临时状态都不序列化，出错的消息也跳过重建。 */
function persist() {
  try {
    const snapshot = {
      threads: threads.value,
      activeThreadId: activeThreadId.value,
      messagesByThread: Object.fromEntries(
        Object.entries(messagesByThread).map(([id, list]) => [
          id,
          list
            .filter((item) => !item.streaming && !item.pending)
            .map((item) => ({ ...item, streaming: false, pending: false }))
        ])
      )
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
  } catch {
    /* 隐私模式或配额满时静默跳过，不影响对话 */
  }
}

function restore() {
  let saved: { threads?: ThreadItem[]; activeThreadId?: string; messagesByThread?: Record<string, ChatMessage[]> } | null = null
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    saved = raw ? JSON.parse(raw) : null
  } catch {
    saved = null
  }

  const restored = saved?.threads?.length ? saved.threads : THREADS
  threads.value = restored.map((item) => ({ ...item }))
  for (const [id, list] of Object.entries(saved?.messagesByThread ?? {})) {
    messagesByThread[id] = list
  }
  activeThreadId.value =
    saved?.activeThreadId && restored.some((item) => item.id === saved?.activeThreadId)
      ? saved.activeThreadId
      : restored[0].id
}

restore()

function switchView(next: ViewId) {
  view.value = next
}

function selectThread(id: string) {
  if (id === activeThreadId.value) return
  // 切走前把上一条服务的流断掉，否则它会把内容续写到别的会话上
  stop()
  activeThreadId.value = id
  view.value = 'chat'
  persist()
}

function newThread() {
  stop()
  const id = 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
  threads.value.unshift({ id, title: '新会话', count: 0, updated: '刚刚' })
  messagesFor(id)
  activeThreadId.value = id
  view.value = 'chat'
  persist()
}

function removeThread(id: string) {
  if (id === activeThreadId.value) stop()
  delete messagesByThread[id]
  delete drafts[id]
  threads.value = threads.value.filter((item) => item.id !== id)
  if (!threads.value.length) {
    threads.value.push({ id: 't' + Date.now().toString(36), title: '新会话', count: 0, updated: '刚刚' })
  }
  if (!threads.value.some((item) => item.id === activeThreadId.value)) {
    activeThreadId.value = threads.value[0].id
  }
  persist()
}


function addAttachments(files: File[]) {
  attachments.value = [...attachments.value, ...files]
}

function removeAttachment(index: number) {
  attachments.value = attachments.value.filter((_, i) => i !== index)
}

function clearAttachments() {
  attachments.value = []
}

function paramsSummary(): string {
  return 'top ' + params.topK + ' · ' + params.threshold.toFixed(2)
}

/* 步骤 5：真的走 POST /api/chat 的 SSE。
   事件顺序是 sources -> reasoning -> content -> done，
   sources 先到，所以用户在等模型吐字时就能看到「找到了哪些材料」。 */
async function send(text: string) {
  const trimmed = text.trim()
  if (!trimmed || streaming.value) return

  // 本次请求锁定的会话：回答必须写回发起它的那个会话，
  // 中途用户切到别处也不能串台。
  const threadId = activeThreadId.value
  const list = messagesFor(threadId)

  const request = { ...params, useKnowledgeBase: useKnowledgeBase.value, kbId: activeKbId.value,
    kbName: activeKnowledgeBase.value?.name ?? '标签已失效' }
  const history = list
    .filter((item) => !item.pending && !item.error && (item.text || item.answer))
    .slice(-4)
    .map((item) => ({ role: item.role, content: (item.role === 'user' ? item.text : item.answer) ?? '' }))
    .filter((item) => item.content)

  const userId = 'u' + Date.now()
  list.push({ id: userId, role: 'user', text: trimmed, time: clock(), request })

  const answerId = 'a' + Date.now()
  list.push({
    id: answerId,
    role: 'assistant',
    time: clock(),
    streaming: true,
    answer: '',
    reasoning: '', request
  })

  const target = () => list.find((item) => item.id === answerId)
  const patch = (fields: Partial<ChatMessage>) => {
    const message = target()
    if (message) Object.assign(message, fields)
  }

  streaming.value = true
  const requestController = new AbortController()
  controller = requestController
  const started = performance.now()

  try {
    await chatApi.stream(
      {
        question: trimmed,
        topK: request.topK,
        threshold: request.threshold,
        hybrid: request.hybrid,
        rerank: request.rerank,
        kbId: request.kbId,
        useKnowledgeBase: request.useKnowledgeBase,
        history
      },
      {
        onSources: ({ citations, hits, diagnostics, knowledgeBaseUsed }) => patch({ citations, hits,
          retrieval: knowledgeBaseUsed === false ? undefined : diagnostics,
          knowledgeBaseUsed: knowledgeBaseUsed ?? request.useKnowledgeBase }),
        onReasoning: (chunk) => patch({ reasoning: (target()?.reasoning ?? '') + chunk }),
        onContent: (chunk) => patch({ answer: (target()?.answer ?? '') + chunk }),
        onNotice: ({ text }) => patch({ notices: [...(target()?.notices ?? []), text] }),
        onDone: ({ finish_reason, usage, actual_model, actual_provider, attempts }) => patch({
          finishReason: finish_reason, usage, actualModel: actual_model, actualProvider: actual_provider, attempts
        }),
        onError: (message, result) => patch({ error: message, actualModel: result?.actual_model,
          actualProvider: result?.actual_provider, attempts: result?.attempts })
      },
      requestController.signal
    )
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      patch({ error: error instanceof Error ? error.message : String(error) })
    }
  } finally {
    if (controller === requestController) {
      streaming.value = false
      controller = null
    }
    const message = target()
    if (message) {
      message.streaming = false
      const elapsed = Math.round(performance.now() - started)
      message.trace = traceOf(message, elapsed)
    }
    touchThread(threadId, trimmed)
    persist()
  }
}

function stop() {
  controller?.abort()
  controller = null
  streaming.value = false
  const last = messages.value[messages.value.length - 1]
  if (last && last.streaming) last.streaming = false
}

/* 头部这排只放「这次怎么查的、花了多久」；
   召回条数等细节放到正文下面的诊断条，避免两处重复同样的数字。 */
function traceOf(message: ChatMessage, elapsed: number): string[] {
  const snapshot = message.request ?? { ...params, useKnowledgeBase: true, kbName: '全部文档' }
  const rows = snapshot.useKnowledgeBase ? [snapshot.kbName,
    snapshot.hybrid ? 'hybrid · BM25 + 向量' : '仅向量',
    snapshot.rerank ? '重排 开启' : '重排 关闭'
  ] : ['普通问答 · 未检索知识库']
  if (message.usage?.total_tokens) {
    rows.push(`${(message.attempts?.length ?? 0) > 1 ? '最终请求 ' : ''}${message.usage.total_tokens} tokens`)
  }
  rows.push(`${(elapsed / 1000).toFixed(1)} s`)
  return rows
}

const { documents, knowledgeBases } = useBackendState()

const activeKnowledgeBase = computed(
  () => knowledgeBases.value.find((item) => item.id === activeKbId.value)
)
const scopedDocuments = computed(() => !activeKnowledgeBase.value ? [] : documents.value.filter((doc) =>
  activeKbId.value === 'all' || (doc.kb_id ?? 'unclassified') === activeKbId.value))
watch(activeThreadId, () => { attachments.value = [] })

function touchThread(threadId: string, question: string) {
  const thread = threads.value.find((item) => item.id === threadId)
  if (!thread) return
  if (thread.title === '新会话') {
    thread.title = question.length > 14 ? question.slice(0, 14) + '…' : question
  }
  thread.count = messagesByThread[threadId]?.length ?? thread.count + 2
  thread.updated = '刚刚'
}

function documentStats(documents: DocumentRecord[]) {
  const totalChunks = documents.reduce((sum, doc) => sum + (doc.chunks || 0), 0)
  return [
    { label: '文档总数', value: String(documents.length), unit: '份' },
    // 入库后，后端会把真实块数写回文档注册表，所以这里是实数。
    { label: '已入库块数', value: totalChunks.toLocaleString('en-US'), unit: '块' },
    { label: '向量维度', value: '1024', unit: 'dims' },
    { label: '嵌入模型', value: 'bge-m3', unit: '' }
  ]
}

export function useWorkspace() {
  return {
    view, threads, activeThreadId, activeThread,
    knowledgeBases, activeKbId, activeKnowledgeBase,
    scopedDocuments, useKnowledgeBase, draft, historyQuestions,
    params, attachments, messages, styleOpen, streaming,
    switchView, selectThread, newThread, removeThread, addAttachments, removeAttachment, clearAttachments,
    paramsSummary, send, stop, documentStats
  }
}
