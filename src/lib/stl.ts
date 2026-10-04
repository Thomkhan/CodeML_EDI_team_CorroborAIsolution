import type { Mesh } from 'manifold-3d'

/**
 * Encodes a manifold-3d Mesh (flat vertProperties + triVerts, see
 * https://github.com/elalish/manifold) as a binary STL ArrayBuffer.
 * Per-triangle normals are computed from vertex positions (STL readers that
 * care recompute them anyway; writing zeros is also valid per spec).
 */
export function meshToBinaryStl(mesh: Mesh): ArrayBuffer {
  const { vertProperties, triVerts, numProp } = mesh
  const triCount = triVerts.length / 3

  const headerSize = 80
  const triangleSize = 50 // 12 floats (normal + 3 verts) + 2-byte attribute count
  const buffer = new ArrayBuffer(headerSize + 4 + triCount * triangleSize)
  const view = new DataView(buffer)

  const header = 'OptiFrame binary STL'
  for (let i = 0; i < header.length; i++) view.setUint8(i, header.charCodeAt(i))
  view.setUint32(headerSize, triCount, true)

  const vx = (v: number) => vertProperties[v * numProp]
  const vy = (v: number) => vertProperties[v * numProp + 1]
  const vz = (v: number) => vertProperties[v * numProp + 2]

  let offset = headerSize + 4
  for (let t = 0; t < triCount; t++) {
    const ia = triVerts[t * 3]
    const ib = triVerts[t * 3 + 1]
    const ic = triVerts[t * 3 + 2]

    const ax = vx(ia), ay = vy(ia), az = vz(ia)
    const bx = vx(ib), by = vy(ib), bz = vz(ib)
    const cx = vx(ic), cy = vy(ic), cz = vz(ic)

    const ux = bx - ax, uy = by - ay, uz = bz - az
    const wx = cx - ax, wy = cy - ay, wz = cz - az
    let nx = uy * wz - uz * wy
    let ny = uz * wx - ux * wz
    let nz = ux * wy - uy * wx
    const len = Math.hypot(nx, ny, nz) || 1
    nx /= len
    ny /= len
    nz /= len

    view.setFloat32(offset, nx, true); offset += 4
    view.setFloat32(offset, ny, true); offset += 4
    view.setFloat32(offset, nz, true); offset += 4
    view.setFloat32(offset, ax, true); offset += 4
    view.setFloat32(offset, ay, true); offset += 4
    view.setFloat32(offset, az, true); offset += 4
    view.setFloat32(offset, bx, true); offset += 4
    view.setFloat32(offset, by, true); offset += 4
    view.setFloat32(offset, bz, true); offset += 4
    view.setFloat32(offset, cx, true); offset += 4
    view.setFloat32(offset, cy, true); offset += 4
    view.setFloat32(offset, cz, true); offset += 4
    view.setUint16(offset, 0, true); offset += 2
  }

  return buffer
}

export function downloadArrayBuffer(data: ArrayBuffer, filename: string) {
  const blob = new Blob([data], { type: 'model/stl' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
