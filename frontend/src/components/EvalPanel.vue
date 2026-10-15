<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { api } from '@/api/client'
import type {
  EvalCasePayload,
  EvalHistoryItem,
  EvalMetric,
  EvalReportPayload,
  EvalStatus
} from '@/types'

const METRICS: { key: EvalMetric; label: string; hint: string }[] = [
  { key: 'faithfulness', label: '忠实度', hint: '回答是否只依据召回片段' },
  { key: 'answer_relevancy', label: '相关性', hint: '回答是否切题' },
  { key: 'context_precision', label: '精确率', hint: '召回片段里有多少是真正有用的' },
  { key: 'context_recall', label: '召回率', hint: '该用到的片段有没有进上下文' }
]

const status = ref<EvalStatus | null>(null)
const report = ref<EvalReportPayload | null>(null)
const history = ref<EvalHistoryItem[]>([])

const limit = ref(3)
const building = ref(false)
const running = ref(false)
const loading = ref(false)
const notice = ref('')
const error = ref('')

interface LiveDatasetProgress {
  stage: string
  status: string
  message: string
  completed: number
  total: number
  candidateCompleted: number
  candidateTotal: number
  questions: EvalCasePayload[]
}

interface LiveEvalProgress {
  phase: string
  message: string
  completed: number
  total: number
  caseId: string
  variant: string
  runningScores: Partial<Record<EvalMetric, number | null>>
  scoreCounts: Partial<Record<EvalMetric, number>>
  legacyTotals: Partial<Record<EvalMetric, number>>
  legacyCounts: Partial<Record<EvalMetric, number>>
}

const datasetProgress = ref<LiveDatasetProgress | null>(null)
const evalProgress = ref<LiveEvalProgress | null>(null)

const datasetCount = computed(() => status.value?.dataset.count ?? 0)
const variant = computed(() => report.value?.variants[0] ?? null)
const openCase = ref<string | null>(null)

function displayNotes(notes: string[]) {
  return [...new Set(notes)].join('；')
}

function pct(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return (value * 100).toFixed(1) + '%'
}

function scoreClass(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'is-null'
  if (value >= 0.8) return 'is-good'
  if (value >= 0.5) return 'is-mid'
  return 'is-bad'
}

function numberOr(value: unknown, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function phaseLabel(phase: string) {
  if (phase === 'answer' || phase === 'generate_answer') return '生成回答'
  if (phase === 'judge' || phase === 'score' || phase === 'scoring') return '判分'
  if (phase === 'dataset' || phase === 'question' || phase === 'generate_question') return '生成题目'
  return phase || '处理中'
}

function progressPercent(progress: { completed: number; total: number } | null) {
  if (!progress || progress.total <= 0) return 0
  return Math.min(100, Math.max(0, (progress.completed / progress.total) * 100))
}

function caseFromEvent(value: unknown): EvalCasePayload | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  const caseId = String(item.case_id ?? item.id ?? '')
  const question = String(item.question ?? '')
  if (!caseId && !question) return null
  return {
    case_id: caseId || `case-${Date.now()}`,
    question,
    ground_truth: String(item.ground_truth ?? item.answer ?? ''),
    expected: (item.expected && typeof item.expected === 'object' ? item.expected : {
      doc_id: '', doc_name: '', line_start: 0, line_end: 0
    }) as EvalCasePayload['expected'],
    source_text: typeof item.source_text === 'string' ? item.source_text : undefined,
    generation_model: typeof item.generation_model === 'string' ? item.generation_model : undefined
  }
}

function metricCount(metric: EvalMetric) {
  const reported = variant.value?.score_counts?.[metric]
  if (typeof reported === 'number' && Number.isFinite(reported)) return reported
  const items = variant.value?.per_case ?? []
  return items.reduce((count, item) => {
    const value = item.scores?.[metric]
    return count + (typeof value === 'number' && Number.isFinite(value) ? 1 : 0)
  }, 0)
}

function liveScore(metric: EvalMetric) {
  return evalProgress.value?.runningScores[metric]
}

function liveCount(metric: EvalMetric) {
  return evalProgress.value?.scoreCounts[metric] ?? evalProgress.value?.legacyCounts[metric] ?? 0
}

