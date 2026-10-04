#!/usr/bin/env python3
"""
Generates the OptiFrame capture sheet: the whole "dispositif de capture", on one
sheet of A4 that anybody can reprint in under two minutes.

What is on it and why:

* Four ArUco markers (ARUCO_MIP_36h12) at known millimetre positions. Four
  markers rather than one give sixteen point correspondences for the
  homography, condition it far better across the whole sheet, and let one
  marker be smudged, occluded by a hand or missed by the detector without
  losing the measurement.
* A plain mid-grey central zone, and plain is the considered choice. The
  obvious idea is to print a fine pattern and look for the lens refracting it.
  The physics does not cooperate: a lens resting on paper sees its background at
  essentially zero object distance, and prismatic deviation scales with that
  distance, so a 4-dioptre lens two millimetres up displaces the print by about
  twenty micrometres. A printed texture therefore adds no usable signal and does
  add clutter competing with the one cue that is genuinely strong -- the lens's
  own rim, a steeply curved wedge of glass that darkens and throws a bright
  caustic. Mid grey is chosen so that both the darkening and the caustic have
  room to show; against white paper the caustic would clip away.
* A horizontal reference arrow. A and B in the boxing system (ISO 8624) are the
  width and height of the box around the lens *as worn*, so the horizontal has
  to be defined by something. Here it is the sheet's own X axis.
* A 100.0 mm control segment, to be checked with a caliper. If the printer
  scaled the page ("fit to page" is on by default almost everywhere) every
  measurement is wrong by that factor, silently. This makes it a five-second
  check instead of an invisible failure.

Outputs (into public/, so the app can serve the PDF to the jury):
    public/capture-sheet.pdf   - print at 100 %, no scaling
    public/capture-sheet.png   - preview, also used to render synthetic data
    src/lib/captureSheet.json  - the layout in mm, the single source of truth
                                 shared by the app and the Python tooling

Usage:
    python3 scripts/make_marker_sheet.py
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent

# --- Sheet layout, in millimetres --------------------------------------------
PAGE_W_MM, PAGE_H_MM = 210.0, 297.0
MARKER_MM = 28.0
MARGIN_MM = 12.0
DICTIONARY = "ARUCO_MIP_36h12"

# Marker id -> (x, y) of its top-left corner in sheet millimetres.
# ids run clockwise from the top-left corner of the page.
MARKER_ORIGINS_MM = {
    0: (MARGIN_MM, MARGIN_MM),
    1: (PAGE_W_MM - MARGIN_MM - MARKER_MM, MARGIN_MM),
    2: (PAGE_W_MM - MARGIN_MM - MARKER_MM, PAGE_H_MM - MARGIN_MM - MARKER_MM),
    3: (MARGIN_MM, PAGE_H_MM - MARGIN_MM - MARKER_MM),
}

# The zone the lens is laid in.
TEXTURE_RECT_MM = (25.0, 58.0, 185.0, 238.0)  # x0, y0, x1, y1

RENDER_DPI = 600
# Reflectance of the lens zone, 0-255. Mid grey leaves headroom both for the
# rim's darkening and for the caustic highlight just inside it.
ZONE_GREY = 150

CONTROL_RULER_MM = (55.0, 48.5, 155.0)  # x0, y, x1 -> exactly 100.0 mm
BASELINE_ARROW_MM = (45.0, 250.0, 165.0)  # x0, y, x1


def mm_to_px(mm: float) -> int:
    return int(round(mm / 25.4 * RENDER_DPI))


def marker_corners_mm(marker_id: int) -> list[list[float]]:
    """The marker's four corners in sheet mm, in the dictionary's own order:
    top-left, top-right, bottom-right, bottom-left of the marker as printed."""
    x, y = MARKER_ORIGINS_MM[marker_id]
    return [
        [x, y],
        [x + MARKER_MM, y],
        [x + MARKER_MM, y + MARKER_MM],
        [x, y + MARKER_MM],
    ]


# Any of these covers the accents and typographic dashes used below; PIL's
# built-in bitmap font does not, and silently draws tofu boxes instead.
FONT_CANDIDATES = (
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/gsfonts/NimbusSans-Regular.otf",
)


def _font(size_px: int):
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size_px)
    raise SystemExit(
        "Aucune police TrueType trouvée pour composer la feuille.\n"
        "Installez-en une (ex. ttf-dejavu / fonts-dejavu-core) ou ajoutez son\n"
        "chemin à FONT_CANDIDATES dans ce script."
    )


def render_sheet() -> Image.Image:
    page = Image.new("L", (mm_to_px(PAGE_W_MM), mm_to_px(PAGE_H_MM)), 255)
    draw = ImageDraw.Draw(page)

    # The lens zone: one flat tone, with a thin outline so a person can see
    # where to put the lens.
    x0, y0, x1, y1 = TEXTURE_RECT_MM
    px0, py0, px1, py1 = mm_to_px(x0), mm_to_px(y0), mm_to_px(x1), mm_to_px(y1)
    draw.rectangle([px0, py0, px1, py1], fill=ZONE_GREY, outline=90, width=mm_to_px(0.4))

    # ArUco markers.
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_ARUCO_MIP_36h12)
    side_px = mm_to_px(MARKER_MM)
    for marker_id, (mx, my) in MARKER_ORIGINS_MM.items():
        img = cv2.aruco.generateImageMarker(dictionary, marker_id, side_px)
        page.paste(Image.fromarray(img), (mm_to_px(mx), mm_to_px(my)))

    # 100 mm control segment, with end ticks to caliper against.
    rx0, ry, rx1 = CONTROL_RULER_MM
    line_w = mm_to_px(0.4)
    tick = mm_to_px(2.5)
    draw.line([mm_to_px(rx0), mm_to_px(ry), mm_to_px(rx1), mm_to_px(ry)], fill=0, width=line_w)
    for rx in (rx0, rx1):
        draw.line([mm_to_px(rx), mm_to_px(ry) - tick, mm_to_px(rx), mm_to_px(ry) + tick], fill=0, width=line_w)
    draw.text(
        (mm_to_px((rx0 + rx1) / 2), mm_to_px(ry) - mm_to_px(5)),
        "100,0 mm — vérifier au pied à coulisse (imprimer à 100 %, sans « ajuster à la page »)",
        fill=0, font=_font(mm_to_px(3.0)), anchor="mm",
    )

    # Horizontal reference: defines the boxing-system axes for A and B.
    ax0, ay, ax1 = BASELINE_ARROW_MM
    draw.line([mm_to_px(ax0), mm_to_px(ay), mm_to_px(ax1), mm_to_px(ay)], fill=0, width=line_w)
    head = mm_to_px(2.5)
    for ax, direction in ((ax0, 1), (ax1, -1)):
        draw.polygon(
            [
                (mm_to_px(ax), mm_to_px(ay)),
                (mm_to_px(ax) + direction * head * 2, mm_to_px(ay) - head),
                (mm_to_px(ax) + direction * head * 2, mm_to_px(ay) + head),
            ],
            fill=0,
        )
    draw.text(
        (mm_to_px((ax0 + ax1) / 2), mm_to_px(ay) + mm_to_px(5)),
        "HORIZONTAL — poser le verre dans la zone grise, bord supérieur vers le haut de la feuille",
        fill=0, font=_font(mm_to_px(3.2)), anchor="mm",
    )
    draw.text(
        (mm_to_px(PAGE_W_MM / 2), mm_to_px(26)),
        "OptiFrame — feuille de capture",
        fill=0, font=_font(mm_to_px(5.5)), anchor="mm",
    )
    draw.text(
        (mm_to_px(PAGE_W_MM / 2), mm_to_px(34)),
        "Poser le verre à plat dans la zone grise, photographier d'au-dessus depuis l'app.",
        fill=0, font=_font(mm_to_px(3.2)), anchor="mm",
    )

    return page


def main() -> int:
    page = render_sheet()

    public = ROOT / "public"
    public.mkdir(exist_ok=True)
    page.save(public / "capture-sheet.png", dpi=(RENDER_DPI, RENDER_DPI))
    page.convert("RGB").save(public / "capture-sheet.pdf", resolution=RENDER_DPI)

    layout = {
        "_comment": "Generated by scripts/make_marker_sheet.py — do not edit by hand.",
        "dictionary": DICTIONARY,
        "pageWidthMm": PAGE_W_MM,
        "pageHeightMm": PAGE_H_MM,
        "markerSizeMm": MARKER_MM,
        "zoneGrey": ZONE_GREY,
        "controlRulerMm": {"x0": CONTROL_RULER_MM[0], "y": CONTROL_RULER_MM[1], "x1": CONTROL_RULER_MM[2]},
        "textureRectMm": {
            "x0": TEXTURE_RECT_MM[0], "y0": TEXTURE_RECT_MM[1],
            "x1": TEXTURE_RECT_MM[2], "y1": TEXTURE_RECT_MM[3],
        },
        # Corner order matches the ArUco dictionary's own: the marker's
        # top-left, top-right, bottom-right, bottom-left as printed.
        "markers": {str(i): marker_corners_mm(i) for i in sorted(MARKER_ORIGINS_MM)},
    }
    out = ROOT / "src" / "lib" / "captureSheet.json"
    out.write_text(json.dumps(layout, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {public / 'capture-sheet.pdf'}")
    print(f"wrote {public / 'capture-sheet.png'}  ({page.width}x{page.height} px @ {RENDER_DPI} dpi)")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
