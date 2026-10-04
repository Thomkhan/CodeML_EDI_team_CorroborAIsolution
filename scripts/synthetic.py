#!/usr/bin/env python3
"""
Renders synthetic OptiFrame captures: a spectacle lens lying on the printed
capture sheet, photographed by a phone.

This exists for two reasons at once.

1. Ground truth. A real lens has to be measured with a caliper, by hand, once
   per sample. A rendered one comes with its exact outline, so the whole
   geometric pipeline can be scored to a hundredth of a millimetre before
   anybody photographs anything — and any regression shows up immediately.
2. Training data. The brief rewards ingenuity in building the dataset, and a
   few dozen hand-labelled photographs is not enough to train a segmenter that
   survives a jury's lighting. Rendering thousands of lenses, over varied
   optics, lighting, viewpoints and noise, is what makes the real photographs
   go further.

What makes a lens visible here, in order of how much it actually helps:

* Its rim. The edge of a lens is a steeply curved wedge of glass; it darkens,
  and throws a bright caustic just inside itself. This is the strongest cue and
  it sits exactly on the boundary being measured.
* Fresnel loss. Two air-glass surfaces reflect about four percent each, so the
  printed pattern seen through the lens loses roughly eight percent of its
  contrast, and the room adds a veiling reflection on top. Against a *known*
  printed pattern that is a measurable area cue, which no edge detector gives.
* Specular highlights, and the soft shadow of a lens lying a hair off the paper.

Refraction is deliberately modelled as almost nothing, which is the honest
answer and not the intuitive one. A lens lying flat on the sheet sees its
background at essentially zero object distance, and prismatic deviation scales
with that distance: a 4-dioptre lens 2 mm above the paper displaces the pattern
by about twenty micrometres. Modelling a dramatic fish-eye bulge here would
train the segmenter on a cue that does not exist in the jury's photographs.

Usage:
    python3 scripts/synthetic.py --count 2000 --out data/synthetic
    python3 scripts/synthetic.py --count 1 --seed 7 --out /tmp/one --size 1400
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SHEET_PNG = ROOT / "public" / "capture-sheet.png"
LAYOUT_JSON = ROOT / "src" / "lib" / "captureSheet.json"


def _bilinear(image: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample at scattered coordinates. cv2.remap would do this, but it
    caps each map dimension at 32767 and a lens covers far more pixels than
    that."""
    h, w = image.shape[:2]
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    x0 = np.clip(x0, 0, w - 2)
    y0 = np.clip(y0, 0, h - 2)
    src = image.astype(np.float32)
    top = src[y0, x0] * (1 - fx) + src[y0, x0 + 1] * fx
    bottom = src[y0 + 1, x0] * (1 - fx) + src[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def load_sheet(px_per_mm: float) -> tuple[np.ndarray, dict]:
    """The printed sheet, resampled to a known millimetre grid."""
    layout = json.loads(LAYOUT_JSON.read_text(encoding="utf-8"))
    sheet = np.array(Image.open(SHEET_PNG).convert("RGB"))
    target = (
        int(round(layout["pageWidthMm"] * px_per_mm)),
        int(round(layout["pageHeightMm"] * px_per_mm)),
    )
    return cv2.resize(sheet, target, interpolation=cv2.INTER_AREA), layout


def lens_outline_mm(a_mm: float, b_mm: float, exponent: float, tilt_deg: float, n: int = 720) -> np.ndarray:
    """A plausible spectacle-lens outline whose bounding box is exactly a x b.

    A superellipse covers the range real lenses occupy: exponent 2 is a plain
    oval, 4 is the rounded rectangle most modern frames use. Because the
    parametrisation touches +/-a/2 and +/-b/2 exactly, A and B are known by
    construction rather than measured off the render.
    """
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ct, st = np.cos(t), np.sin(t)
    x = (a_mm / 2) * np.sign(ct) * np.abs(ct) ** (2.0 / exponent)
    y = (b_mm / 2) * np.sign(st) * np.abs(st) ** (2.0 / exponent)
    # A real lens is slightly deeper on one side; a touch of first harmonic
    # keeps the shapes from all being perfectly symmetric.
    y *= 1.0 + 0.05 * ct
    pts = np.stack([x, y], axis=1)

    r = np.deg2rad(tilt_deg)
    rot = np.array([[np.cos(r), -np.sin(r)], [np.sin(r), np.cos(r)]])
    return pts @ rot.T


def render_sheet_with_lens(
    rng: np.random.Generator,
    px_per_mm: float = 10.0,
    print_scale_range: tuple[float, float] = (1.0, 1.0),
):
    """Lays one lens on the sheet and returns the flat (not yet photographed)
    image, its exact outline in sheet millimetres, and the sample's metadata."""
    sheet, layout = load_sheet(px_per_mm)
    zone = layout["textureRectMm"]

    # Printers scale pages, usually without saying so. A sheet printed at 84 %
    # has a lens zone 26 mm narrower, so the same real lens covers a visibly
    # larger share of it — and the model, which works on the rectified zone,
    # sees a different picture than a 100 % print would give.
    #
    # Varying this is for *training*, which is why it defaults to off. Turning it
    # on for evaluation too would quietly change what is being measured: the
    # lens sizes stay realistic in real millimetres, but on the nominal sheet
    # they grow past anything a person owns, and the benchmark would then be
    # reporting on lenses that do not exist.
    print_scale = float(rng.uniform(*print_scale_range))
    a_mm = float(rng.uniform(38, 58)) / print_scale
    b_mm = float(rng.uniform(26, 44)) / print_scale
    exponent = float(rng.uniform(2.0, 4.2))
    # Imperfect placement against the sheet's printed horizontal. Small: the
    # arrow is there to be followed, and the app offers a rotation control for
    # what is left.
    tilt_deg = float(rng.uniform(-4, 4))
    outline_local = lens_outline_mm(a_mm, b_mm, exponent, tilt_deg)

    margin = 6.0
    cx = float(rng.uniform(zone["x0"] + a_mm / 2 + margin, zone["x1"] - a_mm / 2 - margin))
    cy = float(rng.uniform(zone["y0"] + b_mm / 2 + margin, zone["y1"] - b_mm / 2 - margin))
    outline_mm = outline_local + [cx, cy]

    # --- mask -----------------------------------------------------------------
    h, w = sheet.shape[:2]
    poly_px = np.round(outline_mm * px_per_mm).astype(np.int32)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [poly_px], 255)

    # --- refraction -----------------------------------------------------------
    # Tiny, for the reason set out in the module docstring: the air gap under a
    # lens resting on paper is a millimetre or two at most. The range covers a
    # strongly curved lens whose middle stands a little clear of the sheet.
    magnification = float(rng.uniform(0.985, 1.035))
    optical_cx = cx + float(rng.uniform(-3, 3))
    optical_cy = cy + float(rng.uniform(-3, 3))

    ys, xs = np.nonzero(mask)
    src_x = optical_cx * px_per_mm + (xs - optical_cx * px_per_mm) / magnification
    src_y = optical_cy * px_per_mm + (ys - optical_cy * px_per_mm) / magnification
    np.clip(src_x, 0, w - 1, out=src_x)
    np.clip(src_y, 0, h - 1, out=src_y)

    flat = sheet.astype(np.float32)
    flat[ys, xs] = _bilinear(sheet, src_x, src_y)

    # Fresnel: two air-glass interfaces reflect ~4 % each, so the pattern seen
    # through the lens keeps ~92 % of its contrast, and what is reflected comes
    # back as a veil of room light. Both are modelled as one contrast
    # compression about the local mean plus an additive veil, which is what a
    # camera records.
    local_mean = cv2.GaussianBlur(flat, (0, 0), 2.5 * px_per_mm)
    transmission = float(rng.uniform(0.88, 0.96))
    veiling = float(rng.uniform(0.01, 0.07))
    flat[ys, xs] = (
        local_mean[ys, xs] + (flat[ys, xs] - local_mean[ys, xs]) * transmission
    ) * (1 - veiling) + 255.0 * veiling

    # --- rim ------------------------------------------------------------------
    # The edge of a lens is a thick curved band of glass: it darkens, and throws
    # a bright caustic just inside. This is most of what makes a lens visible.
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    rim_mm = float(rng.uniform(0.35, 1.1))
    rim = np.clip(1.0 - distance / (rim_mm * px_per_mm), 0, 1) * (mask > 0)
    darken = float(rng.uniform(0.10, 0.35))
    flat *= (1.0 - darken * rim)[..., None]

    caustic_band = np.clip(1.0 - np.abs(distance - rim_mm * px_per_mm) / (0.45 * px_per_mm), 0, 1) * (mask > 0)
    flat += (255.0 * float(rng.uniform(0.05, 0.3)) * caustic_band)[..., None]

    # --- specular highlights --------------------------------------------------
    for _ in range(rng.integers(0, 4)):
        hx = float(rng.uniform(cx - a_mm / 3, cx + a_mm / 3)) * px_per_mm
        hy = float(rng.uniform(cy - b_mm / 3, cy + b_mm / 3)) * px_per_mm
        rx = float(rng.uniform(1.5, 6.0)) * px_per_mm
        ry = rx * float(rng.uniform(0.15, 0.6))
        blob = np.zeros((h, w), np.float32)
        cv2.ellipse(blob, (int(hx), int(hy)), (int(rx), int(ry)), float(rng.uniform(0, 180)), 0, 360, 1.0, -1)
        blob = cv2.GaussianBlur(blob, (0, 0), 0.6 * px_per_mm) * (mask > 0)
        flat += (255.0 * float(rng.uniform(0.25, 0.9)) * blob)[..., None]

    # A lens lifted a hair off the paper casts a soft shadow just outside itself.
    shadow = cv2.GaussianBlur((mask > 0).astype(np.float32), (0, 0), 0.9 * px_per_mm)
    shadow = np.clip(shadow - (mask > 0), 0, 1)
    shift = int(round(float(rng.uniform(0.2, 1.0)) * px_per_mm))
    shadow = np.roll(np.roll(shadow, shift, axis=0), shift, axis=1)
    flat *= (1.0 - float(rng.uniform(0.05, 0.25)) * shadow)[..., None]

    # Ground truth is the *rendered* outline's axis-aligned bounding box, which
    # is what a caliper on the sheet would read and what the pipeline reports.
    # Quoting the superellipse's own a and b instead silently ignores the tilt
    # applied afterwards, and at even a few degrees that is millimetres of
    # phantom error charged to the measurement.
    meta = {
        "printScale": print_scale,
        "transmission": transmission,
        "veiling": veiling,
        "rimMm": rim_mm,
        "aMm": float(outline_mm[:, 0].max() - outline_mm[:, 0].min()),
        "bMm": float(outline_mm[:, 1].max() - outline_mm[:, 1].min()),
        "nominalAMm": a_mm,
        "nominalBMm": b_mm,
        "exponent": exponent,
        "tiltDeg": tilt_deg,
        "centreMm": [cx, cy],
        "magnification": magnification,
        "pxPerMm": px_per_mm,
    }
    return np.clip(flat, 0, 255).astype(np.uint8), mask, outline_mm, meta, layout