function progressCountLabel(progress: LiveEvalProgress) {
  if (progress.phase === 'judge' || progress.phase === 'score' || progress.phase === 'scoring') {
    return '已判分'
  }
  if (progress.phase === 'answer' || progress.phase === 'generate_answer') {
    return '已生成回答'
  }
  return '已处理'
}

function historyMetricCount(item: EvalHistoryItem, metric: EvalMetric) {
  const count = item.variants[0]?.score_counts?.[metric]
  return typeof count === 'number' && Number.isFinite(count)
    ? `有效 ${count} / ${item.dataset_count}`
    : '有效数未知'
}

function updateDatasetProgress(event: Record<string, unknown>) {
  const current = datasetProgress.value ?? {
    stage: 'dataset', status: '', message: '', completed: 0, total: limit.value,
    candidateCompleted: 0, candidateTotal: 0, questions: []
  }
  const candidate = caseFromEvent(event.case)
  if (candidate && !current.questions.some((item) => item.case_id === candidate.case_id)) {
    current.questions.push(candidate)
  }
  current.completed = numberOr(event.completed, current.questions.length || current.completed)
  current.total = numberOr(event.total, current.total || limit.value)
  current.message = typeof event.message === 'string' ? event.message : current.message
  current.stage = typeof event.phase === 'string' ? event.phase : current.stage
  current.status = typeof event.status === 'string' ? event.status : current.status
  current.candidateCompleted = numberOr(event.candidate_completed, current.candidateCompleted)
  current.candidateTotal = numberOr(event.candidate_total, current.candidateTotal)
  datasetProgress.value = current
}

function updateEvalProgress(event: Record<string, unknown>) {
  const current = evalProgress.value ?? {
    phase: 'answer', message: '', completed: 0, total: limit.value, caseId: '',
    variant: '', runningScores: {}, scoreCounts: {}, legacyTotals: {}, legacyCounts: {}
  }
  const eventVariant = typeof event.variant === 'string' ? event.variant : ''
  if (eventVariant && current.variant && eventVariant !== current.variant) {
    current.runningScores = {}
    current.scoreCounts = {}
    current.legacyTotals = {}
    current.legacyCounts = {}
    current.completed = 0
  }
  if (eventVariant) current.variant = eventVariant
  current.phase = typeof event.phase === 'string' ? event.phase : current.phase
  current.message = typeof event.message === 'string' ? event.message : current.message
  current.caseId = typeof event.case_id === 'string' ? event.case_id : current.caseId
  current.completed = numberOr(event.completed ?? event.index, current.completed)
  current.total = numberOr(event.total, current.total || limit.value)
  if (event.running_scores && typeof event.running_scores === 'object') {
    current.runningScores = {
      ...current.runningScores,
      ...(event.running_scores as Partial<Record<EvalMetric, number | null>>)
    }
  } else if (event.scores && typeof event.scores === 'object') {
    // 兼容旧后端：scores 是当前样本分数，不能直接当成累计平均值。
    for (const metric of METRICS) {
      const value = (event.scores as Partial<Record<EvalMetric, number | null>>)[metric.key]
      if (typeof value !== 'number' || !Number.isFinite(value)) continue
      const total = (current.legacyTotals[metric.key] ?? 0) + value
      const count = (current.legacyCounts[metric.key] ?? 0) + 1
      current.legacyTotals[metric.key] = total
      current.legacyCounts[metric.key] = count
      current.runningScores[metric.key] = total / count
    }
  }
  if (event.score_counts && typeof event.score_counts === 'object') {
    current.scoreCounts = {
      ...current.scoreCounts,
      ...(event.score_counts as Partial<Record<EvalMetric, number>>)
    }
  }
  evalProgress.value = current
}

async function refresh() {
  loading.value = true
  try {
    status.value = await api.evalStatus()
    error.value = ''
    try {
      report.value = await api.evalLatest()
    } catch {
      report.value = null
    }
    try {
      history.value = (await api.evalHistory(8)).items
    } catch {
      history.value = []
    }
  } catch (caught) {
    error.value = (caught as Error).message
  } finally {
    loading.value = false
  }
}

