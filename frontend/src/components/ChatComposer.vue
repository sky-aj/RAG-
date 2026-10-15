<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { useWorkspace } from '@/composables/useWorkspace'

const { send, stop, streaming, attachments, removeAttachment, clearAttachments, draft: text,
  activeThreadId, useKnowledgeBase, activeKnowledgeBase } = useWorkspace()
watch(activeThreadId, () => nextTick(autosize))
const boxRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

/* 自适应高度：先归零再按 scrollHeight 撑开。
   上限用 rem 计算，保证在「外观与配色」调整字号后依然跟着放大。 */
const MAX_REM = 15

function autosize() {
  const node = boxRef.value
  if (!node) return
  const rootSize = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const max = MAX_REM * rootSize
  node.style.height = 'auto'
  node.style.height = Math.min(node.scrollHeight, max) + 'px'
  // 触顶后允许内部滚动，否则保持隐藏滚动条
  node.style.overflowY = node.scrollHeight > max ? 'auto' : 'hidden'
}

watch(text, () => nextTick(autosize))
onMounted(autosize)

// 字号档位变化时重新量一次高度
watch(
  () => document.documentElement.dataset.fs,
  () => nextTick(autosize)
)

function submit() {
  const value = text.value
  if (!value.trim() || streaming.value) return
  text.value = ''
  nextTick(autosize)
  clearAttachments()
  send(value)
}

function halt() {
  stop()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}

function pickFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = input.files
  if (files) attachments.value = [...attachments.value, ...Array.from(files)]
  input.value = ''
}
</script>

<template>
  <form class="composer" @submit.prevent="submit">
    <div v-if="attachments.length" class="attach-row">
      <span v-for="(file, index) in attachments" :key="file.name + index" class="attach">
        <AppIcon name="doc" :size="13" />
        <span>{{ file.name }}</span>
        <button type="button" title="移除" @click="removeAttachment(index)"><AppIcon name="close" :size="13" /></button>
      </span>
    </div>

    <div class="composer-shell">
    <textarea
        ref="boxRef"
        v-model="text"
        class="composer-input"
        rows="1"
        :disabled="streaming"
        :placeholder="streaming ? '正在生成回答…' : (useKnowledgeBase ? '向知识库提问' : '普通问答') + '，Enter 发送，Shift + Enter 换行'"
        aria-label="提问内容"
        @keydown="onKeydown"
      />
      <div class="composer-tools">
        <button class="icon-btn" type="button" title="添加文件" @click="fileInputRef?.click()"><AppIcon name="clip" /></button>
        <input ref="fileInputRef" type="file" multiple hidden @change="pickFiles">
        <label class="mini-switch">
          <input v-model="useKnowledgeBase" type="checkbox" :disabled="streaming">
          <span>引用知识库</span>
        </label>
        <span class="composer-hint">{{ useKnowledgeBase ? '将检索：' + (activeKnowledgeBase?.name ?? '标签已失效，请重新选择') : '本次不检索知识库，保留当前会话上下文' }}</span>
        <button v-if="streaming" class="send-btn send-btn--stop" type="button" @click="halt"><AppIcon name="close" /><span>停止</span></button>
        <button v-else class="send-btn" type="submit"><AppIcon name="send" /><span>发送</span></button>
      </div>
    </div>
  </form>
</template>
