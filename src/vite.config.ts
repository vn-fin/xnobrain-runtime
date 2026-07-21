import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8642',
      '/agent-gateway': 'http://127.0.0.1:8642',
      '/conversations': 'http://127.0.0.1:8642',
      '/sandboxes': 'http://127.0.0.1:8642',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
