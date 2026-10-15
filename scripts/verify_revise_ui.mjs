import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(process.argv[2] ? path.join(path.resolve(process.argv[2]), 'package.json') : import.meta.url)
const { chromium } = require('playwright')
const url = 'http://127.0.0.1:5002'
const out = path.join(root, 'docs/revise-validation-2026-10-13')
await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1480, height: 1000 } })
page.setDefaultTimeout(15000)
const errors = []
const requests = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', request => {
  if (request.url().endsWith('/api/chat')) requests.push(request.postDataJSON())
})
page.on('dialog', dialog => dialog.accept())
await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort())
const evidence = { fake_models: true, real_flask_qdrant: true, scenarios: [] }
async function record(name) { evidence.scenarios.push(name); console.log('PASS: ' + name) }
function kb(name) { return page.locator('.kb-list .row-btn').filter({ has: page.locator('.label', { hasText: new RegExp('^' + name + '$') }) }) }
async function checkCitationLayout() {
  const cards = await page.locator('.citation-fold:not([open])').evaluateAll(nodes => nodes.map(node => ({
    height: node.getBoundingClientRect().height,
    sourceWidth: node.querySelector('.src').getBoundingClientRect().width,
    summaryWidth: node.querySelector('summary').getBoundingClientRect().width,
    cardWidth: node.getBoundingClientRect().width
  })))
  assert.ok(cards.length)
  for (const card of cards) {
    assert.ok(card.height < 100, 'collapsed citation must stay compact')
    assert.ok(card.sourceWidth > 100, 'citation source must have readable width')
    assert.ok(card.summaryWidth > card.cardWidth - 5, 'summary must span the citation card')
  }
}
async function createLabel(name) {
  await page.getByTitle('新建标签', { exact: true }).click()
  await page.getByLabel('标签名称', { exact: true }).fill(name)
  await page.getByTitle('保存标签', { exact: true }).click()
  await kb(name).waitFor()
}
async function ask(question) {
  await page.locator('.composer-input').fill(question)
  await page.locator('.composer-input').press('Enter')
  await page.waitForFunction(() => !document.querySelector('.send-btn--stop'))
  const answer = page.locator('.msg--assistant').last()
  await answer.locator('.markdown-answer').waitFor()
  return answer
}
try {
  assert.equal((await (await page.request.get(url + '/__validation/status')).json()).isolated, true)
  await page.goto(url, { waitUntil: 'networkidle' })
  await page.locator('.status-pill.is-online').waitFor()
  await createLabel('培训资料')
  await createLabel('生产资料')
  await page.reload({ waitUntil: 'networkidle' })
  assert.equal(await kb('培训资料').count(), 1)
  assert.equal(await kb('生产资料').count(), 1)
  await record('create labels, retain empty labels and restore after reload')

  await kb('培训资料').click()
  await page.getByRole('button', { name: '知识库', exact: true }).click()
  await page.locator('.dropzone + input[type=file]').setInputFiles({ name: '相同文件名.txt', mimeType: 'text/plain', buffer: Buffer.from('焊接培训帮助学生练习安全操作和职业技能。\n培训工具支持虚拟焊接操作。') })
  await page.locator('.table--docs tbody tr').filter({ hasText: '相同文件名.txt' }).waitFor()
  await page.getByRole('button', { name: '向量库', exact: true }).click()
  await page.getByRole('button', { name: '增量入库', exact: true }).click()
  await page.getByText(/写入 \d+ 块/).waitFor()
  const countA = (await (await page.request.get(url + '/__validation/status')).json()).points

  await kb('生产资料').click()
  await page.locator('.dropzone + input[type=file]').setInputFiles({ name: '相同文件名.txt', mimeType: 'text/plain', buffer: Buffer.from('焊接生产工艺采用电流控制，实现稳定的产品生产。\n质量检查用于生产流程。') })
  await page.locator('.table--docs tbody tr').filter({ hasText: '相同文件名.txt' }).waitFor()
  await page.getByRole('button', { name: '增量入库', exact: true }).click()
  await page.getByText(/写入 \d+ 块/).waitFor()
  const total = (await (await page.request.get(url + '/__validation/status')).json()).points
  assert.ok(total > countA)
  await record('upload and index scoped documents with identical filenames')

  await kb('培训资料').click()
  await page.getByRole('button', { name: '对话', exact: true }).click()
  const answer = await ask('介绍教育与培训用途')
  assert.equal(await answer.locator('.markdown-answer strong').first().innerText(), '教育与培训')
  assert.ok(await answer.locator('.markdown-answer ul li').count())
  assert.ok(await answer.locator('.markdown-answer ol li').count())
  assert.equal(await answer.locator('.markdown-answer table').count(), 1)
  assert.equal(await answer.locator('.markdown-answer img').count(), 0)
  assert.equal(await answer.locator('.markdown-answer a[href^="javascript:"]').count(), 0)
  assert.equal(await answer.locator('.markdown-answer pre .citation-link').count(), 0)
  assert.equal(await answer.locator('.citation-fold[open]').count(), 0)
  await checkCitationLayout()
  await answer.locator('.citation-link').click()
  assert.equal(await answer.locator('.citation-fold[open]').count(), 1)
  assert.match(await answer.locator('.quote').innerText(), /培训帮助学生/)
  await answer.locator('.citation-fold summary').click()
  assert.equal(await answer.locator('.citation-fold[open]').count(), 0)
  await record('Markdown formatting, safe content, folded snippets and citation navigation')

  await page.locator('.retrieval-panel summary').click()
  assert.equal(await page.locator('.retrieval-panel').evaluate(node => node.open), true)
  await page.locator('.field-pop input[type=checkbox]').first().click()
  assert.equal(await page.locator('.retrieval-panel').evaluate(node => node.open), true)
  await page.locator('.composer-input').click()
  assert.equal(await page.locator('.retrieval-panel').evaluate(node => node.open), false)
  await page.locator('.retrieval-panel summary').click()
  await page.keyboard.press('Escape')
  assert.equal(await page.locator('.retrieval-panel').evaluate(node => node.open), false)
  await record('outside click and Escape close parameters, controls stay interactive')

  await ask('介绍教育与培训用途')
  assert.equal(await page.locator('.question-history button').filter({ hasText: /^介绍教育与培训用途$/ }).count(), 1)
  const chatCount = requests.length
  await page.locator('.question-history button').filter({ hasText: /^介绍教育与培训用途$/ }).click()
  assert.equal(await page.locator('.composer-input').inputValue(), '介绍教育与培训用途')
  assert.equal(requests.length, chatCount)
  await page.locator('.composer-input').fill('培训会话草稿')
  const threadTitle = await page.locator('.thread-list .row-btn.is-active .label').innerText()
  await page.getByTitle('新建会话', { exact: true }).click()
  assert.equal(await page.locator('.composer-input').inputValue(), '')
  assert.equal(await page.locator('.question-history button').count(), 0)
  await page.getByLabel('引用知识库', { exact: true }).uncheck()
  const ordinary = await ask('你好普通问答')
  assert.match(await ordinary.innerText(), /普通回答/)
  assert.equal(await ordinary.locator('.diagnostics').count(), 0)
  assert.equal(await ordinary.locator('.citation-fold').count(), 0)
  assert.equal(requests.at(-1).use_knowledge_base, false)
  assert.equal(requests.at(-1).history.length, 0)
  await page.reload({ waitUntil: 'networkidle' })
  assert.equal(await page.getByLabel('引用知识库', { exact: true }).isChecked(), false)
  await record('history deduplicates per thread, recall never sends, ordinary mode persists')

  await page.locator('.thread-list .row-btn').filter({ has: page.locator('.label', { hasText: threadTitle }) }).first().click()
  assert.equal(await page.getByLabel('引用知识库', { exact: true }).isChecked(), true)
  const fallback = await ask('回退通知验证')
  assert.match(await fallback.innerText(), /已切换至 glm-4-flash/)
  await record('fallback notices and actual model retained in new rendering')
  for (const theme of ['minimalist', 'soft', 'brutalist', 'stitch']) {
    await page.evaluate(value => document.documentElement.dataset.theme = value, theme)
    await checkCitationLayout()
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2))
  }
  await page.evaluate(() => document.documentElement.dataset.theme = 'minimalist')
  await record('compact citation geometry and page width across all four themes')
  const scroll = page.locator('.scroll')
  assert.ok(await scroll.evaluate(node => node.scrollHeight > node.clientHeight))
  await scroll.evaluate(node => node.scrollTop = node.scrollHeight)
  await page.evaluate(() => document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime)).forEach(animation => animation.finish()))
  assert.ok(await page.locator('.composer').evaluate(node => node.getBoundingClientRect().bottom <= window.innerHeight + 2))
  await record('chat scrolls inside the workspace with a visible composer')
  await page.screenshot({ path: path.join(out, '01-chat.png') })

  await page.getByRole('button', { name: '知识库', exact: true }).click()
  await page.getByRole('button', { name: '向量库', exact: true }).click()
  await page.getByRole('button', { name: '清空当前分类向量', exact: true }).click()
  await page.getByText(/已清空，全库剩余/).waitFor()
  assert.equal((await (await page.request.get(url + '/__validation/status')).json()).points, total - countA)
  await kb('生产资料').click()
  await page.locator('.search-row input[type=text]').fill('生产工艺')
  await page.locator('.search-row button').click()
  await page.locator('.search-snippet').first().waitFor()
  assert.equal(await page.locator('.search-snippet[open]').count(), 0)
  await page.locator('.search-snippet summary').first().click()
  assert.match(await page.locator('.search-snippet .chunk-body').first().innerText(), /生产工艺/)
  await record('scoped clear preserves other vectors; vector search snippets start folded')
  await page.screenshot({ path: path.join(out, '02-knowledge.png') })

  const moving = page.getByLabel('移动文档 相同文件名.txt', { exact: true })
  const labels = (await (await page.request.get(url + '/api/knowledge-bases')).json()).items
  const aId = labels.find(item => item.name === '培训资料').id
  await moving.selectOption(aId)
  await page.getByText('文档已移动，已有向量继续复用', { exact: true }).waitFor()
  assert.equal((await (await page.request.get(url + '/__validation/status')).json()).points, total - countA)
  await kb('培训资料').click()
  assert.equal(await page.locator('.table--docs tbody tr').filter({ hasText: '相同文件名.txt' }).count(), 2)
  await record('moving document retains vectors and updates classification')

  await page.getByTitle('重命名标签 生产资料', { exact: true }).click()
  await page.getByLabel('标签名称', { exact: true }).fill('生产归档')
  await page.getByTitle('保存标签', { exact: true }).click()
  await kb('生产归档').waitFor()
  await page.getByTitle('删除标签 生产归档', { exact: true }).click()
  await page.waitForFunction(() => ![...document.querySelectorAll('.kb-list .label')].some(node => node.textContent === '生产归档'))
  await record('rename and delete empty classification')

  await page.setViewportSize({ width: 760, height: 1000 })
  await page.getByRole('button', { name: '对话', exact: true }).click()
  await checkCitationLayout()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2))
  await scroll.evaluate(node => node.scrollTop = node.scrollHeight)
  await page.evaluate(() => document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime)).forEach(animation => animation.finish()))
  await page.screenshot({ path: path.join(out, '03-compact.png') })
  assert.deepEqual(errors, [])
  evidence.requests = requests.map(({ question, kb_id, use_knowledge_base, history }) => ({ question, kb_id, use_knowledge_base, history_count: history.length }))
  evidence.status = await (await page.request.get(url + '/__validation/status')).json()
  await fs.writeFile(path.join(out, 'evidence.json'), JSON.stringify(evidence, null, 2))
  console.log('All revision browser scenarios passed.')
} finally { await browser.close() }
