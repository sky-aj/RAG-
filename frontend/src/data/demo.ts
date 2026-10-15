import type { KnowledgeBaseItem, ThreadItem } from '@/types'

/* 会话与知识库分组仍是占位数据：等知识库页接完 /api/documents 再换真实分组。
   对话消息从步骤 5 起由 /api/chat 的 SSE 流式返回，这里不再造样例消息——
   留一份固定的问答样例，只是为了让「引用卡片 / 分数」有对照物。 */

export const THREADS: ThreadItem[] = [
  { id: 't1', title: '产品文档问答', count: 0, updated: '刚刚' }
]

export const KNOWLEDGE_BASES: KnowledgeBaseItem[] = [
  { id: 'k1', name: '全部文档', docs: 0 }
]

export const SAMPLE_CITATIONS = [
  {
    index: 1,
    doc_name: '产品需求文档.md',
    location: '行 9-10',
    score: 0.74,
    heading_path: ['产品需求文档', '1. 限流策略', '1.1 默认额度'],
    quote: '对外接口按租户维度限流，默认额度为每分钟 600 次请求、并发 20。'
  }
]
