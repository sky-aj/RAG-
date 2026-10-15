const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')
const ts = require('typescript')

// Execute the actual TypeScript modules with simulated fetch/storage; no API requests.
function harness(fetchImpl, saved = null) {
  const cache = new Map()
  const storage = new Map(saved ? [['corpus.threads.v1', saved]] : [])
  const context = vm.createContext({
    console, Response, ReadableStream, TextDecoder, TextEncoder, AbortController, DOMException, performance,
    fetch: (...args) => fetchImpl(...args),
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value)
    }
  })
  function load(name) {
    const filename = path.resolve(__dirname, '../src', name + '.ts')
    if (cache.has(filename)) return cache.get(filename).exports
    const module = { exports: {} }
    cache.set(filename, module)
    const source = fs.readFileSync(filename, 'utf8')
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
    })
    const run = vm.runInContext('(function(require, module, exports) {\n' + outputText + '\n})', context,
      { filename })
    run((id) => id.startsWith('@/') ? load(id.slice(2)) : require(id), module, module.exports)
    return module.exports
  }
  const client = load('api/client')
  return { api: client.api, chat: client.chatApi, workspace: () => load('composables/useWorkspace').useWorkspace(), storage }
}

function event(type, fields = {}) {
  return 'data: ' + JSON.stringify({ type, ...fields }) + '\n\n'
}

function fragmented(text) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3))
      controller.close()
    }
  }))
}

test('fragmented CRLF stream decodes notices, Unicode, model and separate usage', async () => {
  const attempts = [{ model: 'deepseek-v4-flash', usage: { total_tokens: 80 } },
    { model: 'glm-4-flash', provider: 'zhipu', usage: { total_tokens: 5 } }]
  const body = ': heartbeat\n\n' + event('notice', { text: '已切换至 GLM', reason: '正文为空', target_model: 'glm-4-flash' }) +
    event('content', { text: '中文回答' }) + event('done', {
      actual_model: 'glm-4-flash', actual_provider: 'zhipu', usage: { total_tokens: 5 }, attempts
    })
  const { chat } = harness(async () => fragmented(body.replaceAll('\n', '\r\n')))
  const events = []
  await chat.stream({ question: 'q' }, {
    onNotice: (value) => events.push(['notice', value.text]),
    onContent: (value) => events.push(['content', value]),
    onDone: (value) => events.push(['done', JSON.parse(JSON.stringify(value))])
  })
  assert.deepEqual(events.map(([type]) => type), ['notice', 'content', 'done'])
  assert.equal(events[1][1], '中文回答')
  assert.deepEqual(events[2][1].attempts, attempts)
  assert.equal(events[2][1].usage.total_tokens, 5)
  assert.equal(events[2][1].actual_model, 'glm-4-flash')
})

test('EOF without a terminal event rejects and preserves prior content', async () => {
  const { chat } = harness(async () => fragmented(event('content', { text: 'partial' })))
  const received = []
  await assert.rejects(chat.stream({ question: 'q' }, {
    onContent: (text) => received.push(text), onDone: () => assert.fail('Unexpected done')
  }), /意外中断/)
  assert.deepEqual(received, ['partial'])
})

test('explicit error is the only terminal and retains fallback metadata', async () => {
  const body = event('notice', { text: 'switched' }) + event('content', { text: 'partial' }) +
    event('error', { message: 'fallback disconnected', actual_model: 'glm-4-flash', attempts: [] }) + event('done')
  const { chat } = harness(async () => fragmented(body))
  const received = []
  await chat.stream({ question: 'q' }, {
    onNotice: () => received.push('notice'), onContent: () => received.push('content'),
    onError: (text, result) => { received.push('error'); assert.equal(result.actual_model, 'glm-4-flash') },
    onDone: () => assert.fail('Unexpected success after error')
  })
  assert.deepEqual(received, ['notice', 'content', 'error'])
})

test('invalid JSON is a protocol error rather than successful EOF', async () => {
  const { chat } = harness(async () => fragmented('data: {invalid}\n\n'))
  await assert.rejects(chat.stream({ question: 'q' }, {}), /JSON 无效/)
})

