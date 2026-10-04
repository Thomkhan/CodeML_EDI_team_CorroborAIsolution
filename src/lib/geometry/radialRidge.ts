/**
 * Traces the brightest closed ridge around a point, by dynamic programming in
 * polar coordinates.
 *
 * This replaces thresholding the rim response and flood-filling it, which is
 * the obvious approach and is badly brittle in practice. A threshold has to
 * separate the faintest tenth of the rim — where a reflection washes it out —
 * from the strongest tenth of the sheet's own noise, and on real captures those
 * two overlap. Set it either way and the ring either opens, so the fill escapes
 * and returns nothing, or welds onto the noise, so the fill returns the whole
 * sheet. Both failures are silent and both are common.
 *
 * Asking for a closed curve directly removes the problem rather than tuning
 * around it. A lens is star-shaped about any interior point, so its outline is
 * a single radius per direction. Scoring each candidate radius by the rim
 * response there, and charging for radius changes between neighbouring
 * directions, makes finding the outline a shortest-path problem that is solved
 * exactly. The result is closed and smooth by construction: a stretch of rim
 * that has vanished entirely costs nothing extra to cross, because the
 * smoothness term simply carries the radius straight through it.
 *
 * It also lands where it should. The rim paints a band straddling the true
 * edge, and the ridge of that band — its brightest line — is its middle,
 * independent of how wide the bevel on this particular lens made it.
 *
 * One wrinkle needs a thumb on the scale. A lens often shows a second, inner
 * ring — the caustic thrown by its own bevel, or a ring-shaped reflection — and
 * that inner ring can be *brighter* than the rim outside it. Left alone the
 * search happily takes it, and returns a lens 30 % too small while reporting
 * high confidence, because it genuinely did follow a bright closed ridge all
 * the way round. A small reward per pixel of radius settles those ties outwards
 * without being able to overcome a real difference in brightness.
 */

import type { Point2D } from './homography'

export interface RadialRidgeOptions {
  /** Starting guess for a point inside the lens, in raster pixels. */
  centre: Point2D
  minRadiusPx: number
  maxRadiusPx: number
  /** Directions sampled around the centre. */
  rayCount: number
  /** Cost charged per pixel of radius change between neighbouring directions.
   * Higher keeps the outline smooth across a missing stretch of rim; too high
   * and it rounds off a real corner. */
  smoothness: number
  /** Largest radius change allowed between neighbouring directions. */
  maxStepPx: number
  /** Times to re-centre on the result and trace again. */
  refinements: number
  /** How far beyond the outline to start looking for a competing ridge, in
   * raster pixels. Wide enough to clear the outline's own band. */
  outerMarginPx: number
  /** Small reward per pixel of radius, which breaks ties in favour of the
   * outer ring when two concentric ridges are about equally bright. */
  outwardBias: number
}

function sampleBilinear(data: Float32Array, width: number, height: number, x: number, y: number): number {
  if (x < 0 || y < 0 || x >= width - 1 || y >= height - 1) return 0
  const x0 = Math.floor(x)
  const y0 = Math.floor(y)
  const fx = x - x0
  const fy = y - y0
  const i = y0 * width + x0
  const top = data[i] * (1 - fx) + data[i + 1] * fx
  const bottom = data[i + width] * (1 - fx) + data[i + width + 1] * fx
  return top * (1 - fy) + bottom * fy
}

/** One DP pass. `fixedStart` pins the first ray's radius, which is how the
 * second pass closes the curve properly instead of leaving a step at angle 0. */
