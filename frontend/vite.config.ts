import { defineConfig } from 'vite';
import react from "@vitejs/plugin-react";

export default defineConfig({
  server: {
    port: 5173, // this is where the frontend is viewed from when developer server is running. 5173 is default, change it to whatever you like
  },
  build: { minify: false },
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:5000", changeOrigin: true } // this is where the Flask server is serving
    }
  }
});
