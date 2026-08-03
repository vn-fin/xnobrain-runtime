import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const host = process.env.TAURI_DEV_HOST

export default defineConfig({
  define: {
    'import.meta.env.VITE_XNOBRAIN_APP_NAME': JSON.stringify(
      process.env.XNOBRAIN_APP_NAME?.trim() || 'XNOBrain',
    ),
    'import.meta.env.VITE_XNOBRAIN_APP_DESCRIPTION': JSON.stringify(
      process.env.XNOBRAIN_APP_DESCRIPTION?.trim()
        || 'Install and run the private XNOBrain Docker Web workspace.',
    ),
  },
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || '127.0.0.1',
    hmr: host ? { protocol: 'ws', host, port: 1421 } : undefined,
    watch: { ignored: ['**/src-tauri/**'] },
  },
  build: {
    target: process.env.TAURI_ENV_PLATFORM === 'windows' ? 'chrome105' : 'safari13',
    minify: process.env.TAURI_ENV_DEBUG ? false : 'esbuild',
    sourcemap: Boolean(process.env.TAURI_ENV_DEBUG),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: true,
  },
})
