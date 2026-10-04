/**
 * Main-thread side of the measurement worker: decode a photo, hand the pixels
 * over, get millimetres back.
 */

import type { MeasureRequest, MeasureResponse } from '../workers/pipeline.worker'
import type { Point2D } from './geometry/homography'

let worker: Worker | null = null
let nextId = 1
const pending = new Map<number, (response: MeasureResponse) => void>()

function getWorker(): Worker {
  if (!worker) {
    worker = new Worker(new URL('../workers/pipeline.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<MeasureResponse>) => {
      const resolve = pending.get(event.data.id)
      if (resolve) {
        pending.delete(event.data.id)
        resolve(event.data)
      }
    }
  }
  return worker
}

/**
 * Decodes a photo to raw RGBA on the main thread.
 *
 * `createImageBitmap` does the decode off-thread itself, and the only
 * main-thread work left is one draw into a canvas — milliseconds, even for a
 * 12 megapixel photo. The pixels are then transferred to the worker rather than
 * copied, so a 48 MB buffer changes owner without being duplicated.
 */
export async function decodePhoto(source: Blob | HTMLImageElement): Promise<{
  width: number
  height: number
  pixels: ArrayBuffer
}> {
  const bitmap = await createImageBitmap(source as ImageBitmapSource)
  const canvas = document.createElement('canvas')
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  const context = canvas.getContext('2d', { willReadFrequently: false })!
  context.drawImage(bitmap, 0, 0)
  bitmap.close()
  const imageData = context.getImageData(0, 0, canvas.width, canvas.height)
  return {
    width: imageData.width,
    height: imageData.height,
    pixels: imageData.data.buffer as ArrayBuffer,
  }
}

export function measureInWorker(
  photo: { width: number; height: number; pixels: ArrayBuffer },
  options: {
    rotationDeg?: number
    controlLengthMm?: number
    manual?: { corners: [Point2D, Point2D, Point2D, Point2D]; widthMm: number; heightMm: number }
  } = {},
): Promise<MeasureResponse> {
  const id = nextId++
  const request: MeasureRequest = { id, ...photo, ...options }
  return new Promise((resolve) => {
    pending.set(id, resolve)
    getWorker().postMessage(request, [request.pixels])
  })
}
