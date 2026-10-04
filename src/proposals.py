"""Proposed corrections for the destination system (never applied automatically).

* missing destination assignment  -> proposed new destination row (one line per field)
* field anomaly                   -> proposed field update

Each proposed value carries a certainty level:
  CERTAIN      copied directly from the source (mapping N/A)
  DERIVE       computed by a documented rule with complete inputs
  INFERE       depends on an interpretation or on patterns observed in the data
  A_CONFIRMER  cannot be derived with confidence: a human must provide/confirm it
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from . import config as C
from .normalize import display, is_blank, norm_number, norm_text

PERSON_LEVEL_FIELDS = {"givenName", "surname", "contactEmail", "onboardDate", "detailedStatus",
                       "statusReasonCode", "expectedReturnDate"}
PROPOSAL_COLUMNS = ["proposal_id", "case_id", "person_id", "action_type", "dest_row", "field", "current_value",
                    "proposed_value", "certainty", "derivation", "evidence", "status", "human_value", "human_comment"]


def _canon(v):
    if is_blank(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    n = norm_number(v)
    if isinstance(n, int):
        return str(n)
    s = norm_text(v)
    if s and len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def infer_unmapped_relations(dest: pd.DataFrame, mapped: list[str]) -> dict:
    """Observed relations for destination columns that the mapping does not cover."""
    cols = [c for c in dest.columns if not c.startswith("_")]
    canon = {c: dest[c].map(_canon) for c in cols}
    pid = dest["personId"].map(_canon)
    multi = pid[pid.duplicated(keep=False)]
    rel = {}
    for col in cols:
        if col in mapped or col == "personId":
            continue
        s = canon[col]
        nonblank = s.notna()
        if not nonblank.any():
            rel[col] = ("VIDE", None, "toujours vide dans l'extraction destination")
            continue
        if nonblank.all() and s.nunique() == 1:
            rel[col] = ("CONSTANTE", dest[col].iloc[0], "valeur constante sur toutes les lignes destination")
            continue
        found = None
        for m in mapped:
            if m not in canon or canon[m].nunique() < 2:
                continue   # a constant column cannot prove an equality relation
            both = nonblank | canon[m].notna()
            if both.sum() >= 5 and (s[both] == canon[m][both]).mean() >= 0.95:
                found = m
                break
        if found:
            share = (s == canon[found]).mean()
            rel[col] = ("EGAL_A", found, f"observé égal à {found} sur {share:.0%} des lignes destination")
            continue
        if len(multi):
            groups = s[multi.index].groupby(multi).nunique(dropna=False)
            if (groups <= 1).all():
                rel[col] = ("CONSTANTE_PAR_PERSONNE", None,
                            f"constante par personne (observé sur {multi.nunique()} personne(s) multi-affectations)")
                continue
        rel[col] = ("INCONNUE", None, "aucune relation observable")
    return rel


def observed_code_width(values: pd.Series) -> int | None:
    """Zero-padding width observed in 'code-label' destination values (e.g. '00397-...' -> 5)."""
    lefts = [str(v).split("-", 1)[0].strip() for v in values if not is_blank(v) and "-" in str(v)]
    lefts = [x for x in lefts if x.isdigit()]
    widths = {len(x) for x in lefts}
    if len(lefts) >= 3 and len(widths) == 1 and any(x.startswith("0") for x in lefts):
        return widths.pop()
    return None


def build_proposals(cor, cases: pd.DataFrame, records) -> pd.DataFrame:
    ds, eng = cor.ds, cor.engine
    dest = ds.destination
    mapped = [s.dest_field for s in eng.field_specs]
    # systemic groups whose expected value is itself uncertain (still A_INVESTIGUER)
    systemic_fields = set(cases.loc[cases["pattern_id"].str.startswith("P-SYS-")
                                    & (cases["verdict"] == C.A_INVESTIGUER), "field"])
    anomalous = set(zip(cases.loc[cases["verdict"] == C.ANOMALIE, "person_id"],
                        cases.loc[cases["verdict"] == C.ANOMALIE, "field"]))
    prefix_re = re.compile(cor.cfg.email_allowed_prefix_regex, re.I)
    rows = []
    missing = cases[cases["case_type"].isin(["AFFECTATION_ABSENTE_DESTINATION", "PERSONNE_ABSENTE_DESTINATION"])]
    relations = infer_unmapped_relations(dest, mapped) if len(missing) else {}
    src_by_row = {int(r): i for i, r in ds.source["_excel_row"].items()}
    dest_pid = dest["personId"].map(_canon)

    for _, case in missing.iterrows():
        src_idx = src_by_row[int(case["src_row"])]
        pid = case["person_id"]
        person_rows = dest[dest_pid == pid]
        derived = eng.derive_all(src_idx)
        prop_id = f"PR-{case['case_id']}"
        for col in [c for c in dest.columns if not c.startswith("_")]:
            value, certainty, how = "", C.TO_CONFIRM, ""
            if col == "personId":
                value, certainty, how = pid, C.CERTAIN, "Matricule (clé d'appariement)"
            elif col in derived:
                spec, d = derived[col]
                if not d.ok:
                    value, certainty, how = display(d.expected), C.TO_CONFIRM, f"{spec.rule.rule_id} : {d.issue}"
                else:
                    value = d.shown()
                    if spec.rule.is_direct:
                        certainty = C.CERTAIN
                    elif d.ambiguity_id or d.certainty == C.INFERRED:
                        certainty = C.INFERRED
                    else:
                        certainty = C.DERIVED
                    how = f"{spec.rule.rule_id} : {d.explanation}"
                    if d.alt_expected is not None:
                        how += f" ; autre lecture = {display(d.alt_expected)} ({d.ambiguity_id})"
                    width = observed_code_width(dest[col]) if spec.rule.rule_id.startswith("R_CONCAT") else None
                    if width and "-" in value:
                        code, label = value.split("-", 1)
                        if code.isdigit() and len(code) < width:
                            value = f"{code.zfill(width)}-{label}"
                            how += f" ; code complété à {width} chiffres (format observé dans la destination)"
                    if col in systemic_fields:
                        certainty = C.TO_CONFIRM
                        how += (f" ; ce champ fait l'objet d'un écart systémique (P-SYS-{col}) : la valeur réellement "
                                "attendue par le système B est incertaine")
                    if col in PERSON_LEVEL_FIELDS and len(person_rows):
                        existing = person_rows.iloc[0][col]
                        if _canon(existing) != _canon(value) and not (is_blank(existing) and is_blank(value)):
                            if (pid, col) in anomalous:
                                how += (f" ; la ligne destination existante ('{display(existing)}') est elle-même en "
                                        "ANOMALIE : non reprise")
                            else:
                                how += f" ; la ligne destination existante de la personne contient '{display(existing)}'"
                                certainty = C.TO_CONFIRM
                    if col == "contactEmail" and len(person_rows):
                        m = prefix_re.match(str(person_rows.iloc[0][col] or ""))
                        if m:
                            value = m.group(0) + value
                            how += f" ; préfixe d'environnement '{m.group(0)}' (autorisé) conservé"
            else:
                kind, ref, note = relations.get(col, ("INCONNUE", None, ""))
                if kind == "VIDE":
                    value, certainty, how = "", C.INFERRED, f"champ non couvert par le mapping ; {note}"
                elif kind == "EGAL_A":
                    value = display(derived[ref][1].expected) if ref in derived and derived[ref][1].ok else ""
                    certainty, how = C.INFERRED, f"champ non couvert par le mapping ; {note}"
                elif kind == "CONSTANTE":
                    value, certainty, how = display(ref), C.INFERRED, f"champ non couvert par le mapping ; {note}"
                elif kind == "CONSTANTE_PAR_PERSONNE" and len(person_rows):
                    value = display(person_rows.iloc[0][col])
                    certainty, how = C.INFERRED, (f"champ non couvert par le mapping ; copié de la ligne destination "
                                                  f"existante ({note})")
                else:
                    how = "champ non couvert par le mapping ; aucune valeur dérivable : à renseigner"
            rows.append({"proposal_id": prop_id, "case_id": case["case_id"], "person_id": pid,
                         "action_type": "CREER_AFFECTATION", "dest_row": None, "field": col,
                         "current_value": "", "proposed_value": value, "certainty": certainty, "derivation": how,
                         "evidence": case["evidence"], "status": "PROPOSE", "human_value": "", "human_comment": ""})

    anomalies = cases[(cases["case_type"] == "COMPARAISON_CHAMP") & (cases["verdict"] == C.ANOMALIE)]
    for _, case in anomalies.iterrows():
        certainty = C.CERTAIN if case["rule_id"] == "R_DIRECT" else (
            C.INFERRED if case["certainty"] == C.INFERRED else C.DERIVED)
        proposed = case["expected_value"]
        if case["field"] == "contactEmail":
            m = prefix_re.match(str(case["destination_value"] or ""))
            if m:
                proposed = m.group(0) + proposed      # keep the allowed environment prefix
        rows.append({"proposal_id": f"PR-{case['case_id']}", "case_id": case["case_id"], "person_id": case["person_id"],
                     "action_type": "CORRIGER_CHAMP", "dest_row": case["dst_row"], "field": case["field"],
                     "current_value": case["destination_value"], "proposed_value": proposed,
                     "certainty": certainty, "derivation": f"{case['rule_id']} — {case['explanation']}",
                     "evidence": case["evidence"], "status": "PROPOSE", "human_value": "", "human_comment": ""})
    return pd.DataFrame(rows, columns=PROPOSAL_COLUMNS)


def accepted_corrections(proposals: pd.DataFrame) -> pd.DataFrame:
    """Accepted / modified proposals, ready to be sent to the destination team."""
    if proposals.empty:
        return proposals.copy()
    acc = proposals[proposals["status"].isin(["ACCEPTE", "MODIFIE"])].copy()
    acc["final_value"] = [h if s == "MODIFIE" and h != "" else p
                          for s, h, p in zip(acc["status"], acc["human_value"], acc["proposed_value"])]
    return acc
