<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { api } from '@/api/client'
import type { ChunkingReport, DocumentRecord, StrategyCatalog } from '@/types'

const props = defineProps<{
  doc: DocumentRecord | null
  catalog: StrategyCatalog | null
}>()

const report = ref<ChunkingReport | null>(null)
const loading = ref(false)
const error = ref('')
/* 空串表示跟随路由自动选策略 */
const strategy = ref('')
const targetTokens = ref<number | null>(null)

const activeStrategy = computed(() => strategy.value)

async function load() {
  if (!props.doc) {
    report.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    report.value = await api.chunksForDocument(props.doc.doc_id, {
      strategy: activeStrategy.value || undefined,
      targetTokens: targetTokens.value ?? undefined
    })
  } catch (caught) {
    error.value = (caught as Error).message
    report.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.doc?.doc_id, load, { immediate: true })
watch([strategy, targetTokens], load)

function useDefaultTarget() {
  targetTokens.value = null
}

function fallbackFor(name: string) {
  return props.catalog?.strategies.find((item) => item.name === name)
}
</script>

<template>
  <div class="panel-block">
    <div v-if="doc" class="chunk-controls">
      <label class="field-inline">
        <span>策略</span>
        <select v-model="strategy">
          <option value="">自动（按文件类型）</option>
          <option v-for="item in catalog?.strategies ?? []" :key="item.name" :value="item.name">
            {{ item.name }}
          </option>
        </select>
      </label>
      <label class="field-inline">
        <span>目标 token</span>
        <input
          :value="targetTokens ?? catalog?.defaults.target_tokens ?? 320"
          type="number"
          min="64"
          max="2000"
          step="32"
          @input="targetTokens = Number(($event.target as HTMLInputElement).value) || null"
        >
      </label>
      <button v-if="targetTokens" class="micro-btn" type="button" title="恢复默认" @click="useDefaultTarget">
        <AppIcon name="close" :size="12" />
      </button>
    </div>

    <p v-if="!doc" class="empty">选择左侧任意文档查看分块结果。</p>
    <p v-else-if="loading" class="empty">正在切分…</p>
    <p v-else-if="error" class="panel-note is-warn">{{ error }}</p>

    <template v-else-if="report">
      <div class="meta-grid">
        <div><span>命中策略</span><code>{{ report.strategy }}</code></div>
        <div><span>块数</span><code>{{ report.chunk_total }}</code></div>
        <div><span>字符数</span><code>{{ report.stats.char_min }}~{{ report.stats.char_max }}</code></div>
        <div><span>平均</span><code>{{ report.stats.char_avg }}</code></div>
        <div><span>目标 / 上限</span><code>{{ report.stats.target_tokens }} / {{ report.stats.max_tokens }}</code></div>
        <div><span>重叠</span><code>{{ report.stats.overlap_tokens }}</code></div>
      </div>

      <p v-if="fallbackFor(report.strategy)" class="strategy-desc">
        {{ fallbackFor(report.strategy)?.description }}
      </p>

      <p v-for="note in report.notes" :key="note" class="panel-note">{{ note }}</p>

      <div class="chunk-box">
        <article v-for="chunk in report.chunks" :key="chunk.chunk_id" class="chunk">
          <header>
            <span>行 {{ chunk.line_start }}–{{ chunk.line_end }}</span>
            <span class="chunk-kind">
              #{{ chunk.index + 1 }} {{ chunk.kind }}
              <template v-if="chunk.language"> · {{ chunk.language }}</template>
              <template v-if="chunk.page"> · P{{ chunk.page }}</template>
              · {{ chunk.char_count }} 字
            </span>
          </header>
          <p v-if="chunk.symbol" class="chunk-symbol">{{ chunk.symbol }}</p>
          <p v-if="chunk.heading_path.length" class="chunk-path">{{ chunk.heading_path.join(' › ') }}</p>
          <p class="chunk-body">{{ chunk.text }}</p>
        </article>
      </div>
    </template>
  </div>
</template>
