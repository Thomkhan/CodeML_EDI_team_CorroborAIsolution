#!/usr/bin/env python3
"""
Traces lens outlines on real photographs, to train the segmentation model.

Labelling is done on the **rectified** zone rather than on the raw photo, for
two reasons. It is the only space the model ever sees, so a mask drawn here
needs no transforming and cannot pick up an error on the way. And it is square
to the sheet, so the outline you trace is the outline in millimetres — what you
draw is what the model is scored against.

Controls:
    click        add a point on the lens outline (a dozen is plenty)
    u            undo the last point
    Enter        accept and save
    s            skip this photo
    r            start this photo over
    Esc          quit

Usage:
    node scripts/headless/build.mjs
    python3 scripts/label_lens.py --photos pictures --out data/real
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "headless" / "dist" / "measure.mjs"
MODEL_INPUT = 256
MAX_DISPLAY = 900


def rectify(photo: Path, tmp: Path) -> tuple[np.ndarray, dict] | None:
    image = np.array(Image.open(photo).convert("RGB"))
    rgba = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    raw = tmp / f"{photo.stem}.rgba"
    raw.write_bytes(np.ascontiguousarray(rgba).tobytes())

    job = [{
        "rgbaPath": str(raw),
        "width": image.shape[1],
        "height": image.shape[0],
        "dumpPrefix": str(tmp / photo.stem),
    }]
    proc = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps(job), capture_output=True, text=True, cwd=ROOT
    )
    if proc.returncode != 0:
        print(f"  harnais en échec : {proc.stderr.strip()[:200]}")
        return None

    result = json.loads(proc.stdout)[0]
    dump = result.get("dump")
    if not dump:
        print(f"  feuille non détectée ({result.get('reason', '')[:80]}…) — photo ignorée")
        return None

    data = np.frombuffer((tmp / f"{photo.stem}.rect.rgba").read_bytes(), np.uint8)
    return data.reshape(dump["height"], dump["width"], 4)[:, :, :3], dump


def trace_outline(rect: np.ndarray, title: str) -> np.ndarray | None:
    scale = min(1.0, MAX_DISPLAY / max(rect.shape[:2]))
    display = cv2.resize(rect, None, fx=scale, fy=scale) if scale < 1 else rect.copy()
    points: list[tuple[int, int]] = []

    def on_mouse(event, x, y, _flags, _data):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))

    window = f"OptiFrame — tracez le contour · {title}"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)

    while True:
        frame = display.copy()
        if len(points) > 1:
            cv2.polylines(frame, [np.array(points, np.int32)], len(points) > 2, (94, 230, 197), 2)
        for x, y in points:
            cv2.circle(frame, (x, y), 4, (94, 230, 197), -1)
        cv2.putText(
            frame, f"{len(points)} points — Entree: valider, u: annuler, r: recommencer, s: passer, Esc: quitter",
            (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA,
        )
        cv2.imshow(window, frame)

        key = cv2.waitKey(16) & 0xFF
        if key == ord("u") and points:
            points.pop()
        elif key == ord("r"):
            points.clear()
        elif key == ord("s"):
            cv2.destroyWindow(window)
            return None
        elif key == 27:
            cv2.destroyWindow(window)
            raise KeyboardInterrupt
        elif key in (13, 10) and len(points) >= 5:
            cv2.destroyWindow(window)
            mask = np.zeros(rect.shape[:2], np.uint8)
            cv2.fillPoly(mask, [np.round(np.array(points) / scale).astype(np.int32)], 255)
            return mask


def to_model_space(rect: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour, matching src/lib/model/segmenter.ts exactly."""
    gray = cv2.cvtColor(rect, cv2.COLOR_RGB2GRAY)
    ys = np.minimum(gray.shape[0] - 1, (np.arange(MODEL_INPUT) * gray.shape[0] / MODEL_INPUT).astype(int))
    xs = np.minimum(gray.shape[1] - 1, (np.arange(MODEL_INPUT) * gray.shape[1] / MODEL_INPUT).astype(int))
    return gray[np.ix_(ys, xs)], mask[np.ix_(ys, xs)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/real"))
    args = parser.parse_args()

    if not HARNESS.exists():
        sys.exit(f"Harnais absent : {HARNESS}\nLancez d'abord : node scripts/headless/build.mjs")

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    (args.out / "masks").mkdir(parents=True, exist_ok=True)

    photos = sorted(
        p for p in args.photos.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic"}
    )
    if not photos:
        sys.exit(f"Aucune photo dans {args.photos}")

    saved = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for index, photo in enumerate(photos, 1):
            if (args.out / "images" / f"{photo.stem}.png").exists():
                continue
            print(f"[{index}/{len(photos)}] {photo.name}")
            rectified = rectify(photo, tmp)
            if rectified is None:
                continue
            try:
                mask = trace_outline(rectified[0], photo.name)
            except KeyboardInterrupt:
                print("Interrompu.")
                break
            if mask is None:
                continue
            image_small, mask_small = to_model_space(rectified[0], mask)
            Image.fromarray(image_small).save(args.out / "images" / f"{photo.stem}.png")
            Image.fromarray(mask_small).save(args.out / "masks" / f"{photo.stem}.png")
            saved += 1

    print(f"{saved} masques enregistrés dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
