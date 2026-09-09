import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-server proxy so `npm run dev` also works without nginx -- mirrors
// the /api proxy nginx.conf sets up for the built/deployed version, so
// src/api.js's relative '/api/...' paths work identically in both.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.PAYASAM_BACKEND_URL || 'http://127.0.0.1:18083',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
