import { useCallback, useState } from 'react'
import { decodePhoto, measureInWorker } from '../lib/pipelineClient'
import { orderCorners } from '../lib/pipeline/redress'
import { reboxed, suggestRotationDeg } from '../lib/pipeline/measure'
import { contourToSvg, downloadText } from '../lib/exportSvg'
import { ControlImage } from './ControlImage'
import { MagnifierPicker } from './MagnifierPicker'
import { REFERENCE_OBJECTS, useOptiFrame } from '../store/OptiFrameStore'
import { EYE_LABEL, type EyeSide } from '../store/types'
import type { Point2D } from '../lib/geometry/homography'

type Status = 'idle' | 'running' | 'need-manual' | 'done' | 'error'

export function EyeMeasurement({ eye }: { eye: EyeSide }) {
  const { left, right, referenceObject, controlLengthMm, updateEye } = useOptiFrame()
  const eyeData = eye === 'left' ? left : right

  const [status, setStatus] = useState<Status>(() => (eyeData.measure ? 'done' : 'idle'))
  const [manualPoints, setManualPoints] = useState<Point2D[]>([])
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null)

  const run = useCallback(
    async (manual?: { corners: [Point2D, Point2D, Point2D, Point2D] }) => {
      if (!eyeData.photo) return
      setStatus('running')

      const photo = await decodePhoto(eyeData.photo)
      setNaturalSize({ width: photo.width, height: photo.height })

      const reference = REFERENCE_OBJECTS[referenceObject]
      const response = await measureInWorker(photo, {
        controlLengthMm,
        manual: manual
          ? { corners: manual.corners, widthMm: reference.widthMm, heightMm: reference.heightMm }
          : undefined,
      })

      if (!response.ok || !response.measure) {
        // No sheet found and no manual calibration yet: offer the fallback
        // rather than just reporting failure.
        if (!manual && !response.reference) {
          updateEye(eye, { error: response.reason, elapsedMs: response.elapsedMs })
          setStatus('need-manual')
          return
        }
        updateEye(eye, {
          error: response.reason,
          elapsedMs: response.elapsedMs,
          view: response.rect,
          measure: null,
        })
        setStatus('error')
        return
      }

      // Straighten to the lens's own long axis if it was laid down crooked.
      const suggestion = suggestRotationDeg(response.measure.contourMm)
      const measure =
        Math.abs(suggestion) > 0.4 && Math.abs(suggestion) < 25
          ? reboxed(response.measure, suggestion)
          : response.measure

      updateEye(eye, (previous) => ({
        measure,
        view: response.rect,
        reference: response.reference,
        elapsedMs: response.elapsedMs,
        error: null,
        history: previous.measure ? [...previous.history, previous.measure] : previous.history,
      }))
      setStatus('done')
    },
    [eyeData.photo, referenceObject, controlLengthMm, eye, updateEye],
  )

  const validateManual = useCallback(() => {
    if (manualPoints.length !== 4) return
    void run({ corners: orderCorners(manualPoints) })
  }, [manualPoints, run])

  const setRotation = useCallback(
    (degrees: number) => {
      if (!eyeData.measure) return
      updateEye(eye, { measure: reboxed(eyeData.measure, degrees) })
    },
    [eyeData.measure, eye, updateEye],
  )

  if (!eyeData.photo || !eyeData.photoUrl) {
    return <div className="callout">Photographiez d'abord ce verre dans l'onglet Capturer.</div>
  }

  const measure = eyeData.measure
  const consistency = shotToShot(eyeData.history, measure)

  return (
    <div className="stack">
      {status === 'idle' && (
        <button className="btn btn-primary" onClick={() => void run()}>
          Mesurer {EYE_LABEL[eye]}
        </button>
      )}

      {status === 'running' && (
        <div className="callout">
          Analyse en cours… <span className="muted">(l'interface reste utilisable : le calcul tourne dans un worker)</span>
        </div>
      )}

      {status === 'need-manual' && naturalSize && (
        <div className="stack">
          <div className="callout callout-warn">
            {eyeData.error} <br />
            <strong>Repli :</strong> placez les quatre coins de votre {REFERENCE_OBJECTS[referenceObject].label.toLowerCase()} ({manualPoints.length}/4).
            Touchez pour poser un point, puis faites-le glisser — la loupe montre ce qui est sous le doigt.
          </div>
          <MagnifierPicker
            imageUrl={eyeData.photoUrl}
            naturalSize={naturalSize}
            points={manualPoints}
            onChange={setManualPoints}
          />
          <div className="row">
            <button className="btn" onClick={() => setManualPoints([])} disabled={manualPoints.length === 0}>
              Effacer
            </button>
            <button className="btn btn-primary" onClick={validateManual} disabled={manualPoints.length !== 4}>
              Valider les 4 coins
            </button>
          </div>
        </div>
      )}

      {status === 'error' && (
        <div className="stack">
          <div className="callout callout-danger">{eyeData.error}</div>
          {eyeData.view && <div className="media-frame"><ControlImage view={eyeData.view} measure={null} /></div>}
          <button className="btn" onClick={() => void run()}>Réessayer</button>
        </div>
      )}

      {status === 'done' && measure && (
        <div className="stack">
          {eyeData.view && (
            <div className="media-frame">
              <ControlImage view={eyeData.view} measure={measure} />
            </div>
          )}

          <div className="metric-grid">
            <Metric value={measure.widthMm} label="A · largeur (mm)" />
            <Metric value={measure.heightMm} label="B · hauteur (mm)" />
            <Metric value={measure.perimeterMm} label="périmètre (mm)" />
          </div>

          <ConfidenceNote
            ridgeScore={measure.ridgeScore}
            residualMm={eyeData.reference?.residualMm ?? 0}
            markerCount={eyeData.reference?.markerIds.length ?? 0}
            elapsedMs={eyeData.elapsedMs}
            method={measure.method}
          />

          {consistency && (
            <div className="callout">
              Cohérence entre les prises : ΔA {consistency.deltaA.toFixed(2)} mm, ΔB{' '}
              {consistency.deltaB.toFixed(2)} mm sur {consistency.count} mesures.
            </div>
          )}

          <label htmlFor={`rotation-${eye}`}>
            Redressement du verre : {measure.rotationDeg.toFixed(1)}°
          </label>
          <input
            id={`rotation-${eye}`}
            type="range"
            min={-20}
            max={20}
            step={0.5}
            value={measure.rotationDeg}
            onChange={(event) => setRotation(Number(event.target.value))}
          />

          <div className="row">
            <button className="btn" onClick={() => void run()}>Remesurer</button>
            <button
              className="btn"
              onClick={() =>
                downloadText(
                  contourToSvg(measure, EYE_LABEL[eye]),
                  `contour-${eye === 'left' ? 'gauche' : 'droit'}.svg`,
                  'image/svg+xml',
                )
              }
            >
              Exporter le contour (SVG 1:1)
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Metric({ value, label }: { value: number; label: string }) {
  return (
    <div className="metric">
      <div className="metric-value">{value.toFixed(1)}</div>
      <div className="metric-label">{label}</div>
    </div>
  )
}

/**
 * What the app knows about how much to trust this number. Shown rather than
 * hidden: the brief asks for honesty about limits, and a measurement with no
 * stated confidence invites more faith than it has earned.
 */
function ConfidenceNote({
  ridgeScore,
  residualMm,
  markerCount,
  elapsedMs,
  method,
}: {
  ridgeScore: number
  residualMm: number
  markerCount: number
  elapsedMs: number | null
  method: string
}) {
  return (
    <div className="callout">
      <strong>Qualité de la mesure</strong>
      <ul className="tight">
        <li>
          Contour suivi à {(ridgeScore * 100).toFixed(0)} % le long du bord
          {method === 'model' ? ' (modèle entraîné)' : ' (détection du bord, sans modèle)'}
        </li>
        <li>
          Échelle : {markerCount > 0 ? `${markerCount} marqueurs` : 'calibration manuelle'}, résidu{' '}
          {residualMm.toFixed(2)} mm
        </li>
        {elapsedMs !== null && <li>Calcul : {(elapsedMs / 1000).toFixed(1)} s</li>}
      </ul>
    </div>
  )
}

function shotToShot(
  history: { widthMm: number; heightMm: number }[],
  current: { widthMm: number; heightMm: number } | null,
) {
  if (!current || history.length === 0) return null
  const all = [...history, current]
  const widths = all.map((m) => m.widthMm)
  const heights = all.map((m) => m.heightMm)
  return {
    deltaA: Math.max(...widths) - Math.min(...widths),
    deltaB: Math.max(...heights) - Math.min(...heights),
    count: all.length,
  }
}
