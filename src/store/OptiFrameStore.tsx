import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { EyeData, EyeSide } from './types'
import { emptyEyeData } from './types'
import { CONTROL_LENGTH_MM } from '../lib/captureSheet'

export const DEFAULT_BRIDGE_WIDTH_MM = 18

const CONTROL_LENGTH_KEY = 'optiframe.controlLengthMm'

/**
 * The measured control-segment length is a property of the printed sheet, not
 * of a session, so it is remembered. Retyping it for every lens would be the
 * sort of friction that ends with someone not typing it at all, and a silent
 * scale error is the one mistake this whole design exists to prevent.
 */
function readStoredControlLength(): number {
  try {
    const stored = Number(localStorage.getItem(CONTROL_LENGTH_KEY))
    return Number.isFinite(stored) && stored > 10 ? stored : CONTROL_LENGTH_MM
  } catch {
    return CONTROL_LENGTH_MM
  }
}

/**
 * Reference objects for the manual fallback, used when no capture sheet has
 * been printed. Each is a rectangle whose real size is standardised, so the one
 * thing that cannot be got wrong is its dimensions.
 */
export const REFERENCE_OBJECTS = {
  bankCard: { label: 'Carte bancaire (ISO/IEC 7810 ID-1)', widthMm: 85.6, heightMm: 53.98 },
  a4: { label: 'Feuille A4', widthMm: 210, heightMm: 297 },
} as const

export type ReferenceObjectKey = keyof typeof REFERENCE_OBJECTS

interface OptiFrameContextValue {
  left: EyeData
  right: EyeData
  bridgeWidthMm: number
  /** Length the sheet's printed 100 mm control segment actually measures. */
  controlLengthMm: number
  setControlLengthMm: (mm: number) => void
  referenceObject: ReferenceObjectKey
  updateEye: (eye: EyeSide, patch: Partial<EyeData> | ((previous: EyeData) => Partial<EyeData>)) => void
  resetEye: (eye: EyeSide) => void
  setBridgeWidthMm: (mm: number) => void
  setReferenceObject: (key: ReferenceObjectKey) => void
}

const OptiFrameContext = createContext<OptiFrameContextValue | null>(null)

export function OptiFrameProvider({ children }: { children: ReactNode }) {
  const [left, setLeft] = useState<EyeData>(emptyEyeData)
  const [right, setRight] = useState<EyeData>(emptyEyeData)
  const [bridgeWidthMm, setBridgeWidthMm] = useState(DEFAULT_BRIDGE_WIDTH_MM)
  const [controlLengthMm, setControlLengthMmState] = useState(readStoredControlLength)
  const [referenceObject, setReferenceObject] = useState<ReferenceObjectKey>('bankCard')

  const setControlLengthMm = useCallback((mm: number) => {
    setControlLengthMmState(mm)
    try {
      localStorage.setItem(CONTROL_LENGTH_KEY, String(mm))
    } catch {
      // Private browsing, or storage disabled. The value still applies to this
      // session; only remembering it is lost.
    }
  }, [])

  const updateEye = useCallback<OptiFrameContextValue['updateEye']>((eye, patch) => {
    const setter = eye === 'left' ? setLeft : setRight
    setter((previous) => ({
      ...previous,
      ...(typeof patch === 'function' ? patch(previous) : patch),
    }))
  }, [])

  const resetEye = useCallback((eye: EyeSide) => {
    const setter = eye === 'left' ? setLeft : setRight
    setter((previous) => {
      if (previous.photoUrl) URL.revokeObjectURL(previous.photoUrl)
      return emptyEyeData()
    })
  }, [])

  const value = useMemo<OptiFrameContextValue>(
    () => ({
      left,
      right,
      bridgeWidthMm,
      controlLengthMm,
      setControlLengthMm,
      referenceObject,
      updateEye,
      resetEye,
      setBridgeWidthMm,
      setReferenceObject,
    }),
    [left, right, bridgeWidthMm, controlLengthMm, setControlLengthMm, referenceObject, updateEye, resetEye],
  )

  return <OptiFrameContext.Provider value={value}>{children}</OptiFrameContext.Provider>
}

export function useOptiFrame() {
  const context = useContext(OptiFrameContext)
  if (!context) throw new Error('useOptiFrame must be used within OptiFrameProvider')
  return context
}

export function useEyeData(eye: EyeSide): EyeData {
  const { left, right } = useOptiFrame()
  return eye === 'left' ? left : right
}
