import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Camera capture, with a file import always available beside it.
 *
 * The brief requires the import fallback, and it earns its place well beyond
 * compliance: `getUserMedia` needs HTTPS, needs a permission the person may
 * have denied once and forgotten, and is simply absent in a few in-app
 * browsers. A demo that cannot proceed because a webview will not open a camera
 * is a demo that has ended.
 */
export function CameraCapture({ onCapture }: { onCapture: (photo: Blob) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [active, setActive] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setActive(false)
  }, [])

  useEffect(() => stop, [stop])

  const start = useCallback(async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        // Ask for the rear camera and as much resolution as it will give: the
        // measurement's precision is bounded by pixels per millimetre on the
        // sheet, so this is not a cosmetic request.
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 3840 },
          height: { ideal: 2160 },
        },
        audio: false,
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      setActive(true)
    } catch {
      setError(
        "Caméra indisponible ou refusée. Utilisez « Importer une photo » — prenez-la avec l'appareil photo du téléphone, pas via une messagerie (les photos WhatsApp sont recompressées et inutilisables pour la mesure).",
      )
      setActive(false)
    }
  }, [])

  const capture = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')!.drawImage(video, 0, 0)
    canvas.toBlob((blob) => blob && onCapture(blob), 'image/jpeg', 0.95)
  }, [onCapture])

  const onFile = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      if (file) onCapture(file)
      event.target.value = ''
    },
    [onCapture],
  )

  return (
    <div className="stack">
      {!active && (
        <button className="btn btn-primary" onClick={() => void start()}>
          Ouvrir la caméra
        </button>
      )}

      {active && (
        <>
          <div className="media-frame">
            <video ref={videoRef} playsInline muted />
          </div>
          <div className="row">
            <button className="btn btn-primary" onClick={capture}>Capturer</button>
            <button className="btn btn-ghost" onClick={stop}>Arrêter</button>
          </div>
        </>
      )}

      <label className="btn btn-ghost" style={{ cursor: 'pointer' }}>
        Importer une photo
        <input type="file" accept="image/*" onChange={onFile} hidden />
      </label>

      {error && <div className="callout callout-warn">{error}</div>}
    </div>
  )
}
