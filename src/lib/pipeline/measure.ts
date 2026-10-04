/**
 * Step 3 — "Mesurer": turn the mask into millimetres.
 *
 * A and B follow the boxing system (ISO 8624): the width and height of the
 * rectangle enclosing the lens *as worn*. "As worn" needs a horizontal to be
 * defined, and on the capture sheet it is defined by the print itself — the
 * sheet's own X axis, marked by the arrow at the bottom of the page. A lens
 * laid down a few degrees off can be straightened with `rotationDeg` without
 * re-photographing anything.
 */

import {
  polygonArea,
  polygonPerimeter,
  resampleClosed,
  simplifyClosed,
  traceContours,
} from '../geometry/marchingSquares'
import { rasterToMm, type Rectified } from '../geometry/warp'
import type { Point2D } from '../geometry/homography'
import type { SegmentationMask } from './segment'

/** Plausible lens areas. A real spectacle lens runs about 900-2200 mm2; the
 * bounds are wide enough to accept an unusual shape and tight enough to reject
 * a thumb, a shadow, or the sheet's own border. */
export const MIN_LENS_AREA_MM2 = 300
export const MAX_LENS_AREA_MM2 = 4000

/** Isoperimetric ratio below which an outline is too ragged to be a lens.
 * A circle scores 1, a typical lens about 0.9, a leaked blob well under 0.5. */
const MIN_COMPACTNESS = 0.72

/** Spacing of the final contour's vertices, in millimetres. Fine enough that
 * the polygon perimeter does not under-read a curve, coarse enough that the
 * frame offset in `frame.ts` stays quick. */
const CONTOUR_SPACING_MM = 0.25

export interface MeasureResult {
  /** Outline in raster pixels, kept for drawing the control overlay. */
  contourPx: Point2D[]
  /** Closed lens outline in sheet millimetres, evenly spaced. */
  contourMm: Point2D[]
  /** Boxing width (A), millimetres. */
  widthMm: number
  /** Boxing height (B), millimetres. */
  heightMm: number
  perimeterMm: number
  areaMm2: number
  /** Rotation applied before boxing, degrees, positive counter-clockwise. */
  rotationDeg: number
  /** Which segmentation produced it. */
  method: SegmentationMask['method']
  /** Mean rim response along the outline, 0..1. A confidence, reported in the
   * app and used to refuse a measurement rather than guess at one. */
  ridgeScore: number
  /** Competing rim response outside the outline, 0..1. See radialRidge.ts. */
  outerEvidence: number
  /** Radial offset applied to the outline, in millimetres — the one calibrated
   * number in the pipeline, see scripts/eval_mm.py --sweep. */
  offsetMm: number
}

/** Why the last `measureFromMask` call found nothing. Diagnostic only, read by
 * the evaluation harness; a user never sees it. */
export let lastRejections = ''

function rotate(points: readonly Point2D[], degrees: number): Point2D[] {
  if (degrees === 0) return [...points]
  const radians = (degrees * Math.PI) / 180
  const cos = Math.cos(radians)
  const sin = Math.sin(radians)
  return points.map((p) => ({ x: p.x * cos - p.y * sin, y: p.x * sin + p.y * cos }))
}

/**
 * Orientation of the outline's principal axis, in degrees, as the rotation that
 * would bring it level. Offered in the UI as a one-tap suggestion: a lens is
 * much wider than it is tall, so its long axis is its horizontal, and that is
 * a better guess than assuming the lens was laid down perfectly square.
 */
export function suggestRotationDeg(contourMm: readonly Point2D[]): number {
  const n = contourMm.length
  let cx = 0
  let cy = 0
  for (const p of contourMm) {
    cx += p.x
    cy += p.y
  }
  cx /= n
  cy /= n

  let sxx = 0
  let syy = 0
  let sxy = 0
  for (const p of contourMm) {
    const dx = p.x - cx
    const dy = p.y - cy
    sxx += dx * dx
    syy += dy * dy
    sxy += dx * dy
  }

  const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy)
  return -(angle * 180) / Math.PI
}

/**
 * Moves every vertex of a closed outline along its own outward normal.
 *
 * This is the pipeline's single calibration knob, and expressing it as a
 * distance in millimetres rather than as a threshold is deliberate: a residual
 * systematic error in where the outline sits *is* a distance, so correcting it
 * with one is a correction that can be measured, stated in the README, and
 * checked by anyone with a caliper. A threshold would hide the same correction
 * behind a number whose effect depends on the capture.
 */
