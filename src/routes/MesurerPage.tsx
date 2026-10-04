import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { EyeMeasurement } from '../components/EyeMeasurement'
import { useOptiFrame } from '../store/OptiFrameStore'
import type { EyeSide } from '../store/types'

export function MesurerPage() {
  const [activeEye, setActiveEye] = useState<EyeSide>('right')
  const { left, right } = useOptiFrame()
  const navigate = useNavigate()

  const bothMeasured = Boolean(left.measure && right.measure)

  return (
    <div className="stack">
      <section className="card">
        <h1>2 · Mesurer</h1>
        <p>
          Redressement automatique du plan (marqueur ArUco), isolement du verre, puis mesure en
          millimètres. Si le marqueur n'est pas reconnu, calibrez manuellement en touchant ses 4
          coins.
        </p>
        <div className="eye-tabs">
          <div className="eye-tab" data-active={activeEye === 'left'} onClick={() => setActiveEye('left')}>
            Œil gauche {left.measure ? '✓' : ''}
          </div>
          <div className="eye-tab" data-active={activeEye === 'right'} onClick={() => setActiveEye('right')}>
            Œil droit {right.measure ? '✓' : ''}
          </div>
        </div>
      </section>

      <section className="card">
        <EyeMeasurement key={activeEye} eye={activeEye} />
      </section>

      {bothMeasured && (
        <button className="btn btn-primary" onClick={() => navigate({ to: '/monture' })}>
          Continuer vers Monture →
        </button>
      )}
    </div>
  )
}
