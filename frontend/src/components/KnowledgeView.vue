<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ChunkPreview from './ChunkPreview.vue'
import EvalPanel from './EvalPanel.vue'
import IndexPanel from './IndexPanel.vue'
import ParsedStructure from './ParsedStructure.vue'
import { api } from '@/api/client'
import { useBackendState } from '@/composables/useBackend'
import { useWorkspace } from '@/composables/useWorkspace'
import type { DocumentDetail, DocumentRecord, ParsedElement, StrategyCatalog } from '@/types'

const { status, health, refreshHealth, refreshDocuments, refreshKnowledgeBases } = useBackendState()
const { documentStats, scopedDocuments: documents, activeKbId, activeKnowledgeBase, knowledgeBases } = useWorkspace()
const uploadKb = ref('unclassified')
const typeFilter = ref('')
const visibleDocuments = computed(() => documents.value.filter((doc) => !typeFilter.value || doc.doc_type === typeFilter.value))
const docTypes = computed(() => [...new Set(documents.value.map((doc) => doc.doc_type).filter(Boolean))])
const destinations = computed(() => knowledgeBases.value.filter((kb) => kb.id !== 'all'))

const dragging = ref(false)
const busy = ref(false)
const notice = ref('')
const fileInputRef = ref<HTMLInputElement | null>(null)

const pickedDoc = ref<DocumentRecord | null>(null)
const detail = ref<DocumentDetail | null>(null)
const detailLoading = ref(false)

const catalog = ref<StrategyCatalog | null>(null)
/* 右侧面板在两个视图间切换，避免一屏塞两块长列表 */
const panel = ref<'chunks' | 'structure' | 'index' | 'eval'>('chunks')

const stats = computed(() => documentStats(documents.value))
const pendingCount = computed(() => documents.value.filter((doc) => !doc.deletion_pending && !(doc.chunks > 0)).length)

const steps = computed(() => [
  { label: '步骤 2 · 解析', ready: health.value?.capabilities.step2_documents ?? false },
  { label: '步骤 3 · 分块', ready: health.value?.capabilities.step3_chunking ?? false },
  { label: '步骤 4 · 向量化', ready: health.value?.capabilities.step4_embedding ?? false },
  { label: '步骤 4 · Qdrant', ready: health.value?.capabilities.step4_qdrant ?? false }
])

function badgeClass(record: DocumentRecord) {
  if (record.status === '解析失败') return 'badge badge--danger'
  if (record.status === '已解析') return 'badge'
  return 'badge badge--muted'
}

function openPicker() {
  fileInputRef.value?.click()
}

async function upload(files: FileList | File[] | null) {
  if (!files) return
  const list = Array.from(files)
  if (!list.length) return

  busy.value = true
  notice.value = ''
  const failed: string[] = []
  const notices: string[] = []
  const targetKb = activeKbId.value === 'all' ? uploadKb.value : activeKbId.value
  for (const file of list) {
    try {
      const result = await api.uploadDocument(file, targetKb)
      if (result.notice) notices.push(file.name + '：' + result.notice)
    } catch (error) {
      failed.push(file.name + '：' + (error as Error).message)
    }
  }
  busy.value = false
  notice.value = [...failed, ...notices].join('；') || list.length + ' 个文件已解析完成，请在「向量库」点击「增量入库」后检索'
  await refreshAll()
  if (!failed.length && documents.value.length) await select(documents.value[0])
}

async function select(record: DocumentRecord) {
  pickedDoc.value = record
  detail.value = null
  detailLoading.value = true
  try {
    const result = await api.documentDetail(record.doc_id)
    if (pickedDoc.value?.doc_id === record.doc_id) detail.value = result
  } catch (error) {
    notice.value = (error as Error).message
  } finally {
    if (pickedDoc.value?.doc_id === record.doc_id) detailLoading.value = false
  }
}

function onDrop(event: DragEvent) {
  dragging.value = false
  upload(event.dataTransfer?.files ?? null)
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  upload(input.files)
  input.value = ''
}

