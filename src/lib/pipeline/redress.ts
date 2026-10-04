/**
 * Step 1 — "Redresser": work out the map from photo pixels to real millimetres.
 *
 * The important change from the first version of OptiFrame: we no longer warp
 * the photo to measure it. We find the reference object, derive a homography,
 * and later push the ~200 points of the lens contour through it. That skips
 * resampling a two-megapixel image (and the blur that costs), and it makes the
 * orientation bug that wrecked the first version structurally impossible — a
 * marker's identity, not a guess about which edge is which, fixes the frame.
 */

import {
  applyH,
  invertH,
  reprojectionErrorMm,
  solveHomography,
  type Correspondence,
  type Homography,
  type Point2D,
} from '../geometry/homography'
import { CAPTURE_SHEET, SHEET_MARKER_CORNERS_MM } from '../captureSheet'
import { getArucoDetector, type DetectedMarker } from '../vendor/aruco'

/** Longest side the marker detector runs at. Full-resolution detection on a
 * 12 MP phone photo costs seconds for no gain: corner noise is well under a
 * pixel either way, and the least-squares fit over 16 corners averages what is
 * left down to a few hundredths of a millimetre. */
const DETECT_MAX_DIM = 1600

/** Above this, the capture is not trustworthy: a creased sheet, a badly
 * mis-detected corner, or markers from some other print. Better to say so than
 * to return a confident wrong number. */
export const MAX_REPROJECTION_ERROR_MM = 1.5

export type ReferenceSource = 'sheet' | 'manual'

export interface ReferenceFrame {
  /** Photo pixels -> sheet millimetres. */
  pxToMm: Homography
  /** Sheet millimetres -> photo pixels. */
  mmToPx: Homography
  source: ReferenceSource
  /** Ids of the sheet markers actually used (empty for the manual fallback). */
  markerIds: number[]
  /** Mean reprojection residual over every correspondence used, in mm. */
  residualMm: number
  /** Photo pixels per millimetre near the middle of the reference, for sizing
   * the control image and choosing contour tolerances. */
  pxPerMm: number
}

export interface ImageBuffer {
  width: number
  height: number
  /** RGBA, 4 bytes per pixel, row-major. */
  data: Uint8ClampedArray
}

function downscale(image: ImageBuffer, maxDim: number): { image: ImageBuffer; scale: number } {
  const scale = Math.min(1, maxDim / Math.max(image.width, image.height))
  if (scale >= 1) return { image, scale: 1 }

  const width = Math.max(1, Math.round(image.width * scale))
  const height = Math.max(1, Math.round(image.height * scale))
  const data = new Uint8ClampedArray(width * height * 4)

  // Box filter over the source pixels each output pixel covers: a plain
  // nearest-neighbour drop would alias the marker's black/white cells into
  // grey mush and cost us detections.
  const xRatio = image.width / width
  const yRatio = image.height / height
  for (let y = 0; y < height; y++) {
    const sy0 = Math.floor(y * yRatio)
    const sy1 = Math.min(image.height, Math.max(sy0 + 1, Math.floor((y + 1) * yRatio)))
    for (let x = 0; x < width; x++) {
      const sx0 = Math.floor(x * xRatio)
      const sx1 = Math.min(image.width, Math.max(sx0 + 1, Math.floor((x + 1) * xRatio)))
      let r = 0
      let g = 0
      let b = 0
      let n = 0
      for (let sy = sy0; sy < sy1; sy++) {
        for (let sx = sx0; sx < sx1; sx++) {
          const i = (sy * image.width + sx) * 4
          r += image.data[i]
          g += image.data[i + 1]
          b += image.data[i + 2]
          n++
        }
      }
      const o = (y * width + x) * 4
      data[o] = r / n
      data[o + 1] = g / n
      data[o + 2] = b / n
      data[o + 3] = 255
    }
  }

  return { image: { width, height, data }, scale }
}

/** Intersection of a quadrilateral's diagonals — the exact projection of a
 * square's centre, which the centroid of the four image corners is not. */
