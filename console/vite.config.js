import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { rollupOptions: { input: { index: fileURLToPath(new URL('./index.html', import.meta.url)), backtest: fileURLToPath(new URL('./backtest.html', import.meta.url)) } } },
  server: { proxy: { '/mgr': 'http://127.0.0.1:8100', '/bt': 'http://127.0.0.1:8100', '/ws': { target: 'ws://127.0.0.1:8100', ws: true, rewrite: path => '/' } } }
})
