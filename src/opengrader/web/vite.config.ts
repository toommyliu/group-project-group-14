import path from 'path';
import { readFileSync } from 'node:fs';
import tailwindcss from '@tailwindcss/vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import babel from '@rolldown/plugin-babel';
import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    // Allow Tailscale MagicDNS / Serve hostnames for testing on other devices.
    allowedHosts: ['.ts.net'],
    proxy: {
      '/api': {
        target: process.env.OPENGRADER_API_URL ?? 'http://127.0.0.1:8000',
      },
      '/auth': {
        target: process.env.OPENGRADER_API_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  plugins: [
    {
      name: 'third-party-notices',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'assets/THIRD_PARTY_LICENSES.txt',
          source: readFileSync(
            path.resolve(import.meta.dirname, '../../../THIRD_PARTY_LICENSES'),
            'utf8'
          ),
        });
      },
    },
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
});
