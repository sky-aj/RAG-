<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import MarkdownAnswer from './MarkdownAnswer.vue'
import CitationCard from './CitationCard.vue'
import { chatApi } from '@/api/client'
import { useWorkspace } from '@/composables/useWorkspace'

const { messages, params, activeThreadId, activeKnowledgeBase, useKnowledgeBase, view, switchView, scopedDocuments: documents } = useWorkspace()
const scrollRef = ref<HTMLElement | null>(null)
const openReasoning = ref<Record<string, boolean>>({})
const openCitations = ref<Record<string, boolean>>({})
const openDetails = ref<Record<string, boolean>>({})
const panelRef = ref<HTMLDetailsElement | null>(null)
let scrollNode: HTMLElement | null = null
let followEnd = true
function trackScroll() {
  if (scrollNode) followEnd = scrollNode.scrollHeight - scrollNode.scrollTop - scrollNode.clientHeight < 100
}
function closePanel() { if (panelRef.value) panelRef.value.open = false }
function outside(event: PointerEvent) {
  if (panelRef.value?.open && !event.composedPath().includes(panelRef.value)) closePanel()
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape' && panelRef.value?.open) {
    closePanel(); panelRef.value.querySelector('summary')?.focus()
  }
}
onMounted(() => {
  scrollNode = scrollRef.value?.closest<HTMLElement>('.scroll') ?? null
  scrollNode?.addEventListener('scroll', trackScroll, { passive: true })
  document.addEventListener('pointerdown', outside)
  document.addEventListener('keydown', escape)
})
onBeforeUnmount(() => {
  scrollNode?.removeEventListener('scroll', trackScroll)
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('keydown', escape)
})
watch([activeThreadId, view, useKnowledgeBase], () => { closePanel(); followEnd = true })
function citationId(messageId: string, index: number) { return 'citation-' + messageId + '-' + index }
async function cite(messageId: string, index: number) {
  followEnd = false
  openDetails.value[messageId] = true
  openCitations.value[citationId(messageId, index)] = true
  await nextTick()
  document.getElementById(citationId(messageId, index))?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}
function toggleCitations(message: (typeof messages.value)[number]) {
  followEnd = false
  const allOpen = message.citations?.every((citation) => openCitations.value[citationId(message.id, citation.index)])
  message.citations?.forEach((citation) => { openCitations.value[citationId(message.id, citation.index)] = !allOpen })
}
function toggleCitation(id: string, open: boolean) { followEnd = false; openCitations.value[id] = open }

const indexedCount = computed(() => documents.value.filter((doc) => !doc.deletion_pending && (doc.chunks || 0) > 0).length)
const documentCount = computed(() => documents.value.filter((doc) => !doc.deletion_pending).length)

/* 面板默认值取后端的真实配置，不写死：
   否则界面显示「重排开启」、后端按 .env 跑的却是「关闭」，两边对不上。
   只在首次挂载同步一次，用户手动调过的值不会被覆盖。 */
let synced = false
onMounted(async () => {
  if (synced) return
  try {
    const config = await chatApi.config()
    params.topK = config.retrieval.top_k
    params.threshold = config.retrieval.similarity_threshold
    params.rerank = config.retrieval.rerank_enabled
    synced = true
  } catch {
    /* 后端没起来就先用本地默认值 */
  }
})

const scrollToEnd = () => {
  nextTick(() => {
    if (scrollNode && followEnd && view.value === 'chat') scrollNode.scrollTop = scrollNode.scrollHeight
  })
}

watch(messages, scrollToEnd, { deep: true })


/* 重排分从高到低，一眼看出精排拉开了多少差距。 */
function rerankSpan(message: (typeof messages.value)[number]): string {
  const scores = (message.hits ?? [])
    .map((hit) => hit.rerank_score)
    .filter((value): value is number => typeof value === 'number')
  if (scores.length < 2) return ''
  return scores[0].toFixed(2) + ' → ' + scores[scores.length - 1].toFixed(2)
}

