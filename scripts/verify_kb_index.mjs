import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(path.join(path.resolve(process.argv[2]), 'package.json'))
const { chromium } = require('playwright')
const kbId = process.argv[3]
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1480, height: 1000 }, reducedMotion: 'reduce' })
  const errors = []
  page.on('pageerror', error => errors.push(String(error)))
  await page.addInitScript(id => localStorage.setItem('corpus.threads.v1', JSON.stringify({
    threads: [{ id: 'kb-verify', title: '分类检索验证', count: 0, updated: '刚刚', kbId: id }],
    activeThreadId: 'kb-verify', messagesByThread: { 'kb-verify': [] }
  })), kbId)
  await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  await page.getByText('当前分类有 2 份文档已入库。问一句试试。', { exact: true }).waitFor()
  await page.getByRole('button', { name: '知识库', exact: true }).click()
  await page.getByRole('heading', { name: '简历', exact: true }).waitFor()
  assert.equal(await page.locator('.table--docs tbody tr').count(), 2)
  assert.equal(await page.locator('.table--docs .badge').filter({ hasText: '已入库' }).count(), 2)
  await page.getByRole('button', { name: '向量库', exact: true }).click()
  await page.getByText('当前分类已入库', { exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelector('.meta-grid')?.textContent.includes('16 / 72'))
  await page.getByRole('button', { name: '对话', exact: true }).click()
  await page.getByText('当前分类有 2 份文档已入库。问一句试试。', { exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelector('.nav-item.is-active')?.textContent === '对话'
    && Number(getComputedStyle(document.querySelector('.chat-head').parentElement).opacity) > 0.99)
  const out = path.resolve('docs/kb-index-fix-2026-10-13')
  await fs.mkdir(out, { recursive: true })
  await page.screenshot({ path: path.join(out, 'indexed-category.png'), fullPage: true, animations: 'disabled' })
  assert.deepEqual(errors, [])
  console.log('Browser verified: 2 indexed documents, 16 category chunks, 72 total points; no page errors.')
} finally {
  await browser.close()
}
