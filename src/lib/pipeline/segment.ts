/**
 * Step 2 — "Segmenter": isolate the lens.
 *
 * A spectacle lens is the awkward case the brief warns about: transparent, low
 * contrast, with reflections that break its rim into disconnected arcs. The
 * first version of OptiFrame attacked that with Canny plus a convex hull, which
 * both misses a lens's genuinely non-convex lower edge and silently latches on
 * to desk clutter.
 *
 * Two answers live here, and the pipeline runs whichever is available:
 *
 *  - `segmentByRimRelief` (classical, no training). The lens's rim is the one
 *    thing about it that is genuinely loud in a photograph; this finds that
 *    band, closes it into a ring and fills it.
 *  - `maskFromModelOutput` (Palier 2). A small trained U-Net returns a
 *    probability map instead; everything downstream is identical.
 *
 * Both produce the same thing: a 0..1 mask on the rectified millimetre raster,
 * so the measurement, the SVG export and the frame builder never learn which
 * one ran.
 */

import { boxBlur, smoothBlur, type Rectified } from '../geometry/warp'

export interface SegmentationMask {
  /** 0..1, row-major, same raster as the `Rectified` it came from. */
  data: Float32Array
  width: number
  height: number
  /** Which path produced it. Surfaced in the UI and quoted in the README. */
  method: 'rim' | 'model'
}

/** Width of the band a lens edge paints, in millimetres: a wedge of glass a
 * few tenths of a millimetre across, plus the caustic just inside it. The
 * band-pass below is tuned to this, and keeping it tight matters — a wider
 * filter spreads the response outward and pushes every measurement up. */
const RIM_SCALE_MM = 0.9

/**
 * Hysteresis levels for binarising the rim band, as in Canny — but measured
 * from each capture rather than fixed.
 *
 * Hysteresis itself is needed because a rim is not uniformly bright: where the
 * lamp catches it, a stretch falls to a quarter of its usual strength. Keep
 * only what is strong and the ring opens, the fill leaks through the gap and
 * nothing is measured; keep everything faint and the sheet's own noise welds
 * into a mass that reaches the raster's edge, with the same result. Asking
 * instead for "faint, but joined to something strong" separates an arc of rim
 * from an equally faint speck on its own.
 *
 * The levels have to be derived per capture because the noise floor is not a
 * constant: across a set of otherwise similar renders it moves by a factor of
 * seven with exposure, sharpness and JPEG quality. Fixed levels then work on
 * half the captures and fail on the other half. These multiples are of the
 * median absolute deviation, so they say "well clear of this photograph's own
 * noise" rather than naming a brightness.
 */
const RIM_SIGMA_HIGH = 8
const RIM_SIGMA_LOW = 3

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v
}

/**
 * Classical segmentation: find the lens by its rim, then fill it in.
 *
 * A lens's rim is a steeply curved wedge of glass. It darkens the background
 * behind it and throws a bright caustic just inside, and that band is by a wide
 * margin the strongest thing about a transparent lens in a photograph — far
 * stronger than anything happening across its middle, where two air-glass
 * surfaces cost only about eight percent of contrast.
 *
 * Rather than threshold brightness (which a shadow across the sheet defeats) or
 * run Canny and hope the rim closes (it does not — a reflection opens
 * centimetre-long gaps), this measures each point's *relief*: how far the photo
 * departs there from its own local background, as a fraction of that
 * background. Expressing it as a fraction is what makes it survive a lamp on
 * one side of the table, and taking the background from a blur a few
 * millimetres wide is what makes a rim stand out from it while the sheet's own
 * shading does not.
 *
 * The band is then closed into a ring, the ring is filled, and the filled
 * region's boundary is the lens. Filling is what lets a rim that is genuinely
 * broken — under a specular highlight, say — still produce a complete outline.
 */
