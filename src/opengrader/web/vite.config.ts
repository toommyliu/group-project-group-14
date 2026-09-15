import path from 'path';
import { readFileSync } from 'node:fs';
import tailwindcss from '@tailwindcss/vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import babel from '@rolldown/plugin-babel';
import { defineConfig } from 'vite';

export default defineConfig({
  assetsInclude: ['**/*.wasm'],
  server: {
    // TODO: proxy all under /api so this becomes less fragile.
    proxy: {
      '/api/terminals': {
        target: process.env.OPENGRADER_API_URL ?? 'http://127.0.0.1:8000',
        ws: true,
      },
      '/assignments': process.env.OPENGRADER_API_URL ?? 'http://127.0.0.1:8000',
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
