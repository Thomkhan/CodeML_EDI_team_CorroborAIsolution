import type { MeasureResult } from '../lib/pipeline/measure'
import type { RectifiedView } from '../lib/controlImage'

export type EyeSide = 'left' | 'right'

export interface ReferenceInfo {
  source: 'sheet' | 'manual'
  markerIds: number[]
  /** Mean reprojection residual of the reference fit, in millimetres. Shown in
   * the app because a bad capture shows up here before it shows up in A and B. */
  residualMm: number
  pxPerMm: number
}

export interface EyeData {
  /** The captured photo, kept so a measurement can be redone without
   * re-photographing — the pixel buffer itself is transferred to the worker and
   * is not reusable. */
  photo: Blob | null
  photoUrl: string | null
  measure: MeasureResult | null
  view: RectifiedView | null
  reference: ReferenceInfo | null
  elapsedMs: number | null
  error: string | null
  /** Earlier measurements of this same eye, for the shot-to-shot consistency
   * check. */
  history: MeasureResult[]
}

export function emptyEyeData(): EyeData {
  return {
    photo: null,
    photoUrl: null,
    measure: null,
    view: null,
    reference: null,
    elapsedMs: null,
    error: null,
    history: [],
  }
}

export const EYE_LABEL: Record<EyeSide, string> = {
  left: 'œil gauche',
  right: 'œil droit',
}