async function remove(record: DocumentRecord) {
  if (!window.confirm('删除文档“' + record.name + '”及其向量？')) return
  try { await api.deleteDocument(record.doc_id) }
  catch (error) { notice.value = (error as Error).message; await refreshAll(); return }
  if (pickedDoc.value?.doc_id === record.doc_id) {
    pickedDoc.value = null
    detail.value = null
  }
  await refreshAll()
}

async function refreshAll() { await Promise.all([refreshDocuments(), refreshHealth(), refreshKnowledgeBases()]) }
async function move(record: DocumentRecord, event: Event) {
  try {
    const { document } = await api.moveDocument(record.doc_id, (event.target as HTMLSelectElement).value)
    notice.value = document.chunks > 0 ? '文档已移动，已有向量继续复用，可在目标分类检索'
      : '文档已移动，尚无可检索片段，请在目标分类的「向量库」点击「增量入库」'
    await refreshAll()
  } catch (error) { notice.value = (error as Error).message }
}

const detailElements = computed<ParsedElement[]>(() => detail.value?.elements ?? [])

async function loadCatalog() {
  try {
    catalog.value = await api.strategyCatalog()
  } catch {
    catalog.value = null
  }
}

onMounted(async () => {
  await loadCatalog()
  if (documents.value.length) select(documents.value[0])
})

watch([activeKbId, () => visibleDocuments.value.map((doc) => doc.doc_id).join(',')], () => {
  if (!visibleDocuments.value.some((doc) => doc.doc_id === pickedDoc.value?.doc_id)) {
    pickedDoc.value = null; detail.value = null
    if (visibleDocuments.value[0]) select(visibleDocuments.value[0])
  }
})
</script>

