import { defineConfig, devices } from '@playwright/test'

const npmDevCommand = process.platform === 'win32' ? 'npm.cmd run dev' : 'npm run dev'
const frontendPort = Number(process.env.E2E_FRONTEND_PORT ?? '5173')
const backendPort = Number(process.env.E2E_BACKEND_PORT ?? '8000')

export default defineConfig({
  testDir: './e2e',
  testIgnore: '**/mission-control/**',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? `http://127.0.0.1:${frontendPort}`,
    trace: 'on-first-retry',
    video: 'retain-on-failure',
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : [
        {
          command: 'node e2e/start-backend.mjs',
          url: `http://127.0.0.1:${backendPort}/api/health`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
        {
          command: `${npmDevCommand} -- --port ${frontendPort}`,
          url: `http://127.0.0.1:${frontendPort}`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
      ],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
