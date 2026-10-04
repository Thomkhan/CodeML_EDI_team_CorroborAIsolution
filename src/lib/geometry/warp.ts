/**
 * Inverse-mapped resampling from photo pixels into the reference plane's own
 * millimetre grid. Replaces `cv.warpPerspective`.
 */

import { applyH, type Homography, type Point2D } from './homography'

export interface RectMm {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface Rectified {
  /** Grayscale, 0..1, row-major. */
  gray: Float32Array
  /** RGBA copy of the same raster, for the on-screen control image. */
  rgba: Uint8ClampedArray
  width: number
  height: number
  /** True millimetres per raster pixel — already corrected for how the sheet
   * was actually printed, so everything downstream works in real millimetres
   * without knowing that a correction happened. */
  mmPerPx: number
  /** True millimetre coordinate of raster pixel (0, 0). */
  originMm: Point2D
}

/** Raster pixel -> millimetres on the reference plane. */
export function rasterToMm(r: Rectified, p: Point2D): Point2D {
  return { x: r.originMm.x + p.x * r.mmPerPx, y: r.originMm.y + p.y * r.mmPerPx }
}

/**
 * Resamples `zoneMm` of the reference plane out of the photo, at `pxPerMm`
 * raster pixels per millimetre, with bilinear interpolation.
 *
 * Anything that falls outside the photo is left mid-grey and flagged by a zero
 * alpha in `rgba`, so a sheet shot half out of frame degrades visibly instead
 * of producing a confident contour over nothing.
 */
export function rectify(
  image: { width: number; height: number; data: Uint8ClampedArray },
  mmToPx: Homography,
  zoneMm: RectMm,
  pxPerMm: number,
  printScale = 1,
): Rectified {
  const mmPerPx = 1 / pxPerMm
  const width = Math.max(1, Math.round((zoneMm.x1 - zoneMm.x0) * pxPerMm))
  const height = Math.max(1, Math.round((zoneMm.y1 - zoneMm.y0) * pxPerMm))

  const gray = new Float32Array(width * height)
  const rgba = new Uint8ClampedArray(width * height * 4)
  const { width: sw, height: sh, data } = image

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const source = applyH(mmToPx, {
        x: zoneMm.x0 + (x + 0.5) * mmPerPx,
        y: zoneMm.y0 + (y + 0.5) * mmPerPx,
      })

      const out = (y * width + x) * 4
      if (source.x < 0 || source.y < 0 || source.x >= sw - 1 || source.y >= sh - 1) {
        gray[y * width + x] = 0.5
        rgba[out] = rgba[out + 1] = rgba[out + 2] = 128
        rgba[out + 3] = 0
        continue
      }

      const x0 = Math.floor(source.x)
      const y0 = Math.floor(source.y)
      const fx = source.x - x0
      const fy = source.y - y0
      const i00 = (y0 * sw + x0) * 4
      const i10 = i00 + 4
      const i01 = i00 + sw * 4
      const i11 = i01 + 4

      let r = 0
      let g = 0
      let b = 0
      for (const [index, weight] of [
        [i00, (1 - fx) * (1 - fy)],
        [i10, fx * (1 - fy)],
        [i01, (1 - fx) * fy],
        [i11, fx * fy],
      ] as const) {
        r += data[index] * weight
        g += data[index + 1] * weight
        b += data[index + 2] * weight
      }

      rgba[out] = r
      rgba[out + 1] = g
      rgba[out + 2] = b
      rgba[out + 3] = 255
      // Rec. 601 luma, the same weighting js-aruco2 uses, kept in 0..1.
      gray[y * width + x] = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    }
  }

  // Everything above worked in the sheet's *nominal* millimetres, because that
  // is the space the markers' published positions are in. Reporting the raster's
  // scale in true millimetres instead is the whole of the print-scale
  // correction: the pixels are untouched, and every conversion downstream —
  // the measurement, the radial search radii, the plausible-area checks — comes
  // out in real millimetres with no further knowledge of the correction.
  return {
    gray,
    rgba,
    width,
    height,
    mmPerPx: mmPerPx * printScale,
    originMm: { x: zoneMm.x0 * printScale, y: zoneMm.y0 * printScale },
  }
}

/** Separable moving-average blur. Used to build local statistics in O(n)
 * without integral images, which at these sizes would either cost tens of
 * megabytes in Float64 or lose the small differences we care about in Float32. */
export function boxBlur(src: Float32Array, width: number, height: number, radius: number): Float32Array {
  const horizontal = new Float32Array(width * height)
  const window = 2 * radius + 1

  for (let y = 0; y < height; y++) {
    const row = y * width
    let sum = 0
    for (let x = -radius; x <= radius; x++) sum += src[row + Math.min(width - 1, Math.max(0, x))]
    for (let x = 0; x < width; x++) {
      horizontal[row + x] = sum / window
      const leaving = Math.min(width - 1, Math.max(0, x - radius))
      const entering = Math.min(width - 1, Math.max(0, x + radius + 1))
      sum += src[row + entering] - src[row + leaving]
    }
  }

  const out = new Float32Array(width * height)
  for (let x = 0; x < width; x++) {
    let sum = 0
    for (let y = -radius; y <= radius; y++) sum += horizontal[Math.min(height - 1, Math.max(0, y)) * width + x]
    for (let y = 0; y < height; y++) {
      out[y * width + x] = sum / window
      const leaving = Math.min(height - 1, Math.max(0, y - radius))
      const entering = Math.min(height - 1, Math.max(0, y + radius + 1))
      sum += src[entering * width + x] - src[leaving * width + x]
    }
  }

  return out
}

/**
 * Three box passes, which is the standard cheap stand-in for a Gaussian.
 *
 * The extra passes matter here rather than being a nicety. The local statistics
 * below are gathered over a window that is not a whole number of checker
 * periods, so a plain box window makes every statistic ripple at the checker
 * frequency — and that ripple shows up as vertical and horizontal stripes right
 * across the mask, loud enough to drown the lens. A Gaussian-shaped window
 * suppresses it by orders of magnitude for three times the work.
 */
export function smoothBlur(src: Float32Array, width: number, height: number, radius: number): Float32Array {
  let out = src
  for (let pass = 0; pass < 3; pass++) out = boxBlur(out, width, height, radius)
  return out
}