test('evaluation SSE incrementally dispatches CRLF-fragmented questions and progress', async () => {
  let requestBody
  const body = ': keepalive\n\n' + event('stage', { phase: 'generating', message: '准备题目' }) +
    event('question', { case: { case_id: 'case-1', question: '题目一', ground_truth: '答案' }, completed: 1, total: 2 }) +
    event('progress', { phase: 'judge', completed: 1, total: 2, running_scores: { faithfulness: 1 }, score_counts: { faithfulness: 1 } }) +
    event('done', { ok: true, count: 1, cases: [] })
  const app = harness(async (url, init) => {
    assert.equal(url, '/api/eval/dataset/stream')
    assert.equal(init.method, 'POST')
    requestBody = JSON.parse(init.body)
    return fragmented(body.replaceAll('\n', '\r\n'))
  })
  const events = []
  await app.api.buildEvalDatasetStream({ limit: 2 }, {
    onStage: (item) => events.push(['stage', item.phase]),
    onQuestion: (item) => events.push(['question', item.case.question]),
    onProgress: (item) => events.push(['progress', item.running_scores.faithfulness]),
    onDone: (item) => events.push(['done', item.ok])
  })
  assert.deepEqual(requestBody, { limit: 2, doc_id: [] })
  assert.deepEqual(events, [['stage', 'generating'], ['question', '题目一'], ['progress', 1], ['done', true]])
})

test('evaluation SSE rejects EOF without done and surfaces explicit server errors', async () => {
  const missingTerminal = harness(async () => fragmented(event('progress', { phase: 'answer', completed: 1 })))
  await assert.rejects(missingTerminal.api.runEvalStream({ limit: 3 }, {}), /评测流意外中断/)

  const failed = harness(async () => fragmented(event('error', { message: '裁判调用失败' })))
  let message = ''
  await assert.rejects(failed.api.runEvalStream({}, { onError: (value) => { message = value } }), /裁判调用失败/)
  assert.equal(message, '裁判调用失败')
})

test('done without usage preserves unknown usage and cancels upstream reader', async () => {
  let cancelled = false
  const { chat } = harness(async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode(event('done', { usage: null }))) },
    cancel() { cancelled = true }
  })))
  await chat.stream({ question: 'q' }, { onDone: (result) => assert.equal(result.usage, null) })
  assert.equal(cancelled, true)
})

test('workspace saves fallback notice, model and attempts and restores after refresh', async () => {
  const attempts = [{ model: 'deepseek-v4-flash', usage: { total_tokens: 80 } },
    { model: 'glm-4-flash', usage: { total_tokens: 5 } }]
  const fetch = async () => fragmented(event('notice', { text: '已切换至 GLM' }) +
    event('content', { text: 'answer' }) + event('done', {
      actual_model: 'glm-4-flash', actual_provider: 'zhipu', usage: { total_tokens: 5 }, attempts
    }))
  const app = harness(fetch)
  await app.workspace().send('q')
  const saved = app.storage.get('corpus.threads.v1')
  const refreshed = harness(fetch, saved).workspace()
  const answer = refreshed.messages.value.at(-1)
  assert.equal(answer.answer, 'answer')
  assert.equal(answer.notices[0], '已切换至 GLM')
  assert.equal(answer.actualModel, 'glm-4-flash')
  assert.deepEqual(JSON.parse(JSON.stringify(answer.attempts)), attempts)
  assert.ok(answer.trace.some((text) => text === '最终请求 5 tokens'))
  assert.equal(answer.streaming, false)
})

test('workspace sends and persists ordinary chat mode and an immutable request snapshot', async () => {
  let requestBody
  const app = harness(async (_url, init) => {
    requestBody = JSON.parse(init.body)
    return fragmented(event('sources', { citations: [], hits: [], diagnostics: {}, knowledge_base_used: false }) +
      event('content', { text: 'ordinary' }) + event('done', { finish_reason: 'stop' }))
  })
  const workspace = app.workspace()
  workspace.useKnowledgeBase.value = false
  workspace.activeKbId.value = 'unclassified'
  workspace.params.topK = 3
  await workspace.send('hello')
  workspace.params.topK = 12
  assert.equal(requestBody.use_knowledge_base, false)
  assert.equal(requestBody.kb_id, 'unclassified')
  assert.equal(workspace.messages.value.at(-1).retrieval, undefined)
  assert.equal(workspace.messages.value.at(-1).request.topK, 3)
  const refreshed = harness(async () => {}, app.storage.get('corpus.threads.v1')).workspace()
  assert.equal(refreshed.useKnowledgeBase.value, false)
  assert.equal(refreshed.activeKbId.value, 'unclassified')
  assert.equal(refreshed.messages.value.at(-1).knowledgeBaseUsed, false)
})

