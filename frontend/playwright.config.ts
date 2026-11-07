// Browser end-to-end tests: the production build, served by the real API (`cloudops serve`), with
// the sandbox as the monitored application. Nothing is mocked; each run starts with an empty
// database in a temporary directory.
//
//   npm run build && npm run e2e
//
// Needs the backend and the sandbox installed in a Python environment: backend/.venv by default,
// or the interpreter named in E2E_PYTHON (CI uses the runner's Python).
import { defineConfig, devices } from '@playwright/test';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

import { API_PORT, OPERATOR_TOKEN, SANDBOX_PORT } from './e2e/support';

const staticDir = resolve(__dirname, 'dist/cloudops-web/browser');
if (!existsSync(join(staticDir, 'index.html'))) {
  throw new Error(`No production build in ${staticDir}. Run "npm run build" first.`);
}
const python =
  process.env['E2E_PYTHON'] ??
  resolve(
    __dirname,
    process.platform === 'win32'
      ? '../backend/.venv/Scripts/python.exe'
      : '../backend/.venv/bin/python',
  );
// Set once in the main process; Playwright's workers inherit it.
process.env['E2E_DATA_DIR'] ??= mkdtempSync(join(tmpdir(), 'cloudops-e2e-'));

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  // One server and one database for the whole run: the tests share them, so they run in order.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env['CI'],
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: process.env['CI'] ? [['list'], ['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${API_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `"${python}" -m cloudops.cli serve --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health/ready`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: {
        CLOUDOPS_DATA_DIR: process.env['E2E_DATA_DIR'],
        CLOUDOPS_STATIC_DIR: staticDir,
        CLOUDOPS_OPERATOR_TOKEN: OPERATOR_TOKEN,
        CLOUDOPS_ALLOWED_TARGETS: `127.0.0.1:${SANDBOX_PORT}`,
        CLOUDOPS_API_DOCS: 'false',
        CLOUDOPS_LOG_FORMAT: 'json',
      },
    },
    {
      command: `"${python}" -m cloudops_sandbox`,
      url: `http://127.0.0.1:${SANDBOX_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: { SANDBOX_HOST: '127.0.0.1', SANDBOX_PORT: String(SANDBOX_PORT) },
    },
  ],
});