def photograph(
    rng: np.random.Generator,
    flat: np.ndarray,
    mask: np.ndarray,
    layout: dict,
    px_per_mm: float,
    out_size: int,
    tilt: float = 1.0,
):
    """Turns the flat sheet into something a phone would have produced:
    perspective, uneven light, lens blur, sensor noise, JPEG."""
    h_mm, w_mm = layout["pageHeightMm"], layout["pageWidthMm"]
    out_h = out_size
    out_w = int(round(out_size * w_mm / h_mm))

    pad = 0.07
    jitter = 0.055 * tilt
    corners = np.array([
        [pad + rng.uniform(-jitter, jitter), pad + rng.uniform(-jitter, jitter)],
        [1 - pad + rng.uniform(-jitter, jitter), pad + rng.uniform(-jitter, jitter)],
        [1 - pad + rng.uniform(-jitter, jitter), 1 - pad + rng.uniform(-jitter, jitter)],
        [pad + rng.uniform(-jitter, jitter), 1 - pad + rng.uniform(-jitter, jitter)],
    ]) * np.array([out_w, out_h], dtype=np.float32)

    src = (np.array([[0, 0], [w_mm, 0], [w_mm, h_mm], [0, h_mm]], dtype=np.float32) * px_per_mm).astype(np.float32)
    transform = cv2.getPerspectiveTransform(src, corners.astype(np.float32))

    background = np.full((out_h, out_w, 3), rng.integers(30, 190), np.uint8)
    image = cv2.warpPerspective(flat, transform, (out_w, out_h), dst=background.copy(),
                                borderMode=cv2.BORDER_TRANSPARENT)
    warped_mask = cv2.warpPerspective(mask, transform, (out_w, out_h))

    img = image.astype(np.float32)

    # Uneven illumination: a broad gradient plus one soft hot spot.
    yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    gradient = 1.0 + rng.uniform(-0.3, 0.3) * (xx / out_w) + rng.uniform(-0.3, 0.3) * (yy / out_h)
    hot_x, hot_y = rng.uniform(0, out_w), rng.uniform(0, out_h)
    hot = np.exp(-(((xx - hot_x) ** 2 + (yy - hot_y) ** 2) / (2 * (0.45 * out_w) ** 2)))
    img *= (gradient * (1.0 + rng.uniform(0.0, 0.45) * hot))[..., None]

    # White balance drift.
    img *= rng.uniform(0.9, 1.1, size=3)[None, None, :]
    img *= rng.uniform(0.65, 1.15)

    blur_sigma = float(rng.uniform(0.3, 1.6))
    img = cv2.GaussianBlur(img, (0, 0), blur_sigma)
    img += rng.normal(0, rng.uniform(1.0, 6.0), img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)

    quality = int(rng.integers(55, 96))
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if ok:
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    return img, warped_mask, transform