function solve(
  cost: Float32Array,
  rayCount: number,
  radiusCount: number,
  smoothness: number,
  maxStep: number,
  fixedStart: number | null,
): Int32Array {
  const dp = new Float32Array(rayCount * radiusCount)
  const back = new Int32Array(rayCount * radiusCount)
  const INFINITY = 1e30

  for (let r = 0; r < radiusCount; r++) {
    dp[r] = fixedStart !== null && r !== fixedStart ? INFINITY : cost[r]
  }

  for (let k = 1; k < rayCount; k++) {
    const row = k * radiusCount
    const previous = row - radiusCount
    for (let r = 0; r < radiusCount; r++) {
      let best = INFINITY
      let bestIndex = r
      const from = Math.max(0, r - maxStep)
      const to = Math.min(radiusCount - 1, r + maxStep)
      for (let q = from; q <= to; q++) {
        const candidate = dp[previous + q] + smoothness * Math.abs(r - q)
        if (candidate < best) {
          best = candidate
          bestIndex = q
        }
      }
      dp[row + r] = best + cost[row + r]
      back[row + r] = bestIndex
    }
  }

  // Close the loop: the last ray must also be reachable from the first.
  let endBest = INFINITY
  let endIndex = 0
  const lastRow = (rayCount - 1) * radiusCount
  for (let r = 0; r < radiusCount; r++) {
    const closure =
      fixedStart === null ? 0 : smoothness * Math.min(Math.abs(r - fixedStart), maxStep + 1)
    const total = dp[lastRow + r] + closure
    if (total < endBest) {
      endBest = total
      endIndex = r
    }
  }

  const path = new Int32Array(rayCount)
  path[rayCount - 1] = endIndex
  for (let k = rayCount - 1; k > 0; k--) path[k - 1] = back[k * radiusCount + path[k]]
  return path
}

export interface RadialRidgeResult {
  outline: Point2D[]
  /** Enclosed area in raster pixels squared. */
  area: number
  /**
   * How much rim-like response is left *outside* this outline, relative to the
   * response along it. Near zero for a lens's true rim, since there is nothing
   * beyond it but bare sheet; near one when the trace has settled on a lens's
   * inner caustic and the real rim is still out there. It is the one symptom
   * that distinguishes a confident wrong answer from a right one, because both
   * follow a genuinely bright closed ridge.
   */
  outerEvidence: number
  /** Mean rim response along the outline. Used to choose between starting
   * points, and as an honest confidence: a trace over blank sheet scores near
   * zero, a trace along a real rim scores near one. */
  score: number
  centre: Point2D
}

function traceFrom(
  response: Float32Array,
  width: number,
  height: number,
  options: RadialRidgeOptions,
): RadialRidgeResult | null {
  const { rayCount, minRadiusPx, maxRadiusPx, smoothness, maxStepPx } = options
  const radiusCount = Math.floor(maxRadiusPx - minRadiusPx) + 1
  if (radiusCount < 4 || rayCount < 8) return null

  let centre = options.centre
  let polygon: Point2D[] | null = null
  let score = 0
  let radii: Int32Array | null = null

  for (let pass = 0; pass <= options.refinements; pass++) {
    const cost = new Float32Array(rayCount * radiusCount)
    for (let k = 0; k < rayCount; k++) {
      const angle = (2 * Math.PI * k) / rayCount
      const dx = Math.cos(angle)
      const dy = Math.sin(angle)
      for (let r = 0; r < radiusCount; r++) {
        const radius = minRadiusPx + r
        cost[k * radiusCount + r] =
          -sampleBilinear(response, width, height, centre.x + dx * radius, centre.y + dy * radius) -
          options.outwardBias * radius
      }
    }

    // Two passes: the first finds a good radius at angle zero, the second pins
    // it so the curve joins up without a seam.
    const open = solve(cost, rayCount, radiusCount, smoothness, maxStepPx, null)
    const closed = solve(cost, rayCount, radiusCount, smoothness, maxStepPx, open[0])
    radii = closed

    polygon = []
    let cx = 0
    let cy = 0
    score = 0
    for (let k = 0; k < rayCount; k++) {
      const angle = (2 * Math.PI * k) / rayCount
      const radius = minRadiusPx + closed[k]
      const point = { x: centre.x + Math.cos(angle) * radius, y: centre.y + Math.sin(angle) * radius }
      polygon.push(point)
      cx += point.x
      cy += point.y
      // Score the response alone, not the outward bias: the bias exists to
      // break ties, and letting it inflate the confidence would make a large
      // weak circle look more convincing than a small strong one.
      score += sampleBilinear(response, width, height, point.x, point.y)
    }
    score /= rayCount

    // Re-centre on the outline just found. A centre nearer the middle makes the
    // rays cross the rim closer to square, which is where a radial trace is
    // most precise, and it is what lets the first guess be rough.
    const next = { x: cx / rayCount, y: cy / rayCount }
    if (Math.hypot(next.x - centre.x, next.y - centre.y) < 0.5) break
    centre = next
  }

  if (!polygon || !radii) return null

  // Look for a competing ridge outside the one just traced.
  const outerRatios: number[] = []
  for (let k = 0; k < rayCount; k++) {
    const angle = (2 * Math.PI * k) / rayCount
    const dx = Math.cos(angle)
    const dy = Math.sin(angle)
    const onOutline = sampleBilinear(
      response, width, height,
      centre.x + dx * (minRadiusPx + radii[k]),
      centre.y + dy * (minRadiusPx + radii[k]),
    )
    let beyond = 0
    for (let r = radii[k] + options.outerMarginPx; r < radiusCount; r++) {
      const radius = minRadiusPx + r
      const value = sampleBilinear(response, width, height, centre.x + dx * radius, centre.y + dy * radius)
      if (value > beyond) beyond = value
    }
    if (onOutline > 1e-3) outerRatios.push(beyond / onOutline)
  }
  outerRatios.sort((a, b) => a - b)
  const outerEvidence = outerRatios.length === 0 ? 0 : outerRatios[outerRatios.length >> 1]

  let twiceArea = 0
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i]
    const b = polygon[(i + 1) % polygon.length]
    twiceArea += a.x * b.y - b.x * a.y
  }

  return { outline: polygon, area: Math.abs(twiceArea) / 2, score, outerEvidence, centre }
}

