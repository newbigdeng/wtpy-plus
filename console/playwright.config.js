import { defineConfig } from '@playwright/test'

const baseURL = process.env.WT_FRONTEND_URL || 'http://127.0.0.1:4173'

export default defineConfig({
  testDir: './tests',
  testMatch: '*.spec.js',
  timeout: 30000,
  workers: 1,
  retries: 0,
  reporter: 'list',
  outputDir: 'test-results',
  use: {
    baseURL,
    headless: true,
    channel: process.env.WT_BROWSER_CHANNEL || undefined,
    viewport: { width: 1600, height: 1000 },
    screenshot: 'off',
    trace: 'off'
  },
  webServer: process.env.WT_FRONTEND_URL ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1 --port 4173 --strictPort',
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 30000
  }
})
