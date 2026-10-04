import { useEffect, useRef } from 'react'
import QRCode from 'qrcode'

export function QrCode({ value, size = 180 }: { value: string; size?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    QRCode.toCanvas(canvas, value, {
      width: size,
      margin: 1,
      color: { dark: '#0b0c10', light: '#ffffff' },
    }).catch(() => {
      /* value too long or empty — canvas just stays blank */
    })
  }, [value, size])

  return <canvas ref={canvasRef} width={size} height={size} style={{ borderRadius: 10 }} />
}
