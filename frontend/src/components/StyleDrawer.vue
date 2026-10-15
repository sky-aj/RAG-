<script setup lang="ts">
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { ACCENT_OPTIONS, FONT_SCALE_OPTIONS, THEME_OPTIONS, useStyle } from '@/composables/useStyle'
import { useWorkspace } from '@/composables/useWorkspace'
import type { AccentId, FontScaleId, ThemeId } from '@/types'

const { theme, accent, fontScale, reset } = useStyle()
const { styleOpen } = useWorkspace()
interface TokenRow { label: string; value: string; color?: string }
const tokens = ref<TokenRow[]>([])

const TOKEN_KEYS: { key: string; label: string }[] = [
  { key: '--bg', label: '背景 canvas' },
  { key: '--surface', label: '卡片 surface' },
  { key: '--border', label: '边框 border' },
  { key: '--text', label: '正文 text' },
  { key: '--accent', label: '强调 accent' },
  { key: '--radius-lg', label: '圆角 radius-lg' },
  { key: '--shadow-card', label: '卡片阴影' }
]

function syncTokens() {
  const computed = getComputedStyle(document.documentElement)
  const rows: TokenRow[] = TOKEN_KEYS.map(({ key, label }) => {
    const value = computed.getPropertyValue(key).trim()
    return { label, value, color: value.startsWith('#') ? value : undefined }
  })
  rows.push({ label: '根字号 base', value: computed.fontSize })
  rows.push({ label: '字号档位', value: fontScale.value })
  rows.push({ label: '根字号倍数', value: computed.getPropertyValue('--fs-scale').trim() })
  tokens.value = rows
}

function pickTheme(id: ThemeId) {
  theme.value = id
  requestAnimationFrame(syncTokens)
}

function pickAccent(id: AccentId) {
  accent.value = id
  requestAnimationFrame(syncTokens)
}

function pickFontScale(id: FontScaleId) {
  fontScale.value = id
  // 等浏览器完成重排后再读回真实值
  requestAnimationFrame(() => requestAnimationFrame(syncTokens))
}

function handleReset() {
  reset()
  requestAnimationFrame(() => requestAnimationFrame(syncTokens))
}
</script>

<template>
  <div>
    <div v-if="styleOpen" class="scrim" @click="styleOpen = false" />
    <aside v-if="styleOpen" class="drawer" role="dialog" aria-modal="true" aria-label="外观与配色" @vue:mounted="syncTokens">
      <header class="drawer-head">
        <div>
          <h2>外观与配色</h2>
          <p>设计语言取自本地 skills 规范，字号与配色随你切换，选择自动记住。</p>
        </div>
        <button class="icon-btn" type="button" aria-label="关闭" @click="styleOpen = false"><AppIcon name="close" /></button>
      </header>

      <div class="drawer-body">
        <section class="drawer-section">
          <h3>设计语言</h3>
          <div class="theme-grid">
            <button
              v-for="option in THEME_OPTIONS"
              :key="option.id"
              class="theme-card"
              :class="{ 'is-active': option.id === theme }"
              :data-preview="option.id"
              type="button"
              :aria-pressed="option.id === theme"
              @click="pickTheme(option.id)"
            >
              <span class="theme-preview"><i /><i /><i /></span>
              <strong>{{ option.name }}</strong>
              <small>{{ option.desc }}</small>
            </button>
          </div>
        </section>

        <section class="drawer-section">
          <h3>强调色</h3>
          <div class="swatches">
            <button
              v-for="option in ACCENT_OPTIONS"
              :key="option.id"
              class="swatch"
              :class="{ 'is-active': option.id === accent }"
              type="button"
              :aria-pressed="option.id === accent"
              @click="pickAccent(option.id)"
            >
              <i :style="{ background: option.hex }" />
              <span>{{ option.name }}</span>
            </button>
          </div>
        </section>

        <section class="drawer-section">
          <h3>字号</h3>
          <div class="scale-list">
            <button
              v-for="option in FONT_SCALE_OPTIONS"
              :key="option.id"
              class="scale-card"
              :class="{ 'is-active': option.id === fontScale }"
              type="button"
              :aria-pressed="option.id === fontScale"
              @click="pickFontScale(option.id)"
            >
              <span class="scale-sample" :style="{ transform: 'scale(' + option.scale + ')' }">A字</span>
              <span class="scale-meta">
                <strong>{{ option.name }}</strong>
                <small>{{ option.base }} · {{ option.desc }}</small>
              </span>
            </button>
          </div>
        </section>

        <section class="drawer-section">
          <h3>当前令牌</h3>
          <dl class="token-list">
            <div v-for="token in tokens" :key="token.label">
              <dt>{{ token.label }}</dt>
              <dd>
                <span v-if="token.color" class="swatch-dot" :style="{ background: token.color }" />
                <span>{{ token.value.length > 26 ? token.value.slice(0, 24) + '…' : token.value }}</span>
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <footer class="drawer-foot">
        <button class="ghost-btn" type="button" @click="handleReset">恢复默认</button>
        <button class="solid-btn" type="button" @click="styleOpen = false">完成</button>
      </footer>
    </aside>
  </div>
</template>
