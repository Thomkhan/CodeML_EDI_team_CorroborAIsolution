/**
 * Sub-pixel contour extraction from a scalar mask, in plain TypeScript.
 *
 * Replaces `cv.findContours`, and improves on it: `findContours` snaps to whole
 * pixels, while marching squares interpolates where the mask actually crosses
 * the iso level. At the ~10 px/mm our captures run at, that difference is worth
 * roughly a tenth of a millimetre on A and B — real money against a 1 mm target.
 */

import type { Point2D } from './homography'

export type { Point2D }

/** One crossing on a cell edge, keyed so two neighbouring cells agree on it. */
interface Crossing {
  key: number
  point: Point2D
}

function lerp(a: number, b: number, iso: number): number {
  const d = b - a
  // A flat edge can't be crossed; fall back to the midpoint rather than NaN.
  return Math.abs(d) < 1e-9 ? 0.5 : (iso - a) / d
}

/**
 * Marching-squares case table. Bits: 1 = top-left inside, 2 = top-right,
 * 4 = bottom-right, 8 = bottom-left. Each entry lists the cell edges a segment
 * connects — 'T' top, 'R' right, 'B' bottom, 'L' left. The two saddle cases
 * (5 and 10) emit two segments; which pairing is chosen only matters for how
 * two diagonally-touching blobs are split, never for a lens.
 */
const CASES: ReadonlyArray<ReadonlyArray<readonly [string, string]>> = [
  [], [['L', 'T']], [['T', 'R']], [['L', 'R']],
  [['R', 'B']], [['L', 'T'], ['R', 'B']], [['T', 'B']], [['L', 'B']],
  [['B', 'L']], [['T', 'B']], [['T', 'R'], ['B', 'L']], [['R', 'B']],
  [['R', 'L']], [['T', 'R']], [['L', 'T']], [],
]

/**
 * Traces every closed iso-contour of `mask` at `iso` and returns them as
 * sub-pixel polygons, largest enclosed area first.
 *
 * `mask` is row-major, `width * height`, any scalar range (a probability map
 * from the model, or a normalised gradient magnitude from the classical
 * fallback). Only contours that close are returned: a shape running off the
 * edge of the image is not a usable lens outline anyway.
 */
export function traceContours(
  mask: Float32Array,
  width: number,
  height: number,
  iso: number,
): Point2D[][] {
  // Edge keys: a horizontal edge starting at (x, y) and a vertical edge
  // starting at (x, y) get distinct keys, so the two cells sharing an edge
  // produce the identical key and the segments chain exactly, with no
  // floating-point coordinate matching.
  const horizontalKey = (x: number, y: number) => 2 * (y * (width + 1) + x)
  const verticalKey = (x: number, y: number) => 2 * (y * (width + 1) + x) + 1

  const points = new Map<number, Point2D>()
  // Each crossing is shared by at most two segments, so two slots suffice.
  const links = new Map<number, number[]>()

  const addSegment = (a: Crossing, b: Crossing) => {
    if (a.key === b.key) return
    points.set(a.key, a.point)
    points.set(b.key, b.point)
    for (const [from, to] of [[a.key, b.key], [b.key, a.key]] as const) {
      const existing = links.get(from)
      if (existing) {
        if (existing.length < 2 && !existing.includes(to)) existing.push(to)
      } else {
        links.set(from, [to])
      }
    }
  }

  for (let y = 0; y < height - 1; y++) {
    for (let x = 0; x < width - 1; x++) {
      const tl = mask[y * width + x]
      const tr = mask[y * width + x + 1]
      const br = mask[(y + 1) * width + x + 1]
      const bl = mask[(y + 1) * width + x]

      const code =
        (tl >= iso ? 1 : 0) | (tr >= iso ? 2 : 0) | (br >= iso ? 4 : 0) | (bl >= iso ? 8 : 0)
      const segments = CASES[code]
      if (segments.length === 0) continue

      const crossingFor = (edge: string): Crossing => {
        switch (edge) {
          case 'T':
            return { key: horizontalKey(x, y), point: { x: x + lerp(tl, tr, iso), y } }
          case 'R':
            return { key: verticalKey(x + 1, y), point: { x: x + 1, y: y + lerp(tr, br, iso) } }
          case 'B':
            return { key: horizontalKey(x, y + 1), point: { x: x + lerp(bl, br, iso), y: y + 1 } }
          default:
            return { key: verticalKey(x, y), point: { x, y: y + lerp(tl, bl, iso) } }
        }
      }

      for (const [from, to] of segments) addSegment(crossingFor(from), crossingFor(to))
    }
  }

  // Walk the segment graph. Every crossing has degree 2 inside a closed
  // contour, so each walk either returns to its start (a usable loop) or dies
  // at a degree-1 crossing, which only happens where the contour ran off the
  // image.
  const visited = new Set<number>()
  const contours: Point2D[][] = []

  for (const startKey of links.keys()) {
    if (visited.has(startKey)) continue

    const loop: Point2D[] = []
    let current = startKey
    let previous = -1
    let closed = false

    while (true) {
      visited.add(current)
      loop.push(points.get(current)!)

      const neighbours = links.get(current)
      if (!neighbours) break
      const next = neighbours.find((n) => n !== previous)
      if (next === undefined) break
      if (next === startKey) {
        closed = true
        break
      }
      if (visited.has(next)) break
      previous = current
      current = next
    }

    if (closed && loop.length >= 8) contours.push(loop)
  }

  return contours.sort((a, b) => polygonArea(b) - polygonArea(a))
}

