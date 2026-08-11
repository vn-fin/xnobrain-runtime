import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: './artifacts/playwright-results',
  fullyParallel: false,
  workers: 1,
  reporter: [['line']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:1420',
    channel: 'chrome',
    viewport: { width: 1280, height: 800 },
    colorScheme: 'light',
    locale: 'en-US',
    timezoneId: 'America/Los_Angeles',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'on',
  },
  webServer: {
    command: 'npm run dev:web -- --port 1420',
    url: 'http://127.0.0.1:1420',
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