function label(message: (typeof messages.value)[number]): string {
  if (message.role === 'user') return '提问'
  if (message.streaming) return '生成中'
  if (message.error) return '出错'
  return '回答'
}
</script>

<template>
  <section class="view is-active">
    <div class="chat-head">
      <div>
        <h1 class="page-title">{{ useKnowledgeBase ? '知识库问答' : '普通问答' }}</h1>
        <p class="page-sub">{{ useKnowledgeBase ? '检索：' + (activeKnowledgeBase?.name ?? '标签已失效，请重新选择') + ' · 回答附带引用片段。' : '本次不检索知识库，保留当前会话上下文。' }}</p>
      </div>

      <details v-show="useKnowledgeBase" ref="panelRef" class="retrieval-panel">
        <summary class="field-toggle">
          <AppIcon name="sliders" />
          <span>检索参数</span>
          <span class="chip">top {{ params.topK }} · {{ params.threshold.toFixed(2) }}</span>
        </summary>
        <div class="field-pop">
          <label class="field">
            <span>召回条数 top-k</span>
            <input v-model.number="params.topK" type="range" min="1" max="12">
            <output>{{ params.topK }}</output>
          </label>
          <label class="field">
            <span>相似度阈值</span>
            <input v-model.number="params.threshold" type="range" min="0" max="0.9" step="0.05">
            <output>{{ params.threshold.toFixed(2) }}</output>
          </label>
          <label class="field field--row">
            <span>重排序 rerank</span>
            <input v-model="params.rerank" type="checkbox">
          </label>
          <label class="field field--row">
            <span>混合检索 BM25 + 向量</span>
            <input v-model="params.hybrid" type="checkbox">
          </label>
        </div>
      </details>
    </div>

    <ol ref="scrollRef" class="messages">
      <li v-if="!messages.length" class="empty-state">
        <h2>{{ useKnowledgeBase ? '向知识库提问' : '开始普通问答' }}</h2>
        <p v-if="!useKnowledgeBase">可以直接提问，本次将使用模型回答，无需上传文档。</p>
        <p v-else-if="indexedCount">当前分类有 {{ indexedCount }} 份文档已入库。问一句试试。</p>
        <p v-else-if="documentCount">当前分类有 {{ documentCount }} 份文件，尚无可检索片段。请在「知识库 → 向量库」点击「增量入库」。</p>
        <p v-else>当前分类没有文件，请先上传或移入文件，再进行入库。</p>
        <button v-if="useKnowledgeBase && !indexedCount" class="ghost-btn" type="button" @click="switchView('kb')">前往知识库</button>
        <ul v-if="useKnowledgeBase" class="empty-tips">
          <li>检索走混合双路：语义找「意思相近」，关键字找「原词命中」，RRF 融合后可选精排。</li>
          <li>回答里的【片段 N】对应下面的引用卡片，卡片带文件名与行号，能回到原文。</li>
        </ul>
      </li>

      <li v-for="message in messages" :id="'message-' + message.id" :key="message.id" class="msg" :class="'msg--' + message.role">
        <div class="avatar">{{ message.role === 'user' ? '你' : 'RAG' }}</div>
        <div class="msg-body">
          <div class="msg-meta">
            <span>{{ label(message) }}</span>
            <span>{{ message.time }}</span>
          </div>

          <p v-if="message.role === 'user'" class="msg-text">{{ message.text }}</p>

          <span v-else-if="message.pending || (message.streaming && !message.answer && !message.citations?.length && !message.error)" class="typing"><i /><i /><i /></span>

          <template v-else>
            <div v-if="message.reasoning" class="reasoning">
              <button class="reasoning-toggle" type="button" @click="openReasoning[message.id] = !openReasoning[message.id]">
                <AppIcon name="sliders" :size="13" />
                <span>{{ openReasoning[message.id] ? '收起思考过程' : '查看思考过程' }}</span>
                <span class="chip">{{ message.reasoning.length }} 字</span>
              </button>
              <p v-if="openReasoning[message.id]" class="reasoning-text">{{ message.reasoning }}</p>
            </div>

            <p v-if="message.error" class="msg-error">{{ message.error }}</p>

            <p v-if="message.finishReason === 'length'" class="panel-note">
              回答达到本次输出上限，内容可能未写完。可以继续提问，让模型补充剩余内容。
            </p>

            <div v-if="message.notices?.length" class="diag-notes">
              <span v-for="(notice, index) in message.notices" :key="index" class="diag-note">{{ notice }}</span>
            </div>

            <MarkdownAnswer v-if="message.answer" :text="message.answer" :streaming="message.streaming" :citations="message.citations?.map((citation) => citation.index)" @cite="cite(message.id, $event)" @rendered="scrollToEnd" />

            <span v-if="message.streaming && message.answer" class="caret" />

            <details v-if="message.retrieval || message.trace?.length || message.actualModel || message.citations?.length" class="answer-details" :open="openDetails[message.id] ?? false" @toggle="openDetails[message.id] = ($event.target as HTMLDetailsElement).open">
              <summary>回答详情<span v-if="message.citations?.length" class="detail-count">· {{ message.citations.length }} 条来源</span><span class="fold-arrow" aria-hidden="true">›</span></summary>
              <div v-if="message.citations?.length" class="citations">
                <div class="citation-tools"><button type="button" @click="toggleCitations(message)">展开 / 收起全部</button></div>
                <CitationCard v-for="citation in message.citations" :id="citationId(message.id, citation.index)" :key="citation.index" :citation="citation" :open="openCitations[citationId(message.id, citation.index)] ?? false" @toggle="toggleCitation(citationId(message.id, citation.index), $event)" />
              </div>
              <div v-if="message.retrieval || message.trace?.length || message.actualModel" class="retrieval-detail-body">
                <p v-if="message.actualModel" class="panel-meta">本次模型：{{ message.actualModel }}</p>
                <p v-if="message.trace?.length" class="detail-trace">{{ message.trace.join(' · ') }}</p>
                <div v-if="message.retrieval?.notes?.length" class="retrieval-notes">
                  <p v-for="note in message.retrieval.notes" :key="note">{{ note }}</p>
                </div>
                <div v-if="message.retrieval" class="diagnostics">
                  <span class="diag-item">语义 {{ message.retrieval.vector_kept ?? 0 }}/{{ message.retrieval.vector_candidates ?? 0 }}</span>
                  <span v-if="message.retrieval.hybrid" class="diag-item">关键字 {{ message.retrieval.keyword_candidates ?? 0 }}</span>
                  <span class="diag-item">融合 {{ message.retrieval.fused ?? 0 }} → 返回 {{ message.retrieval.returned ?? 0 }}</span>
                  <span class="diag-item">top {{ message.request?.topK ?? '—' }} · 阈值 {{ (message.retrieval.threshold ?? message.request?.threshold)?.toFixed(2) ?? '—' }}</span>
                  <span v-if="message.retrieval.below_threshold" class="diag-item">阈值滤掉 {{ message.retrieval.below_threshold }}</span>
                  <span class="diag-item">重排 {{ message.retrieval.rerank_used ? '已生效' : '未启用' }}</span>
                  <span v-if="message.retrieval.rerank_used && rerankSpan(message)" class="diag-item">重排分 {{ rerankSpan(message) }}</span>
                  <span v-if="message.retrieval.context_tokens" class="diag-item">上下文 {{ message.retrieval.context_used }} 段 · {{ message.retrieval.context_tokens }} token</span>
                </div>
              </div>
            </details>

            <div v-if="message.paragraphs?.length" class="msg-text">
              <p v-for="(paragraph, index) in message.paragraphs" :key="index">{{ paragraph }}</p>
              <ul v-if="message.points?.length">
                <li v-for="point in message.points" :key="point">{{ point }}</li>
              </ul>
            </div>

          </template>
        </div>
      </li>
    </ol>
  </section>
</template>
