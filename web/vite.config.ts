import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// The BFF is the only backend the browser talks to.
const bff = process.env.POC_BFF_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {port: 5173, proxy: {'/api': {target: bff, changeOrigin: true}}},
  preview: {port: 5173, proxy: {'/api': {target: bff, changeOrigin: true}}},
});
