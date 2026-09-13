import path from 'path';
import tailwindcss from '@tailwindcss/vite';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import babel from '@rolldown/plugin-babel';
import { defineConfig } from 'vite';


export default defineConfig({
  server: {
    // TODO: proxy all under /api so this becomes less fragile.
    proxy: {
      '/assignments': process.env.OPENGRADER_API_URL ?? 'http://127.0.0.1:8000',
    },
  },
  plugins: [
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