async function build() {
  building.value = true
  error.value = ''
  datasetProgress.value = {
    stage: 'dataset', status: 'waiting', message: '', completed: 0, total: limit.value,
    candidateCompleted: 0, candidateTotal: 0, questions: []
  }
  notice.value = '正在让模型给已入库片段出题…（每条样本一次调用）'
  try {
    let finalPayload: { count?: number; notes?: string[]; cases?: EvalCasePayload[] } | undefined
    await api.buildEvalDatasetStream({ limit: limit.value }, {
      onStage: (event) => updateDatasetProgress(event),
      onProgress: (event) => updateDatasetProgress(event),
      onQuestion: (event) => updateDatasetProgress(event),
      onDone: (event) => {
        finalPayload = {
          count: typeof event.count === 'number' ? event.count : undefined,
          notes: Array.isArray(event.notes) ? event.notes.filter((item): item is string => typeof item === 'string') : undefined
        }
        const cases = Array.isArray(event.cases) ? event.cases.map(caseFromEvent).filter(Boolean) as EvalCasePayload[] : []
        if (cases.length) {
          const current = datasetProgress.value
          if (current) current.questions = cases
        }
      },
      onError: (message) => { throw new Error(message) }
    })
    const payload = finalPayload
    const count = payload?.count ?? datasetProgress.value?.questions.length ?? 0
    const notes = payload?.notes ?? []
    notice.value = `生成 ${count} 道题` + (notes.length ? `；${displayNotes(notes)}` : '')
    await refresh()
  } catch (caught) {
    error.value = (caught as Error).message
    notice.value = ''
  } finally {
    building.value = false
  }
}

async function run() {
  running.value = true
  error.value = ''
  evalProgress.value = {
    phase: 'answer', message: '', completed: 0, total: limit.value, caseId: '',
    variant: '', runningScores: {}, scoreCounts: {}, legacyTotals: {}, legacyCounts: {}
  }
  notice.value = '评测会走真实 RAG 链路并逐条打分，限流较紧，可能要几分钟…'
  try {
    let finalPayload: EvalReportPayload | null = null
    await api.runEvalStream({ limit: limit.value }, {
      onStage: (event) => updateEvalProgress(event),
      onProgress: (event) => updateEvalProgress(event),
      onDone: (event) => {
        const candidate = event.report && typeof event.report === 'object' ? event.report : event
        if (candidate && typeof candidate === 'object') {
          finalPayload = {
            ...(candidate as EvalReportPayload),
            ok: event.ok as boolean | undefined,
            path: typeof event.path === 'string' ? event.path : undefined
          }
          report.value = finalPayload
        }
      },
      onError: (message) => { throw new Error(message) }
    })
    const payload = finalPayload ?? report.value
    if (!payload) throw new Error('评测流结束但没有返回报告')
    notice.value = `跑完 ${payload.dataset_count} 条样本` + (payload.notes.length ? `；${displayNotes(payload.notes)}` : '')
    await refresh()
  } catch (caught) {
    error.value = (caught as Error).message
    notice.value = ''
  } finally {
    running.value = false
  }
}

function toggleCase(caseId: string) {
  openCase.value = openCase.value === caseId ? null : caseId
}

onMounted(refresh)
defineExpose({ refresh })
</script>

