/**
 * The control image the brief asks for: the rectified lens zone with the
 * measured outline and the boxing rectangle drawn on it, so a person can see
 * at a glance whether the number is trustworthy.
 */

import type { Point2D } from './geometry/homography'
import type { MeasureResult } from './pipeline/measure'

export interface RectifiedView {
  width: number
  height: number
  pixels: ArrayBuffer
  mmPerPx: number
  originMm: Point2D
}

/** Millimetres on the sheet -> pixels of the rectified raster. */
function toRaster(view: RectifiedView, p: Point2D): Point2D {
  return { x: (p.x - view.originMm.x) / view.mmPerPx, y: (p.y - view.originMm.y) / view.mmPerPx }
}

/**
 * Draws the control image, cropped to the lens with a margin.
 *
 * Cropping matters on a phone: the full zone is 160 by 180 millimetres and the
 * lens is a tenth of that, so showing the whole thing makes the one part worth
 * checking too small to judge.
 */
export function drawControlImage(
  canvas: HTMLCanvasElement,
  view: RectifiedView,
  measure: MeasureResult | null,
  marginMm = 8,
): void {
  const source = new ImageData(
    new Uint8ClampedArray(view.pixels),
    view.width,
    view.height,
  )

  let cropX = 0
  let cropY = 0
  let cropW = view.width
  let cropH = view.height

  if (measure) {
    const points = measure.contourMm.map((p) => toRaster(view, p))
    const margin = marginMm / view.mmPerPx
    const xs = points.map((p) => p.x)
    const ys = points.map((p) => p.y)
    cropX = Math.max(0, Math.floor(Math.min(...xs) - margin))
    cropY = Math.max(0, Math.floor(Math.min(...ys) - margin))
    cropW = Math.min(view.width - cropX, Math.ceil(Math.max(...xs) - cropX + margin))
    cropH = Math.min(view.height - cropY, Math.ceil(Math.max(...ys) - cropY + margin))
  }

  canvas.width = cropW
  canvas.height = cropH
  const context = canvas.getContext('2d')!
  context.putImageData(source, -cropX, -cropY)

  if (!measure) return

  const points = measure.contourMm.map((p) => {
    const r = toRaster(view, p)
    return { x: r.x - cropX, y: r.y - cropY }
  })

  // Boxing rectangle, in the orientation A and B were taken in.
  const radians = (measure.rotationDeg * Math.PI) / 180
  const rotated = points.map((p) => ({
    x: p.x * Math.cos(radians) - p.y * Math.sin(radians),
    y: p.x * Math.sin(radians) + p.y * Math.cos(radians),
  }))
  const minX = Math.min(...rotated.map((p) => p.x))
  const maxX = Math.max(...rotated.map((p) => p.x))
  const minY = Math.min(...rotated.map((p) => p.y))
  const maxY = Math.max(...rotated.map((p) => p.y))

  context.save()
  context.rotate(-radians)
  context.strokeStyle = 'rgba(255, 190, 80, 0.95)'
  context.setLineDash([8, 6])
  context.lineWidth = 2
  context.strokeRect(minX, minY, maxX - minX, maxY - minY)
  context.restore()

  context.setLineDash([])
  context.lineWidth = 3
  context.strokeStyle = 'rgba(0, 0, 0, 0.45)'
  tracePath(context, points)
  context.stroke()
  context.lineWidth = 1.6
  context.strokeStyle = '#5ee6c5'
  tracePath(context, points)
  context.stroke()
}

function tracePath(context: CanvasRenderingContext2D, points: Point2D[]) {
  context.beginPath()
  points.forEach((p, i) => (i === 0 ? context.moveTo(p.x, p.y) : context.lineTo(p.x, p.y)))
  context.closePath()
}
