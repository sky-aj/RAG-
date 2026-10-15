import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(path.join(path.resolve(process.argv[2]), 'package.json'))
const { chromium } = require('playwright')
const dist = path.join(root, 'frontend/dist')
const out = path.join(root, 'docs/workspace-navigation-2026-10-13')
const server = http.createServer(async (request, response) => {
  const target = path.resolve(dist, new URL(request.url, 'http://localhost').pathname.slice(1) || 'index.html')
  if (!target.startsWith(dist + path.sep)) return response.writeHead(403).end()
  try {
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg' }
    response.writeHead(200, { 'Content-Type': mime[path.extname(target)] ?? 'application/octet-stream' })
    response.end(await fs.readFile(target))
  } catch { response.writeHead(404).end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const browser = await chromium.launch({ headless: true })
try {
  await fs.mkdir(out, { recursive: true })
  const page = await browser.newPage({ viewport: { width: 1480, height: 1000 }, reducedMotion: 'reduce' })
  const errors = []
  const patches = []
  let labels = [{ id: 'all', name: '全部文档', docs: 2, builtin: true },
    { id: 'unclassified', name: '未分类', docs: 0, builtin: true },
    { id: 'kb-cv', name: '简历', docs: 2 }, { id: 'kb-work', name: '项目资料', docs: 0 }]
  page.on('pageerror', error => errors.push(String(error)))
  const snapshot = {
    threads: [{ id: 't1', title: '简历问答', count: 2, updated: '刚刚', kbId: 'kb-cv' }],
    activeThreadId: 't1', messagesByThread: { t1: [
      { id: 'u1', role: 'user', text: '有哪些项目经验和奖项？', time: '19:30' },
      { id: 'a1', role: 'assistant', time: '19:30', answer: '简历包含全栈开发与 AI 应用项目经历【片段 1】。\n\n获奖清单记录了创新创业大赛省赛银奖【片段 3】。',
        actualModel: 'deepseek-v4-flash', trace: ['简历', 'hybrid · BM25 + 向量', '重排 开启', '2034 tokens', '4.5 s'],
        request: { topK: 12, threshold: 0.35, useKnowledgeBase: true, kbId: 'kb-cv', kbName: '简历' },
        retrieval: { notes: ['语义路有 9 条低于相似度阈值 0.35 被过滤', '阈值 0.35 又滤掉 9 条融合结果'],
          hybrid: true, vector_candidates: 16, vector_kept: 7, keyword_candidates: 7, fused: 4, returned: 7,
          below_threshold: 9, threshold: 0.35, rerank_used: true, context_used: 3, context_tokens: 1817 },
        citations: [
          { index: 1, doc_name: 'jianli1.pdf', location: '行 2-14', page: 1, score: 0.97, quote: '全栈开发与 AI 应用项目经历。' },
          { index: 2, doc_name: 'jianli1.pdf', location: '行 17-66', page: 1, score: 0.02, quote: '项目技术说明。' },
          { index: 3, doc_name: '奖.docx', location: '行 6-6', score: 0.01, quote: '创新创业大赛省赛银奖。' }
        ] }
    ] }
  }
  await page.addInitScript(saved => {
    if (!localStorage.getItem('corpus.threads.v1')) localStorage.setItem('corpus.threads.v1', JSON.stringify(saved))
    localStorage.setItem('corpus.style', JSON.stringify({ theme: 'minimalist', fontScale: 'md', accent: 'ink' }))
  }, snapshot)
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.hostname !== '127.0.0.1') return route.abort()
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname === '/api/health') return reply({ config: { chat_model: 'deepseek-v4-flash', llm_provider: 'sub-coco', embedding_model: 'bge-m3', qdrant_mode: 'embedded' }, capabilities: {} })
    if (url.pathname === '/api/documents') return reply({ items: [] })
    if (url.pathname === '/api/chat/config') return reply({ retrieval: { top_k: 12, similarity_threshold: 0.35, rerank_enabled: true } })
    if (url.pathname === '/api/knowledge-bases') {
      if (route.request().method() === 'POST') {
        const item = { id: 'kb-new', name: route.request().postDataJSON().name, docs: 0 }
        labels.push(item)
        return reply({ item }, 201)
      }
      return reply({ items: labels })
    }
    if (url.pathname.startsWith('/api/knowledge-bases/') && route.request().method() === 'PATCH') {
      const id = url.pathname.split('/').at(-1)
      const name = route.request().postDataJSON().name
      patches.push({ id, name })
      if (labels.some(label => label.id !== id && label.name === name)) return reply({ error: '标签名称已存在或属于保留名称' }, 409)
      const item = labels.find(label => label.id === id)
      item.name = name
      return reply({ item })
    }
    throw new Error('Unexpected API request: ' + route.request().method() + ' ' + url.pathname)
  })
  await page.goto('http://127.0.0.1:' + server.address().port, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelector('.brand-mark').naturalWidth > 0)
  assert.equal(await page.locator('.topbar .ghost-btn').count(), 0)
  assert.equal(await page.locator('.msg-meta .trace').count(), 0)
  assert.equal(await page.locator('.answer-details').count(), 1)
  assert.equal(await page.locator('.answer-details').getAttribute('open'), null)
  assert.equal(await page.locator('.diagnostics').isVisible(), false)
  assert.equal(await page.locator('.retrieval-notes').isVisible(), false)
  assert.equal(await page.locator('.reference-details').count(), 0)
  assert.equal(await page.locator('.question-history').count(), 0)
  assert.equal(await page.locator('.citation-fold').first().isVisible(), false)
  await page.screenshot({ path: path.join(out, 'workspace.png'), fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '【片段 1】', exact: true }).click()
  await page.locator('#citation-a1-1[open]').waitFor()
  assert.equal(await page.locator('.answer-details').getAttribute('open'), '')
  assert.equal(await page.locator('#citation-a1-1 .quote').isVisible(), true)
  assert.equal(await page.locator('.diagnostics').isVisible(), true)
  assert.equal(await page.getByText('语义路有 9 条低于相似度阈值 0.35 被过滤', { exact: true }).isVisible(), true)
  await page.locator('.answer-details > summary').click()

  await page.getByTitle('重命名标签 简历', { exact: true }).click()
  const editor = page.getByRole('textbox', { name: '重命名标签 简历', exact: true })
  await editor.waitFor()
  assert.equal(await editor.evaluate(node => node.tagName), 'SPAN')
  assert.equal(await page.locator('.label-editor').count(), 0)
  assert.equal(await editor.textContent(), '简历')
  await editor.fill('个人简历')
  await page.screenshot({ path: path.join(out, 'rename.png'), fullPage: true, animations: 'disabled' })
  await editor.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  assert.equal(patches.length, 0)
  await editor.press('Enter')
  await page.getByTitle('重命名标签 个人简历', { exact: true }).waitFor()
  assert.deepEqual(patches, [{ id: 'kb-cv', name: '个人简历' }])
  assert.equal(await page.locator('.kb-row.is-active .label').textContent(), '个人简历')
  await page.getByTitle('重命名标签 个人简历', { exact: true }).click()
  await page.locator('.editable-name').fill('取消的名称')
  await page.locator('.editable-name').press('Escape')
  await page.getByTitle('重命名标签 个人简历', { exact: true }).waitFor()
  assert.equal(patches.length, 1)
  await page.getByTitle('重命名标签 个人简历', { exact: true }).click()
  await page.locator('.editable-name').fill('履历资料')
  await page.getByRole('heading', { name: '知识库问答', exact: true }).click()
  await page.getByTitle('重命名标签 履历资料', { exact: true }).waitFor()
  assert.equal(patches.length, 2)
  await page.getByTitle('重命名标签 履历资料', { exact: true }).click()
  await page.locator('.editable-name').fill('项目资料')
  await page.locator('.editable-name').press('Enter')
  await page.getByText('标签名称已存在或属于保留名称', { exact: true }).waitFor()
  assert.equal(await page.locator('.editable-name').isVisible(), true)
  await page.locator('.editable-name').fill('履历资料')
  await page.locator('.editable-name').press('Escape')

  // Check active row background encloses sibling actions without nested buttons.
  for (const selector of ['.thread-row.is-active', '.kb-row.is-active']) {
    const valid = await page.locator(selector).evaluate(row => {
      const bounds = row.getBoundingClientRect()
      return getComputedStyle(row).backgroundColor !== 'rgba(0, 0, 0, 0)'
        && [...row.querySelectorAll('.micro-btn')].every(button => {
          const box = button.getBoundingClientRect()
          return box.left >= bounds.left && box.right <= bounds.right && box.top >= bounds.top && box.bottom <= bounds.bottom
        }) && !row.querySelector('button button')
    })
    assert.equal(valid, true)
  }
  for (const theme of ['minimalist', 'soft', 'brutalist', 'stitch']) {
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; document.documentElement.dataset.fs = 'xl' }, theme)
    await page.setViewportSize({ width: 980, height: 900 })
    const layout = await page.locator('.appearance-setting').evaluate(node => {
      const parent = node.getBoundingClientRect()
      const button = node.querySelector('button').getBoundingClientRect()
      const caption = node.querySelector('.appearance-caption').getBoundingClientRect()
      const rail = node.closest('.rail')
      return caption.top >= button.bottom && caption.right <= parent.right + 1 && rail.scrollWidth <= rail.clientWidth + 1
    })
    assert.equal(layout, true, 'Appearance overflow for ' + theme)
  }
  const colors = []
  for (const accent of ['ink', 'indigo', 'emerald']) {
    await page.evaluate(async accent => {
      document.documentElement.dataset.accent = accent
      await new Promise(requestAnimationFrame)
      await new Promise(requestAnimationFrame)
    }, accent)
    const expected = { ink: 'rgb(237, 237, 235)', indigo: 'rgb(231, 230, 253)', emerald: 'rgb(222, 245, 235)' }[accent]
    await page.waitForFunction(expected => getComputedStyle(document.querySelector('.appearance-caption')).backgroundColor === expected, expected)
    colors.push(await page.locator('.appearance-caption').evaluate(node => getComputedStyle(node).backgroundColor))
  }
  assert.equal(new Set(colors).size, 3, JSON.stringify(colors))

  // A long conversation verifies jump positions, hover previews, scroll tracking, and session isolation.
  await page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('corpus.threads.v1'))
    for (let index = 2; index <= 10; index++) {
      saved.messagesByThread.t1.push({ id: 'u' + index, role: 'user', text: '第 ' + index + ' 个问题：项目经验', time: '19:31' },
        { id: 'a' + index, role: 'assistant', answer: ('项目经验说明。\n\n').repeat(12), time: '19:31' })
    }
    localStorage.setItem('corpus.threads.v1', JSON.stringify(saved))
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.setViewportSize({ width: 1480, height: 1000 })
  assert.equal(await page.locator('.conversation-tick').count(), 10)
  await page.locator('.composer-input').fill('保留我的未发送草稿')
  await page.locator('.conversation-tick').nth(5).hover()
  assert.equal(await page.locator('.conversation-preview strong').textContent(), '第 6 个问题：项目经验')
  assert.equal(await page.locator('.conversation-preview p').isVisible(), true)
  await page.screenshot({ path: path.join(out, 'history-navigation.png'), fullPage: true, animations: 'disabled' })
  await page.locator('.conversation-tick').nth(5).click()
  await page.waitForFunction(() => document.querySelector('.conversation-tick[aria-current="location"]')?.getAttribute('aria-label')?.includes('第 6 个问题'))
  const relativeTop = await page.locator('#message-u6').evaluate(node => node.getBoundingClientRect().top - document.querySelector('.scroll').getBoundingClientRect().top)
  assert.ok(Math.abs(relativeTop - 24) < 2)
  assert.equal(await page.locator('.composer-input').inputValue(), '保留我的未发送草稿')
  await page.locator('.conversation-tick').first().click()
  await page.waitForFunction(() => document.querySelector('.conversation-tick[aria-current="location"]')?.getAttribute('aria-label')?.includes('第 1 个问题'))
  await page.locator('.conversation-tick').first().focus()
  assert.equal(await page.locator('.conversation-preview').isVisible(), true)
  await page.locator('.conversation-tick').first().press('Escape')
  assert.equal(await page.locator('.conversation-preview').count(), 0)
  await page.evaluate(() => { const root = document.querySelector('.scroll'); root.scrollTop = root.scrollHeight })
  await page.waitForFunction(() => document.querySelector('.conversation-tick[aria-current="location"]')?.getAttribute('aria-label')?.includes('第 10 个问题'))
  await page.getByTitle('新建会话', { exact: true }).click()
  assert.equal(await page.locator('.conversation-nav').count(), 0)
  assert.deepEqual(errors, [])
  await fs.writeFile(path.join(out, 'result.json'), JSON.stringify({ unifiedDetails: true, citationNavigation: true, accentBackgrounds: colors,
    historyNavigation: ['hover preview', 'click jump', 'active scroll tracking', 'keyboard preview', 'draft preserved', 'session isolation'],
    inlineRename: ['Enter save', 'Escape cancel', 'blur save', 'duplicate error', 'IME composition'],
    sharedRowBackground: true, appearanceThemesAtLargeFont: 4, brandImageLoaded: true, pageErrors: errors }, null, 2))
  console.log('Browser checks passed: collapsed details, citation navigation, inline rename, shared row backgrounds, avatar and four themes at large font.')
} finally {
  await browser.close()
  await new Promise(resolve => server.close(resolve))
}
