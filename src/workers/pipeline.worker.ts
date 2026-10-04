/**
 * The whole measurement, off the main thread.
 *
 * This is the direct answer to the first version of OptiFrame freezing phones.
 * That build parsed a 10 MB OpenCV.js bundle and then ran marker detection,
 * a full-image perspective warp, Canny and a hand-written flood fill
 * synchronously on the UI thread; a mid-range phone simply stopped responding
 * for the duration. Nothing heavy happens on the main thread any more, so the
 * page stays scrollable while a measurement runs — which is also the brief's
 * performance requirement, and the easiest thing for a jury to check.
 */

import { measureCapture, manualFrame, type CaptureOptions } from '../lib/pipeline/runPipeline'
import type { ReferenceFrame } from '../lib/pipeline/redress'
import type { Point2D } from '../lib/geometry/homography'
import type { MeasureResult } from '../lib/pipeline/measure'
import { predictLensMask } from '../lib/model/segmenter'

/** Where a converted model is published, if the team trained one. Resolved
 * against the deployment's base URL so it works under a subpath too. */
const MODEL_URL = new URL('model/model.json', self.location.href).href

export interface MeasureRequest {
  id: number
  /** The photo, as raw RGBA. Transferred, not copied. */
  width: number
  height: number
  pixels: ArrayBuffer
  rotationDeg?: number
  /** Measured length of the sheet's printed control segment, in millimetres. */
  controlLengthMm?: number
  /** Manual four-corner calibration, when the sheet could not be found. */
  manual?: {
    corners: [Point2D, Point2D, Point2D, Point2D]
    widthMm: number
    heightMm: number
  }
}

export interface MeasureResponse {
  id: number
  ok: boolean
  reason: string | null
  measure: MeasureResult | null
  reference: {
    source: ReferenceFrame['source']
    markerIds: number[]
    residualMm: number
    pxPerMm: number
  } | null
  /** The rectified lens zone, for the control image and the step-by-step page.
   * Drawn on the main thread: a worker would need OffscreenCanvas, which buys
   * nothing here and is one more thing to fail on an older iOS. */
  rect: {
    width: number
    height: number
    pixels: ArrayBuffer
    mmPerPx: number
    originMm: Point2D
  } | null
  elapsedMs: number
}

self.onmessage = async (event: MessageEvent<MeasureRequest>) => {
  const request = event.data
  const startedAt = performance.now()

  try {
    const image = {
      width: request.width,
      height: request.height,
      data: new Uint8ClampedArray(request.pixels),
    }

    const overrides: Partial<CaptureOptions> = {
      rotationDeg: request.rotationDeg ?? 0,
      ...(request.controlLengthMm ? { controlLengthMm: request.controlLengthMm } : {}),
      // Returns null when no model has been published, and the pipeline then
      // uses the classical path without comment.
      predictor: (rect) => predictLensMask(MODEL_URL, rect),
    }
    if (request.manual) {
      const frame = manualFrame(
        request.manual.corners,
        request.manual.widthMm,
        request.manual.heightMm,
      )
      if (!frame) {
        post({
          id: request.id,
          ok: false,
          reason: "Les quatre points touchés ne forment pas un quadrilatère exploitable. Reprenez-les en suivant bien les coins de l'objet.",
          measure: null,
          reference: null,
          rect: null,
          elapsedMs: performance.now() - startedAt,
        })
        return
      }
      overrides.frame = frame
    }

    const outcome = await measureCapture(image, overrides)

    const response: MeasureResponse = {
      id: request.id,
      ok: outcome.measure !== null,
      reason: outcome.reason,
      measure: outcome.measure,
      reference: outcome.frame
        ? {
            source: outcome.frame.source,
            markerIds: outcome.frame.markerIds,
            residualMm: outcome.frame.residualMm,
            pxPerMm: outcome.frame.pxPerMm,
          }
        : null,
      rect: outcome.rect
        ? {
            width: outcome.rect.width,
            height: outcome.rect.height,
            pixels: outcome.rect.rgba.buffer as ArrayBuffer,
            mmPerPx: outcome.rect.mmPerPx,
            originMm: outcome.rect.originMm,
          }
        : null,
      elapsedMs: performance.now() - startedAt,
    }

    post(response, response.rect ? [response.rect.pixels] : [])
  } catch (error) {
    post({
      id: request.id,
      ok: false,
      reason: error instanceof Error ? error.message : String(error),
      measure: null,
      reference: null,
      rect: null,
      elapsedMs: performance.now() - startedAt,
    })
  }
}

function post(message: MeasureResponse, transfer: Transferable[] = []) {
  ;(self as unknown as Worker).postMessage(message, transfer)
}
