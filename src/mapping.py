"""Parse Mapping.xlsx into an explicit internal representation.

    source field(s) -> destination field(s) -> rule text -> evidence (Excel rows)

Only destination fields listed here are corroborated (challenge constraint).
The supporting sheets (employment-situation rules, joins) are parsed too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .load_data import find_sheet
from .normalize import canon_key, is_blank

# Names used in the mapping that differ from the source extract headers.
SOURCE_ALIASES = {
    "EMPT_CD": "CatégorieEmploi",
    "EMPTP_CD": "CatégorieEmploi",
    "PERM_IND": "EstPermanent",
    "FT_IND": "EstTempsPlein",
}
KEY_FIELDS = {"personId"}  # used for matching, not corroborated as a value


@dataclass
class MappingEntry:
    entry_id: str
    description: str
    source_fields: list
    dest_fields: list
    rule_text: str
    first_row: int
    last_row: int
    source_columns: list = field(default_factory=list)   # resolved against the extract
    unresolved_sources: list = field(default_factory=list)

    @property
    def evidence(self) -> str:
        rows = f"ligne {self.first_row}" if self.first_row == self.last_row else \
            f"lignes {self.first_row}-{self.last_row}"
        return f"Mapping.xlsx › Mapping, {rows}"

    @property
    def is_direct(self) -> bool:
        return canon_key(self.rule_text) in ("na", "")

    @property
    def corroborated(self) -> bool:
        return bool(self.dest_fields)


def _clean(v) -> str:
    return "" if is_blank(v) else re.sub(r"\s+", " ", str(v)).strip()


def _split_source(text: str) -> list[str]:
    text = _clean(text)
    if not text or text == "-":
        return []
    alias = re.search(r"\(([^)]+)\)", text)
    name = re.sub(r"\([^)]*\)", "", text).strip()
    out = [name] if name else []
    if alias:
        out.append(alias.group(1).strip())
    return out


def _split_dest(text: str) -> list[str]:
    text = _clean(text)
    if not text or text == "-":
        return []
    return [p.strip() for p in re.split(r"\s+et\s+|,", text) if p.strip()]


def parse_mapping(sheets: dict, source_columns=None) -> list[MappingEntry]:
    sheet = find_sheet(sheets, "mapping")
    if sheet is None:
        raise ValueError("Feuille 'Mapping' introuvable dans Mapping.xlsx")
    grid, children = sheet["grid"], sheet["merged_children"]
    entries: list[MappingEntry] = []
    current = None
    last_description = ""
    for r_idx, row in enumerate(grid[1:], start=2):       # row 1 = header
        row = list(row) + [None] * (4 - len(row))
        a, b, c, d = row[:4]
        c_top = not is_blank(c) and (r_idx, 3) not in children
        a_top = not is_blank(a) and (r_idx, 1) not in children
        if c_top or (a_top and is_blank(c)):
            description = _clean(a) or last_description
            last_description = description
            current = MappingEntry(
                entry_id=f"M{r_idx:02d}", description=description,
                source_fields=[], dest_fields=_split_dest(c), rule_text="",
                first_row=r_idx, last_row=r_idx)
            entries.append(current)
        if current is None:
            continue
        if any(not is_blank(v) for v in (a, b, c, d)) or (r_idx, 3) in children:
            current.last_row = max(current.last_row, r_idx)
        for s in _split_source(b):
            if s not in current.source_fields:
                current.source_fields.append(s)
        if not is_blank(d):
            current.rule_text = (current.rule_text + "\n" + str(d).strip()).strip()

    if source_columns is not None:
        lookup = {canon_key(c): c for c in source_columns}
        for e in entries:
            for s in e.source_fields:
                target = SOURCE_ALIASES.get(s, s)
                col = lookup.get(canon_key(target))
                if col and col not in e.source_columns:
                    e.source_columns.append(col)
                elif not col:
                    e.unresolved_sources.append(s)
    return entries


def mapping_table(entries: list[MappingEntry]) -> pd.DataFrame:
    rows = []
    for e in entries:
        rows.append({
            "entry_id": e.entry_id,
            "description": e.description,
            "champs_source": ", ".join(e.source_fields),
            "colonnes_source_resolues": ", ".join(e.source_columns),
            "champs_source_absents_extraction": ", ".join(e.unresolved_sources),
            "champs_destination": ", ".join(e.dest_fields) or "-",
            "regle": e.rule_text,
            "type": "directe (N/A)" if e.is_direct else "transformation",
            "corrobore": "oui" if e.corroborated and not set(e.dest_fields) <= KEY_FIELDS
            else ("cle d'appariement" if set(e.dest_fields) & KEY_FIELDS else "non (pas de champ cible)"),
            "preuve": e.evidence,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Supporting sheets
# --------------------------------------------------------------------------
@dataclass
class SituationRule:
    access_codes: set
    specific_status: str | None        # -> detailedStatus
    reason_code_rule: str              # "NULL" or "LOOKUP_REMPHOR"
    return_date_rule: str              # "NULL" or "SOURCE_RETURN_DATE"
    excel_row: int
    raw: dict


def parse_situation_rules(sheets: dict) -> list[SituationRule]:
    sheet = find_sheet(sheets, "situation")
    if sheet is None:
        return []
    grid = sheet["grid"]
    rules = []
    for r_idx, row in enumerate(grid[1:], start=2):
        if all(is_blank(v) for v in row):
            continue
        codes_txt, status, cad, cadp = (list(row) + [None] * 4)[:4]
        codes = {int(x) for x in re.findall(r"\d+", str(codes_txt))}
        status = None if is_blank(status) or _clean(status).lower() == "null" else _clean(status).strip('"')
        cad_t = _clean(cad).lower()
        cadp_t = _clean(cadp).lower()
        rules.append(SituationRule(
            access_codes=codes,
            specific_status=status,
            reason_code_rule="LOOKUP_REMPHOR" if "remphor" in cad_t else "NULL",
            return_date_rule="SOURCE_RETURN_DATE" if "retour" in cadp_t else "NULL",
            excel_row=r_idx,
            raw={"codes": codes_txt, "cf_specificStatus": status, "cf_CAD": cad, "cf_CADP": cadp},
        ))
    return rules


@dataclass
class ContractRule:
    conditions: dict      # source column -> required value (normalised str)
    result: str
    line: str


CONTRACT_VAR_TO_COLUMN = {"PERM_IND": "EstPermanent", "FT_IND": "EstTempsPlein",
                          "EMPTP_CD": "CatégorieEmploi", "EMPT_CD": "CatégorieEmploi"}


def parse_contract_rules(rule_text: str) -> list[ContractRule]:
    """Parse lines like: SI PERM_IND=1 et FT_IND=1 et EMPTP_CD="V" --> Mettre "JWN"."""
    rules = []
    for line in rule_text.splitlines():
        if "-->" not in line:
            continue
        cond_part, result_part = line.split("-->", 1)
        res = re.search(r'"([^"]+)"', result_part)
        if not res:
            continue
        conds = {}
        for var, val in re.findall(r'([A-Z_]+)\s*=\s*"?([A-Za-z0-9]+)"?', cond_part):
            col = CONTRACT_VAR_TO_COLUMN.get(var, var)
            conds[col] = val.strip()
        if conds:
            rules.append(ContractRule(conditions=conds, result=res.group(1), line=line.strip()))
    return rules


def parse_join_sheets(sheets: dict) -> pd.DataFrame:
    rows = []
    for name, sheet in sheets.items():
        if "jointure" not in canon_key(name):
            continue
        for r_idx, row in enumerate(sheet["grid"][1:], start=2):
            if all(is_blank(v) for v in row):
                continue
            rows.append({"feuille": name, "champ_SIGRH": _clean(row[0]),
                         "equivalence": _clean(row[1] if len(row) > 1 else ""), "ligne": r_idx})
    return pd.DataFrame(rows)
