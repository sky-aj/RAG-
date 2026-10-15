<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import type { Citation } from '@/types'

const props = defineProps<{ citation: Citation; id: string; open?: boolean }>()
const emit = defineEmits<{ toggle: [open: boolean] }>()
const quote = ref('')
const loading = ref(false)
const unavailable = ref(false)
async function loadQuote() {
  if (props.citation.quote || quote.value || loading.value || unavailable.value) return
  if (!props.citation.chunk_ids?.length) { unavailable.value = true; return }
  loading.value = true
  try {
    const result = await api.chunkDetail(props.citation.chunk_ids)
    quote.value = result.items.map((item) => String(item.text ?? '')).filter(Boolean).join('\n\n')
    unavailable.value = !quote.value
  } catch { unavailable.value = true }
  finally { loading.value = false }
}
function toggle(event: Event) {
  const open = (event.target as HTMLDetailsElement).open
  emit('toggle', open)
  if (open) loadQuote()
}
watch(() => props.open, (open) => { if (open) loadQuote() }, { immediate: true })
</script>

<template>
  <details :id="id" class="citation citation-fold" :open="open" @toggle="toggle">
    <summary>
      <span class="idx">{{ citation.index }}</span>
      <span class="src"><span>{{ citation.doc_name || citation.source || '未知来源' }}</span></span>
      <span class="fold-arrow" aria-hidden="true">›</span>
    </summary>
    <div class="citation-detail">
      <p class="path">{{ citation.location }}<span v-if="citation.page"> · 第 {{ citation.page }} 页</span> · 相关度 {{ citation.score.toFixed(2) }}</p>
      <p v-if="citation.heading_path?.length" class="path">{{ citation.heading_path.join(' › ') }}</p>
      <p v-if="citation.symbol" class="symbol">{{ citation.symbol }}</p>
      <p v-if="citation.summary" class="summary">{{ citation.summary }}</p>
      <p class="quote">{{ citation.quote || quote || (loading ? '正在读取原片段…' : '原片段已不可用') }}</p>
    </div>
  </details>
</template>
