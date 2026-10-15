<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { api } from '@/api/client'
import type { DocumentRecord, IndexStatus, SearchResult } from '@/types'

const props = defineProps<{
  doc: DocumentRecord | null
  documents: DocumentRecord[]
  kbId: string
  kbName: string
}>()

const emit = defineEmits<{ (event: 'indexed'): void }>()

const status = ref<IndexStatus | null>(null)
const clearing = ref(false)
const indexing = ref(false)
const notice = ref('')
const error = ref('')

const query = ref('')
const searching = ref(false)
const result = ref<SearchResult | null>(null)

/* 当前分类 / 当前文档，跨分类需显式选择全部文档。 */
const scopeAll = ref(true)

const store = computed(() => status.value?.store ?? null)
const indexedIds = computed(() => new Set((status.value?.indexed ?? []).map(([id]) => id)))
const pending = computed(() => props.documents.filter((doc) => !indexedIds.value.has(doc.doc_id)))
const scopedPoints = computed(() => (status.value?.indexed ?? []).filter(([id]) => props.documents.some((doc) => doc.doc_id === id)).reduce((total, [, item]) => total + item.chunks, 0))
let refreshId = 0

function toast(message: string) {
  notice.value = message
  window.setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 4000)
}

async function refresh() {
  const id = ++refreshId
  const kbId = props.kbId
  try {
    const result = await api.indexStatus(kbId)
    if (id !== refreshId || kbId !== props.kbId) return
    status.value = result
    error.value = ''
  } catch (caught) {
    if (id === refreshId && kbId === props.kbId) error.value = (caught as Error).message
  }
}

async function runIndex(force: boolean) {
  const kbId = props.kbId
  const docIds = props.documents.filter((doc) => !doc.deletion_pending).map((doc) => doc.doc_id)
  indexing.value = true
  error.value = ''
  try {
    const report = await api.indexDocuments({
      docIds, kbId,
      force
    })
    const failed = report.failed ?? []
    if (kbId === props.kbId) {
      toast(`写入 ${report.written} 块，跳过 ${report.skipped.length} 份` + (failed.length ? `，失败 ${failed.length} 份` : ''))
      if (failed.length) error.value = failed.map((item) => item.doc_name + '：' + item.error).join('；')
    }
    await refresh()
    emit('indexed')
  } catch (caught) {
    if (kbId === props.kbId) error.value = (caught as Error).message
  } finally {
    indexing.value = false
  }
}

async function clearIndex() {
  const kbId = props.kbId
  const docIds = props.documents.map((doc) => doc.doc_id)
  if (!window.confirm('清空“' + props.kbName + '”的向量？影响 ' + docIds.length + ' 份文档，上传文件与标签保留。')) return
  clearing.value = true
  try {
    const payload = await api.clearIndex(kbId === 'all' ? { kbId, resetAll: true } : { kbId, docIds })
    if (kbId === props.kbId) toast('已清空，全库剩余 ' + payload.total_points + ' 点')
    if (kbId === props.kbId) result.value = null
    await refresh()
    emit('indexed')
  } catch (caught) {
    if (kbId === props.kbId) error.value = (caught as Error).message
  } finally {
    clearing.value = false
  }
}

async function runSearch() {
  const text = query.value.trim()
  if (!text || searching.value) return
  searching.value = true
  error.value = ''
  const kbId = props.kbId
  const all = scopeAll.value
  const docId = all ? undefined : props.doc?.doc_id
  const currentScope = () => kbId === props.kbId && all === scopeAll.value && (all || docId === props.doc?.doc_id)
  try {
    const found = await api.search(text, {
      topN: 80,
      kbId, docIds: docId ? [docId] : undefined
    })
    if (currentScope()) result.value = found
  } catch (caught) {
    if (currentScope()) {
      error.value = (caught as Error).message
      result.value = null
    }
  } finally {
    searching.value = false
  }
}

