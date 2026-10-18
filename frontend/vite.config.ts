/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The server answers on 8765 (`graphme serve`, or the demo: `python tools/hierarchy_demo/app.py`);
// in development the app is served here and asks it through this proxy, so both run as one origin.
const DEMO = process.env.DEMO_URL ?? 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // changeOrigin off: the Host stays this page's, so a dev server opened with --host is refused
  // by a keyless server instead of passing as 127.0.0.1.
  server: { port: 5173, proxy: { '/v1': { target: DEMO, changeOrigin: false } } },
  test: { include: ['src/**/*.test.ts'] },
});
