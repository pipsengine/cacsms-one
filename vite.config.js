import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { startSportsDevServices } from './scripts/sports-dev-services.mjs';
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'sports-dev-services',
      apply: 'serve',
      configureServer() {
        startSportsDevServices();
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      '/api/sports-ai': {
        target: 'http://127.0.0.1:8011',
        changeOrigin: true
      }
    }
  }
});
