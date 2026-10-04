#!/usr/bin/env python3
"""
Scores the measurement pipeline in millimetres, and calibrates its one knob.

Runs the *real* pipeline — the bundled TypeScript from scripts/headless, the
same code the browser executes — over a set of captures whose true A and B are
known, and reports the mean absolute error the jury's rubric is scored on.

Two sources of truth:

  --synthetic N   Render N captures with scripts/synthetic.py. Their outlines
                  are exact by construction, so this measures the geometry
                  itself with no labelling noise at all.
  --real DIR      Photographs in DIR, with a truth.json alongside giving each
                  file's caliper-measured A and B in millimetres:
                      {"IMG_0001.jpg": {"aMm": 49.8, "bMm": 34.2}, ...}

`--sweep` tries a range of radial offsets and prints the error for each. The
best one goes into DEFAULT_OFFSET_MM in src/lib/pipeline/runPipeline.ts, and
its measured error goes into the README. That is the whole calibration story:
one distance, in millimetres, that anybody can check with a caliper.

Usage:
    node scripts/headless/build.mjs          # once, after changing any .ts
    python3 scripts/eval_mm.py --synthetic 40
    python3 scripts/eval_mm.py --synthetic 40 --sweep
    python3 scripts/eval_mm.py --real pictures --sweep
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "headless" / "dist" / "measure.mjs"

# Set from --control-length: the measured length of the sheet's printed control
# segment, when it was not printed at 100 %.
CONTROL_LENGTH_MM: float | None = None


def dump_rgba(image: np.ndarray, path: Path) -> tuple[int, int]:
    """Writes an RGB/RGBA numpy image as the raw RGBA bytes the harness reads."""
    if image.ndim == 2:
        image = np.dstack([image] * 3)
    if image.shape[2] == 3:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    path.write_bytes(np.ascontiguousarray(image.astype(np.uint8)).tobytes())
    return image.shape[1], image.shape[0]


def run_harness(jobs: list[dict]) -> list[dict]:
    if not HARNESS.exists():
        sys.exit(f"Harnais absent : {HARNESS}\nLancez d'abord : node scripts/headless/build.mjs")
    proc = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(jobs),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        sys.exit(f"Harnais en échec :\n{proc.stderr}")
    return json.loads(proc.stdout)


def load_synthetic(count: int, seed0: int, size: int, tmp: Path) -> list[dict]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import synthetic  # noqa: E402

    samples = []
    for i in range(count):
        seed = seed0 + i
        image, _mask, meta = synthetic.render_sample(seed, out_size=size)
        rgba_path = tmp / f"synth_{seed}.rgba"
        width, height = dump_rgba(image, rgba_path)
        samples.append(
            {
                "name": f"synth_{seed}",
                "rgbaPath": str(rgba_path),
                "width": width,
                "height": height,
                "truth": {"aMm": meta["aMm"], "bMm": meta["bMm"]},
            }
        )
    return samples


def load_real(directory: Path, tmp: Path) -> list[dict]:
    truth_path = directory / "truth.json"
    if not truth_path.exists():
        sys.exit(
            f"{truth_path} manquant.\n"
            "Mesurez chaque verre au pied à coulisse (système boxing : A = largeur,\n"
            'B = hauteur de la boîte englobante) et écrivez par exemple :\n'
            '  {"IMG_0001.jpg": {"aMm": 49.8, "bMm": 34.2}}'
        )
    truth = json.loads(truth_path.read_text(encoding="utf-8"))

    samples = []
    for name, values in truth.items():
        if name.startswith("_"):
            continue
        path = directory / name
        if not path.exists():
            print(f"  (ignoré, fichier absent : {name})")
            continue
        image = np.array(Image.open(path).convert("RGB"))
        rgba_path = tmp / f"{Path(name).stem}.rgba"
        width, height = dump_rgba(image, rgba_path)
        samples.append(
            {"name": name, "rgbaPath": str(rgba_path), "width": width, "height": height, "truth": values}
        )
    return samples


def score(samples: list[dict], mask_level: float | None, verbose: bool) -> dict:
    # `None` means "whatever DEFAULT_OFFSET_MM is in the app". That has to be
    # the default here, or the figure quoted in the README is the figure for a
    # pipeline nobody runs.
    options = {} if mask_level is None else {"offsetMm": mask_level}
    if CONTROL_LENGTH_MM is not None:
        options = {**options, "controlLengthMm": CONTROL_LENGTH_MM}
    jobs = [
        {"rgbaPath": s["rgbaPath"], "width": s["width"], "height": s["height"], "options": options}
        for s in samples
    ]
    results = run_harness(jobs)

    errors_a, errors_b, failures, scored = [], [], [], []
    for sample, result in zip(samples, results):
        if not result.get("ok"):
            failures.append((sample["name"], result.get("diagnostic") or result.get("reason", "?")))
            continue
        ea = result["aMm"] - sample["truth"]["aMm"]
        eb = result["bMm"] - sample["truth"]["bMm"]
        errors_a.append(ea)
        errors_b.append(eb)
        scored.append((max(abs(ea), abs(eb)), result.get("ridgeScore") or 0.0, sample["name"]))
        if verbose:
            print(
                f"  {sample['name']:<22} A {result['aMm']:6.2f} (vrai {sample['truth']['aMm']:6.2f}, "
                f"{ea:+5.2f})   B {result['bMm']:6.2f} (vrai {sample['truth']['bMm']:6.2f}, {eb:+5.2f})"
                f"   [{result['method']}, résidu {result['residualMm']:.3f} mm, score {result['ridgeScore']:.2f}, ext {result['outerEvidence']:.2f}]"
            )

    all_errors = np.array(errors_a + errors_b)
    return {
        "scored": sorted(scored, reverse=True),
        "offsetMm": mask_level,
        "n": len(errors_a),
        "failures": failures,
        "mae": float(np.abs(all_errors).mean()) if len(all_errors) else float("nan"),
        "bias": float(all_errors.mean()) if len(all_errors) else float("nan"),
        "p95": float(np.percentile(np.abs(all_errors), 95)) if len(all_errors) else float("nan"),
        "maxAbs": float(np.abs(all_errors).max()) if len(all_errors) else float("nan"),
        "stdA": float(np.std(errors_a)) if errors_a else float("nan"),
        "stdB": float(np.std(errors_b)) if errors_b else float("nan"),
    }


def points_for(mae: float) -> float:
    """The brief's own scale: 30 points at or under 1 mm, down to 0 at 4 mm."""
    if mae <= 1.0:
        return 30.0
    if mae >= 4.0:
        return 0.0
    return 30.0 * (4.0 - mae) / 3.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--synthetic", type=int, metavar="N", help="Évaluer sur N captures synthétiques")
    parser.add_argument("--seed", type=int, default=10_000)
    parser.add_argument("--size", type=int, default=1600, help="Côté long des photos synthétiques, px")
    parser.add_argument("--real", type=Path, metavar="DIR", help="Évaluer sur un dossier de photos + truth.json")
    parser.add_argument(
        "--offset", type=float, default=None,
        help="Correction radiale en mm ; par défaut, celle compilée dans l'app (DEFAULT_OFFSET_MM)",
    )
    parser.add_argument("--sweep", action="store_true", help="Balayer la correction radiale et choisir la meilleure")
    parser.add_argument(
        "--control-length", type=float, default=None, metavar="MM",
        help="Longueur mesurée du trait de contrôle de la feuille (100 mm si imprimée à 100 %%)",
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args()

    global CONTROL_LENGTH_MM
    CONTROL_LENGTH_MM = args.control_length

    if not args.synthetic and not args.real:
        parser.error("Précisez --synthetic N et/ou --real DIR")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        samples: list[dict] = []
        if args.synthetic:
            print(f"Rendu de {args.synthetic} captures synthétiques…")
            samples += load_synthetic(args.synthetic, args.seed, args.size, tmp)
        if args.real:
            samples += load_real(args.real, tmp)
        print(f"{len(samples)} captures à évaluer.\n")

        levels = np.round(np.arange(-1.2, 1.21, 0.2), 2).tolist() if args.sweep else [args.offset]

        best, best_key = None, (10**9, 10**9)
        for level in levels:
            stats = score(samples, None if level is None else float(level), verbose=not args.quiet and not args.sweep)
            if args.sweep:
                print(
                    f"  correction {level:+.2f} mm : MAE {stats['mae']:.3f} mm  biais {stats['bias']:+.3f}  "
                    f"p95 {stats['p95']:.3f}  échecs {len(stats['failures'])}/{len(samples)}"
                )
            key = (len(stats["failures"]), stats["mae"] if stats["mae"] == stats["mae"] else 1e9)  # échecs d'abord
            if best is None or key < best_key:
                best, best_key = stats, key

        assert best is not None
        print("\n--- Résultat ---")
        applied = best["offsetMm"]
        print(f"correction radiale     : {'défaut de l app' if applied is None else f'{applied:+.2f} mm'}")
        print(f"captures mesurées      : {best['n']}/{len(samples)}  ({2 * best['n']} valeurs A/B)")
        print(f"erreur absolue moyenne : {best['mae']:.3f} mm")
        print(f"biais systématique     : {best['bias']:+.3f} mm")
        print(f"p95 / max              : {best['p95']:.3f} / {best['maxAbs']:.3f} mm")
        print(f"écart-type A / B       : {best['stdA']:.3f} / {best['stdB']:.3f} mm")
        print(f"→ barème « précision » : {points_for(best['mae']):.1f} / 30 points")
        for name, reason in best["failures"]:
            print(f"  ÉCHEC {name}: {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
