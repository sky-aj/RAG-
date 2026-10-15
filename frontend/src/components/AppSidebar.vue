<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { api } from '@/api/client'
import AppIcon from './AppIcon.vue'
import { useBackendState } from '@/composables/useBackend'
import { useStyle } from '@/composables/useStyle'
import { useWorkspace } from '@/composables/useWorkspace'

const { threads, activeThreadId, knowledgeBases, activeKbId, view, selectThread, newThread, removeThread, switchView, styleOpen } = useWorkspace()
const { status, health, knowledgeError, refreshKnowledgeBases } = useBackendState()
const editing = ref<string | null>(null)
const labelName = ref('')
const labelBusy = ref(false)
const labelError = ref('')
const nameRef = ref<HTMLElement | null>(null)

async function editLabel(id: string, name = '') {
  if (labelBusy.value) return
  editing.value = id
  labelName.value = name
  labelError.value = ''
  if (id !== 'new') {
    await nextTick()
    const node = nameRef.value
    if (!node) return
    node.textContent = name
    node.focus()
    const range = document.createRange()
    range.selectNodeContents(node)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  }
}
async function saveLabel() {
  if (labelBusy.value || !editing.value) return
  const id = editing.value
  const name = labelName.value.trim()
  if (!name || name.length > 40) {
    labelError.value = '标签名称需要 1–40 个字符'
    return
  }
  if (id !== 'new' && knowledgeBases.value.find(kb => kb.id === id)?.name === name) {
    editing.value = null
    return
  }
  labelBusy.value = true
  try {
    if (id === 'new') {
      const { item } = await api.createKnowledgeBase(name)
      await refreshKnowledgeBases()
      activeKbId.value = item.id
    } else {
      await api.renameKnowledgeBase(id, name)
      await refreshKnowledgeBases()
    }
    editing.value = null
  } catch (error) { labelError.value = (error as Error).message }
  finally { labelBusy.value = false }
}
function updateName(event: Event) { labelName.value = (event.target as HTMLElement).textContent ?? '' }
function nameKey(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Enter') { event.preventDefault(); saveLabel() }
  if (event.key === 'Escape' && !labelBusy.value) { event.preventDefault(); editing.value = null; labelError.value = '' }
}
function pasteName(event: ClipboardEvent) {
  event.preventDefault()
  const text = event.clipboardData?.getData('text/plain').replace(/[\r\n]+/g, ' ') ?? ''
  const selection = window.getSelection()
  if (!selection?.rangeCount) return
  const range = selection.getRangeAt(0)
  if (!nameRef.value?.contains(range.commonAncestorContainer)) return
  range.deleteContents()
  const node = document.createTextNode(text)
  range.insertNode(node)
  range.setStartAfter(node)
  range.collapse(true)
  selection.removeAllRanges()
  selection.addRange(range)
  labelName.value = nameRef.value.textContent ?? ''
}
async function deleteLabel(id: string, name: string) {
  if (!window.confirm('删除标签“' + name + '”？仅空标签可删除。')) return
  try {
    await api.deleteKnowledgeBase(id)
    await refreshKnowledgeBases()
    if (activeKbId.value === id) activeKbId.value = 'unclassified'
  } catch (error) { labelError.value = (error as Error).message }
}
const { theme, fontScaleLabel } = useStyle()

const THEME_LABEL: Record<string, string> = {
  minimalist: '极简编辑',
  soft: '柔和高级',
  brutalist: '工业粗野',
  stitch: 'Taste 标准'
}
</script>

