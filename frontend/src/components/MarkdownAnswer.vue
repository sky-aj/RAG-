<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{ text: string; streaming?: boolean; citations?: number[] }>()
const emit = defineEmits<{ cite: [index: number]; rendered: [] }>()
const html = ref('')
let frame = 0
function render() {
  frame = 0
  html.value = renderMarkdown(props.text, props.citations)
  emit('rendered')
}
watch(() => [props.text, props.streaming, props.citations], () => {
  if (!props.streaming) { cancelAnimationFrame(frame); render() }
  else if (!frame) frame = requestAnimationFrame(render)
}, { immediate: true })
onBeforeUnmount(() => cancelAnimationFrame(frame))
function click(event: MouseEvent) {
  const button = (event.target as Element).closest('button[data-citation]')
  if (button) emit('cite', Number(button.getAttribute('data-citation')))
}
</script>

<template>
  <div class="msg-text markdown-answer" @click="click" v-html="html" />
</template>
