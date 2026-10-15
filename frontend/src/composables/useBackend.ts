import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'
import type { DocumentRecord, HealthPayload, KnowledgeBaseItem } from '@/types'

type Status = 'pending' | 'online' | 'offline'

const status = ref<Status>('pending')
const health = ref<HealthPayload | null>(null)
const documents = ref<DocumentRecord[]>([])
const knowledgeBases = ref<KnowledgeBaseItem[]>([
  { id: 'all', name: '全部文档', docs: 0, builtin: true },
  { id: 'unclassified', name: '未分类', docs: 0, builtin: true }
])
const knowledgeError = ref('')
const lastError = ref('')
let timer: number | undefined

async function refreshHealth(): Promise<void> {
  try {
    health.value = await api.health()
    status.value = 'online'
    lastError.value = ''
  } catch (error) {
    status.value = 'offline'
    lastError.value = error instanceof Error ? error.message : String(error)
  }
}

async function refreshDocuments(): Promise<void> {
  try {
    documents.value = (await api.listDocuments()).items
  } catch {
    /* 后端没起来时保持空列表，页面会提示 */
  }
}

async function refreshKnowledgeBases(): Promise<void> {
  try {
    knowledgeBases.value = (await api.listKnowledgeBases()).items
    knowledgeError.value = ''
  } catch (error) {
    knowledgeError.value = error instanceof Error ? error.message : String(error)
  }
}

const STAGES: { step: number; label: string; caps: string[] }[] = [
  { step: 1, label: '骨架跑通', caps: ['step1_skeleton'] },
  { step: 2, label: '上传与解析', caps: ['step2_documents', 'step2_markdown', 'step2_spreadsheet'] },
  { step: 3, label: '分块策略', caps: ['step3_chunking', 'step3_tree_sitter'] },
  { step: 4, label: '向量化与入库', caps: ['step4_embedding', 'step4_qdrant'] },
  { step: 5, label: '混合检索与对话', caps: ['step5_retrieval', 'step5_chat'] },
  { step: 6, label: 'RAGAS 评测与追踪', caps: ['step6_dataset', 'step6_ragas', 'step6_tracing'] }
]

// 顶栏徽标：从 /api/health 的 capabilities 推导，不写死。
const stageLabel = computed(() => {
  const caps = health.value?.capabilities
  if (!caps) return '后端未连接'
  let done = 0
  let next: { step: number; label: string; caps: string[] } | null = null
  let nextPartial = false
  for (const stage of STAGES) {
    const hit = stage.caps.filter((c) => caps[c]).length
    if (hit === stage.caps.length) {
      done = stage.step
      continue
    }
    if (!next) {
      next = stage
      nextPartial = hit > 0
    }
  }
  if (next && nextPartial) return '步骤 ' + next.step + ' · ' + next.label + ' 进行中'
  if (done) {
    const stage = STAGES.find((s) => s.step === done)
    return '步骤 ' + done + ' · ' + (stage ? stage.label : '') + ' 已跑通'
  }
  return '步骤 1 · 骨架跑通'
})

export function useBackend() {
  onMounted(async () => {
    await Promise.all([refreshHealth(), refreshDocuments(), refreshKnowledgeBases()])
    timer = window.setInterval(refreshHealth, 15000)
  })

  onUnmounted(() => {
    if (timer) window.clearInterval(timer)
  })

  return { status, health, documents, knowledgeBases, knowledgeError, refreshKnowledgeBases, lastError, stageLabel, refreshHealth, refreshDocuments }
}

export function useBackendState() {
  return { status, health, documents, knowledgeBases, knowledgeError, refreshKnowledgeBases, lastError, stageLabel, refreshHealth, refreshDocuments }
}
