import { useCallback, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { CameraCapture } from '../components/CameraCapture'
import { REFERENCE_OBJECTS, useOptiFrame, type ReferenceObjectKey } from '../store/OptiFrameStore'
import { CONTROL_LENGTH_MM } from '../lib/captureSheet'
import { EYE_LABEL, type EyeSide } from '../store/types'

export function CapturePage() {
  const [activeEye, setActiveEye] = useState<EyeSide>('right')
  const { left, right, referenceObject, setReferenceObject, controlLengthMm, setControlLengthMm, updateEye, resetEye } =
    useOptiFrame()
  const navigate = useNavigate()
  const eyeData = activeEye === 'left' ? left : right

  const onCapture = useCallback(
    (photo: Blob) => {
      if (eyeData.photoUrl) URL.revokeObjectURL(eyeData.photoUrl)
      updateEye(activeEye, {
        photo,
        photoUrl: URL.createObjectURL(photo),
        measure: null,
        view: null,
        reference: null,
        error: null,
      })
    },
    [activeEye, eyeData.photoUrl, updateEye],
  )

  return (
    <div className="stack">
      <section className="card">
        <h1>1 · Capturer</h1>
        <p>
          Imprimez la <a href="capture-sheet.pdf" download>feuille de capture</a> sur du A4, posez-la
          bien à plat, puis mesurez son trait de contrôle au pied à coulisse et reportez la valeur
          ci-dessous. Posez ensuite le verre à plat dans la zone grise, le haut du verre vers le haut
          de la feuille, et photographiez d'au-dessus en cadrant les quatre marqueurs.
        </p>

        <label htmlFor="control-length">
          Longueur mesurée du trait de contrôle (mm)
        </label>
        <input
          id="control-length"
          type="number"
          min={40}
          max={160}
          step={0.1}
          value={controlLengthMm}
          onChange={(event) =>
            setControlLengthMm(Number(event.target.value) || CONTROL_LENGTH_MM)
          }
        />
        <p className="muted">
          Le trait fait {CONTROL_LENGTH_MM} mm si l'impression est à 100 %. Beaucoup d'imprimantes
          réduisent la page sans le dire : mesurez, saisissez la vraie valeur, et l'app corrige tout
          le reste. Retenu pour les prochaines fois.
          {Math.abs(controlLengthMm - CONTROL_LENGTH_MM) > 0.5 && (
            <>
              {' '}
              <strong>
                Impression à {((controlLengthMm / CONTROL_LENGTH_MM) * 100).toFixed(1)} % —
                correction appliquée.
              </strong>
            </>
          )}
        </p>

        <div className="callout">
          Éclairage diffus de préférence : un reflet direct de lampe sur le verre efface une partie
          de son bord, et c'est le bord qui est mesuré.
        </div>
      </section>

      <section className="card">
        <div className="eye-tabs">
          {(['right', 'left'] as EyeSide[]).map((side) => (
            <div
              key={side}
              className="eye-tab"
              data-active={activeEye === side}
              onClick={() => setActiveEye(side)}
            >
              {side === 'left' ? 'Œil gauche' : 'Œil droit'}{' '}
              {(side === 'left' ? left : right).photo ? '✓' : ''}
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        {eyeData.photoUrl ? (
          <div className="stack">
            <div className="media-frame">
              <img src={eyeData.photoUrl} alt={`Verre ${EYE_LABEL[activeEye]}`} />
            </div>
            <button className="btn" onClick={() => resetEye(activeEye)}>Reprendre la photo</button>
          </div>
        ) : (
          <CameraCapture onCapture={onCapture} />
        )}
      </section>

      <section className="card">
        <h3>Pas d'imprimante ?</h3>
        <p className="muted">
          L'app bascule alors sur une calibration manuelle à partir d'un objet de taille normalisée.
          C'est moins précis — il faut placer quatre coins à la main — mais cela fonctionne.
        </p>
        <label htmlFor="reference-object">Objet de référence de secours</label>
        <select
          id="reference-object"
          value={referenceObject}
          onChange={(event) => setReferenceObject(event.target.value as ReferenceObjectKey)}
        >
          {Object.entries(REFERENCE_OBJECTS).map(([key, object]) => (
            <option key={key} value={key}>
              {object.label} ({object.widthMm} × {object.heightMm} mm)
            </option>
          ))}
        </select>
      </section>

      {left.photo && right.photo && (
        <button className="btn btn-primary" onClick={() => navigate({ to: '/mesurer' })}>
          Continuer vers Mesurer →
        </button>
      )}
    </div>
  )
}
