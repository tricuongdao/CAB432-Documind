import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local dev proxies the cloud API (or a local uvicorn) so the SPA is
// same-origin in every environment.
export default defineConfig({
  plugins: [react()],
  base: './', // relative asset URLs so one build works at / and /documind/
  build: { outDir: 'dist', assetsDir: 'assets' },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/mcp': 'http://localhost:8000',
      '/healthz': 'http://localhost:8000',
    },
  },
})
