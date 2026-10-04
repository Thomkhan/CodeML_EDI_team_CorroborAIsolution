"""Read-only loading of the challenge workbooks.

All files are opened read-only; nothing is ever written back. Each loaded
dataframe keeps the raw cell values (``dtype=object``) and an ``_excel_row``
column so that every verdict can point back to the exact source row.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

from .config import DATA_DIR
from .normalize import canon_key, is_blank, norm_date, norm_number

# Canonical column names (as found in the official extracts). Columns in a
# supplied file are matched to these names ignoring accents, case and spacing.
SOURCE_COLUMNS = [
    "Matricule", "NomFamille", "PrénomUsuel", "DateEmbaucheRécente", "TypeAffectation",
    "DateEntréePoste", "DateSortiePoste", "CodePoste", "IntituléPoste", "CodeEmploi",
    "IntituléEmploi", "ÉchelleSalariale", "LibelléÉchelleSalariale", "CodeImputation",
    "LibelléImputation", "CodeDirection", "LibelléDirection", "CodeSite", "LibelléSite",
    "CatégorieEmploi", "EstPermanent", "EstTempsPlein", "CodeStatutEmploi",
    "CodeRaisonStatut", "LibelléRaisonStatut", "DateEffetRaison", "DateRetourAnticipée",
    "CodeSuspensionAccès", "IdentifiantResponsable", "NomResponsable", "CodeQuart",
    "HeuresNormeHebdo", "HeuresNormeQuotidienne",
]
DETAIL_COLUMNS = [
    "IdentifiantPoste", "IdentifiantEmploi", "CodeDirectionAffectée", "DateEffetAffectation",
    "CodeBudget", "IndicateurGestion", "CodePosteSecondaire", "MatriculeGestionnaire",
    "HeuresSemaineContrat", "HeuresJourContrat", "JoursTravailléesSemaine",
]
MOTIF_COLUMNS = ["CodeCatégorieStatut", "CodeStatutSystèmeExterne", "CodeGestionAccès"]

FILE_PATTERNS = {
    "source": ["source"],
    "destination": ["destination"],
    "detail": ["detailduposte", "detail"],
    "motif": ["motif"],
    "mapping": ["mapping"],
}


@dataclass
class Dataset:
    source: pd.DataFrame
    destination: pd.DataFrame
    job_detail: pd.DataFrame
    motif: pd.DataFrame
    mapping_sheets: dict            # sheet name -> raw grid info (see load_mapping_workbook)
    files: dict                     # role -> Path
    fingerprints: dict = field(default_factory=dict)   # role -> sha256
    warnings: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return str(self.files.get("source", "?"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonicalize_columns(df: pd.DataFrame, canonical: list[str]) -> pd.DataFrame:
    lookup = {canon_key(c): c for c in canonical}
    rename = {}
    for col in df.columns:
        key = canon_key(col)
        if key in lookup and lookup[key] != col:
            rename[col] = lookup[key]
    return df.rename(columns=rename)


def _read_table(path: Path, sheet=0) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in (".csv", ".txt"):
        df = pd.read_csv(path, dtype=object, sep=None, engine="python")
    else:
        df = pd.read_excel(path, sheet_name=sheet, dtype=object, engine="openpyxl")
    df = df.dropna(how="all").reset_index(drop=True)
    # header is Excel row 1, first data row is Excel row 2
    df.insert(0, "_excel_row", df.index + 2)
    return df


def find_file(directory: Path, role: str) -> Path | None:
    directory = Path(directory)
    if not directory.exists():
        return None
    candidates = [p for p in directory.iterdir()
                  if p.is_file() and p.suffix.lower() in (".xlsx", ".xlsm", ".csv")
                  and not p.name.startswith("~$")]
    for pattern in FILE_PATTERNS[role]:
        for p in sorted(candidates):
            if pattern in canon_key(p.stem):
                return p
    return None


def load_source(path: Path) -> pd.DataFrame:
    """Système A - RH employee/assignment extract (one row per assignment)."""
    return _canonicalize_columns(_read_table(path), SOURCE_COLUMNS)


def load_destination(path: Path) -> pd.DataFrame:
    """Système B - Temps extract (one row per destination assignment)."""
    return _read_table(path)


def _split_embedded_csv(df: pd.DataFrame) -> pd.DataFrame | None:
    """Detect a sheet whose rows are stored as comma-separated text in one cell."""
    non_empty = [c for c in df.columns if c != "_excel_row" and df[c].notna().any()]
    header_text = str(df.columns[1]) if len(df.columns) > 1 else ""
    if len(non_empty) <= 1 and header_text.count(",") >= 2:
        lines = [header_text] + [str(v) for v in df[non_empty[0]].dropna()] if non_empty else [header_text]
        parsed = pd.read_csv(io.StringIO("\n".join(lines)), dtype=object, quoting=csv.QUOTE_MINIMAL)
        parsed.insert(0, "_excel_row", range(2, len(parsed) + 2))
        return parsed
    return None


def load_job_detail(path: Path) -> pd.DataFrame:
    """Détail du poste history. The official workbook stores each record as a
    comma-separated string inside column A; this is detected and parsed.
    Excel serial dates are converted to real dates (``DateEffet`` column)."""
    raw = _read_table(path)
    embedded = _split_embedded_csv(raw)
    df = embedded if embedded is not None else raw
    df = _canonicalize_columns(df, DETAIL_COLUMNS)
    df["_embedded_csv"] = embedded is not None
    df["DateEffet"] = df["DateEffetAffectation"].map(lambda v: norm_date(v, allow_serial=True))
    for col in ("IdentifiantPoste", "IdentifiantEmploi", "CodeDirectionAffectée",
                "HeuresSemaineContrat", "HeuresJourContrat"):
        if col in df.columns:
            df[col + "_n"] = df[col].map(norm_number)
    return df


def load_motif(path: Path) -> pd.DataFrame:
    """Motif de la situation d'emploi (reason code -> Remphor code, access code)."""
    return _canonicalize_columns(_read_table(path), MOTIF_COLUMNS)