export function offsetPolygon(points: readonly Point2D[], offset: number): Point2D[] {
  if (offset === 0 || points.length < 3) return [...points]
  const n = points.length
  return points.map((p, i) => {
    const previous = points[(i - 1 + n) % n]
    const next = points[(i + 1) % n]
    const tx = next.x - previous.x
    const ty = next.y - previous.y
    const length = Math.hypot(tx, ty)
    if (length < 1e-9) return p
    // Outward normal, for a polygon wound counter-clockwise in raster
    // coordinates (y down).
    return { x: p.x + (ty / length) * offset, y: p.y - (tx / length) * offset }
  })
}

/**
 * Validates an outline and expresses it in millimetres.
 *
 * The plausibility checks come first and matter as much as the measurement:
 * the mask legitimately holds more than the lens, and reporting "the biggest
 * thing we found" with no questions asked is how the previous version of
 * OptiFrame announced a 78 mm wide lens without hesitating.
 */
export function measureFromOutline(
  contourPx: readonly Point2D[],
  rect: Rectified,
  options: {
    method: SegmentationMask['method']
    rotationDeg?: number
    offsetMm?: number
    ridgeScore?: number
    outerEvidence?: number
  },
): MeasureResult | null {
  const { method, rotationDeg = 0, offsetMm = 0, ridgeScore = 1, outerEvidence = 0 } = options
  const mm2PerPx2 = rect.mmPerPx * rect.mmPerPx

  const offset = offsetPolygon(contourPx, offsetMm / rect.mmPerPx)
  const areaMm2 = polygonArea(offset) * mm2PerPx2
  if (areaMm2 < MIN_LENS_AREA_MM2 || areaMm2 > MAX_LENS_AREA_MM2) {
    lastRejections = `aire ${areaMm2.toFixed(0)} mm2 hors plage`
    return null
  }
  const compactness = (4 * Math.PI * polygonArea(offset)) / polygonPerimeter(offset) ** 2
  if (compactness < MIN_COMPACTNESS) {
    lastRejections = `forme irrégulière (compacité ${compactness.toFixed(2)})`
    return null
  }
  if (!offset.every((p) => p.x > 1 && p.y > 1 && p.x < rect.width - 2 && p.y < rect.height - 2)) {
    lastRejections = "l'outline touche le bord de la zone"
    return null
  }

  const spacingPx = CONTOUR_SPACING_MM / rect.mmPerPx
  const resampled = resampleClosed(offset, spacingPx)
  const contourMm = resampled.map((p) => rasterToMm(rect, p))

  const boxed = rotate(contourMm, rotationDeg)
  const xs = boxed.map((p) => p.x)
  const ys = boxed.map((p) => p.y)

  return {
    contourPx: resampled,
    contourMm,
    widthMm: Math.max(...xs) - Math.min(...xs),
    heightMm: Math.max(...ys) - Math.min(...ys),
    perimeterMm: polygonPerimeter(contourMm),
    areaMm2: polygonArea(contourMm),
    rotationDeg,
    method,
    ridgeScore,
    outerEvidence,
    offsetMm,
  }
}

/**
 * Traces the lens outline out of a probability mask (the trained-model path)
 * and measures it. The classical path does not come through here: it finds its
 * outline directly with a radial ridge trace, which is closed by construction.
 */
export function measureFromMask(
  mask: SegmentationMask,
  rect: Rectified,
  options: { maskLevel: number; rotationDeg?: number; offsetMm?: number },
): MeasureResult | null {
  const traced = traceContours(mask.data, mask.width, mask.height, options.maskLevel)
  if (traced.length === 0) {
    lastRejections = 'aucun contour fermé dans la prédiction du modèle'
    return null
  }

  for (const contour of traced) {
    const simplified = simplifyClosed(contour, 0.2)
    const measure = measureFromOutline(simplified, rect, {
      method: mask.method,
      rotationDeg: options.rotationDeg,
      offsetMm: options.offsetMm,
    })
    if (measure) return measure
  }
  return null
}

/** Re-boxes an existing measurement at a different rotation, without redoing
 * any image work — this is what the rotation control in the UI drives. */
export function reboxed(measure: MeasureResult, rotationDeg: number): MeasureResult {
  const boxed = rotate(measure.contourMm, rotationDeg)
  const xs = boxed.map((p) => p.x)
  const ys = boxed.map((p) => p.y)
  return {
    ...measure,
    rotationDeg,
    widthMm: Math.max(...xs) - Math.min(...xs),
    heightMm: Math.max(...ys) - Math.min(...ys),
  }
}

/** Recentres a contour on its own bounding box, in the orientation it will be
 * worn. Used by the SVG export and by the frame builder, which both want the
 * lens at the origin rather than wherever it happened to sit on the sheet. */
export function normalisedContour(measure: MeasureResult): Point2D[] {
  const boxed = rotate(measure.contourMm, measure.rotationDeg)
  const xs = boxed.map((p) => p.x)
  const ys = boxed.map((p) => p.y)
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2
  return boxed.map((p) => ({ x: p.x - cx, y: p.y - cy }))
}
