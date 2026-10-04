import * as THREE from 'three'
import type { Mesh } from 'manifold-3d'

/** Converts a manifold-3d Mesh into a three.js BufferGeometry for preview. */
export function meshToThreeGeometry(mesh: Mesh): THREE.BufferGeometry {
  const { vertProperties, triVerts, numProp } = mesh
  const geometry = new THREE.BufferGeometry()

  let positions: Float32Array
  if (numProp === 3) {
    positions = vertProperties
  } else {
    const vertCount = vertProperties.length / numProp
    positions = new Float32Array(vertCount * 3)
    for (let v = 0; v < vertCount; v++) {
      positions[v * 3] = vertProperties[v * numProp]
      positions[v * 3 + 1] = vertProperties[v * numProp + 1]
      positions[v * 3 + 2] = vertProperties[v * numProp + 2]
    }
  }

  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setIndex(new THREE.BufferAttribute(triVerts, 1))
  geometry.computeVertexNormals()
  geometry.center()
  return geometry
}