<template>
  <aside class="rail">
    <div class="brand">
      <img class="brand-mark" src="/brand-avatar.jpg" alt="CORPUS 标识" width="38" height="38">
      <span class="brand-text">
        <strong>CORPUS</strong>
        <small>RAG 控制台 · v0.1</small>
      </span>
    </div>

    <nav class="nav">
      <button class="nav-item" :class="{ 'is-active': view === 'chat' }" type="button" @click="switchView('chat')">对话</button>
      <button class="nav-item" :class="{ 'is-active': view === 'kb' }" type="button" @click="switchView('kb')">知识库</button>
    </nav>

    <section class="rail-block">
      <header class="rail-label">
        <span>会话</span>
        <button class="micro-btn" type="button" title="新建会话" @click="newThread()"><AppIcon name="plus" :size="14" /></button>
      </header>
      <ul class="thread-list">
        <li v-for="thread in threads" :key="thread.id" class="thread-row" :class="{ 'is-active': thread.id === activeThreadId }">
          <button
            class="row-btn"
            :class="{ 'is-active': thread.id === activeThreadId }"
            type="button"
            @click="selectThread(thread.id)"
          >
            <span v-if="thread.id === activeThreadId" class="dot" />
            <span class="label">{{ thread.title }}</span>
            <span class="count">{{ thread.count }}</span>
          </button>
          <button class="micro-btn thread-del" type="button" title="删除会话" @click.stop="removeThread(thread.id)">
            <AppIcon name="close" :size="12" />
          </button>
        </li>
      </ul>
    </section>

    <section class="rail-block rail-block--grow">
      <header class="rail-label"><span>知识库</span><button class="micro-btn" type="button" title="新建标签" @click="editLabel('new')"><AppIcon name="plus" :size="14" /></button></header>
      <form v-if="editing === 'new'" class="label-editor" @submit.prevent="saveLabel">
        <input v-model="labelName" aria-label="标签名称" maxlength="40" placeholder="标签名称" :disabled="labelBusy">
        <button type="submit" class="micro-btn" :disabled="labelBusy || !labelName.trim()" title="保存标签"><AppIcon name="check" :size="14" /></button>
        <button type="button" class="micro-btn" title="取消" @click="editing = null"><AppIcon name="close" :size="14" /></button>
      </form>
      <p v-if="labelError || knowledgeError" class="rail-error">{{ labelError || knowledgeError }}</p>
      <ul class="kb-list">
        <li v-for="kb in knowledgeBases" :key="kb.id" class="kb-row" :class="{ 'is-active': activeKbId === kb.id, 'is-editing': editing === kb.id }">
          <div v-if="editing === kb.id" class="row-btn row-name-editor">
            <AppIcon name="doc" :size="15" />
            <span :ref="(el) => { nameRef = el as HTMLElement | null }" class="label editable-name" :contenteditable="!labelBusy" role="textbox" :aria-label="'重命名标签 ' + kb.name" aria-multiline="false" :aria-busy="labelBusy" spellcheck="false" @input="updateName" @keydown="nameKey" @paste="pasteName" @blur="saveLabel" />
          </div>
          <button v-else class="row-btn" :class="{ 'is-active': activeKbId === kb.id }" type="button" @click="activeKbId = kb.id">
            <AppIcon name="doc" :size="15" />
            <span class="label">{{ kb.name }}</span>
            <span class="count">{{ kb.docs }}</span>
          </button>
          <div v-if="!kb.builtin" class="kb-actions">
            <template v-if="editing === kb.id">
              <button class="micro-btn" type="button" title="保存名称" :disabled="labelBusy" @mousedown.prevent @click="saveLabel"><AppIcon name="check" :size="14" /></button>
              <button class="micro-btn" type="button" title="取消改名" :disabled="labelBusy" @mousedown.prevent @click="editing = null; labelError = ''"><AppIcon name="close" :size="12" /></button>
            </template>
            <template v-else>
              <button class="micro-btn" type="button" :title="'重命名标签 ' + kb.name" :disabled="labelBusy" @click="editLabel(kb.id, kb.name)"><AppIcon name="edit" :size="12" /></button>
              <button class="micro-btn" type="button" :title="'删除标签 ' + kb.name" :disabled="labelBusy" @click="deleteLabel(kb.id, kb.name)"><AppIcon name="close" :size="12" /></button>
            </template>
          </div>
        </li>
      </ul>
    </section>

    <div class="rail-foot">
      <div class="status-pill" :class="status === 'online' ? 'is-online' : status === 'offline' ? 'is-offline' : 'is-pending'">
        <span class="led" />
        <span>{{ status === 'online' ? '后端已连接' : status === 'offline' ? '后端未连接' : '检测中' }}</span>
      </div>
      <dl class="engine">
        <div><dt>嵌入模型</dt><dd>{{ health?.config.embedding_model ?? 'bge-m3' }}</dd></div>
        <div><dt>生成模型</dt><dd>{{ health?.config.llm_provider ? health.config.llm_provider + ' · ' : '' }}{{ health?.config.chat_model ?? '读取中' }}</dd></div>
        <div><dt>向量库</dt><dd>{{ health?.config.qdrant_mode ?? 'embedded' }}</dd></div>
      </dl>
      <div class="appearance-setting">
        <button class="ghost-btn" type="button" @click="styleOpen = true">
          <AppIcon name="sliders" />外观与配色
        </button>
        <span class="chip appearance-caption">{{ THEME_LABEL[theme] }} · {{ fontScaleLabel }}</span>
      </div>
    </div>
  </aside>
</template>
