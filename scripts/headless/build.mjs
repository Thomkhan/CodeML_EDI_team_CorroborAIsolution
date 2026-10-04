/**
 * Bundles the headless measurement harness for Node.
 *
 * Vite is already the project's bundler and resolves the same extensionless
 * TypeScript imports and JSON modules the app uses, so building through it is
 * what guarantees the harness and the browser run byte-identical pipeline code.
 */
import { build } from 'vite'

await build({
  logLevel: 'warn',
  build: {
    ssr: 'scripts/headless/measure.ts',
    outDir: 'scripts/headless/dist',
    emptyOutDir: true,
    copyPublicDir: false,
    target: 'node20',
    minify: false,
    rollupOptions: { output: { entryFileNames: 'measure.mjs' } },
  },
})
console.log('built scripts/headless/dist/measure.mjs')