function diagonalIntersection(quad: Point2D[]): Point2D | null {
  const [a, b, c, d] = quad
  const r1x = c.x - a.x
  const r1y = c.y - a.y
  const r2x = d.x - b.x
  const r2y = d.y - b.y
  const denominator = r1x * r2y - r1y * r2x
  if (Math.abs(denominator) < 1e-9) return null
  const t = ((b.x - a.x) * r2y - (b.y - a.y) * r2x) / denominator
  return { x: a.x + t * r1x, y: a.y + t * r1y }
}

function buildFrame(
  correspondences: Correspondence[],
  source: ReferenceSource,
  markerIds: number[],
): ReferenceFrame | null {
  const pxToMm = solveHomography(correspondences)
  if (!pxToMm) return null
  const mmToPx = invertH(pxToMm)
  if (!mmToPx) return null

  // Local scale at the middle of the reference: how many photo pixels one
  // millimetre spans there.
  const centreMm = { x: CAPTURE_SHEET.pageWidthMm / 2, y: CAPTURE_SHEET.pageHeightMm / 2 }
  const origin = applyH(mmToPx, centreMm)
  const alongX = applyH(mmToPx, { x: centreMm.x + 1, y: centreMm.y })
  const alongY = applyH(mmToPx, { x: centreMm.x, y: centreMm.y + 1 })
  const pxPerMm =
    (Math.hypot(alongX.x - origin.x, alongX.y - origin.y) +
      Math.hypot(alongY.x - origin.x, alongY.y - origin.y)) /
    2

  return {
    pxToMm,
    mmToPx,
    source,
    markerIds,
    residualMm: reprojectionErrorMm(pxToMm, correspondences),
    pxPerMm,
  }
}

/**
 * Finds the printed capture sheet and returns the pixel->millimetre frame.
 *
 * Two passes. The first uses only each marker's diagonal intersection — a point
 * that does not depend on the detector's corner ordering at all — to get a
 * provisional homography from three or four markers. The second uses that
 * homography to predict where every corner should be and matches the detected
 * corners to it, then refits on all sixteen. The provisional pass is what makes
 * the result independent of any assumption about corner order, and the refit is
 * what makes it precise.
 */
export function detectCaptureSheet(image: ImageBuffer): ReferenceFrame | null {
  const { image: small, scale } = downscale(image, DETECT_MAX_DIM)

  const detector = getArucoDetector(CAPTURE_SHEET.dictionary)
  const detected: DetectedMarker[] = detector.detectImage(small.width, small.height, small.data)

  // Keep only ids that are actually on our sheet, and only the first sighting
  // of each: the detector occasionally reports a spurious extra marker from
  // high-contrast clutter, and a duplicate id would poison the fit.
  const byId = new Map<number, Point2D[]>()
  for (const marker of detected) {
    if (!SHEET_MARKER_CORNERS_MM.has(marker.id) || byId.has(marker.id)) continue
    if (marker.corners.length !== 4) continue
    byId.set(
      marker.id,
      marker.corners.map((c) => ({ x: c.x / scale, y: c.y / scale })),
    )
  }
  if (byId.size < 4) return null

  const centreCorrespondences: Correspondence[] = []
  for (const [id, quad] of byId) {
    const sheetQuad = SHEET_MARKER_CORNERS_MM.get(id)!
    const src = diagonalIntersection(quad)
    const dst = diagonalIntersection(sheetQuad)
    if (src && dst) centreCorrespondences.push({ src, dst })
  }
  if (centreCorrespondences.length < 4) return null

  const provisional = solveHomography(centreCorrespondences)
  if (!provisional) return null

  // Match each detected corner to the sheet corner it lands nearest to. Any
  // corner that is closer to a *different* corner of its own marker than to the
  // one it should be means the marker was mis-read; drop the whole marker.
  const corner: Correspondence[] = []
  const usedIds: number[] = []
  for (const [id, quad] of byId) {
    const sheetQuad = SHEET_MARKER_CORNERS_MM.get(id)!
    const projected = quad.map((p) => applyH(provisional, p))
    const assignment = projected.map((p) => {
      let best = 0
      let bestDistance = Number.POSITIVE_INFINITY
      sheetQuad.forEach((s, j) => {
        const d = Math.hypot(p.x - s.x, p.y - s.y)
        if (d < bestDistance) {
          bestDistance = d
          best = j
        }
      })
      return best
    })

    const distinct = new Set(assignment).size === 4
    if (!distinct) continue

    usedIds.push(id)
    quad.forEach((src, i) => corner.push({ src, dst: sheetQuad[assignment[i]] }))
  }

  if (corner.length < 8) return null

  const frame = buildFrame(corner, 'sheet', usedIds.sort((a, b) => a - b))
  if (!frame || frame.residualMm > MAX_REPROJECTION_ERROR_MM) return null
  return frame
}

