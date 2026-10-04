/**
 * Palier 2: the trained segmentation model, run in the browser.
 *
 * Why a model at all, when the classical path already measures to about a
 * millimetre: because the classical path finds the lens by its rim, and a rim
 * is the one thing a bad capture destroys. A reflection across the edge, a dark
 * lens on a dim sheet, a tinted or heavily coated lens — the ridge goes faint
 * and the app refuses rather than guesses. A model trained on exactly those
 * cases sees the lens as a region, with its faint interior contrast, its
 * shadow and its highlights all contributing, and returns a mask where the
 * classical path returns nothing.
 *
 * Why a small U-Net rather than a large model: it has to run on the jury's own
 * phone, inside the thirty seconds the brief allows, with no server. A hundred
 * and fifty thousand parameters at 256x256 is a few hundred milliseconds on the
 * WebAssembly backend, and under a megabyte to download.
 *
 * Why the WebAssembly backend rather than WebGL: this runs inside a Web Worker,
 * and WebGL in a worker needs OffscreenCanvas, whose support across the iOS and
 * Android versions a jury might bring is the kind of thing that works on the
 * laptop and fails on the day. WASM is slower and dependable, and the model is
 * small enough that the difference does not matter.
 *
 * The model is optional at runtime. If `public/model/` holds no model, this
 * reports as much and the pipeline falls back to the classical path — the brief
 * is explicit that a reliable measurement without AI beats AI without a
 * measurement.
 */

import type { Rectified } from '../geometry/warp'

/** Side of the square the model takes as input. */
const INPUT_SIZE = 256

export interface ModelPrediction {
  probabilities: Float32Array
  width: number
  height: number
}

type GraphModel = {
  executeAsync: (input: unknown) => Promise<unknown>
  dispose: () => void
}

let loadPromise: Promise<GraphModel | null> | null = null
let tf: typeof import('@tensorflow/tfjs-core') | null = null

/**
 * Is there actually a model published here?
 *
 * Worth asking explicitly rather than letting the loader fail. A single-page app
 * answers every unknown path with its own index.html, so a missing model does
 * not 404 — it returns HTML, and `loadGraphModel` then fails somewhere deep in a
 * JSON parse with a message that looks like a bug in the app. Checking first
 * turns "no model published" into the ordinary, silent, expected case it is.
 */
async function modelIsPublished(modelUrl: string): Promise<boolean> {
  try {
    const response = await fetch(modelUrl, { cache: 'force-cache' })
    if (!response.ok) return false
    const manifest: unknown = await response.json()
    return typeof manifest === 'object' && manifest !== null && 'modelTopology' in manifest
  } catch {
    return false
  }
}

async function load(modelUrl: string): Promise<GraphModel | null> {
  if (!(await modelIsPublished(modelUrl))) return null

  try {
    const [core, converter, wasmBackend] = await Promise.all([
      import('@tensorflow/tfjs-core'),
      import('@tensorflow/tfjs-converter'),
      import('@tensorflow/tfjs-backend-wasm'),
    ])
    tf = core
    // Serve the backend's own .wasm files from our origin: the default is a
    // CDN, and the brief requires the app to work with little or no connection.
    wasmBackend.setWasmPaths(new URL('tfjs-wasm/', import.meta.url).href)
    await core.setBackend('wasm')
    await core.ready()
    return (await converter.loadGraphModel(modelUrl)) as unknown as GraphModel
  } catch {
    // No model published, or the device could not start the backend. Both are
    // recoverable: the caller falls back to the classical path.
    return null
  }
}

export function loadSegmentationModel(modelUrl: string): Promise<GraphModel | null> {
  if (!loadPromise) loadPromise = load(modelUrl)
  return loadPromise
}

/**
 * Resamples the rectified lens zone to the model's input, runs it, and returns
 * the raw probability map. Resizing back onto the measurement raster is left to
 * `maskFromModelOutput`, so both segmentation paths hand the rest of the
 * pipeline the same thing.
 */
export async function predictLensMask(
  modelUrl: string,
  rect: Rectified,
): Promise<ModelPrediction | null> {
  const model = await loadSegmentationModel(modelUrl)
  if (!model || !tf) return null

  // Nearest-neighbour down to the model's input. The lens occupies a good
  // fraction of the zone and the model was trained on the same transform, so
  // nothing is gained by being cleverer here.
  const input = new Float32Array(INPUT_SIZE * INPUT_SIZE)
  const stepX = rect.width / INPUT_SIZE
  const stepY = rect.height / INPUT_SIZE
  for (let y = 0; y < INPUT_SIZE; y++) {
    const sy = Math.min(rect.height - 1, Math.floor(y * stepY))
    for (let x = 0; x < INPUT_SIZE; x++) {
      const sx = Math.min(rect.width - 1, Math.floor(x * stepX))
      input[y * INPUT_SIZE + x] = rect.gray[sy * rect.width + sx]
    }
  }

  const tensor = tf.tensor4d(input, [1, INPUT_SIZE, INPUT_SIZE, 1])
  try {
    const output = (await model.executeAsync(tensor)) as { data: () => Promise<Float32Array>; dispose: () => void }
    const probabilities = await output.data()
    output.dispose()
    return { probabilities: new Float32Array(probabilities), width: INPUT_SIZE, height: INPUT_SIZE }
  } catch {
    return null
  } finally {
    tensor.dispose()
  }
}
