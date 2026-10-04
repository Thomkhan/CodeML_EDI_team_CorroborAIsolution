import { useEffect, useRef } from 'react'
import { drawControlImage, type RectifiedView } from '../lib/controlImage'
import type { MeasureResult } from '../lib/pipeline/measure'

/** The redressed lens zone with the measured outline drawn on it. */
export function ControlImage({
  view,
  measure,
}: {
  view: RectifiedView
  measure: MeasureResult | null
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (canvasRef.current) drawControlImage(canvasRef.current, view, measure)
  }, [view, measure])

  return <canvas ref={canvasRef} style={{ width: '100%', height: 'auto', display: 'block' }} />
}
