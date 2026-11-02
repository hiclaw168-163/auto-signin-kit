import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发态: npm run dev 起 :5173,/api 代理到 Python 侧的 8688(ui serve)。
// 构建态: 产物进 dist/,由 FastAPI StaticFiles 托管(SPEC-09 §3)。
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8688',
    },
  },
})