/** Unsigned area of a closed polygon (shoelace). */
export function polygonArea(points: readonly Point2D[]): number {
  let area = 0
  for (let i = 0; i < points.length; i++) {
    const a = points[i]
    const b = points[(i + 1) % points.length]
    area += a.x * b.y - b.x * a.y
  }
  return Math.abs(area) / 2
}

export function polygonPerimeter(points: readonly Point2D[]): number {
  let total = 0
  for (let i = 0; i < points.length; i++) {
    const a = points[i]
    const b = points[(i + 1) % points.length]
    total += Math.hypot(b.x - a.x, b.y - a.y)
  }
  return total
}

function perpendicularDistance(p: Point2D, a: Point2D, b: Point2D): number {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const lengthSq = dx * dx + dy * dy
  if (lengthSq < 1e-12) return Math.hypot(p.x - a.x, p.y - a.y)
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq))
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
}

/** Douglas–Peucker on an open polyline. */
function simplifyOpen(points: Point2D[], tolerance: number): Point2D[] {
  if (points.length < 3) return points

  let worst = 0
  let worstIndex = 0
  const first = points[0]
  const last = points[points.length - 1]

  for (let i = 1; i < points.length - 1; i++) {
    const d = perpendicularDistance(points[i], first, last)
    if (d > worst) {
      worst = d
      worstIndex = i
    }
  }

  if (worst <= tolerance) return [first, last]
  const left = simplifyOpen(points.slice(0, worstIndex + 1), tolerance)
  const right = simplifyOpen(points.slice(worstIndex), tolerance)
  return [...left.slice(0, -1), ...right]
}

/**
 * Douglas–Peucker for a closed ring. Split at the two mutually-farthest points
 * first, so the simplification has no arbitrary seam where the ring's start
 * happened to fall.
 */
export function simplifyClosed(points: Point2D[], tolerance: number): Point2D[] {
  if (points.length < 4) return points

  let anchorIndex = 0
  let bestDistance = -1
  for (let i = 1; i < points.length; i++) {
    const d = Math.hypot(points[i].x - points[0].x, points[i].y - points[0].y)
    if (d > bestDistance) {
      bestDistance = d
      anchorIndex = i
    }
  }

  const half1 = points.slice(0, anchorIndex + 1)
  const half2 = [...points.slice(anchorIndex), points[0]]
  const simplified = [
    ...simplifyOpen(half1, tolerance).slice(0, -1),
    ...simplifyOpen(half2, tolerance).slice(0, -1),
  ]
  return simplified.length >= 3 ? simplified : points
}

/**
 * Resamples a closed polygon to evenly spaced vertices. Marching squares emits
 * one vertex per crossed cell edge, which is dense where the contour runs
 * diagonally and sparse where it runs along an axis; evening that out makes the
 * perimeter and the frame offset behave predictably.
 */
export function resampleClosed(points: readonly Point2D[], spacing: number): Point2D[] {
  if (points.length < 3) return [...points]
  const perimeter = polygonPerimeter(points)
  if (perimeter < 1e-9) return [...points]

  const count = Math.max(16, Math.round(perimeter / spacing))
  const step = perimeter / count
  const out: Point2D[] = []

  const vertexAt = (i: number) => points[i % points.length]
  let segmentIndex = 0
  let distanceAtSegmentStart = 0
  let segmentLength = Math.hypot(
    vertexAt(1).x - vertexAt(0).x,
    vertexAt(1).y - vertexAt(0).y,
  )

  for (let i = 0; i < count; i++) {
    const target = i * step
    while (distanceAtSegmentStart + segmentLength < target && segmentIndex < points.length) {
      distanceAtSegmentStart += segmentLength
      segmentIndex++
      const a = vertexAt(segmentIndex)
      const b = vertexAt(segmentIndex + 1)
      segmentLength = Math.hypot(b.x - a.x, b.y - a.y)
      if (segmentIndex >= points.length) break
    }

    const a = vertexAt(segmentIndex)
    const b = vertexAt(segmentIndex + 1)
    const t = segmentLength < 1e-9 ? 0 : (target - distanceAtSegmentStart) / segmentLength
    out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })
  }

  return out
}
