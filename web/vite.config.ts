import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/v1': 'http://127.0.0.1:8000',
      '/mcp': 'http://127.0.0.1:8000',
      '/sim': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
      '/demo-frames': 'http://127.0.0.1:8000',
      '/assembly': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
