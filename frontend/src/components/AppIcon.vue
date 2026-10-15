<script setup lang="ts">
const props = defineProps<{ name: string; size?: number }>()

const PATHS: Record<string, string[]> = {
  send: ['M3 10h12', 'M10.5 5l5 5-5 5'],
  clip: ['M13.5 6.5 8 12a2.1 2.1 0 0 0 3 3l5.5-5.5a4 4 0 0 0-5.7-5.7L5 9.6a5.5 5.5 0 0 0 7.8 7.8l4-4'],
  close: ['M5.5 5.5l9 9', 'M14.5 5.5l-9 9'],
  check: ['M4.5 10.5l3.5 3.5 7.5-8'],
  doc: ['M5 2.75h6.5L16 7.25V17a.25.25 0 0 1-.25.25H5A.25.25 0 0 1 4.75 17V3A.25.25 0 0 1 5 2.75Z', 'M11.25 2.9V7.2h4.4'],
  plus: ['M10 4.5v11', 'M4.5 10h11'],
  edit: ['M12.5 3.5l4 4', 'M4 12l9-9a1.4 1.4 0 0 1 2 0l2 2a1.4 1.4 0 0 1 0 2l-9 9-5 1 1-5Z'],
  search: ['M13.05 13.05 17 17'],
  sliders: ['M4 6.5h12', 'M4 13.5h12'],
  link: ['M8.2 11.8 11.8 8.2', 'M9.4 5.6l1.3-1.3a3.1 3.1 0 0 1 4.4 4.4l-1.3 1.3', 'M10.6 14.4l-1.3 1.3a3.1 3.1 0 0 1-4.4-4.4l1.3-1.3'],
  seed: ['M10 3v6', 'M10 9c-2.6 0-4.2 1.6-4.2 4 0 1.6 1.3 2.9 2.9 2.9h2.6c1.6 0 2.9-1.3 2.9-2.9 0-2.4-1.6-4-4.2-4Z']
}

const circles: Record<string, [number, number, number]> = { search: [9, 9, 5.25] }
const dots: Record<string, [number, number, number][]> = {
  sliders: [[8, 6.5, 1.6], [13, 13.5, 1.6]]
}

const size = props.size ?? 18
</script>

<template>
  <svg
    class="ico"
    :width="size"
    :height="size"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <circle v-if="circles[name]" :cx="circles[name][0]" :cy="circles[name][1]" :r="circles[name][2]" />
    <circle v-for="(dot, index) in dots[name] ?? []" :key="index" :cx="dot[0]" :cy="dot[1]" :r="dot[2]" />
    <path v-for="(d, index) in PATHS[name] ?? []" :key="index" :d="d" />
  </svg>
</template>
