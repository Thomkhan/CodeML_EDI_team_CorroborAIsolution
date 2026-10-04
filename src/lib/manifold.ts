import type { ManifoldToplevel } from 'manifold-3d'

let loadPromise: Promise<ManifoldToplevel> | null = null

/**
 * Loads manifold-3d, and its half-megabyte WebAssembly binary, on first use.
 *
 * Imported dynamically so neither reaches the phone until somebody actually
 * generates a frame. The measurement — which is what the jury does first, and
 * what has to work on a slow connection — needs none of it.
 *
 * The .wasm is resolved through Vite's asset pipeline rather than a hard-coded
 * public path, so it is fingerprinted, cached properly, and keeps working under
 * any deployment base path.
 */
export async function loadManifold(): Promise<ManifoldToplevel> {
  if (!loadPromise) {
    loadPromise = (async () => {
      const [{ default: Module }, wasmUrl] = await Promise.all([
        import('manifold-3d'),
        import('manifold-3d/manifold.wasm?url').then((m) => m.default),
      ])
      const wasm = await Module({ locateFile: () => wasmUrl })
      wasm.setup()
      return wasm
    })()
  }
  return loadPromise
}
