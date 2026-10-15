import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

// Optional argument: directory containing the installed Playwright package.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(process.argv[2] ? path.join(path.resolve(process.argv[2]), 'package.json') : import.meta.url)
const { chromium } = require('playwright')
const dist = path.join(root, 'frontend/dist')
const out = path.join(root, 'docs/model-migration-2026-10-12')
await fs.mkdir(out, { recursive: true })
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }
const server = http.createServer(async (request, response) => {
  const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html'
  const target = path.resolve(dist, relative)
  if (!target.startsWith(dist + path.sep)) { response.writeHead(403).end(); return }
  try {
    response.writeHead(200, { 'Content-Type': mime[path.extname(target)] ?? 'application/octet-stream' })
    response.end(await fs.readFile(target))
  } catch { response.writeHead(404).end() }
})
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1480, height: 1000 } })
  const errors = []
  page.on('pageerror', (error) => errors.push(String(error)))
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) errors.push(message.text())
  })
  let mode = 'success'
  let reportReady = false
  let releaseHealth
  const healthReady = new Promise((resolve) => { releaseHealth = resolve })
  const attempts = [{ model: 'deepseek-v4-flash', provider: 'sub-coco', usage: { total_tokens: 80 } },
    { model: 'glm-4-flash', provider: 'zhipu', usage: { total_tokens: 5 } }]
  const notice = '主模型暂不可用（正文为空），已切换至 glm-4-flash，正在重新生成'
  const frame = (type, fields = {}) => 'data: ' + JSON.stringify({ type, ...fields }) + '\n\n'
  const report = {
    created_at: '2026-10-12T00:00:00Z', dataset_count: 1,
    notes: [notice, '部分判分使用了 GLM（1 次成功）'],
    variants: [{ name: '当前配置', params: {}, scores: { faithfulness: null },
      retrieval: { case_count: 1, failed_answers: 1 }, per_case: [{
        case_id: 'case-001', question: '问题', ground_truth: '标准答案', answer: '部分评测答案', contexts: [],
        expected: { doc_id: 'd1', doc_name: '示例.txt', line_start: 1, line_end: 2 }, hit: false,
        top_doc: '', actual_model: 'glm-4-flash', judge_model: '混合：DeepSeek / GLM', notices: [notice],
        usage: { error: 'GLM 备用流式调用失败', attempts }, scores: { faithfulness: null }
      }] }]
  }
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.hostname !== '127.0.0.1') return route.abort()
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const reply = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    switch (url.pathname) {
      case '/api/health':
        await healthReady
        return reply({ status: 'ok', config: { chat_model: 'deepseek-v4-flash', llm_provider: 'sub-coco' }, capabilities: {} })
      case '/api/documents': return reply({ items: [] })
      case '/api/chat/config': return reply({ generation: {}, retrieval: {
        top_k: 5, similarity_threshold: 0.35, rerank_enabled: false
      } })
      case '/api/chat': {
        let body = frame('notice', { text: notice }) + frame('content', { text: '保留的部分回答' })
        if (mode === 'success') body += frame('done', {
          actual_model: 'glm-4-flash', actual_provider: 'zhipu', usage: { total_tokens: 5 }, attempts
        })
        if (mode === 'error') body += frame('error', {
          message: 'GLM 备用流式调用失败', actual_model: 'glm-4-flash', actual_provider: 'zhipu', attempts
        })
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body })
      }
      case '/api/chunks/strategies': return reply({ strategies: [] })
      case '/api/eval': return reply({ dataset: { count: 1 }, staleness: { stale: false },
        config: { langsmith_enabled: false, report_dir: '(simulated)' } })
      case '/api/eval/dataset': return reply({ count: 1, cases: [], notes: [notice] })
      case '/api/eval/run': reportReady = true; return reply(report)
      case '/api/eval/latest':
        return reportReady ? reply(report) : route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"暂无报告"}' })
      case '/api/eval/history': return reply({ items: [] })
      default: return reply({})
    }
  })
  const url = 'http://127.0.0.1:' + server.address().port
  await page.goto(url, { waitUntil: 'domcontentloaded' })
  await page.locator('.engine').getByText('读取中', { exact: true }).waitFor()
  releaseHealth()
  await page.locator('.engine').getByText('sub-coco · deepseek-v4-flash', { exact: true }).waitFor()
  async function ask(question) {
    await page.fill('.composer-input', question)
    await page.press('.composer-input', 'Enter')
    await page.waitForFunction(() => {
      const last = [...document.querySelectorAll('.msg--assistant')].at(-1)
      return last && !document.querySelector('.send-btn--stop') && last.querySelector('.msg-text')
    })
    return page.locator('.msg--assistant').last()
  }
  let answer = await ask('模拟空正文回退')
  assert.ok((await answer.innerText()).includes(notice))
  assert.ok((await answer.innerText()).includes('本次模型：glm-4-flash'))
  await page.reload({ waitUntil: 'networkidle' })
  answer = page.locator('.msg--assistant').last()
  assert.ok((await answer.innerText()).includes(notice))
  assert.ok((await answer.innerText()).includes('最终请求 5 tokens'))
  await page.screenshot({ path: path.join(out, '01-fallback-restored.png') })
  mode = 'eof'
  answer = await ask('模拟意外断流')
  await answer.locator('.msg-error').waitFor()
  assert.ok((await answer.innerText()).includes('回答流意外中断'))
  assert.ok((await answer.innerText()).includes(notice))
  await page.screenshot({ path: path.join(out, '02-unexpected-eof.png') })
  mode = 'error'
  answer = await ask('模拟备用再次失败')
  await answer.locator('.msg-error').waitFor()
  assert.ok((await answer.innerText()).includes('GLM 备用流式调用失败'))
  assert.ok((await answer.innerText()).includes('保留的部分回答'))
  assert.ok((await answer.innerText()).includes('本次模型：glm-4-flash'))
  await page.screenshot({ path: path.join(out, '03-fallback-failure.png') })
  await page.locator('.nav-item').getByText('知识库', { exact: true }).click()
  await page.getByRole('button', { name: '评测', exact: true }).click()
  await page.getByRole('button', { name: '生成评测集', exact: true }).click()
  await page.getByText('生成 1 道题', { exact: false }).waitFor()
  assert.ok((await page.locator('.panel-note').allTextContents()).some((text) => text.includes(notice)))
  await page.getByRole('button', { name: '跑一轮评测', exact: true }).click()
  await page.locator('.eval-case').first().waitFor()
  await page.locator('.eval-case').first().click()
  await page.locator('.eval-case-body').getByText('部分评测答案', { exact: true }).waitFor()
  assert.ok((await page.locator('.eval-case-body').innerText()).includes(notice))
  assert.ok((await page.locator('.eval-case-body').innerText()).includes('glm-4-flash'))
  assert.ok((await page.locator('.panel-note').allTextContents()).some((text) => text.includes('部分判分使用了 GLM')))
  await page.locator('.eval-case-body').scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(out, '04-eval-notices.png') })
  assert.deepEqual(errors, [])
  console.log('PASS: sidebar loading/model, fallback notice and refresh, unexpected EOF, fallback failure, dataset/evaluation notices; simulated APIs only.')
  console.log('Screenshots: ' + out)
} finally {
  await browser?.close()
  await new Promise((resolve) => server.close(resolve))
}