def render_sample(
    seed: int,
    out_size: int = 1280,
    px_per_mm: float = 10.0,
    tilt: float = 1.0,
    print_scale_range: tuple[float, float] = (1.0, 1.0),
):
    """Renders one capture. The image comes back as **RGB**, not OpenCV's BGR:
    the sheet is loaded through PIL and stays in that order throughout. Callers
    writing it with cv2.imwrite must convert; callers feeding it to the pipeline
    (which expects RGBA) must not."""
    rng = np.random.default_rng(seed)
    flat, mask, outline_mm, meta, layout = render_sheet_with_lens(rng, px_per_mm, print_scale_range)
    image, warped_mask, transform = photograph(rng, flat, mask, layout, px_per_mm, out_size, tilt)
    meta["seed"] = seed
    meta["outlineMm"] = outline_mm.tolist()
    return image, warped_mask, meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--size", type=int, default=1280, help="Longest side of the rendered photo, px")
    parser.add_argument("--out", default="data/synthetic")
    parser.add_argument(
        "--print-scale-min", type=float, default=1.0,
        help="Simuler des impressions réduites (ex. 0.8) — pour l'entraînement, pas pour l'évaluation",
    )
    args = parser.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "masks").mkdir(parents=True, exist_ok=True)

    index = []
    for i in range(args.count):
        seed = args.seed + i
        image, mask, meta = render_sample(seed, args.size, print_scale_range=(args.print_scale_min, 1.0))
        name = f"synth_{seed:06d}"
        cv2.imwrite(str(out / "images" / f"{name}.jpg"), image[:, :, ::-1], [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        cv2.imwrite(str(out / "masks" / f"{name}.png"), mask)
        index.append({"name": name, **{k: v for k, v in meta.items() if k != "outlineMm"}})
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{args.count}")

    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"wrote {args.count} samples to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
