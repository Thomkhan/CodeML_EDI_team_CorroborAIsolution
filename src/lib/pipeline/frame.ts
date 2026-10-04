import type { ManifoldToplevel, Mesh, Vec2 } from 'manifold-3d'
import type { Point2D } from '../geometry/homography'

export interface FrameParams {
  leftContourMm: Point2D[]
  rightContourMm: Point2D[]
  bridgeWidthMm: number
  /** Width of the frame material around each lens. */
  ringWidthMm?: number
  /** Gap left between the lens and the inner ring edge — a real lens is
   * slightly bombé, see the brief's own tip (0.1-0.3mm). */
  clearanceMm?: number
  /** Extrusion depth (Z) of the frame front. */
  frameThicknessMm?: number
  tenonWidthMm?: number
  tenonHeightMm?: number
}

const DEFAULTS = {
  ringWidthMm: 3,
  clearanceMm: 0.2,
  frameThicknessMm: 2.2,
  tenonWidthMm: 5,
  tenonHeightMm: 6,
}

function toPolygon(points: Point2D[]): Vec2[] {
  return points.map((p): Vec2 => [p.x, p.y])
}

/**
 * Builds the frame front as a single manifold (watertight) solid and returns
 * its Mesh, ready for `meshToBinaryStl` or a three.js preview.
 *
 * Geometry: each measured lens outline is inflated outward by clearance plus
 * ring width, and the clearance-only offset is subtracted back out, giving a
 * ring that clips around that specific lens. The two rings are placed
 * `bridgeWidthMm` apart at their facing (nasal) edges, joined by a bridge, with
 * a tenon block on each temporal edge for the temple arms.
 *
 * Two conventions worth stating because they are what make a pair of
 * differently-shaped lenses fit:
 *
 *  - Each outline is used as measured. Nothing is averaged or symmetrised
 *    between the two eyes, which is the whole point of the challenge: a
 *    recycled pair can hold two lenses of different shapes.
 *  - Each outline arrives levelled and centred on its own boxing box (see
 *    `normalisedContour`), which is the datum ISO 8624 defines: the frame's
 *    horizontal runs through the centres of the two boxes. The two lenses were
 *    photographed separately and sat wherever they sat on the sheet, so their
 *    absolute positions carry no information — but their orientation does,
 *    because both were measured against the same printed horizontal.
 */
export function buildFrameMesh(wasm: ManifoldToplevel, params: FrameParams): Mesh {
  const { Manifold, CrossSection } = wasm
  const ringWidthMm = params.ringWidthMm ?? DEFAULTS.ringWidthMm
  const clearanceMm = params.clearanceMm ?? DEFAULTS.clearanceMm
  const thickness = params.frameThicknessMm ?? DEFAULTS.frameThicknessMm
  const tenonW = params.tenonWidthMm ?? DEFAULTS.tenonWidthMm
  const tenonH = params.tenonHeightMm ?? DEFAULTS.tenonHeightMm
  const bridgeWidthMm = params.bridgeWidthMm

  function buildRing(contourMm: Point2D[]) {
    const lens = new CrossSection([toPolygon(contourMm)])
    const outer = lens.offset(clearanceMm + ringWidthMm, 'Round')
    const inner = lens.offset(clearanceMm, 'Round')
    return outer.subtract(inner)
  }

  const rightRing = buildRing(params.rightContourMm)
  const leftRing = buildRing(params.leftContourMm)

  const rightBounds = rightRing.bounds()
  const leftBounds = leftRing.bounds()

  // Nasal edges face each other across the bridge. Only x moves: the vertical
  // datum is already set by each outline being centred on its own boxing box.
  const rightDx = bridgeWidthMm / 2 - rightBounds.min[0]
  const leftDx = -bridgeWidthMm / 2 - leftBounds.max[0]

  const rightPositioned = rightRing.translate(rightDx, 0)
  const leftPositioned = leftRing.translate(leftDx, 0)

  const bridgeHeightMm =
    Math.min(rightBounds.max[1] - rightBounds.min[1], leftBounds.max[1] - leftBounds.min[1]) * 0.22
  const bridge = CrossSection.square([bridgeWidthMm, Math.max(bridgeHeightMm, 3)], true)

  const frontFace = CrossSection.union([rightPositioned, leftPositioned, bridge])
  const solid = frontFace.extrude(thickness)

  const rightOuterX = rightBounds.max[0] + rightDx
  const leftOuterX = leftBounds.min[0] + leftDx
  const tenonRight = Manifold.cube([tenonW, tenonH, thickness]).translate(rightOuterX, -tenonH / 2, 0)
  const tenonLeft = Manifold.cube([tenonW, tenonH, thickness]).translate(leftOuterX - tenonW, -tenonH / 2, 0)

  const final = Manifold.union([solid, tenonRight, tenonLeft])
  return final.getMesh()
}
