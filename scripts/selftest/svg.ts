import { contourToSvg } from '../../src/lib/exportSvg'
import type { MeasureResult } from '../../src/lib/pipeline/measure'

// A 49.50 x 34.00 mm lens, centred on the sheet.
const contourMm = Array.from({ length: 240 }, (_, i) => {
  const t = (2 * Math.PI * i) / 240
  return { x: 100 + 24.75 * Math.cos(t), y: 150 + 17 * Math.sin(t) }
})
const measure: MeasureResult = {
  contourPx: [], contourMm,
  widthMm: 49.5, heightMm: 34, perimeterMm: 132.4, areaMm2: 1321,
  rotationDeg: 0, method: 'rim', ridgeScore: 0.9, offsetMm: 0,
}

const svg = contourToSvg(measure, 'œil droit')
const width = /width="([\d.]+)mm"/.exec(svg)?.[1]
const height = /height="([\d.]+)mm"/.exec(svg)?.[1]
const viewBox = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg)

console.log(`width=${width}mm height=${height}mm viewBox=${viewBox?.[1]} x ${viewBox?.[2]}`)
if (width !== viewBox?.[1] || height !== viewBox?.[2]) throw new Error('viewBox does not match physical size — the print would not be 1:1')
if (Number(width) !== 49.5 + 8) throw new Error(`expected lens width + 2*margin, got ${width}`)

// The traced path must span exactly the lens, inset by the margin.
const coords = [...svg.matchAll(/[ML]([\d.-]+),([\d.-]+)/g)].map((m) => [Number(m[1]), Number(m[2])])
const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1])
const spanX = Math.max(...xs) - Math.min(...xs)
const spanY = Math.max(...ys) - Math.min(...ys)
console.log(`path spans ${spanX.toFixed(3)} x ${spanY.toFixed(3)} mm (expected 49.500 x 34.000)`)
if (Math.abs(spanX - 49.5) > 0.01 || Math.abs(spanY - 34) > 0.01) throw new Error('path is not at 1:1 scale')
console.log('control square present:', svg.includes('width="10" height="10"'))
console.log('SVG export OK')
