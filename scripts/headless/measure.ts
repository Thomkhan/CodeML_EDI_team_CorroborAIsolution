/**
 * Headless front end to the exact pipeline the web app runs.
 *
 * It reads raw RGBA frames on stdin (described by a small JSON header) and
 * writes one JSON result per frame on stdout. `scripts/eval_mm.py` drives it.
 *
 * The point is that this shares every line of the measurement code with the
 * browser: tuning a threshold here and reading a different number in the app is
 * the failure mode that made the first version of OptiFrame impossible to
 * debug. There is no Python reimplementation to drift out of sync any more.
 *
 * Build and run:
 *     node scripts/headless/build.mjs
 *     python3 scripts/eval_mm.py ...
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { measureCapture, type CaptureOptions } from '../../src/lib/pipeline/runPipeline'

interface Job {
  rgbaPath: string
  width: number
  height: number
  options?: Partial<CaptureOptions>
  /** When set, writes the rectified raster and the mask next to this path, so
   * a failed measurement can be looked at instead of guessed at. */
  dumpPrefix?: string
}

const jobs: Job[] = JSON.parse(readFileSync(0, 'utf8'))
const results = []
for (const job of jobs) {
  try {
    const data = new Uint8ClampedArray(readFileSync(job.rgbaPath))
    const outcome = await measureCapture({ width: job.width, height: job.height, data }, job.options)
    results.push({
      ok: outcome.measure !== null,
      reason: outcome.reason,
      diagnostic: outcome.diagnostic ?? null,
      markerIds: outcome.frame?.markerIds ?? [],
      residualMm: outcome.frame?.residualMm ?? null,
      pxPerMm: outcome.frame?.pxPerMm ?? null,
      aMm: outcome.measure?.widthMm ?? null,
      bMm: outcome.measure?.heightMm ?? null,
      perimeterMm: outcome.measure?.perimeterMm ?? null,
      areaMm2: outcome.measure?.areaMm2 ?? null,
      method: outcome.measure?.method ?? null,
      ridgeScore: outcome.measure?.ridgeScore ?? null,
      outerEvidence: outcome.measure?.outerEvidence ?? null,
      contourMm: outcome.measure?.contourMm ?? null,
      ...dump(job, outcome),
    })
  } catch (error) {
    results.push({ ok: false, reason: error instanceof Error ? error.message : String(error) })
  }
}

function dump(job: Job, outcome: Awaited<ReturnType<typeof measureCapture>>) {
  if (!job.dumpPrefix || !outcome.rect || !outcome.mask) return {}
  writeFileSync(`${job.dumpPrefix}.rect.rgba`, Buffer.from(outcome.rect.rgba.buffer))
  writeFileSync(`${job.dumpPrefix}.mask.f32`, Buffer.from(outcome.mask.data.buffer))
  writeFileSync(`${job.dumpPrefix}.gray.f32`, Buffer.from(outcome.rect.gray.buffer))
  if (outcome.maskRaw) writeFileSync(`${job.dumpPrefix}.maskraw.f32`, Buffer.from(outcome.maskRaw.data.buffer))
  return {
    dump: {
      width: outcome.rect.width,
      height: outcome.rect.height,
      mmPerPx: outcome.rect.mmPerPx,
      originMm: outcome.rect.originMm,
    },
  }
}

process.stdout.write(JSON.stringify(results))
