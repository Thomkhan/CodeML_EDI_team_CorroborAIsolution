/**
 * The whole measurement, in one place: photo in, millimetres out.
 *
 * Both the Web Worker (`src/workers/pipeline.worker.ts`) and the headless
 * evaluation harness (`scripts/headless/measure.ts`) call this and nothing
 * else, so the numbers quoted in the README are produced by the same code the
 * jury's phone runs. The previous version kept a parallel Python
 * reimplementation for debugging; keeping two copies of a measurement in step
 * is a losing game.
 */

import { CAPTURE_SHEET, CONTROL_LENGTH_MM } from '../captureSheet'
import { rectify, type Rectified } from '../geometry/warp'
import type { Point2D } from '../geometry/homography'
import {
  detectCaptureSheet,
  referenceFrameFromCorners,
  type ImageBuffer,
  type ReferenceFrame,
} from './redress'
import { maskFromModelOutput, segmentByRimRelief, type SegmentationMask } from './segment'
import { strongResponseCentre, traceRadialRidge } from '../geometry/radialRidge'
import {
  lastRejections,
  measureFromMask,
  measureFromOutline,
  type MeasureResult,
} from './measure'

/**
 * Resolution of the rectified millimetre raster. Six pixels per millimetre is
 * well past the point of diminishing returns: marching squares locates the
 * mask's iso-line to roughly a twentieth of a raster pixel, so about 0.01 mm,
 * two orders of magnitude inside the 1 mm target. Going finer only costs a
 * phone its memory.
 */
export const RASTER_PX_PER_MM = 6

/**
 * Radial correction applied to the finished outline, in millimetres.
 *
 * This is the pipeline's one tuned number, and it is deliberately the only one:
 * everything else in the chain is geometry with no free parameters. It is a
 * distance rather than a threshold so that whatever it is correcting stays
 * legible — "our outlines sit 0.2 mm proud of the glass" is a claim a reader
 * can check with a caliper, where "we threshold at 0.63" is not.
 *
 * Fitted by `scripts/eval_mm.py --sweep`, and it comes out at zero: over 40
 * held-out captures the sweep bottoms out at no correction at all, with a
 * residual bias of 0.04 mm. That is the outcome worth having — it says the
 * outline lands where the glass is because the geometry is right, not because
 * a constant was tuned until it did. The knob stays, because the number to
 * re-fit against real caliper measurements is this one.
 */
export const DEFAULT_OFFSET_MM = 0

/** Radii the radial trace searches, in millimetres from the lens's own centre.
 * A spectacle lens runs about 13 to 30 mm from centre to edge; the range is
 * wider so an off-centre first guess still brackets the rim all the way round. */
const MIN_RADIUS_MM = 4
const MAX_RADIUS_MM = 38

/** Cost charged per raster pixel of radius change between neighbouring rays.
 * This is what carries the outline straight through a stretch of rim that a
 * reflection has erased, and what keeps it from jumping to a brighter highlight
 * further in. Raise it and real corners round off. */
const RADIAL_SMOOTHNESS = 0.08

/** Reward per raster pixel of radius, breaking ties outwards. Sized so that
 * crossing the few millimetres between a lens's inner caustic and its true rim
 * is worth about a tenth of the response — enough to settle a tie, nowhere near
 * enough to walk off a real edge. */
const RADIAL_OUTWARD_BIAS = 0.0025

/**
 * Mean rim response an outline must average to be believed.
 *
 * A radial trace always returns *some* closed curve — that is precisely what
 * makes it robust — so something has to distinguish "followed the rim all the
 * way round" from "found nothing and drew a circle". This does, and it is
 * sharply predictive: over a validation set, every outline scoring above this
 * came within about two millimetres and most within one, while the only two
 * that were wrong by more than ten millimetres were also the only two scoring
 * below it. Set here so those two are refused rather than reported.
 *
 * Refusing is the right outcome. A measuring instrument that occasionally says
 * "retake this photo" is useful; one that occasionally reports 63 mm for a
 * 49 mm lens, with no outward sign, is not.
 */
const MIN_RIDGE_SCORE = 0.70

/**
 * How much competing rim response may sit outside an outline before it is
 * disbelieved.
 *
 * The score above says "this outline follows a bright closed ridge", which an
 * inner caustic satisfies just as well as a real rim — that is exactly why
 * those failures were confident. This asks the complementary question: is there
 * still something rim-like further out? Beyond a real rim there is only bare
 * sheet, so the ratio sits near a tenth; inside a lens whose true edge was
 * missed it approaches one.
 *
 * Having both means neither has to separate the two cases on its own, which
 * matters because on the captures measured so far the score alone separates
 * them by only a couple of hundredths.
 */
const MAX_OUTER_EVIDENCE = 0.55

/**
 * How far the rim outline may disagree with the trained model's own opinion of
 * where the lens is, as a fraction, before it is disbelieved.
 *
 * This is the third guard, and the only one that can catch the failures the
 * other two cannot. Both of those reason about the ridge itself — how bright it
 * is, whether something brighter lies outside it — so a trace that follows a
 * convincing ridge round the wrong thing satisfies both. The model has no stake
 * in any ridge: it was trained to say where lenses are, so when it and the
 * trace disagree by a quarter, one of them is wrong and neither number should
 * be reported.
 *
 * Inactive until a model is published in public/model/, which is why the
 * classical path still carries its own two guards.
 */
