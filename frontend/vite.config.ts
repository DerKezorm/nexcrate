import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative Adressen: unter einem Unterpfad (NEXCRATE_URL_BASE) setzt der Server <base href> in die Seite, und
  // Skripte, Stile und nachgeladene Teile finden sich von dort aus.
  base: './',
  build: {
    rollupOptions: {
      output: {
        // React, Router und i18next aendern sich selten. In einer eigenen Datei
        // bleiben sie nach einem Update im Browser liegen.
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    exclude: ['node_modules/**', 'dist/**'],
  },
  server: {
    // Fester Port: Ist er belegt, bricht Vite ab, statt still auf einen anderen auszuweichen.
    port: 5390,
    strictPort: true,
    // Der Server laeuft in der Entwicklung auf 8390. Gleiche Herkunft fuer den Browser,
    // damit das Sitzungscookie (SameSite=Strict, Pfad /api) mitgeht.
    proxy: {
      '/api': { target: 'http://localhost:8390' },
    },
  },
})
