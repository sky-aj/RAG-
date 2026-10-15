import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发时把 /api 转发到 Flask，前端代码里只写相对路径
    proxy: {
      '/api': { target: 'http://127.0.0.1:5000', changeOrigin: true }
    }
  }
})