onMounted(refresh)
watch(() => props.kbId, () => { result.value = null; status.value = null; notice.value = ''; error.value = ''; refresh() })
watch(() => props.doc?.doc_id, () => { if (!scopeAll.value) result.value = null })
watch(scopeAll, () => { result.value = null })
defineExpose({ refresh })
</script>

<template>
  <div class="panel-block">
    <div class="chunk-controls">
      <button class="solid-btn" type="button" :disabled="indexing || clearing || !documents.length" @click="runIndex(false)">
        <AppIcon name="plus" />{{ indexing ? '入库中…' : '增量入库' }}
      </button>
      <button class="ghost-btn" type="button" :disabled="indexing || clearing || !documents.length" @click="runIndex(true)">
        重新入库当前分类
      </button>
      <button class="ghost-btn" type="button" :disabled="clearing || indexing || !documents.length" @click="clearIndex">{{ kbId === 'all' ? '清空全库向量' : '清空当前分类向量' }}</button>
      <span class="field-inline">
        <span>待入库</span>
        <code>{{ pending.length }} / {{ documents.length }}</code>
      </span>
    </div>

    <p v-if="notice" class="panel-note">{{ notice }}</p>
    <p v-if="error" class="panel-note is-warn">{{ error }}</p>

    <template v-if="store">
      <div class="meta-grid">
        <div><span>模式</span><code>{{ store.mode }}</code></div>
        <div><span>collection</span><code>{{ store.collection }}</code></div>
        <div><span>当前分类已入库</span><code>{{ documents.length - pending.length }}</code></div>
        <div><span>当前分类块数 / 全库</span><code>{{ scopedPoints }} / {{ store.points }}</code></div>
        <div><span>向量维度</span><code>{{ store.dimension ?? store.embedder.declared_dimension }}</code></div>
        <div><span>嵌入模型</span><code>{{ store.embedder.model }}</code></div>
      </div>
      <p class="strategy-desc">
        向量走 {{ store.embedder.provider }} 的 {{ store.embedder.model }}
        （{{ store.embedder.base_url }}），一次请求最多 {{ store.embedder.batch_size }} 条；库位置 {{ store.location }}。
      </p>
      <p v-if="store.error" class="panel-note is-warn">向量库探测异常：{{ store.error }}</p>
    </template>
    <p v-else-if="!error" class="empty">正在读取向量库状态…</p>

    <div class="search-row">
      <AppIcon name="search" :size="15" />
      <input
        v-model="query"
        type="text"
        placeholder="纯语义检索试一下：滑动窗口怎么统计请求量"
        @keydown.enter="runSearch"
      >
      <label class="field-inline">
        <input v-model="scopeAll" type="checkbox">
        <span>当前分类</span>
      </label>
      <button class="solid-btn solid-btn--sm" type="button" :disabled="searching || !query.trim() || !documents.length || (!scopeAll && !doc)" @click="runSearch">
        {{ searching ? '检索中…' : '检索' }}
      </button>
    </div>

    <template v-if="result">
      <p class="panel-note">
        返回 {{ result.returned }} 条（一次请求取回 {{ result.requested_top_n }} 条候选），
        过阈值 {{ result.threshold }} 的有 {{ result.kept }} 条。
      </p>
      <div class="chunk-box">
        <details v-for="hit in result.hits" :key="hit.chunk_id" class="chunk search-snippet">
          <summary>
            <span>{{ hit.doc_name }} · 行 {{ hit.line_start }}–{{ hit.line_end }}</span>
            <span class="chunk-kind">score {{ hit.score.toFixed(3) }}</span>
          </summary>
          <p v-if="hit.symbol" class="chunk-symbol">{{ hit.symbol }}</p>
          <p v-if="hit.heading_path.length" class="chunk-path">{{ hit.heading_path.join(' › ') }}</p>
          <p class="chunk-body">{{ hit.text }}</p>
        </details>
      </div>
      <p v-if="!result.hits.length" class="empty">没有过阈值的片段，试试换个问法。</p>
    </template>
  </div>
</template>
