import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    launchOptions: process.env.CI ? undefined : { channel: 'chrome' },
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node frontend/scripts/e2e-server-guard.mjs 8000 && python -m uvicorn scripts.e2e_fastapi:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      reuseExistingServer: false,
      timeout: 30_000,
      wait: { stderr: /Application startup complete\./ },
    },
    {
      command: 'node scripts/e2e-server-guard.mjs 5173 && npm run dev -- --port 5173 --strictPort',
      reuseExistingServer: false,
      timeout: 30_000,
      wait: { stdout: /Local:\s+http:\/\/127\.0\.0\.1:5173\// },
      env: { VITE_API_BASE_URL: '' },
    },
  ],
});
