// frontend/vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  publicDir: 'public',
  build: { minify: false, copyPublicDir: true },
  server: {
    host: '127.0.0.1',        // bind to all interfaces so LAN devices can reach Vite
    port: 5173,             // development server port (change if you like)
    hmr: { protocol: 'wss', host: 'junkwarehouse.eleccelerator.com', clientPort: 443 },
    // strictPort: true,    // uncomment to fail if 5173 is taken
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',   // Flask server
        changeOrigin: true,
        // If Flask routes are like \"/items\" (no \"/api\" prefix), strip it:
        // rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/imgs': {
        target: 'http://127.0.0.1:5000',   // Flask server
        changeOrigin: true,
        // If Flask routes are like \"/items\" (no \"/api\" prefix), strip it:
        // rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
