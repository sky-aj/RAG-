<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import type { DocumentDetail, ParsedElement } from '@/types'

defineProps<{ detail: DocumentDetail | null; loading: boolean; elements: ParsedElement[] }>()

const KIND_LABEL: Record<string, string> = {
  heading: '标题',
  paragraph: '正文',
  code: '代码',
  table: '表格',
  list_item: '列表',
  quote: '引用',
  page_mark: '分页',
  sheet_mark: '工作表'
}

function kindLabel(kind: string) {
  return KIND_LABEL[kind] ?? kind
}
</script>

<template>
  <div class="panel-block">
    <p v-if="loading" class="empty">正在解析…</p>

    <template v-else-if="detail">
      <div class="meta-grid">
        <div><span>加载器</span><code>{{ detail.summary.parser }}</code></div>
        <div><span>文档类型</span><code>{{ detail.summary.doc_type }}</code></div>
        <div><span>编码</span><code>{{ detail.summary.encoding || '—' }}</code></div>
        <div><span>页数</span><code>{{ detail.summary.page_count || '—' }}</code></div>
        <div><span>行总数</span><code>{{ detail.summary.line_total }}</code></div>
        <div><span>结构块</span><code>{{ detail.summary.element_total }}</code></div>
      </div>

      <div class="kind-chips">
        <span v-for="(count, kind) in detail.summary.counts_by_kind" :key="kind" class="chip">
          {{ kindLabel(String(kind)) }} {{ count }}
        </span>
      </div>

      <div v-if="detail.summary.outline_entries?.length" class="outline-entries">
        <span class="outline-title">文档自带目录</span>
        <span v-for="entry in detail.summary.outline_entries.slice(0, 8)" :key="entry.title" class="chip">
          {{ entry.title }}
        </span>
      </div>

      <p v-for="warning in detail.summary.warnings" :key="warning" class="panel-note is-warn">
        警告：{{ warning }}
      </p>

      <div class="chunk-box">
        <article v-for="(element, index) in elements" :key="index" class="chunk">
          <header>
            <span>行 {{ element.line_start }}–{{ element.line_end }}</span>
            <span class="chunk-kind">
              {{ kindLabel(element.kind) }}<template v-if="element.level"> h{{ element.level }}</template>
              <template v-if="element.language"> · {{ element.language }}</template>
              <template v-if="element.page"> · P{{ element.page }}</template>
            </span>
          </header>
          <p>{{ element.text }}</p>
        </article>
      </div>
    </template>

    <p v-else class="empty">选择左侧任意文档查看解析结构。</p>
  </div>
</template>
