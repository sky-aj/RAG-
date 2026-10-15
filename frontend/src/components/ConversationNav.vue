<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useWorkspace } from '@/composables/useWorkspace'

const props = defineProps<{ scrollRoot: HTMLElement | null }>()
const { messages, activeThreadId } = useWorkspace()
const activeId = ref('')
const previewId = ref('')
const previewTop = ref(0)
const turns = computed(() => messages.value.flatMap((message, index) => {
  if (message.role !== 'user' || !message.text?.trim()) return []
  const answer = messages.value[index + 1]
  return [{ id: message.id, question: message.text,
    answer: answer?.role === 'assistant' ? answer.answer || answer.error || (answer.streaming ? '正在生成回答…' : '') : '' }]
}))
const preview = computed(() => turns.value.find(turn => turn.id === previewId.value))
function plain(text: string) { return text.replace(/[#*`>]/g, '').replace(/\s+/g, ' ').trim() }
function updateActive() {
  const root = props.scrollRoot
  if (!root) return
  const boundary = root.getBoundingClientRect().top + 100
  let current = turns.value[0]?.id ?? ''
  for (const turn of turns.value) {
    const node = document.getElementById('message-' + turn.id)
    if (node && node.getBoundingClientRect().top <= boundary) current = turn.id
  }
  if (root.scrollHeight > root.clientHeight && root.scrollHeight - root.scrollTop - root.clientHeight < 2) {
    current = turns.value.at(-1)?.id ?? current
  }
  activeId.value = current
}
function showPreview(id: string, event: Event) {
  previewId.value = id
  const button = event.currentTarget as HTMLElement
  const nav = button.closest('.conversation-nav') as HTMLElement
  previewTop.value = Math.min(Math.max(0, button.getBoundingClientRect().top - nav.getBoundingClientRect().top - 24), Math.max(0, nav.clientHeight - 100))
}
function jump(id: string) {
  const root = props.scrollRoot
  const node = document.getElementById('message-' + id)
  if (!root || !node) return
  root.scrollTo({ top: root.scrollTop + node.getBoundingClientRect().top - root.getBoundingClientRect().top - 24,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  activeId.value = id
}
let observer: ResizeObserver | null = null
watch(() => props.scrollRoot, (root, previous) => {
  previous?.removeEventListener('scroll', updateActive)
  observer?.disconnect()
  root?.addEventListener('scroll', updateActive, { passive: true })
  observer = new ResizeObserver(updateActive)
  if (root) observer.observe(root)
  if (root?.querySelector('.messages')) observer.observe(root.querySelector('.messages')!)
  updateActive()
}, { immediate: true, flush: 'post' })
watch([activeThreadId, () => turns.value.map(turn => turn.id).join(',')], async () => {
  previewId.value = ''
  await nextTick()
  updateActive()
})
onBeforeUnmount(() => { props.scrollRoot?.removeEventListener('scroll', updateActive); observer?.disconnect() })
</script>

<template>
  <nav v-if="turns.length" class="conversation-nav" aria-label="对话历史导航" @mouseleave="previewId = ''" @keydown.esc="previewId = ''">
    <div class="conversation-ticks">
      <button v-for="(turn, index) in turns" :key="turn.id" type="button" class="conversation-tick" :class="{ 'is-active': activeId === turn.id }"
        :aria-label="'跳转到第 ' + (index + 1) + ' 个问题：' + turn.question" :aria-current="activeId === turn.id ? 'location' : undefined"
        @mouseenter="showPreview(turn.id, $event)" @focus="showPreview(turn.id, $event)" @blur="previewId = ''" @click="jump(turn.id)"><span /></button>
    </div>
    <div v-if="preview" class="conversation-preview" :style="{ top: previewTop + 'px' }" aria-hidden="true">
      <strong>{{ preview.question }}</strong>
      <p v-if="preview.answer">{{ plain(preview.answer) }}</p>
    </div>
  </nav>
</template>
