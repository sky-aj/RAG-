import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(process.argv[2] ? path.join(path.resolve(process.argv[2]), 'package.json') : import.meta.url)
const { chromium } = require('playwright')
const out = path.join(path.dirname(fileURLToPath(import.meta.url)), '../docs/live-model-validation-2026-10-12')
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1480, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(String(error)))
  await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  await page.locator('.status-pill.is-online').waitFor({ timeout: 30000 })
  await page.locator('.engine').getByText('sub-coco · deepseek-v4-flash', { exact: true }).waitFor()
  const health = await (await page.request.get('http://127.0.0.1:5173/api/health')).json()
  assert.equal(health.config.chat_model, 'deepseek-v4-flash')
  await page.locator('.nav-item').getByText('知识库', { exact: true }).click()
  await page.screenshot({ path: path.join(out, '06-services-ready.png') })
  assert.deepEqual(errors, [])
  console.log('PASS: current frontend/backend and proxy are online; no model request or production data mutation.')
} finally {
  await browser.close()
}
