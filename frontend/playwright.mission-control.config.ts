import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.MC_E2E_PORT ?? '18005')

export default defineConfig({
  testDir: './e2e/mission-control',
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  timeout: 45_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'on-first-retry',
    video: 'retain-on-failure',
  },
  webServer: {
    command: 'node e2e/mission-control/start-backend.mjs',
    url: `http://127.0.0.1:${port}/api/mission-control/health`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