const MAX_MODEL_DISAGREEMENT = 0.25

/** Keep clear of the printed border of the texture zone. */
const ZONE_INSET_MM = 2

export type MaskPredictor = (rect: Rectified) => Promise<{
  probabilities: Float32Array
  width: number
  height: number
} | null>

export interface CaptureOptions {
  /**
   * Length the sheet's printed control segment actually measures, in
   * millimetres. It is nominally CONTROL_LENGTH_MM; a printer that scaled the
   * page makes it something else, and everything on the sheet is scaled by the
   * same factor. Measuring this one line with a caliper is what lets the sheet
   * be printed on any printer, at any scale, and still measure correctly.
   */
  controlLengthMm: number
  /** Radial correction applied to the outline, in millimetres. */
  offsetMm: number
  /** Iso-level for the trained model's probability map. Not used by the
   * classical path, which has no threshold. */
  maskLevel: number
  rotationDeg: number
  rasterPxPerMm: number
  /** Pre-computed reference frame, for the manual four-corner fallback. */
  frame: ReferenceFrame | null
  /** Trained segmentation model (Palier 2). Falls back to the classical path
   * when absent or when it returns nothing usable. */
  predictor: MaskPredictor | null
}

export interface CaptureOutcome {
  frame: ReferenceFrame | null
  rect: Rectified | null
  mask: SegmentationMask | null
  /** Mask before morphological closing. Only used by the debug dump. */
  maskRaw: SegmentationMask | null
  measure: MeasureResult | null
  /** Internal detail about a failure, for the evaluation harness only. */
  diagnostic?: string
  /** User-facing explanation when there is no measurement. The brief asks for
   * a clear message rather than a technical error, and every one of these is
   * something the person holding the phone can actually act on. */
  reason: string | null
}

const DEFAULTS: CaptureOptions = {
  controlLengthMm: CONTROL_LENGTH_MM,
  offsetMm: DEFAULT_OFFSET_MM,
  maskLevel: 0.5,
  rotationDeg: 0,
  rasterPxPerMm: RASTER_PX_PER_MM,
  frame: null,
  predictor: null,
}

export async function measureCapture(
  image: ImageBuffer,
  overrides: Partial<CaptureOptions> = {},
): Promise<CaptureOutcome> {
  const options = { ...DEFAULTS, ...overrides }

  const frame = options.frame ?? detectCaptureSheet(image)
  if (!frame) {
    return {
      frame: null,
      rect: null,
      mask: null,
      maskRaw: null,
      measure: null,
      reason:
        "Feuille de capture non reconnue. Vérifiez que les quatre marqueurs noirs des coins sont entièrement visibles et nets, sans reflet, puis reprenez la photo — ou calibrez à la main avec un objet de taille connue.",
    }
  }

  // How much smaller or larger the sheet came out of the printer. The markers'
  // published positions are in nominal millimetres, so the homography and the
  // zone below stay nominal; only the raster's reported scale is corrected.
  const printScale = options.controlLengthMm / CONTROL_LENGTH_MM

  const zone = CAPTURE_SHEET.textureRectMm
  const rect = rectify(
    image,
    frame.mmToPx,
    {
      x0: zone.x0 + ZONE_INSET_MM,
      y0: zone.y0 + ZONE_INSET_MM,
      x1: zone.x1 - ZONE_INSET_MM,
      y1: zone.y1 - ZONE_INSET_MM,
    },
    options.rasterPxPerMm,
    printScale,
  )

  // Two views of the same lens, with different jobs.
  //
  // The trained model answers "where is the lens", robustly, including on the
  // captures the rim response cannot handle — but it answers at 256 pixels
  // across a 156 mm zone, so each of its pixels is 0.6 mm and it can never be
  // the thing that measures. The rim response answers "exactly where is the
  // edge", to a fraction of a pixel, but only when there is an edge to see.
  //
  // So the model points and the rim measures. Where there is no model
  // published, the rim response finds its own starting point and nothing is
  // lost but robustness; where the rim has been washed out by a reflection, the
  // model's own outline is used instead and the app says so.
  let modelMask: SegmentationMask | null = null
  if (options.predictor) {
    const prediction = await options.predictor(rect)
    if (prediction) {
      modelMask = maskFromModelOutput(
        prediction.probabilities, prediction.width, prediction.height, rect,
      )
    }
  }

  const mask = segmentByRimRelief(rect)

  const measure =
    measureRimRidge(mask, rect, options, modelMask) ??
    // Last resort: measure the model's own region. Coarser — its pixels are
    // more than half a millimetre on the sheet — but a coarse measurement
    // beats none, and the UI reports which path produced the number.
    (modelMask
      ? measureFromMask(modelMask, rect, {
          maskLevel: options.maskLevel,
          rotationDeg: options.rotationDeg,
          offsetMm: options.offsetMm,
        })
      : null)

  if (!measure) {
    return {
      frame,
      rect,
      mask,
      maskRaw: mask,
      measure: null,
      reason:
        "Verre non détecté dans la zone grise. Posez-le bien à plat au milieu de la zone, évitez un reflet direct de la lampe, et ne laissez rien d'autre dedans.",
      diagnostic: lastRejections,
    }
  }

  return { frame, rect, mask, maskRaw: mask, measure, reason: null }
}

