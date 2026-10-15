import { computed, ref, watch } from 'vue'
import type { AccentId, FontScaleId, ThemeId } from '@/types'

const STORAGE_KEY = 'corpus.style'

interface StoredStyle {
  theme?: ThemeId
  accent?: AccentId
  fontScale?: FontScaleId
}

function readStored(): StoredStyle {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as StoredStyle
  } catch {
    return {}
  }
}

const stored = readStored()
const theme = ref<ThemeId>(stored.theme ?? 'minimalist')
const accent = ref<AccentId>(stored.accent ?? 'ink')
const fontScale = ref<FontScaleId>(stored.fontScale ?? 'md')

function apply() {
  const root = document.documentElement
  root.dataset.theme = theme.value
  root.dataset.accent = accent.value
  root.dataset.fs = fontScale.value
}

watch([theme, accent, fontScale], () => {
  apply()
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ theme: theme.value, accent: accent.value, fontScale: fontScale.value })
    )
  } catch {
    /* 隐私模式下忽略 */
  }
})

export const THEME_OPTIONS: { id: ThemeId; name: string; skill: string; desc: string }[] = [
  { id: 'minimalist', name: '极简编辑', skill: 'minimalist-skill', desc: '暖色单色系，1px 浅边框，极小阴影，Notion / Linear 式克制。' },
  { id: 'soft', name: '柔和高级', skill: 'soft-skill', desc: '大留白，双层容器，漫射阴影，衬线标题的高端品牌感。' },
  { id: 'brutalist', name: '工业粗野', skill: 'brutalist-skill', desc: '直角零圆角，等宽大写标签，强边框分隔的终端工艺感。' },
  { id: 'stitch', name: 'Taste 标准', skill: 'stitch-skill', desc: '冷调灰白，宽柔漫射阴影，2.6rem 圆角与重排式排版。' }
]

export const ACCENT_OPTIONS: { id: AccentId; name: string; hex: string }[] = [
  { id: 'ink', name: '墨黑', hex: '#1F1F1E' },
  { id: 'indigo', name: '靛蓝', hex: '#4F46E5' },
  { id: 'emerald', name: '翡翠', hex: '#0E9F6E' },
  { id: 'rose', name: '深玫', hex: '#E11D48' },
  { id: 'amber', name: '琥珀', hex: '#C2760A' },
  { id: 'hazard', name: '警示红', hex: '#E61919' }
]

/* 字号档位：改的是根字号，正文全部用 rem，所以整体等比缩放 */
export const FONT_SCALE_OPTIONS: { id: FontScaleId; name: string; desc: string; base: string; scale: number }[] = [
  { id: 'sm', name: '紧凑', desc: '小屏笔记本', base: '15px', scale: 0.9375 },
  { id: 'md', name: '标准', desc: '默认', base: '16px', scale: 1 },
  { id: 'lg', name: '舒适', desc: '台式机 / 高分屏', base: '18px', scale: 1.125 },
  { id: 'xl', name: '宽松', desc: '大屏远距离阅读', base: '20px', scale: 1.25 }
]

const FONT_SCALE_MAP = Object.fromEntries(FONT_SCALE_OPTIONS.map((item) => [item.id, item])) as Record<
  FontScaleId,
  (typeof FONT_SCALE_OPTIONS)[number]
>

export function useStyle() {
  const fontScaleLabel = computed(() => FONT_SCALE_MAP[fontScale.value]?.name ?? '标准')

  const reset = () => {
    theme.value = 'minimalist'
    accent.value = 'ink'
    fontScale.value = 'md'
  }

  return { theme, accent, fontScale, fontScaleLabel, apply, reset }
}
