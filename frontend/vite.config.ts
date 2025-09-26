// frontend/vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { minify: false },
  server: {
    port: 5173,          // dev server port (change if you like)
    // strictPort: true, // uncomment to fail if 5173 is taken
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',   // Flask server
        changeOrigin: true,
        // If Flask routes are like "/items" (no "/api" prefix), strip it:
        // rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/imgs': {
        target: 'http://127.0.0.1:5000',   // Flask server
        changeOrigin: true,
        // If Flask routes are like "/items" (no "/api" prefix), strip it:
        // rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