/**
 * Finds the lens outline on the classical path: a closed ridge trace around the
 * rim response, rather than a threshold and a fill.
 */
function measureRimRidge(
  mask: SegmentationMask,
  rect: Rectified,
  options: CaptureOptions,
  modelMask: SegmentationMask | null,
): MeasureResult | null {
  const pxPerMm = 1 / rect.mmPerPx
  const zoneCentre = { x: mask.width / 2, y: mask.height / 2 }
  const seed = strongResponseCentre(mask.data, mask.width, mask.height, 0.5) ?? zoneCentre

  // The model's answer first when there is one: it is far harder to mislead
  // than "wherever the response happens to be strong", which a bright scratch
  // on the sheet can capture.
  const modelCentre = modelMask ? maskCentroid(modelMask, 0.5) : null

  const spread = 9 * pxPerMm
  const startCandidates = [
    ...(modelCentre ? [modelCentre] : []),
    seed,
    zoneCentre,
    { x: seed.x + spread, y: seed.y },
    { x: seed.x - spread, y: seed.y },
    { x: seed.x, y: seed.y + spread },
    { x: seed.x, y: seed.y - spread },
  ]

  const candidates = traceRadialRidge(mask.data, mask.width, mask.height, {
    centre: seed,
    startCandidates,
    minRadiusPx: MIN_RADIUS_MM * pxPerMm,
    maxRadiusPx: MAX_RADIUS_MM * pxPerMm,
    rayCount: 360,
    smoothness: RADIAL_SMOOTHNESS,
    maxStepPx: 4,
    refinements: 3,
    outwardBias: RADIAL_OUTWARD_BIAS,
    outerMarginPx: Math.round(2 * pxPerMm),
  })
  // In order of preference. `measureFromOutline` applies the plausibility
  // checks, so the first candidate that is both a convincing ridge and a
  // credible lens wins; the later starting points exist only to rescue the
  // captures where the first one lands outside the glass.
  const modelExtent = modelMask ? maskExtent(modelMask, 0.5) : null

  for (const candidate of candidates) {
    if (candidate.score < MIN_RIDGE_SCORE) continue
    if (candidate.outerEvidence > MAX_OUTER_EVIDENCE) continue
    if (modelExtent && disagreesWith(candidate.outline, modelExtent) > MAX_MODEL_DISAGREEMENT) continue
    const measure = measureFromOutline(candidate.outline, rect, {
      method: mask.method,
      rotationDeg: options.rotationDeg,
      offsetMm: options.offsetMm,
      ridgeScore: candidate.score,
      outerEvidence: candidate.outerEvidence,
    })
    if (measure) return measure
  }
  return null
}

/** Bounding box of everything above `level` in a mask, in raster pixels. */
function maskExtent(mask: SegmentationMask, level: number) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (let y = 0; y < mask.height; y++) {
    for (let x = 0; x < mask.width; x++) {
      if (mask.data[y * mask.width + x] >= level) {
        if (x < minX) minX = x
        if (x > maxX) maxX = x
        if (y < minY) minY = y
        if (y > maxY) maxY = y
      }
    }
  }
  return maxX < minX ? null : { width: maxX - minX, height: maxY - minY }
}

/** Relative disagreement between an outline's size and the model's. */
function disagreesWith(outline: Point2D[], extent: { width: number; height: number }): number {
  const xs = outline.map((p) => p.x)
  const ys = outline.map((p) => p.y)
  const width = Math.max(...xs) - Math.min(...xs)
  const height = Math.max(...ys) - Math.min(...ys)
  return Math.max(
    Math.abs(width - extent.width) / Math.max(width, extent.width, 1),
    Math.abs(height - extent.height) / Math.max(height, extent.height, 1),
  )
}

/** Centroid of everything above `level` in a mask, or null if nothing is. */
function maskCentroid(mask: SegmentationMask, level: number) {
  let sx = 0
  let sy = 0
  let n = 0
  for (let y = 0; y < mask.height; y++) {
    for (let x = 0; x < mask.width; x++) {
      if (mask.data[y * mask.width + x] >= level) {
        sx += x
        sy += y
        n++
      }
    }
  }
  return n === 0 ? null : { x: sx / n, y: sy / n }
}

/** Builds a reference frame from four manually tapped corners. Exposed here so
 * the worker has a single import surface. */
export function manualFrame(
  corners: [Point2D, Point2D, Point2D, Point2D],
  widthMm: number,
  heightMm: number,
): ReferenceFrame | null {
  return referenceFrameFromCorners(corners, widthMm, heightMm)
}
