import { useCallback, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { Point2D } from '../lib/geometry/homography'

/**
 * Manual four-corner calibration, with a magnifier.
 *
 * This only runs when no capture sheet was found, but when it runs it carries
 * the whole measurement: every millimetre of A and B is scaled by where these
 * four points land. On a six-inch screen showing a 12 megapixel photo, one
 * fingertip covers something like forty source pixels, so tapping a corner
 * "accurately" is accurate to a couple of millimetres of real desk — which is
 * the entire error budget, spent before the lens has even been looked at.
 *
 * Dragging a handle with a magnifier showing what is under the finger turns
 * that into a sub-pixel placement, and is the difference between the fallback
 * being usable and being decorative.
 */
export function MagnifierPicker({
  imageUrl,
  naturalSize,
  points,
  onChange,
}: {
  imageUrl: string
  naturalSize: { width: number; height: number }
  points: Point2D[]
  onChange: (points: Point2D[]) => void
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState<number | null>(null)
  const [magnifier, setMagnifier] = useState<{ x: number; y: number } | null>(null)

  const toNatural = useCallback(
    (clientX: number, clientY: number): Point2D | null => {
      const host = hostRef.current
      if (!host) return null
      const box = host.getBoundingClientRect()
      return {
        x: Math.max(0, Math.min(naturalSize.width, ((clientX - box.left) / box.width) * naturalSize.width)),
        y: Math.max(0, Math.min(naturalSize.height, ((clientY - box.top) / box.height) * naturalSize.height)),
      }
    },
    [naturalSize],
  )

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const point = toNatural(event.clientX, event.clientY)
      if (!point) return
      event.currentTarget.setPointerCapture(event.pointerId)

      // Grab the nearest existing handle if the finger is near one, otherwise
      // place the next corner.
      let nearest = -1
      let nearestDistance = Number.POSITIVE_INFINITY
      points.forEach((p, i) => {
        const d = Math.hypot(p.x - point.x, p.y - point.y)
        if (d < nearestDistance) {
          nearestDistance = d
          nearest = i
        }
      })

      const grabRadius = naturalSize.width * 0.06
      if (nearest >= 0 && nearestDistance < grabRadius) {
        setDragging(nearest)
      } else if (points.length < 4) {
        onChange([...points, point])
        setDragging(points.length)
      } else {
        setDragging(nearest)
        const next = [...points]
        next[nearest] = point
        onChange(next)
      }
      setMagnifier({ x: event.clientX, y: event.clientY })
    },
    [points, onChange, toNatural, naturalSize.width],
  )

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (dragging === null) return
      const point = toNatural(event.clientX, event.clientY)
      if (!point) return
      const next = [...points]
      next[dragging] = point
      onChange(next)
      setMagnifier({ x: event.clientX, y: event.clientY })
    },
    [dragging, points, onChange, toNatural],
  )

  const endDrag = useCallback(() => {
    setDragging(null)
    setMagnifier(null)
  }, [])

  const active = dragging !== null ? points[dragging] : null

  return (
    <div className="picker">
      <div
        ref={hostRef}
        className="picker-surface"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <img src={imageUrl} alt="Photo à calibrer" draggable={false} />
        <svg viewBox={`0 0 ${naturalSize.width} ${naturalSize.height}`} preserveAspectRatio="none">
          {points.length === 4 && (
            <polygon
              points={points.map((p) => `${p.x},${p.y}`).join(' ')}
              fill="rgba(94, 230, 197, 0.12)"
              stroke="#5ee6c5"
              strokeWidth={naturalSize.width * 0.003}
            />
          )}
          {points.map((p, i) => (
            <g key={i}>
              <circle
                cx={p.x}
                cy={p.y}
                r={naturalSize.width * 0.012}
                fill={i === dragging ? '#ffbe50' : '#5ee6c5'}
                stroke="rgba(0,0,0,0.5)"
                strokeWidth={naturalSize.width * 0.002}
              />
            </g>
          ))}
        </svg>
      </div>

      {active && magnifier && (
        <div
          className="magnifier"
          style={{
            backgroundImage: `url(${imageUrl})`,
            backgroundSize: `${naturalSize.width * 3}px ${naturalSize.height * 3}px`,
            backgroundPosition: `${-active.x * 3 + 70}px ${-active.y * 3 + 70}px`,
          }}
          aria-hidden
        >
          <span className="magnifier-cross" />
        </div>
      )}
    </div>
  )
}
