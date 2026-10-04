import { Suspense, lazy, useCallback, useMemo, useState } from 'react'
import type * as THREE from 'three'
import { loadManifold } from '../lib/manifold'
import { buildFrameMesh } from '../lib/pipeline/frame'
import { meshToBinaryStl, downloadArrayBuffer } from '../lib/stl'
import { normalisedContour } from '../lib/pipeline/measure'
import { DEFAULT_BRIDGE_WIDTH_MM, useOptiFrame } from '../store/OptiFrameStore'
import type { Mesh } from 'manifold-3d'

/** three.js is a third of the bundle and is only ever needed on this page,
 * once somebody has asked for a frame. Loading it lazily keeps the first paint
 * — and the measurement — off a 600 kB download. */
const ThreePreview = lazy(() =>
  import('../components/ThreePreview').then((m) => ({ default: m.ThreePreview })),
)

type Status = 'idle' | 'running' | 'done' | 'error'

export function MonturePage() {
  const { left, right, bridgeWidthMm, setBridgeWidthMm } = useOptiFrame()
  const [status, setStatus] = useState<Status>('idle')
  const [errorMessage, setErrorMessage] = useState('')
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null)
  const [mesh, setMesh] = useState<Mesh | null>(null)

  const ready = Boolean(left.measure && right.measure)

  const summary = useMemo(() => {
    if (!left.measure || !right.measure) return null
    return {
      deltaA: Math.abs(left.measure.widthMm - right.measure.widthMm),
      deltaB: Math.abs(left.measure.heightMm - right.measure.heightMm),
    }
  }, [left.measure, right.measure])

  const generate = useCallback(async () => {
    if (!left.measure || !right.measure) return
    setStatus('running')
    setErrorMessage('')
    try {
      const [wasm, { meshToThreeGeometry }] = await Promise.all([
        loadManifold(),
        import('../lib/meshToThree'),
      ])
      const built = buildFrameMesh(wasm, {
        leftContourMm: normalisedContour(left.measure),
        rightContourMm: normalisedContour(right.measure),
        bridgeWidthMm,
      })
      setMesh(built)
      setGeometry(meshToThreeGeometry(built))
      setStatus('done')
    } catch (error) {
      setStatus('error')
      setErrorMessage(error instanceof Error ? error.message : String(error))
    }
  }, [left.measure, right.measure, bridgeWidthMm])

  if (!ready) {
    return (
      <div className="card">
        <h1>3 · Monture</h1>
        <div className="callout">Mesurez d'abord les deux verres dans l'onglet Mesurer.</div>
      </div>
    )
  }

  return (
    <div className="stack">
      <section className="card">
        <h1>3 · Monture</h1>
        <p>
          La face de monture est construite autour des <strong>deux contours mesurés</strong>, chacun
          avec sa propre forme : rien n'est moyenné entre les deux yeux, ce qui est précisément ce
          qu'exige une paire recyclée.
        </p>
        {summary && (
          <div className="callout">
            Écart entre les deux verres : A {summary.deltaA.toFixed(1)} mm, B{' '}
            {summary.deltaB.toFixed(1)} mm.
            {summary.deltaA + summary.deltaB > 6 &&
              ' Les deux verres sont nettement différents — la monture le reflètera.'}
          </div>
        )}

        <label htmlFor="bridge-width">Largeur du pont (mm)</label>
        <input
          id="bridge-width"
          type="number"
          min={10}
          max={30}
          step={0.5}
          value={bridgeWidthMm}
          onChange={(event) => setBridgeWidthMm(Number(event.target.value) || DEFAULT_BRIDGE_WIDTH_MM)}
        />

        <div style={{ height: 12 }} />
        <button className="btn btn-primary" onClick={() => void generate()} disabled={status === 'running'}>
          {status === 'running' ? 'Génération…' : 'Générer la monture'}
        </button>
      </section>

      {status === 'error' && <div className="callout callout-danger">{errorMessage}</div>}

      {status === 'done' && geometry && (
        <section className="card">
          <div className="media-frame" style={{ aspectRatio: '1 / 1' }}>
            <Suspense fallback={<div className="callout">Chargement de l'aperçu 3D…</div>}>
              <ThreePreview geometry={geometry} />
            </Suspense>
          </div>
          <div style={{ height: 12 }} />
          <button
            className="btn btn-primary"
            onClick={() => mesh && downloadArrayBuffer(meshToBinaryStl(mesh), 'monture.stl')}
          >
            Télécharger monture.stl
          </button>
          <p className="muted">
            Maillage fermé garanti (manifold-3d) : il s'imprime sans réparation et sans supports.
            Jeu de clipsage laissé autour de chaque verre : 0,2 mm — un vrai verre est légèrement bombé.
          </p>
        </section>
      )}
    </div>
  )
}
