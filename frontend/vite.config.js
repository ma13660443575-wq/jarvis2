import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期用 Vite dev server（proxy /api 到后端）；生产 build 到 dist 由 FastAPI 同源挂载
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8100', ws: true },
      '/events': { target: 'http://127.0.0.1:8100', ws: true },
    },
  },
  build: { outDir: 'dist' },
})
