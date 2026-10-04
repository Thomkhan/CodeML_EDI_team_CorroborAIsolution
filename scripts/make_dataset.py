#!/usr/bin/env python3
"""
Builds the training set for the lens segmentation model.

The model does not look at raw photographs. It looks at the *rectified* lens
zone — the sheet seen square-on, scaled to a known number of pixels per
millimetre — because that is what the app hands it at run time. Training on raw
photos instead would make the model spend its capacity learning perspective and
scale, which the homography already solves exactly, and would leave it worse at
the one thing only it can do.

So every sample here is produced by running the real pipeline (the same bundled
TypeScript the browser runs, via scripts/headless) up to the rectification step,
and pairing that raster with a mask in the same coordinates.

Two sources, and the mix is the point:

  --synthetic N  renders N captures with scripts/synthetic.py. Their outlines
                 are exact, so the masks are perfect and free, and the renderer
                 can vary lighting, bevel, reflections and viewpoint far more
                 widely than a day of photographing would.
  --photos DIR   real photographs, with masks drawn in scripts/label_lens.py.
                 Far fewer, and indispensable: they are the only thing that
                 tells the model what this printer, this paper and these lenses
                 really look like.

Usage:
    node scripts/headless/build.mjs
    python3 scripts/make_dataset.py --synthetic 3000 --out data/train
    python3 scripts/make_dataset.py --photos pictures --out data/real
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

# Must match INPUT_SIZE in src/lib/model/segmenter.ts.
MODEL_INPUT = 256


def rectify_batch(jobs: list[dict]) -> list[dict]:
    """Runs the real pipeline and returns each capture's rectified raster."""
    if not HARNESS.exists():
        sys.exit(f"Harnais absent : {HARNESS}\nLancez d'abord : node scripts/headless/build.mjs")
    proc = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps(jobs), capture_output=True, text=True, cwd=ROOT
    )
    if proc.returncode != 0:
        sys.exit(f"Harnais en échec :\n{proc.stderr}")
    return json.loads(proc.stdout)


def dump_rgba(image: np.ndarray, path: Path) -> tuple[int, int]:
    if image.shape[2] == 3:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    path.write_bytes(np.ascontiguousarray(image.astype(np.uint8)).tobytes())
    return image.shape[1], image.shape[0]


def load_rect(prefix: Path, dump: dict) -> np.ndarray:
    w, h = dump["width"], dump["height"]
    raw = np.frombuffer((prefix.with_suffix(".rect.rgba")).read_bytes(), np.uint8)
    return raw.reshape(h, w, 4)[:, :, :3]


def to_model_space(rect_rgb: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Grayscale and resize both to the model's square input.

    Nearest-neighbour for the image, to match exactly what segmenter.ts does at
    run time — a model trained on area-averaged downsampling and then fed
    nearest-neighbour samples sees a different image than it was taught on.
    """
    gray = cv2.cvtColor(rect_rgb, cv2.COLOR_RGB2GRAY)
    ys = np.minimum(gray.shape[0] - 1, (np.arange(MODEL_INPUT) * gray.shape[0] / MODEL_INPUT).astype(int))
    xs = np.minimum(gray.shape[1] - 1, (np.arange(MODEL_INPUT) * gray.shape[1] / MODEL_INPUT).astype(int))
    small = gray[np.ix_(ys, xs)]
    small_mask = mask[np.ix_(ys, xs)]
    return small, small_mask


def build_synthetic(count: int, seed0: int, size: int, out: Path) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import synthetic  # noqa: E402

    written = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        batch = 24
        for start in range(0, count, batch):
            jobs, metas = [], []
            for i in range(start, min(start + batch, count)):
                seed = seed0 + i
                # Printed sheets come out at all sorts of scales, which changes
                # how much of the rectified zone a lens covers. The model has to
                # cope with the whole range.
                image, _mask, meta = synthetic.render_sample(
                    seed, out_size=size, print_scale_range=(0.80, 1.0)
                )
                path = tmp / f"s{seed}.rgba"
                w, h = dump_rgba(image, path)
                jobs.append(
                    {"rgbaPath": str(path), "width": w, "height": h, "dumpPrefix": str(tmp / f"s{seed}")}
                )
                metas.append((seed, meta))

            for (seed, meta), result in zip(metas, rectify_batch(jobs)):
                dump = result.get("dump")
                if not dump:
                    continue  # the sheet was not found; nothing to learn from
                rect = load_rect(tmp / f"s{seed}", dump)
                origin, scale = dump["originMm"], 1.0 / dump["mmPerPx"]
                polygon = np.round(
                    (np.array(meta["outlineMm"]) - [origin["x"], origin["y"]]) * scale
                ).astype(np.int32)
                mask = np.zeros(rect.shape[:2], np.uint8)
                cv2.fillPoly(mask, [polygon], 255)

                image, label = to_model_space(rect, mask)
                Image.fromarray(image).save(out / "images" / f"synth_{seed:06d}.png")
                Image.fromarray(label).save(out / "masks" / f"synth_{seed:06d}.png")
                written += 1
            print(f"  {min(start + batch, count)}/{count}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--synthetic", type=int, metavar="N")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--size", type=int, default=2000, help="Côté long des photos synthétiques")
    parser.add_argument("--out", type=Path, default=Path("data/train"))
    args = parser.parse_args()

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    (args.out / "masks").mkdir(parents=True, exist_ok=True)

    if args.synthetic:
        written = build_synthetic(args.synthetic, args.seed, args.size, args.out)
        print(f"{written} paires écrites dans {args.out} (sur {args.synthetic} rendus)")
    else:
        parser.error("Précisez --synthetic N (les photos réelles passent par scripts/label_lens.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