<template>
  <div class="panel-block">
    <p class="panel-meta">全库评测 · 使用现有评测集，不随侧栏标签切换。</p>
    <div class="chunk-controls">
      <button class="solid-btn" type="button" :disabled="running || building" @click="run">
        <AppIcon name="check" />{{ running ? '跑分中…' : '跑一轮评测' }}
      </button>
      <button class="ghost-btn" type="button" :disabled="building || running" @click="build">
        {{ building ? '出题中…' : '生成评测集' }}
      </button>
      <span class="field-inline">
        <span>样本数</span>
        <input v-model.number="limit" class="num-input" type="number" min="1" max="12" :disabled="running || building">
      </span>
      <span class="field-inline">
        <span>评测集</span>
        <code>{{ datasetCount }} 条</code>
      </span>
      <span class="field-hint">改「样本数」后需重新生成评测集，跑分只读已有题目</span>
    </div>

    <p v-if="notice" class="panel-note">{{ notice }}</p>
    <p v-if="error" class="panel-note panel-note--error">{{ error }}</p>

    <section v-if="building && datasetProgress" class="eval-live" aria-live="polite" aria-atomic="false">
      <div class="eval-live-heading">
        <strong>正在生成评测题</strong>
        <span>{{ datasetProgress.completed }} / {{ datasetProgress.total }} 道</span>
      </div>
      <div class="eval-progress-track" role="progressbar" :aria-valuenow="datasetProgress.completed"
        :aria-valuemin="0" :aria-valuemax="datasetProgress.total">
        <span :style="{ width: `${progressPercent(datasetProgress)}%` }"></span>
      </div>
      <p class="eval-live-message">
        <template v-if="datasetProgress.message">{{ datasetProgress.message }} · </template>
        <template v-if="datasetProgress.candidateTotal">
          <template v-if="datasetProgress.status === 'started'">
            正在处理候选片段 {{ Math.min(datasetProgress.candidateCompleted + 1, datasetProgress.candidateTotal) }} / {{ datasetProgress.candidateTotal }}
          </template>
          <template v-else>
            已处理候选片段 {{ datasetProgress.candidateCompleted }} / {{ datasetProgress.candidateTotal }}
          </template>
          · 已生成 {{ datasetProgress.completed }} / {{ datasetProgress.total }} 道
        </template>
      </p>
      <div v-if="datasetProgress.questions.length" class="eval-live-questions">
        <article v-for="(item, index) in datasetProgress.questions" :key="item.case_id" class="eval-live-question">
          <span class="badge">已生成 {{ index + 1 }}</span>
          <strong>{{ item.question || '（题目文本为空）' }}</strong>
          <small v-if="item.ground_truth">标准答案：{{ item.ground_truth }}</small>
        </article>
      </div>
    </section>

    <section v-if="running && evalProgress" class="eval-live" aria-live="polite" aria-atomic="false">
      <div class="eval-live-heading">
        <strong>{{ phaseLabel(evalProgress.phase) }}中</strong>
        <span>{{ progressCountLabel(evalProgress) }} {{ evalProgress.completed }} / {{ evalProgress.total }} 条</span>
      </div>
      <div class="eval-progress-track" role="progressbar" :aria-valuenow="evalProgress.completed"
        :aria-valuemin="0" :aria-valuemax="evalProgress.total">
        <span :style="{ width: `${progressPercent(evalProgress)}%` }"></span>
      </div>
      <p class="eval-live-message">
        <template v-if="evalProgress.message">{{ evalProgress.message }}</template>
        <template v-else>当前阶段：{{ phaseLabel(evalProgress.phase) }}</template>
        <code v-if="evalProgress.caseId"> · {{ evalProgress.caseId }}</code>
      </p>
      <div class="eval-metrics eval-metrics--live">
        <div v-for="metric in METRICS" :key="metric.key" class="eval-metric">
          <span class="eval-metric-label">{{ metric.label }} · 当前平均</span>
          <strong :class="scoreClass(liveScore(metric.key))">{{ pct(liveScore(metric.key)) }}</strong>
          <small>有效 {{ liveCount(metric.key) }} / {{ progressCountLabel(evalProgress) }} {{ evalProgress.completed }} 条（共 {{ evalProgress.total }}）</small>
        </div>
      </div>
    </section>

    <p v-if="status?.staleness?.stale" class="panel-note panel-note--warn">
      <AppIcon name="sliders" :size="13" />
      评测集已过期：{{ status.staleness.reason }}。点「生成评测集」重建后，分数才和知识库对得上。
    </p>

    <div v-if="!report" class="empty">
      <template v-if="!datasetCount">还没有评测集。先点「生成评测集」——它会从已入库片段反向出题。</template>
      <template v-else>已有 {{ datasetCount }} 条样本，点「跑一轮评测」开始打分。</template>
    </div>

    <template v-else>
      <div class="eval-metrics">
        <div v-for="metric in METRICS" :key="metric.key" class="eval-metric">
          <span class="eval-metric-label">{{ metric.label }}</span>
          <strong :class="scoreClass(variant?.scores?.[metric.key])">
            {{ pct(variant?.scores?.[metric.key]) }}
          </strong>
          <small>{{ metric.hint }}</small>
          <small>有效 {{ metricCount(metric.key) }} / {{ report.dataset_count }} 条</small>
        </div>
      </div>

      <div class="eval-hard" v-if="variant">
        <span><AppIcon name="search" :size="13" />命中率 <code>{{ pct(variant.retrieval.hit_rate) }}</code></span>
        <span>平均上下文 <code>{{ variant.retrieval.avg_contexts ?? '—' }}</code></span>
        <span>空回答 <code>{{ variant.retrieval.empty_answers ?? 0 }}</code></span>
        <span>调用失败 <code>{{ variant.retrieval.failed_answers ?? 0 }}</code></span>
        <span>样本数 <code>{{ variant.retrieval.case_count ?? 0 }}</code></span>
      </div>

      <p v-if="variant?.error" class="panel-note panel-note--error">{{ variant.error }}</p>
      <p v-if="report.notes.length" class="panel-note">{{ displayNotes(report.notes) }}</p>

      <div class="eval-cases">
        <button
          v-for="item in variant?.per_case ?? []"
          :key="item.case_id"
          class="eval-case"
          type="button"
          @click="toggleCase(item.case_id)"
        >
          <header>
            <code>{{ item.case_id }}</code>
            <span class="badge" :class="item.hit ? '' : 'badge--muted'">{{ item.hit ? '命中' : '未命中' }}</span>
            <span class="eval-case-q">{{ item.question }}</span>
            <span v-if="openCase === item.case_id" class="eval-case-usage">
              上下文 {{ item.contexts.length }} 段
            </span>
          </header>
          <div v-if="openCase === item.case_id" class="eval-case-body">
            <dl>
              <div><dt>标准答案</dt><dd>{{ item.ground_truth }}</dd></div>
              <div><dt>模型回答</dt><dd>{{ item.answer || '（空）' }}</dd></div>
              <div v-if="item.actual_model"><dt>生成模型</dt><dd>{{ item.actual_model }}</dd></div>
              <div v-if="item.judge_model"><dt>裁判模型</dt><dd>{{ item.judge_model }}</dd></div>
              <div v-if="item.notices?.length"><dt>模型通知</dt><dd>{{ item.notices.join('；') }}</dd></div>
              <div>
                <dt>期望来源</dt>
                <dd><code>{{ item.expected.doc_name }} 行 {{ item.expected.line_start }}-{{ item.expected.line_end }}</code></dd>
              </div>
              <div v-if="item.scores">
                <dt>逐条分数</dt>
                <dd>
                  <span v-for="metric in METRICS" :key="metric.key" class="eval-chip">
                    {{ metric.label }} {{ pct(item.scores[metric.key]) }}
                  </span>
                </dd>
              </div>
              <div v-if="item.usage?.error">
                <dt>调用失败</dt><dd class="eval-error">{{ item.usage.error }}</dd>
              </div>
            </dl>
          </div>
        </button>
      </div>

      <div v-if="history.length" class="eval-history">
        <h3>历史跑分</h3>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>时间</th><th class="num">样本</th><th class="num">忠实度</th>
                <th class="num">相关性</th><th class="num">精确率</th><th class="num">召回率</th><th class="num">命中率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in history" :key="item.file">
                <td><code>{{ item.created_at.slice(0, 19).replace('T', ' ') }}</code></td>
                <td class="num">{{ item.dataset_count }}</td>
                <td class="num">
                  {{ pct(item.variants[0]?.scores?.faithfulness) }}
                  <small class="eval-history-count">{{ historyMetricCount(item, 'faithfulness') }}</small>
                </td>
                <td class="num">
                  {{ pct(item.variants[0]?.scores?.answer_relevancy) }}
                  <small class="eval-history-count">{{ historyMetricCount(item, 'answer_relevancy') }}</small>
                </td>
                <td class="num">
                  {{ pct(item.variants[0]?.scores?.context_precision) }}
                  <small class="eval-history-count">{{ historyMetricCount(item, 'context_precision') }}</small>
                </td>
                <td class="num">
                  {{ pct(item.variants[0]?.scores?.context_recall) }}
                  <small class="eval-history-count">{{ historyMetricCount(item, 'context_recall') }}</small>
                </td>
                <td class="num">{{ pct(item.variants[0]?.retrieval?.hit_rate) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p class="panel-meta eval-foot">
        LangSmith 追踪：{{ status?.config.langsmith_enabled ? '已开启' : '未配置' }} ·
        报告目录 <code>{{ status?.config.report_dir }}</code>
      </p>
    </template>
  </div>
</template>