/**
 * Runs the trace from several starting points and keeps the strongest result.
 *
 * A radial trace needs its centre to be inside the lens, and the single best
 * guess available before the lens has been found is sometimes wrong — a bright
 * reflection off the sheet, or a highlight sitting near one edge of the glass,
 * pulls it out. When that happens the rays cross the rim twice on one side and
 * never on the other, and the trace collapses onto the reflection instead,
 * which is the one failure of this method that produces a confident wrong
 * answer rather than an obvious one.
 *
 * Trying a handful of starting points removes that failure for a few
 * milliseconds: each trace is a dynamic program over a few hundred thousand
 * states. Results come back in the order the starting points were given, and
 * the caller takes the first that passes its own plausibility checks, rather
 * than this function ranking them.
 *
 * Both obvious rankings are actively wrong, which is why neither is used. Keep
 * the brightest and a specular highlight wins, because a reflection on a lens
 * is brighter than the rim around it — a 10 mm glint beats the 50 mm lens
 * containing it. Keep the largest and a spurious wide ridge wins instead, on
 * exactly the captures where the rim is faint. Asking the caller "is this a
 * credible lens?" in the caller's own terms settles it, and the first start,
 * seeded from where the response is actually strong, is usually right anyway.
 */
export function traceRadialRidge(
  response: Float32Array,
  width: number,
  height: number,
  options: RadialRidgeOptions & { startCandidates?: Point2D[] },
): RadialRidgeResult[] {
  const candidates = options.startCandidates?.length ? options.startCandidates : [options.centre]

  const results: RadialRidgeResult[] = []
  for (const centre of candidates) {
    const result = traceFrom(response, width, height, { ...options, centre })
    if (!result) continue
    // Drop a result that merely repeats one already found from a nearby start.
    const duplicate = results.some(
      (other) =>
        Math.hypot(other.centre.x - result.centre.x, other.centre.y - result.centre.y) < 2 &&
        Math.abs(other.area - result.area) < 0.02 * other.area,
    )
    if (!duplicate) results.push(result)
  }
  return results
}

/**
 * A robust guess at a point inside the lens: the weighted median of where the
 * response is strong. A median rather than a centroid because a capture with
 * clutter in a corner of the zone would drag a centroid out of the lens
 * entirely, and every ray would then cross the rim twice.
 */
export function strongResponseCentre(
  response: Float32Array,
  width: number,
  height: number,
  level: number,
): Point2D | null {
  const xs: number[] = []
  const ys: number[] = []
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (response[y * width + x] >= level) {
        xs.push(x)
        ys.push(y)
      }
    }
  }
  if (xs.length < 50) return null
  xs.sort((a, b) => a - b)
  ys.sort((a, b) => a - b)
  return { x: xs[xs.length >> 1], y: ys[ys.length >> 1] }
}