/**
 * Manual fallback for a team (or a jury) with no printer: four tapped corners
 * of a rectangle of known size — a bank card, an A4 sheet.
 *
 * This is where the original OptiFrame lost thirty points. It always mapped the
 * first tapped edge onto the reference's *width*, so photographing a bank card
 * in portrait forced its 54 mm side to measure 85.6 mm and sheared the whole
 * plane by about 87 %. A rectangle photographed near head-on tells you its own
 * orientation: the ratio of its two image edge lengths matches one assignment
 * and not the other, and on real captures the two candidates differ by 86 %
 * against 1 %, so the choice is never close.
 */
export function referenceFrameFromCorners(
  corners: [Point2D, Point2D, Point2D, Point2D],
  referenceWidthMm: number,
  referenceHeightMm: number,
): ReferenceFrame | null {
  const edge = (i: number) =>
    Math.hypot(corners[(i + 1) % 4].x - corners[i].x, corners[(i + 1) % 4].y - corners[i].y)

  const firstEdgeLength = (edge(0) + edge(2)) / 2
  const secondEdgeLength = (edge(1) + edge(3)) / 2
  if (firstEdgeLength < 1e-6 || secondEdgeLength < 1e-6) return null

  const observedRatio = firstEdgeLength / secondEdgeLength
  const landscapeMismatch = Math.abs(Math.log(observedRatio / (referenceWidthMm / referenceHeightMm)))
  const portraitMismatch = Math.abs(Math.log(observedRatio / (referenceHeightMm / referenceWidthMm)))

  const [w, h] =
    landscapeMismatch <= portraitMismatch
      ? [referenceWidthMm, referenceHeightMm]
      : [referenceHeightMm, referenceWidthMm]

  // Centre the reference inside the sheet's own millimetre frame, so everything
  // downstream (the lens zone test, the control image) keeps one coordinate
  // system whichever reference was used.
  const cx = CAPTURE_SHEET.pageWidthMm / 2
  const cy = CAPTURE_SHEET.pageHeightMm / 2
  const destination: Point2D[] = [
    { x: cx - w / 2, y: cy - h / 2 },
    { x: cx + w / 2, y: cy - h / 2 },
    { x: cx + w / 2, y: cy + h / 2 },
    { x: cx - w / 2, y: cy + h / 2 },
  ]

  return buildFrame(
    corners.map((src, i) => ({ src, dst: destination[i] })),
    'manual',
    [],
  )
}

/** Sorts 4 arbitrary tapped points into [topLeft, topRight, bottomRight, bottomLeft]. */
export function orderCorners(points: Point2D[]): [Point2D, Point2D, Point2D, Point2D] {
  const cx = points.reduce((s, p) => s + p.x, 0) / points.length
  const cy = points.reduce((s, p) => s + p.y, 0) / points.length
  const sorted = [...points].sort(
    (a, b) => Math.atan2(a.y - cy, a.x - cx) - Math.atan2(b.y - cy, b.x - cx),
  )
  const startIndex = sorted.reduce(
    (best, p, i) => (p.x + p.y < sorted[best].x + sorted[best].y ? i : best),
    0,
  )
  return [...sorted.slice(startIndex), ...sorted.slice(0, startIndex)] as [
    Point2D, Point2D, Point2D, Point2D,
  ]
}
