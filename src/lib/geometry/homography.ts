/**
 * Plane-to-plane homography, in plain TypeScript.
 *
 * This replaces `cv.getPerspectiveTransform` / `cv.perspectiveTransform`: the
 * only things OptiFrame ever needed OpenCV's geometry for. Dropping the 10 MB
 * opencv.js bundle is what keeps the app from freezing a mid-range phone (see
 * README "Pourquoi pas OpenCV.js").
 */

export interface Point2D {
  x: number
  y: number
}

/** Row-major 3x3 homography. h[8] is normalised to 1 by `solveHomography`. */
export type Homography = Float64Array

export interface Correspondence {
  /** Source point, e.g. a marker corner in photo pixels. */
  src: Point2D
  /** Destination point, e.g. that corner's known position on the sheet, in mm. */
  dst: Point2D
}

/** Projects a point through a homography. */
export function applyH(h: Homography, p: Point2D): Point2D {
  const w = h[6] * p.x + h[7] * p.y + h[8]
  return {
    x: (h[0] * p.x + h[1] * p.y + h[2]) / w,
    y: (h[3] * p.x + h[4] * p.y + h[5]) / w,
  }
}

export function applyHAll(h: Homography, points: readonly Point2D[]): Point2D[] {
  return points.map((p) => applyH(h, p))
}

/** Solves `A x = b` in place by Gaussian elimination with partial pivoting. */
function solveLinearSystem(a: number[][], b: number[]): number[] | null {
  const n = b.length

  for (let col = 0; col < n; col++) {
    let pivot = col
    for (let row = col + 1; row < n; row++) {
      if (Math.abs(a[row][col]) > Math.abs(a[pivot][col])) pivot = row
    }
    if (Math.abs(a[pivot][col]) < 1e-12) return null
    if (pivot !== col) {
      ;[a[pivot], a[col]] = [a[col], a[pivot]]
      ;[b[pivot], b[col]] = [b[col], b[pivot]]
    }

    for (let row = col + 1; row < n; row++) {
      const factor = a[row][col] / a[col][col]
      if (factor === 0) continue
      for (let k = col; k < n; k++) a[row][k] -= factor * a[col][k]
      b[row] -= factor * b[col]
    }
  }

  const x = new Array<number>(n).fill(0)
  for (let row = n - 1; row >= 0; row--) {
    let sum = b[row]
    for (let k = row + 1; k < n; k++) sum -= a[row][k] * x[k]
    x[row] = sum / a[row][row]
  }
  return x
}

interface Normalisation {
  /** Maps a raw point into the normalised frame. */
  forward: (p: Point2D) => Point2D
  /** The 3x3 similarity `forward` applies, row-major. */
  matrix: Float64Array
}

/**
 * Hartley normalisation: recentre on the centroid and scale so the mean
 * distance to the origin is sqrt(2). Without it the normal equations below are
 * formed from pixel coordinates in the thousands, whose fourth powers lose most
 * of a double's precision — the homography then drifts by a visible fraction of
 * a millimetre, which is the whole budget we have.
 */
function normalise(points: readonly Point2D[]): Normalisation {
  const n = points.length
  let cx = 0
  let cy = 0
  for (const p of points) {
    cx += p.x
    cy += p.y
  }
  cx /= n
  cy /= n

  let meanDist = 0
  for (const p of points) meanDist += Math.hypot(p.x - cx, p.y - cy)
  meanDist /= n

  const s = meanDist > 1e-9 ? Math.SQRT2 / meanDist : 1

  return {
    forward: (p) => ({ x: (p.x - cx) * s, y: (p.y - cy) * s }),
    matrix: new Float64Array([s, 0, -s * cx, 0, s, -s * cy, 0, 0, 1]),
  }
}

function multiply3x3(a: Float64Array, b: Float64Array): Float64Array {
  const out = new Float64Array(9)
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c]
    }
  }
  return out
}

export function invertH(h: Homography): Homography | null {
  const [a, b, c, d, e, f, g, i, j] = h
  const det = a * (e * j - f * i) - b * (d * j - f * g) + c * (d * i - e * g)
  if (Math.abs(det) < 1e-18) return null

  const inv = new Float64Array([
    e * j - f * i, c * i - b * j, b * f - c * e,
    f * g - d * j, a * j - c * g, c * d - a * f,
    d * i - e * g, b * g - a * i, a * e - b * d,
  ])
  for (let k = 0; k < 9; k++) inv[k] /= det
  // Keep the h22 == 1 convention the rest of the module relies on.
  if (Math.abs(inv[8]) > 1e-18) {
    const scale = 1 / inv[8]
    for (let k = 0; k < 9; k++) inv[k] *= scale
  }
  return inv
}

/**
 * Least-squares homography from 4 or more correspondences (DLT via the normal
 * equations, on Hartley-normalised coordinates).
 *
 * More than 4 points is the normal case here: the capture sheet carries four
 * ArUco markers, so a clean capture yields 16 correspondences. That both
 * conditions the fit far better than a single marker and lets one marker be
 * occluded or missed without losing the measurement.
 */
export function solveHomography(correspondences: readonly Correspondence[]): Homography | null {
  if (correspondences.length < 4) return null

  const srcNorm = normalise(correspondences.map((c) => c.src))
  const dstNorm = normalise(correspondences.map((c) => c.dst))

  // Normal equations: 8x8 regardless of how many correspondences we feed in.
  const ata: number[][] = Array.from({ length: 8 }, () => new Array<number>(8).fill(0))
  const atb = new Array<number>(8).fill(0)

  for (const corr of correspondences) {
    const { x, y } = srcNorm.forward(corr.src)
    const { x: u, y: v } = dstNorm.forward(corr.dst)

    const rows: Array<[number[], number]> = [
      [[x, y, 1, 0, 0, 0, -x * u, -y * u], u],
      [[0, 0, 0, x, y, 1, -x * v, -y * v], v],
    ]

    for (const [row, rhs] of rows) {
      for (let i = 0; i < 8; i++) {
        for (let j = 0; j < 8; j++) ata[i][j] += row[i] * row[j]
        atb[i] += row[i] * rhs
      }
    }
  }

  const solution = solveLinearSystem(ata, atb)
  if (!solution || solution.some((v) => !Number.isFinite(v))) return null

  const hNorm = new Float64Array([...solution, 1])

  // Undo the normalisation: H = Tdst^-1 . Hnorm . Tsrc
  const dstInv = invertH(dstNorm.matrix as Homography)
  if (!dstInv) return null
  const h = multiply3x3(dstInv, multiply3x3(hNorm, srcNorm.matrix))

  if (Math.abs(h[8]) < 1e-18) return null
  const scale = 1 / h[8]
  for (let k = 0; k < 9; k++) h[k] *= scale
  return h
}

/**
 * Mean reprojection error, in destination units (mm for us). Reported in the
 * app so a bad capture is visible as a number rather than as a silently wrong
 * measurement — a sheet photographed through a crease, or a marker corner
 * mis-detected, shows up here before it shows up in A and B.
 */
export function reprojectionErrorMm(h: Homography, correspondences: readonly Correspondence[]): number {
  if (correspondences.length === 0) return Number.POSITIVE_INFINITY
  let total = 0
  for (const corr of correspondences) {
    const projected = applyH(h, corr.src)
    total += Math.hypot(projected.x - corr.dst.x, projected.y - corr.dst.y)
  }
  return total / correspondences.length
}