export function segmentByRimRelief(rect: Rectified): SegmentationMask {
  const { gray, width, height, mmPerPx } = rect
  const n = width * height
  const rimPx = Math.max(2, Math.round(RIM_SCALE_MM / mmPerPx))

  // Band-pass at the rim's own scale: a difference of two blurs, the fine one
  // just enough to kill sensor noise, the coarse one just wide enough that the
  // rim is not part of its own background. Using a much wider background (the
  // obvious first try) works, but spreads the response several millimetres
  // beyond the glass and inflates every measurement by about that much.
  const fine = smoothBlur(gray, width, height, Math.max(1, Math.round(rimPx / 3)))
  const coarse = smoothBlur(gray, width, height, rimPx)

  const relief = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    relief[i] = coarse[i] < 1e-3 ? 0 : Math.abs(fine[i] - coarse[i]) / coarse[i]
  }

  // Pool over about the rim's own width: the darkening and the caustic sit
  // next to each other, and what marks the edge is that *something* happens
  // across that width, not which way it goes.
  const pooled = smoothBlur(relief, width, height, Math.max(1, Math.round(rimPx / 2)))

  // Scale by what the strongest few percent of the raster is doing. The rim is
  // the strongest relief in any usable capture, so this fixes the mask's units
  // without a hard-coded contrast that would depend on the printer and the room.
  const scale = percentile(pooled, 0.995)
  const mask = new Float32Array(n)
  if (scale > 1e-6) for (let i = 0; i < n; i++) mask[i] = clamp01(pooled[i] / scale)

  return { data: mask, width, height, method: 'rim' }
}

function percentile(values: Float32Array, q: number): number {
  const stride = Math.max(1, Math.floor(values.length / 20000))
  const sample: number[] = []
  for (let i = 0; i < values.length; i += stride) sample.push(values[i])
  sample.sort((a, b) => a - b)
  return sample[Math.min(sample.length - 1, Math.floor(q * sample.length))]
}

/**
 * Closes the rim ring and turns it into a filled region whose boundary is the
 * ring's *centreline*.
 *
 * The centreline is the point. A lens's rim paints a band that straddles the
 * true edge, and that band's width is not a constant — it depends on how
 * steeply that particular lens is bevelled, which varies by a factor of three
 * between lenses. Take the band's outer edge and every measurement runs long by
 * its half-width; take the inner edge and every measurement runs short by the
 * same; and because the half-width varies, no single calibration can fix
 * either. The middle of the band does not care how wide the band is.
 *
 * Getting there needs one flood fill. Everything the raster's border can reach
 * through the gaps is outside; what it cannot reach is the ring plus the lens's
 * interior. Scoring the interior 1, the band 0.5 and the outside 0 and then
 * tracing at 0.5 lands exactly on the middle of the band.
 *
 * The flood also makes a broken ring usable: the closing bridges gaps up to
 * twice `bridgePx`, and a lens whose rim vanishes under a reflection still
 * encloses an interior.
 */
