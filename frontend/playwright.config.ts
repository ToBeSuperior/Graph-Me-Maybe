import { defineConfig } from '@playwright/test';

// The app against a fake API (`e2e/fake-api.ts`): every request it makes is answered with
// made-up data, so these run with no demo, no graph DB and no model behind them. The browser
// is the Chrome on this machine -- nothing is downloaded.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:4173', channel: 'chrome', headless: true,
    viewport: { width: 1440, height: 900 } },
  // Built first, and never an older server reused: what is checked is the code as it is now.
  webServer: { command: 'npm run build && npx vite preview --host 127.0.0.1 --port 4173 --strictPort', port: 4173,
    reuseExistingServer: false, timeout: 120_000 },
});
