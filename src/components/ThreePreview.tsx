import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/**
 * Interactive 3D preview of the generated frame front.
 *
 * The camera frames whatever it is given rather than sitting at a fixed
 * distance. Frames vary from about 110 to 150 mm across depending on the
 * lenses and the bridge, and a fixed camera that happens to suit one of those
 * crops the rest — which on a phone looks like the generator produced a broken
 * mesh rather than like the view being too close.
 */
export function ThreePreview({ geometry }: { geometry: THREE.BufferGeometry | null }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const meshRef = useRef<THREE.Mesh | null>(null)
  const gridRef = useRef<THREE.GridHelper | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x15171e)
    sceneRef.current = scene

    // Z up, to match the millimetre frame the mesh is built in: the lens
    // outlines lie in XY and the front is extruded along Z.
    const camera = new THREE.PerspectiveCamera(45, 1, 0.5, 4000)
    camera.up.set(0, 0, 1)
    cameraRef.current = camera

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    container.appendChild(renderer.domElement)

    const resize = () => {
      const { clientWidth, clientHeight } = container
      if (clientWidth === 0 || clientHeight === 0) return
      renderer.setSize(clientWidth, clientHeight)
      camera.aspect = clientWidth / clientHeight
      camera.updateProjectionMatrix()
    }
    resize()
    window.addEventListener('resize', resize)
    // The card this sits in is laid out after the canvas mounts, so the first
    // measurement can be of a zero-height box.
    const observer = new ResizeObserver(resize)
    observer.observe(container)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controlsRef.current = controls

    scene.add(new THREE.HemisphereLight(0xffffff, 0x222233, 2.2))
    const key = new THREE.DirectionalLight(0xffffff, 1.2)
    key.position.set(40, -60, 80)
    scene.add(key)

    let raf = 0
    const animate = () => {
      controls.update()
      renderer.render(scene, camera)
      raf = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
      window.removeEventListener('resize', resize)
      controls.dispose()
      renderer.dispose()
      container.removeChild(renderer.domElement)
    }
  }, [])

  useEffect(() => {
    const scene = sceneRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!scene || !camera || !controls) return

    if (meshRef.current) {
      scene.remove(meshRef.current)
      ;(meshRef.current.material as THREE.Material).dispose()
      meshRef.current = null
    }
    if (gridRef.current) {
      scene.remove(gridRef.current)
      gridRef.current.dispose()
      gridRef.current = null
    }
    if (!geometry) return

    const material = new THREE.MeshStandardMaterial({ color: 0x5ee6c5, roughness: 0.4, metalness: 0.1 })
    const mesh = new THREE.Mesh(geometry, material)
    scene.add(mesh)
    meshRef.current = mesh

    geometry.computeBoundingSphere()
    const sphere = geometry.boundingSphere
    if (!sphere) return

    // Distance at which the bounding sphere fills the narrower of the two
    // fields of view, with a little room to spare.
    const verticalFov = (camera.fov * Math.PI) / 180
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * camera.aspect)
    const distance = (1.35 * sphere.radius) / Math.sin(Math.min(verticalFov, horizontalFov) / 2)

    controls.target.copy(sphere.center)
    // Slightly above and in front, so the extrusion depth reads as depth.
    camera.position.set(
      sphere.center.x,
      sphere.center.y - distance * 0.55,
      sphere.center.z + distance * 0.83,
    )
    camera.near = Math.max(0.5, distance * 0.01)
    camera.far = distance * 10
    camera.updateProjectionMatrix()
    controls.update()

    // A grid sized to the frame, lying in its own plane, as a sense of scale:
    // one square is 10 mm.
    const span = Math.ceil((sphere.radius * 2.4) / 10) * 10
    const grid = new THREE.GridHelper(span, span / 10, 0x2a2d38, 0x2a2d38)
    grid.rotation.x = Math.PI / 2
    grid.position.set(sphere.center.x, sphere.center.y, 0)
    scene.add(grid)
    gridRef.current = grid
  }, [geometry])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