export function closeAndFill(mask: SegmentationMask, bridgePx: number, rampPx: number): SegmentationMask {
  const { width, height } = mask
  const n = width * height

  // Noise floor of this particular capture. Median and MAD rather than mean and
  // standard deviation, because the rim and any specular highlight are exactly
  // the kind of large outliers that would drag a mean-based estimate up and
  // hide the thing being looked for.
  const centre = percentile(mask.data, 0.5)
  const deviations = new Float32Array(n)
  for (let i = 0; i < n; i++) deviations[i] = Math.abs(mask.data[i] - centre)
  const spread = Math.max(percentile(deviations, 0.5), 1e-4)
  const highLevel = Math.min(0.85, centre + RIM_SIGMA_HIGH * spread)
  const lowLevel = Math.min(highLevel * 0.9, centre + RIM_SIGMA_LOW * spread)

  // Hysteresis: seed on the strong rim, then grow through anything that is at
  // least plausibly rim and touches it.
  const ring = new Uint8Array(n)
  const queue: number[] = []
  for (let i = 0; i < n; i++) {
    if (mask.data[i] >= highLevel) {
      ring[i] = 1
      queue.push(i)
    }
  }
  while (queue.length > 0) {
    const i = queue.pop()!
    const x = i % width
    const y = (i - x) / width
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const nx = x + dx
        const ny = y + dy
        if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue
        const j = ny * width + nx
        if (ring[j] === 0 && mask.data[j] >= lowLevel) {
          ring[j] = 1
          queue.push(j)
        }
      }
    }
  }

  // Morphological closing: dilate then erode by the same radius, so a gap up to
  // twice the radius is bridged and the band comes back the width it started.
  const asFloat = new Float32Array(n)
  for (let i = 0; i < n; i++) asFloat[i] = ring[i]
  const dilated = boxBlur(asFloat, width, height, bridgePx)
  for (let i = 0; i < n; i++) dilated[i] = dilated[i] > 0.02 ? 1 : 0
  const closedRing = boxBlur(dilated, width, height, bridgePx)
  for (let i = 0; i < n; i++) closedRing[i] = closedRing[i] > 0.98 ? 1 : 0

  // Flood the complement of the ring, from the border inwards.
  const outside = new Uint8Array(n)
  const stack: number[] = []
  const push = (i: number) => {
    if (!outside[i] && closedRing[i] === 0) {
      outside[i] = 1
      stack.push(i)
    }
  }
  for (let x = 0; x < width; x++) {
    push(x)
    push((height - 1) * width + x)
  }
  for (let y = 0; y < height; y++) {
    push(y * width)
    push(y * width + width - 1)
  }
  while (stack.length > 0) {
    const i = stack.pop()!
    const x = i % width
    const y = (i - x) / width
    if (x > 0) push(i - 1)
    if (x < width - 1) push(i + 1)
    if (y > 0) push(i - width)
    if (y < height - 1) push(i + width)
  }

  // Two regions: everything the flood could not reach (the band *and* the
  // interior), and the interior alone.
  const outerRegion = new Float32Array(n)
  const innerRegion = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    if (outside[i]) continue
    outerRegion[i] = 1
    if (!closedRing[i]) innerRegion[i] = 1
  }

  // Average the two *after* smoothing each, not before.
  //
  // Averaging the raw indicators gives a field that is flat 0.5 right across
  // the band, and an iso-line at 0.5 is then undefined there: marching squares
  // snaps to one edge of the band or the other depending on which side of 0.5
  // the level falls, which is the difference between measuring several
  // millimetres long and several short. Smoothing first, with a radius of about
  // the band's own half-width, makes the field climb steadily from 0 outside
  // the band to 1 inside it, crossing 0.5 exactly at the band's middle.
  const outerSmooth = smoothBlur(outerRegion, width, height, rampPx)
  const innerSmooth = smoothBlur(innerRegion, width, height, rampPx)
  const field = new Float32Array(n)
  for (let i = 0; i < n; i++) field[i] = 0.5 * (outerSmooth[i] + innerSmooth[i])

  return { ...mask, data: field }
}

/**
 * Wraps a trained model's raw output as a mask (Palier 2).
 *
 * `probabilities` is the model's single-channel output at `modelWidth` x
 * `modelHeight`; it is bilinearly resampled onto the rectified raster so the
 * rest of the pipeline cannot tell the two paths apart.
 */
export function maskFromModelOutput(
  probabilities: Float32Array,
  modelWidth: number,
  modelHeight: number,
  rect: Rectified,
): SegmentationMask {
  const { width, height } = rect
  const data = new Float32Array(width * height)
  const sx = modelWidth / width
  const sy = modelHeight / height

  for (let y = 0; y < height; y++) {
    const my = Math.min(modelHeight - 1.001, (y + 0.5) * sy - 0.5)
    const y0 = Math.max(0, Math.floor(my))
    const fy = my - y0
    const y1 = Math.min(modelHeight - 1, y0 + 1)
    for (let x = 0; x < width; x++) {
      const mx = Math.min(modelWidth - 1.001, (x + 0.5) * sx - 0.5)
      const x0 = Math.max(0, Math.floor(mx))
      const fx = mx - x0
      const x1 = Math.min(modelWidth - 1, x0 + 1)
      const top = probabilities[y0 * modelWidth + x0] * (1 - fx) + probabilities[y0 * modelWidth + x1] * fx
      const bottom = probabilities[y1 * modelWidth + x0] * (1 - fx) + probabilities[y1 * modelWidth + x1] * fx
      data[y * width + x] = top * (1 - fy) + bottom * fy
    }
  }

  return { data, width, height, method: 'model' }
}
