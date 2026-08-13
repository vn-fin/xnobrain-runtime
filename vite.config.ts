import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const configuredName = (env.VITE_APP_NAME || env.XNOBRAIN_APP_NAME || '').trim();
  const configuredDescription = (
    env.VITE_APP_DESCRIPTION
    || env.XNOBRAIN_APP_DESCRIPTION
    || ''
  ).trim();
  const appName = configuredName || 'XNOBrain';
  const appDescription = configuredDescription
    || 'Your private AI workspace for agents, conversations, skills, and automation.';
  const backendPort = env.XNOBRAIN_DEV_API_PORT || '8642';

  return {
    define: {
      __APP_NAME__: JSON.stringify(appName),
      __APP_DESCRIPTION__: JSON.stringify(appDescription),
    },
    plugins: [react()],
    server: {
      watch: {
        // The repository-local Python, Node, browser, and office toolchains
        // contain tens of thousands of files and are never frontend sources.
        // Watching them can exhaust Linux's inotify limit during `make dev`.
        ignored: ['**/app/**', '**/.tools/**', '**/dist/**'],
      },
      proxy: {
        '/api': `http://127.0.0.1:${backendPort}`,
        '/xnobrain/api/runtime': `http://127.0.0.1:${backendPort}`,
      },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      exclude: ['**/node_modules*/**', 'dist/**', '.tools/**'],
      setupFiles: ['./src/test/setup.ts'],
    },
  };
});
