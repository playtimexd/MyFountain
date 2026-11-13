import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // CUTLAB_API lets a second dev server talk to a test engine (e.g. CUTLAB_API=http://127.0.0.1:8797 npx vite --port 5174)
    proxy: { '/api': { target: process.env.CUTLAB_API || 'http://127.0.0.1:8787', changeOrigin: true } },
  },
})
