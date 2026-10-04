import { useState } from 'react'
import { ControlImage } from '../components/ControlImage'
import { useOptiFrame } from '../store/OptiFrameStore'
import type { EyeSide } from '../store/types'

/** The "pas à pas" deliverable: a photo shown with its intermediate images, so
 * the jury can see what the measurement is actually looking at. */
export function PasAPasPage() {
  const [activeEye, setActiveEye] = useState<EyeSide>('right')
  const { left, right } = useOptiFrame()
  const eyeData = activeEye === 'left' ? left : right

  return (
    <div className="stack">
      <section className="card">
        <h1>Pas à pas</h1>
        <p>
          Les étapes intermédiaires pour un verre : la photo brute, le plan redressé en millimètres
          réels, puis le contour mesuré.
        </p>
        <div className="eye-tabs">
          {(['right', 'left'] as EyeSide[]).map((side) => (
            <div
              key={side}
              className="eye-tab"
              data-active={activeEye === side}
              onClick={() => setActiveEye(side)}
            >
              {side === 'left' ? 'Œil gauche' : 'Œil droit'}
            </div>
          ))}
        </div>
      </section>

      {!eyeData.photoUrl && <div className="callout">Aucune photo pour cet œil pour le moment.</div>}

      {eyeData.photoUrl && (
        <section className="card">
          <h3>1 · Photo brute</h3>
          <p className="muted">
            Telle que prise. Les quatre marqueurs ArUco donnent l'échelle et la perspective.
          </p>
          <div className="media-frame">
            <img src={eyeData.photoUrl} alt="Photo brute" />
          </div>
        </section>
      )}

      {eyeData.view && (
        <section className="card">
          <h3>
            2 · Plan redressé{' '}
            {eyeData.reference && (
              <span className="muted">
                ({eyeData.reference.source === 'sheet'
                  ? `${eyeData.reference.markerIds.length} marqueurs`
                  : 'calibration manuelle'}
                , résidu {eyeData.reference.residualMm.toFixed(2)} mm)
              </span>
            )}
          </h3>
          <p className="muted">
            La zone de pose, vue de dessus, à l'échelle : un pixel vaut{' '}
            {eyeData.view.mmPerPx.toFixed(3)} mm.
          </p>
          <div className="media-frame">
            <ControlImage view={eyeData.view} measure={null} />
          </div>
        </section>
      )}

      {eyeData.view && eyeData.measure && (
        <section className="card">
          <h3>3 · Contour mesuré</h3>
          <p className="muted">
            Le contour suivi (vert) et la boîte du système « boxing » (orange) qui définit A et B.
          </p>
          <div className="media-frame">
            <ControlImage view={eyeData.view} measure={eyeData.measure} />
          </div>
        </section>
      )}
    </div>
  )
}