test('history suggestions and input drafts are isolated by thread and suggestions deduplicate', async () => {
  const app = harness(async () => fragmented(event('content', { text: 'answer' }) + event('done')))
  const workspace = app.workspace()
  const original = workspace.activeThreadId.value
  await workspace.send('焊接  培训')
  await workspace.send('焊接 培训')
  assert.deepEqual(Array.from(workspace.historyQuestions.value), ['焊接 培训'])
  assert.equal(workspace.messages.value.filter((item) => item.role === 'user').length, 2)
  workspace.draft.value = 'A 草稿'
  workspace.newThread()
  assert.equal(workspace.draft.value, '')
  assert.equal(workspace.historyQuestions.value.length, 0)
  workspace.draft.value = 'B 草稿'
  workspace.selectThread(original)
  assert.equal(workspace.draft.value, 'A 草稿')
  assert.equal(workspace.historyQuestions.value.length, 1)
})

test('workspace persists partial answer and notice when stream ends unexpectedly', async () => {
  const fetch = async () => fragmented(event('notice', { text: 'switched' }) + event('content', { text: 'partial' }))
  const app = harness(fetch)
  await app.workspace().send('q')
  const refreshed = harness(fetch, app.storage.get('corpus.threads.v1')).workspace()
  const answer = refreshed.messages.value.at(-1)
  assert.equal(answer.answer, 'partial')
  assert.equal(answer.notices[0], 'switched')
  assert.match(answer.error, /意外中断/)
  assert.equal(answer.streaming, false)
})

test('workspace persists explicit fallback failure metadata', async () => {
  const attempts = [{ model: 'glm-4-flash', usage: null, error: 'disconnected' }]
  const fetch = async () => fragmented(event('notice', { text: 'switched' }) + event('content', { text: 'partial' }) +
    event('error', { message: 'failed', actual_model: 'glm-4-flash', actual_provider: 'zhipu', attempts }))
  const app = harness(fetch)
  await app.workspace().send('q')
  const answer = harness(fetch, app.storage.get('corpus.threads.v1')).workspace().messages.value.at(-1)
  assert.equal(answer.answer, 'partial')
  assert.equal(answer.error, 'failed')
  assert.equal(answer.actualModel, 'glm-4-flash')
  assert.deepEqual(JSON.parse(JSON.stringify(answer.attempts)), attempts)
})

test('truncated output keeps its finish reason after refresh', async () => {
  const fetch = async () => fragmented(event('content', { text: 'partial at output limit' }) +
    event('done', { finish_reason: 'length', actual_model: 'deepseek-v4-flash', usage: { total_tokens: 16384 } }))
  const app = harness(fetch)
  await app.workspace().send('q')
  const answer = harness(fetch, app.storage.get('corpus.threads.v1')).workspace().messages.value.at(-1)
  assert.equal(answer.answer, 'partial at output limit')
  assert.equal(answer.finishReason, 'length')
  assert.equal(answer.actualModel, 'deepseek-v4-flash')
})

test('stop aborts the request and preserves content without a false success or error', async () => {
  let cancelled = false
  const app = harness(async (_, { signal }) => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(event('notice', { text: 'switched' }) + event('content', { text: 'partial' })))
      signal.addEventListener('abort', () => {
        cancelled = true
        controller.error(new DOMException('Aborted', 'AbortError'))
      }, { once: true })
    }
  })))
  const workspace = app.workspace()
  const pending = workspace.send('q')
  for (let i = 0; i < 10 && !workspace.messages.value.at(-1).answer; i++) {
    await new Promise((resolve) => setImmediate(resolve))
  }
  assert.equal(workspace.messages.value.at(-1).answer, 'partial')
  workspace.stop()
  await pending
  const answer = workspace.messages.value.at(-1)
  assert.equal(cancelled, true)
  assert.equal(answer.error, undefined)
  assert.equal(answer.finishReason, undefined)
  assert.equal(answer.notices[0], 'switched')
  assert.equal(answer.streaming, false)
})
