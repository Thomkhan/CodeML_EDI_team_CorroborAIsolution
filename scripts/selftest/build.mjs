/** Bundles the frame/STL self-test for Node, the same way the measurement
 * harness is bundled — through Vite, so the imports resolve identically to the
 * app's. */
import { build } from 'vite'

await build({
  logLevel: 'warn',
  build: {
    ssr: process.env.SELFTEST_ENTRY ?? 'scripts/selftest/frame.ts',
    outDir: 'scripts/selftest/dist',
    emptyOutDir: true,
    copyPublicDir: false,
    target: 'node20',
    minify: false,
    rollupOptions: { output: { entryFileNames: process.env.SELFTEST_OUT ?? 'frame.mjs' } },
  },
})