<template>
  <section class="view is-active">
    <div class="kb-head">
      <div>
        <h1 class="page-title">{{ activeKnowledgeBase?.name ?? '标签已失效' }}</h1>
        <p class="page-sub">
          上传后立刻解析、并按文件类型切分：四条策略各管一类文件，右侧可以切换策略与目标大小现场对比。
        </p>
      </div>
      <label v-if="activeKbId === 'all'" class="field-inline">上传到
        <select v-model="uploadKb" aria-label="上传目标标签"><option v-for="kb in destinations" :key="kb.id" :value="kb.id">{{ kb.name }}</option></select>
      </label>
      <button class="solid-btn" type="button" :disabled="busy" @click="openPicker">
        <AppIcon name="clip" />{{ busy ? '处理中…' : '上传文件' }}
      </button>
    </div>

    <div class="stats">
      <dl v-for="item in stats" :key="item.label" class="stat">
        <dt>{{ item.label }}</dt>
        <dd>{{ item.value }}<small v-if="item.unit">{{ item.unit }}</small></dd>
      </dl>
    </div>

    <div v-if="status === 'offline'" class="placeholder-note">
      <AppIcon name="close" :size="15" />后端未连接，先跑 <code>backend/scripts/run_dev.ps1</code>。
    </div>

    <section class="panel readiness">
      <header class="panel-head">
        <h2>链路就绪情况</h2>
        <span class="panel-meta">来自 GET /api/health</span>
      </header>
      <div class="health-grid">
        <div v-for="step in steps" :key="step.label" class="health-item" :class="{ 'is-missing': !step.ready }">
          <span>{{ step.label }}</span>
          <code>{{ step.ready ? '就绪' : '待接入' }}</code>
        </div>
      </div>
    </section>

    <div class="kb-grid">
      <section class="panel panel--docs">
        <header class="panel-head">
          <h2>文档</h2>
          <select v-model="typeFilter" aria-label="文档类型筛选"><option value="">全部类型</option><option v-for="type in docTypes" :key="type" :value="type">{{ type }}</option></select>
          <span class="panel-meta">{{ documents.length }} 个文件</span>
        </header>

        <div
          class="dropzone"
          :class="{ 'is-over': dragging }"
          tabindex="0"
          @click="openPicker"
          @keydown.enter.prevent="openPicker"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
        >
          <AppIcon name="clip" :size="26" />
          <p>把文件拖到这里，或点击选择</p>
          <small>PDF / DOCX / MD / TXT / CSV / XLSX / 代码文件，单文件上限 50 MB</small>
        </div>
        <input ref="fileInputRef" type="file" multiple hidden @change="onPick">

        <p v-if="notice" class="panel-note">{{ notice }}</p>
        <p v-if="pendingCount" class="panel-note">
          当前分类有 {{ pendingCount }} 份文件待入库，上传或移动文件后仍需完成向量化才能检索。
          <button class="ghost-btn" type="button" @click="panel = 'index'">前往入库</button>
        </p>

        <div class="table-wrap">
          <table class="table table--docs">
            <thead>
              <tr><th>名称</th><th>类型</th><th>标签</th><th class="num">块</th><th class="num">行</th><th>状态</th><th /></tr>
            </thead>
            <tbody>
              <tr
                v-for="doc in visibleDocuments"
                :key="doc.doc_id"
                :class="{ 'is-picked': pickedDoc?.doc_id === doc.doc_id }"
                @click="select(doc)"
              >
                <td>
                  <span class="name">
                    <AppIcon name="doc" :size="15" />
                    <span>{{ doc.name }}</span>
                  </span>
                </td>
                <td>{{ (doc.doc_type || doc.suffix.replace('.', '')).toUpperCase() }}</td>
                <td><select :value="doc.kb_id ?? 'unclassified'" :aria-label="'移动文档 ' + doc.name" @click.stop @change="move(doc, $event)"><option v-for="kb in destinations" :key="kb.id" :value="kb.id">{{ kb.name }}</option></select></td>
                <td class="num">{{ doc.chunks || 0 }}</td>
                <td class="num">{{ doc.line_total ?? 0 }}</td>
                <td><span :class="badgeClass(doc)">{{ doc.deletion_pending ? '清理待重试' : doc.status === '解析失败' ? doc.status : doc.chunks > 0 ? '已入库' : '待入库' }}</span></td>
                <td class="num">
                  <button class="micro-btn" type="button" title="删除" @click.stop="remove(doc)">
                    <AppIcon name="close" :size="13" />
                  </button>
                </td>
              </tr>
              <tr v-if="!visibleDocuments.length">
                <td colspan="7"><p class="empty">还没有上传文件。</p></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel panel--chunks">
        <header class="panel-head">
          <h2>{{ pickedDoc ? pickedDoc.name : '文档结果' }}</h2>
          <div class="panel-tabs">
            <button
              class="panel-tab"
              :class="{ 'is-active': panel === 'chunks' }"
              type="button"
              @click="panel = 'chunks'"
            >分块结果</button>
            <button
              class="panel-tab"
              :class="{ 'is-active': panel === 'structure' }"
              type="button"
              @click="panel = 'structure'"
            >解析结构</button>
            <button
              class="panel-tab"
              :class="{ 'is-active': panel === 'index' }"
              type="button"
              @click="panel = 'index'"
            >向量库</button>
            <button
              class="panel-tab"
              :class="{ 'is-active': panel === 'eval' }"
              type="button"
              @click="panel = 'eval'"
            >评测</button>
          </div>
        </header>

        <KeepAlive>
          <ChunkPreview v-if="panel === 'chunks'" :doc="pickedDoc" :catalog="catalog" />
          <ParsedStructure v-else-if="panel === 'structure'" :detail="detail" :loading="detailLoading" :elements="detailElements" />
          <IndexPanel v-else-if="panel === 'index'" :doc="pickedDoc" :documents="documents" :kb-id="activeKbId" :kb-name="activeKnowledgeBase?.name ?? '标签已失效'" @indexed="refreshAll" />
          <EvalPanel v-else />
        </KeepAlive>
      </section>
    </div>
  </section>
</template>
