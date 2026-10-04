/**
 * Typed surface over the vendored js-aruco2 (see scripts/vendor_aruco.mjs).
 *
 * The detector takes raw RGBA bytes rather than an `ImageData` object, which is
 * what lets it run inside a Web Worker with no DOM at all.
 */
// @ts-expect-error - vendored plain JS, no types upstream
import { AR } from './jsAruco2/aruco.js'

export interface DetectedMarker {
  id: number
  /** Marker corners in image pixels, in the dictionary's own order:
   * top-left, top-right, bottom-right, bottom-left of the marker as printed.
   * Verified against a synthetically warped render of our own capture sheet. */
  corners: Array<{ x: number; y: number }>
}

interface DetectorInstance {
  detectImage: (width: number, height: number, data: Uint8ClampedArray) => DetectedMarker[]
}

let detector: DetectorInstance | null = null

export function getArucoDetector(dictionaryName: string): DetectorInstance {
  if (!detector) {
    detector = new (AR as { Detector: new (c: { dictionaryName: string }) => DetectorInstance }).Detector({
      dictionaryName,
    })
  }
  return detector
}