def load_mapping_workbook(path: Path) -> dict:
    """Return every sheet of Mapping.xlsx as a grid with merged-cell information.
    Parsing into mapping entries is done in ``mapping.py``."""
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        merged_children = set()
        merged_parent = {}
        for rng in ws.merged_cells.ranges:
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if (r, c) != (rng.min_row, rng.min_col):
                        merged_children.add((r, c))
                        merged_parent[(r, c)] = (rng.min_row, rng.min_col)
        grid = [[ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
                for r in range(1, ws.max_row + 1)]
        sheets[ws.title] = {"grid": grid, "merged_children": merged_children,
                            "merged_parent": merged_parent}
    wb.close()
    return sheets


def find_sheet(sheets: dict, *keywords: str) -> dict | None:
    for name, sheet in sheets.items():
        key = canon_key(name)
        if all(canon_key(k) in key for k in keywords):
            return sheet
    return None


def load_all(data_dir: Path | str = DATA_DIR, mapping_path: Path | str | None = None,
             motif_path: Path | str | None = None, **overrides) -> Dataset:
    """Load every input of one corroboration run.

    ``data_dir`` is searched for the source, destination, détail du poste, motif
    and mapping files. Missing mapping/motif files fall back to the official
    ``data/`` folder (used for synthetic datasets, which reuse the official
    mapping read-only). Explicit paths can be given via ``overrides``
    (source=..., destination=..., detail=...).
    """
    data_dir = Path(data_dir)
    files = {}
    warnings = []
    for role in ("source", "destination", "detail", "motif", "mapping"):
        explicit = overrides.get(role) or (mapping_path if role == "mapping" else None) \
            or (motif_path if role == "motif" else None)
        path = Path(explicit) if explicit else find_file(data_dir, role)
        if path is None and role in ("mapping", "motif"):
            path = find_file(DATA_DIR, role)
            if path is not None:
                warnings.append(f"{role}: fichier absent de {data_dir}, utilisation de {path} (lecture seule)")
        if path is None:
            raise FileNotFoundError(f"Fichier '{role}' introuvable dans {data_dir}")
        files[role] = path

    ds = Dataset(
        source=load_source(files["source"]),
        destination=load_destination(files["destination"]),
        job_detail=load_job_detail(files["detail"]),
        motif=load_motif(files["motif"]),
        mapping_sheets=load_mapping_workbook(files["mapping"]),
        files=files,
        fingerprints={k: sha256(v) for k, v in files.items()},
        warnings=warnings,
    )
    missing = [c for c in ("Matricule", "TypeAffectation", "CodePoste", "CodeEmploi") if c not in ds.source.columns]
    if missing:
        raise ValueError(f"Colonnes source obligatoires absentes : {missing}")
    if "personId" not in ds.destination.columns:
        raise ValueError("Colonne destination 'personId' absente")
    for df, name in ((ds.source, "source"), (ds.destination, "destination")):
        blank_ids = df.index[df.iloc[:, 1].map(is_blank)].tolist()
        if blank_ids:
            warnings.append(f"{name}: {len(blank_ids)} ligne(s) sans identifiant")
    return ds


def dataset_overview(ds: Dataset) -> pd.DataFrame:
    rows = []
    for role, df in (("source", ds.source), ("destination", ds.destination),
                     ("detail", ds.job_detail), ("motif", ds.motif)):
        rows.append({
            "role": role,
            "fichier": Path(ds.files[role]).name,
            "lignes": len(df),
            "colonnes": len([c for c in df.columns if not c.startswith("_") and not c.endswith("_n")]),
            "sha256": ds.fingerprints[role][:12],
        })
    rows.append({"role": "mapping", "fichier": Path(ds.files["mapping"]).name,
                 "lignes": None, "colonnes": None, "sha256": ds.fingerprints["mapping"][:12]})
    return pd.DataFrame(rows)
