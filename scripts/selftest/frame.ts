import { writeFileSync } from 'node:fs'
import Module from 'manifold-3d'
import { buildFrameMesh } from '../../src/lib/pipeline/frame'
import { meshToBinaryStl } from '../../src/lib/stl'

// Two deliberately different lens shapes, which is the whole point of the brief.
function lens(a: number, b: number, exponent: number) {
  const points = []
  for (let i = 0; i < 180; i++) {
    const t = (2 * Math.PI * i) / 180
    const c = Math.cos(t)
    const s = Math.sin(t)
    points.push({
      x: (a / 2) * Math.sign(c) * Math.abs(c) ** (2 / exponent),
      y: (b / 2) * Math.sign(s) * Math.abs(s) ** (2 / exponent),
    })
  }
  return points
}

const wasm = await Module()
wasm.setup()

const mesh = buildFrameMesh(wasm, {
  leftContourMm: lens(50, 35, 2.6),
  rightContourMm: lens(46, 38, 3.4),
  bridgeWidthMm: 18,
})

const triangles = mesh.numTri ?? mesh.triVerts.length / 3
const vertices = mesh.numVert ?? mesh.vertProperties.length / 3
console.log(`mesh: ${vertices} vertices, ${triangles} triangles`)

// Euler characteristic for a closed orientable surface: V - E + F = 2 - 2g.
// Every triangle has 3 edges, every edge is shared by exactly 2 triangles in a
// watertight mesh, so E = 3F/2.
const edges = (triangles * 3) / 2
console.log(`V - E + F = ${vertices - edges + triangles} (closed surface, genus from this)`)

const stl = meshToBinaryStl(mesh)
console.log(`STL: ${(stl.byteLength / 1024).toFixed(1)} kB, header says ${new DataView(stl).getUint32(80, true)} triangles`)
if (new DataView(stl).getUint32(80, true) !== triangles) throw new Error('STL triangle count mismatch')
if (stl.byteLength !== 84 + triangles * 50) throw new Error('STL length mismatch')

// Bounding box: a frame front for these lenses should be ~120 mm wide.
const vp = mesh.vertProperties
let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity
for (let i = 0; i < vp.length; i += 3) {
  minX = Math.min(minX, vp[i]); maxX = Math.max(maxX, vp[i])
  minY = Math.min(minY, vp[i + 1]); maxY = Math.max(maxY, vp[i + 1])
  minZ = Math.min(minZ, vp[i + 2]); maxZ = Math.max(maxZ, vp[i + 2])
}
console.log(`bounds: ${(maxX - minX).toFixed(1)} x ${(maxY - minY).toFixed(1)} x ${(maxZ - minZ).toFixed(1)} mm`)
writeFileSync('scripts/selftest/monture.stl', Buffer.from(stl))
