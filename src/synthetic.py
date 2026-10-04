"""Synthetic data generator with injected ground truth.

Produces files with the same general schema as the official extracts (source,
destination, détail du poste stored as CSV-in-cell, motifs) in
``synthetic_data/<name>/`` — never in ``data/``. The official Mapping.xlsx is
reused read-only. The destination is built by an *independent* implementation
of the documented rules, then known discrepancies are injected and recorded in
``ground_truth.csv`` so the engine can be evaluated.

Usage:  python -m src.synthetic --n 1000 --seed 42 --evaluate
"""
from __future__ import annotations

import argparse
import json
import random
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .load_data import SOURCE_COLUMNS

FIRST_NAMES = ["Émilie", "Jérôme", "François", "Hélène", "Noël", "Zoé", "Marc", "Julie", "Luc", "Anaïs",
               "Benoît", "Chloé", "Gaël", "Inès", "Léa", "Mathis", "Océane", "Rémi", "Sophie", "Théo"]
LAST_NAMES = ["Côté", "Gagné", "Lévesque", "Bélanger", "Tremblay", "Roy", "Morin", "Bouchard", "Gauthier",
              "Pelletier", "Bérubé", "Ouellet", "Gélinas", "Paré", "Thériault", "Fortin", "Lamontagne", "Ménard"]
CATEGORY_DIST = [("V", 0.78), ("O", 0.06), ("T", 0.04), ("M", 0.03), ("R", 0.03), ("J", 0.02), ("Z", 0.02), ("Q", 0.02)]
CONTRACT = {"T": "KELH", "O": "WHX", "M": "CEGQ", "R": "CNZC", "J": "RMQ", "Z": "JAW", "Q": "TRSY"}
ACTIVE_REASONS = [651, 697, 703]
ENV_PREFIX = "dev-08-v2_"
DEST_COLUMNS = (["personId", "givenName", "surname", "contactEmail", "activityStatus", "onboardDate"]
                + [f"customAttribute_{i:02d}" for i in range(1, 13)] + ["statusReasonCode", "expectedReturnDate"]
                + [f"customAttribute_{i:02d}" for i in range(13, 42)] + ["contractTypeCode"]
                + [f"customAttribute_{i:02d}" for i in range(42, 49)]
                + ["detailedStatus", "siteId", "siteCode", "siteName", "divisionId", "divisionCode", "divisionName",
                   "positionId", "positionCode", "positionName", "assignmentStartDate", "assignmentEndDate",
                   "payGradeId", "isPrimaryAssignment", "isTemporaryAssignment", "termStartDate", "termEndDate",
                   "wageOverrideAmount", "wageMultiplierFactor", "weeklyHoursOverride", "dailyHoursOverride",
                   "externalReferenceId"])
DETAIL_HEADER = ("IdentifiantPoste,IdentifiantEmploi,CodeDirectionAffectée,DateEffetAffectation,CodeBudget,"
                 "IndicateurGestion,CodePosteSecondaire,MatriculeGestionnaire,HeuresSemaineContrat,"
                 "HeuresJourContrat,JoursTravailléesSemaine")

DEFAULT_RATES = {
    "wrong_value": 0.04, "swap": 0.01, "missing_assignment": 0.30, "missing_dest_value": 0.01,
    "missing_source_value": 0.01, "wrong_rule": 0.01, "stale_return_date": 0.03, "format_only": 0.05,
    "dup_ambiguous": 0.005, "undocumented_access": 0.005, "uncovered_contract": 0.005,
    "unit_change_after_entry": 0.12, "extra_assign_A": 0.07, "extra_assign_S": 0.08,
}
WRONG_VALUE_FIELDS = ["siteName", "siteCode", "divisionCode", "payGradeId", "contractTypeCode", "weeklyHoursOverride",
                      "onboardDate", "surname", "divisionName", "statusReasonCode"]


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    if end <= start:
        return start
    return start + timedelta(days=rng.randrange((end - start).days + 1))


@dataclass
class SyntheticBundle:
    directory: Path
    ground_truth: pd.DataFrame
    metadata: dict


class Generator:
    def __init__(self, n_employees: int, seed: int, rates: dict | None = None):
        self.n = n_employees
        self.seed = seed
        self.rng = random.Random(seed)
        self.np = np.random.default_rng(seed)
        self.rates = {**DEFAULT_RATES, **(rates or {})}
        self.gt: list[dict] = []
        self.injected: set = set()        # (row_key, field)
        self.injected_postes: set = set()
        self.ref_date = date(2026, 6, 30)

    # ------------------------------------------------------------ reference data
    def _reference(self):
        r = self.rng
        self.sites = {c: f"Emplacement{c}" for c in r.sample(range(10, 99), 6)}
        n_div = max(8, self.n // 60)
        self.divisions = {}
        for code in r.sample(range(300, 999), n_div):
            self.divisions[code] = {"imput": r.randrange(2000, 2999), "label": f"UnitAdmin{code:05d}",
                                    "site": r.choice(list(self.sites))}
        n_emp = max(10, self.n // 40)
        self.emplois = {c: {"label": f"Empl{c}", "grade": r.choice([223, 260, 274, 293, 301])}
                        for c in r.sample(range(6000, 6999), n_emp)}
        codes = r.sample([c for c in range(500, 999) if c not in ACTIVE_REASONS], 90)
        remphor = [r.randrange(100, 199) for _ in codes]
        access = [1] * 25 + [r.choice([2, 2, 2, 3, 6, 7]) for _ in range(65)]
        self.motif = pd.DataFrame({"CodeCatégorieStatut": codes, "CodeStatutSystèmeExterne": remphor,
                                   "CodeGestionAccès": access})
        self.motif_by_access = {a: self.motif[self.motif["CodeGestionAccès"] == a] for a in (2, 3, 6, 7)}
        self.postes_used = set()

    def _new_poste(self) -> int:
        while True:
            p = self.rng.randrange(10000, 99999)
            if p not in self.postes_used:
                self.postes_used.add(p)
                return p

    # ------------------------------------------------------------ source + history
    def _employee(self, mat: int) -> dict:
        r = self.rng
        cat = r.choices([c for c, _ in CATEGORY_DIST], [w for _, w in CATEGORY_DIST])[0]
        perm = "Oui" if cat == "V" else "Non"
        ft = ("Oui" if r.random() < 0.85 else "Non") if cat == "V" else r.choice(["Oui", "Non"])
        quart = r.choices([23, 8, 9], [0.85, 0.08, 0.07])[0]
        hours = {23: (40, 8), 8: (35, 7), 9: (36, "7.2")}[quart]
        hire = _rand_date(r, date(1980, 1, 1), date(2024, 12, 31))
        absent = r.random() < 0.10
        if absent:
            acc = r.choice([2, 2, 2, 3, 6, 7])
            m = self.motif_by_access[acc]
            reason = int(m.iloc[r.randrange(len(m))]["CodeCatégorieStatut"])
            ret = _rand_date(r, date(2026, 7, 1), date(2027, 12, 31))
            statut = 2
        else:
            acc, statut, reason, ret = 1, 1, r.choice(ACTIVE_REASONS), None
        return {"Matricule": mat, "NomFamille": r.choice(LAST_NAMES), "PrénomUsuel": r.choice(FIRST_NAMES),
                "DateEmbaucheRécente": hire, "CatégorieEmploi": cat, "EstPermanent": perm, "EstTempsPlein": ft,
                "CodeStatutEmploi": statut, "CodeRaisonStatut": reason,
                "LibelléRaisonStatut": f"MotifSitua{reason % 1000:03d}",
                "DateEffetRaison": _rand_date(r, hire, date(2026, 1, 1)), "DateRetourAnticipée": ret,
                "CodeSuspensionAccès": acc, "IdentifiantResponsable": r.randrange(1000000, 9999999),
                "CodeQuart": quart, "HeuresNormeHebdo": hours[0], "HeuresNormeQuotidienne": hours[1]}

    def _assignment(self, emp: dict, atype: str, emploi=None, division=None, start=None) -> dict:
        r = self.rng
        emploi = emploi or r.choice(list(self.emplois))
        division = division or r.choice(list(self.divisions))
        dv = self.divisions[division]
        hire = emp["DateEmbaucheRécente"]
        if start is None:
            start = _rand_date(r, max(hire, date(2023, 1, 1)), date(2026, 3, 31)) if atype == "A" else \
                _rand_date(r, hire, date(2025, 12, 31))
        end = _rand_date(r, start + timedelta(days=30), date(2027, 6, 30)) if atype == "A" and r.random() < 0.5 else None
        poste = self._new_poste()
        return {**emp, "TypeAffectation": atype, "DateEntréePoste": start, "DateSortiePoste": end,
                "CodePoste": poste, "IntituléPoste": f"Poste{poste}", "CodeEmploi": emploi,
                "IntituléEmploi": self.emplois[emploi]["label"], "ÉchelleSalariale": self.emplois[emploi]["grade"],
                "LibelléÉchelleSalariale": f"GrRemun{self.emplois[emploi]['grade']}",
                "CodeImputation": dv["imput"], "LibelléImputation": f"Centre{dv['imput']}",
                "CodeDirection": division, "LibelléDirection": dv["label"], "CodeSite": dv["site"],
                "LibelléSite": self.sites[dv["site"]],
                "NomResponsable": f"Nom{emp['IdentifiantResponsable']}, Pre{emp['IdentifiantResponsable']}"}

    def _history(self, a: dict, mode: str):
        """mode: 'stable' | 'change_before' | 'change_after' | 'stable_updates_after'.
        Returns the CSV rows and the records as (date, unit, values) tuples."""
        r = self.rng
        entry, unit = a["DateEntréePoste"], a["CodeDirection"]
        other = r.choice([d for d in self.divisions if d != unit])
        k = r.randint(1, 6)
        before = sorted(_rand_date(r, date(1950, 1, 1), entry - timedelta(days=1)) for _ in range(k))
        if mode == "change_after":
            change = _rand_date(r, entry + timedelta(days=1), min(self.ref_date, entry + timedelta(days=3000)))
            plan = [(d, other) for d in before] + [(change, unit)]
        elif mode == "change_before":
            plan = [(d, other) for d in before[:-1]] + [(before[-1], unit)] if k > 1 else [(before[0], unit)]
        else:
            plan = [(d, unit) for d in before]
            if mode == "stable_updates_after":
                plan.append((_rand_date(r, entry + timedelta(days=1), self.ref_date), unit))
        recs = []
        for i, (d, u) in enumerate(sorted(set(plan))):
            mgr = "" if i == 0 else str(r.randrange(10000, 99999))
            values = (a["CodeEmploi"], u, a["CodeImputation"], 1 if i == 0 else 0, r.randrange(100, 80000), mgr, 40, 8, 5)
            recs.append((d, u, values))
        # sometimes the last record is re-issued unchanged later: the current record keeps its original date
        if mode == "stable_updates_after" and r.random() < 0.3 and recs[-1][0] < self.ref_date:
            d, u, values = recs[-1]
            recs.append((_rand_date(r, d + timedelta(days=1), self.ref_date), u, values))
        rows = [f"{a['CodePoste']},{v[0]},{v[1]},{_serial(d)},{v[2]},{v[3]},{v[4]},{v[5]},{v[6]},{v[7]},{v[8]}"
                for d, u, v in recs]
        return rows, recs

    # ------------------------------------------------------------ expected destination (documented rules)
    @staticmethod
    def _current_record_date(recs):
        """Date of the last détail record that differs from its predecessor (else MIN EFFDT)."""
        i = len(recs) - 1
        while i > 0 and recs[i][2] == recs[i - 1][2]:
            i -= 1
        return recs[i][0]

    def _expected_dest(self, a: dict, recs) -> dict:
        start = max(a["DateEntréePoste"], self._current_record_date(recs))
        cat, perm, ft = a["CatégorieEmploi"], a["EstPermanent"] == "Oui", a["EstTempsPlein"] == "Oui"
        contract = ("JWN" if ft else "XFLR") if cat == "V" and perm else CONTRACT.get(cat)
        absent = a["CodeSuspensionAccès"] in (2, 3, 6, 7)
        remphor = None
        if absent:
            remphor = int(self.motif.loc[self.motif["CodeCatégorieStatut"] == a["CodeRaisonStatut"],
                                         "CodeStatutSystèmeExterne"].iloc[0])
        email = f"{_strip(a['PrénomUsuel'])[0]}{_strip(a['NomFamille'])}{str(a['Matricule'])[-3:]}@{C.DEFAULT_CONFIG.email_domain}"
        flags = {"P": ("true", "false"), "A": ("false", "true"), "S": ("false", "false")}[a["TypeAffectation"]]
        d = {c: None for c in DEST_COLUMNS}
        d.update({
            "personId": a["Matricule"], "givenName": a["PrénomUsuel"], "surname": a["NomFamille"],
            "contactEmail": ENV_PREFIX + email, "activityStatus": "ACTIVE",
            "onboardDate": a["DateEmbaucheRécente"].strftime("%Y-%m-%dT00:00:00.000Z"),
            "customAttribute_12": "false", "statusReasonCode": remphor,
            "expectedReturnDate": a["DateRetourAnticipée"] if absent else None, "contractTypeCode": contract,
            "customAttribute_45": "999-999-9999", "customAttribute_48": "000-000-0000",
            "detailedStatus": "Absence complète" if absent else "Actif",
            "siteId": a["CodeSite"], "siteCode": a["CodeSite"], "siteName": a["LibelléSite"],
            "divisionId": a["CodeDirection"], "divisionCode": a["CodeImputation"],
            "divisionName": f"{a['CodeDirection']:05d}-{a['LibelléDirection']}",
            "positionId": a["CodeEmploi"], "positionCode": a["CodeEmploi"],
            "positionName": f"{a['CodeEmploi']}-{a['IntituléEmploi']}",
            "assignmentStartDate": start, "assignmentEndDate": a["DateSortiePoste"],
            "payGradeId": a["ÉchelleSalariale"], "isPrimaryAssignment": flags[0], "isTemporaryAssignment": flags[1],
            "termStartDate": start, "termEndDate": a["DateSortiePoste"],
            "weeklyHoursOverride": a["HeuresNormeHebdo"],
            "dailyHoursOverride": float(a["HeuresNormeQuotidienne"]),
        })
        return d

    # ------------------------------------------------------------ ground truth
    def _gt(self, a, field, category, verdict, desc, simulated=""):
        self.gt.append({"person_id": str(a["Matricule"]), "src_poste": str(a["CodePoste"]), "field": field,
                        "category": category, "expected_verdict": verdict, "description": desc,
                        "simulated_expert_decision": simulated})
        self.injected.add((a["CodePoste"], field))
        self.injected_postes.add(a["CodePoste"])

    # ------------------------------------------------------------ main
    def generate(self):
        r, rates = self.rng, self.rates
        self._reference()
        mats = r.sample(range(1000000, 9999999), self.n)
        src_rows, dst_rows, detail_rows = [], [], []
        histories = {}
        pairs = []   # (source assignment dict, dest dict or None)
        for mat in mats:
            emp = self._employee(mat)
            special = r.random()
            if special < rates["undocumented_access"]:
                emp.update(CodeSuspensionAccès=4, CodeStatutEmploi=4)
            elif special < rates["undocumented_access"] + rates["uncovered_contract"]:
                emp.update(CatégorieEmploi="V", EstPermanent="Non")
            assignments = [self._assignment(emp, "P")]
            if r.random() < rates["extra_assign_A"]:
                assignments.append(self._assignment(emp, "A"))
            if r.random() < rates["extra_assign_S"]:
                assignments.append(self._assignment(emp, "S"))
            dup = r.random() < rates["dup_ambiguous"]
            if dup:
                base = self._assignment(emp, "S")
                twin = self._assignment(emp, "S", emploi=base["CodeEmploi"], division=base["CodeDirection"],
                                        start=base["DateEntréePoste"])
                twin["DateSortiePoste"] = base["DateSortiePoste"]
                base["_twin"] = twin["_twin"] = True   # must stay indistinguishable (same transformed dates)
                assignments += [base, twin]
            for a in assignments:
                roll = r.random()
                if a.get("_twin"):
                    mode = "stable"
                elif roll < rates["unit_change_after_entry"]:
                    mode = "change_after"
                elif roll < rates["unit_change_after_entry"] + 0.25:
                    mode = "stable_updates_after"
                elif roll < rates["unit_change_after_entry"] + 0.30:
                    mode = "change_before"
                else:
                    mode = "stable"
                rows, recs = self._history(a, mode)
                detail_rows += rows
                histories[a["CodePoste"]] = (mode, recs)
                d = self._expected_dest(a, recs)
                pairs.append([a, d])
            if dup:
                pairs[-1][1] = None   # one destination row for two identical source assignments
                for a, _ in pairs[-2:]:
                    self._gt(a, "__assignment__", "ambiguous_duplicate_assignment", C.A_INVESTIGUER,
                             "deux affectations source indiscernables pour une seule ligne destination", C.ANOMALIE)
            if emp["CodeSuspensionAccès"] == 4:
                for a, _ in pairs[-len(assignments):]:
                    for f in ("detailedStatus", "statusReasonCode", "expectedReturnDate"):
                        self._gt(a, f, "ambiguous_undocumented_code", C.A_INVESTIGUER,
                                 "code de traitement des accès 4 non documenté")
                for a, d in pairs[-len(assignments):]:
                    if d:
                        d.update(detailedStatus="Cessation", statusReasonCode=None, expectedReturnDate=None)
            if emp["CatégorieEmploi"] == "V" and emp["EstPermanent"] == "Non":
                for a, d in pairs[-len(assignments):]:
                    if d:
                        d["contractTypeCode"] = "JWN"
                    self._gt(a, "contractTypeCode", "ambiguous_uncovered_rule", C.A_INVESTIGUER,
                             "EMPTP_CD=V avec PERM_IND=0 non couvert par le mapping")

        # justified differences produced by the start-date transformation (job history after entry);
        # a few of them are corrupted: destination keeps the raw source date (transformation not applied)
        for a, d in pairs:
            mode, recs = histories[a["CodePoste"]]
            if d is None or d["assignmentStartDate"] == a["DateEntréePoste"] \
                    or (a["CodePoste"], "assignmentStartDate") in self.injected:
                continue
            if self.rng.random() < rates["wrong_rule"] * 8:
                d["assignmentStartDate"] = d["termStartDate"] = a["DateEntréePoste"]
                self._gt(a, "assignmentStartDate", "wrong_rule_application", C.ANOMALIE,
                         "date source reprise sans appliquer la transformation avec le détail du poste")
            elif mode == "change_after":
                self._gt(a, "assignmentStartDate", "justified_rule_unit_change", C.ECART_JUSTIFIE,
                         "changement d'unité adm. après l'entrée en poste : date d'effet = date du changement")
            else:
                self._gt(a, "assignmentStartDate", "justified_rule_job_history_update", C.ECART_JUSTIFIE,
                         "détail du poste mis à jour après l'entrée : date d'effet = date du détail courant")

        self._inject(pairs, histories)
        for a, d in pairs:
            src_rows.append({c: a.get(c) for c in SOURCE_COLUMNS})
            if d is not None:
                dst_rows.append(d)
        src = pd.DataFrame(src_rows, columns=SOURCE_COLUMNS)
        dst = pd.DataFrame(dst_rows, columns=DEST_COLUMNS)
        # shuffle destination rows (order must not matter)
        dst = dst.sample(frac=1.0, random_state=self.seed).reset_index(drop=True)
        detail = pd.DataFrame({DETAIL_HEADER: detail_rows})
        gt = pd.DataFrame(self.gt)
        return src, dst, detail, self.motif.copy(), gt

    def _inject(self, pairs, histories):
        r, rates = self.rng, self.rates
        live = [(a, d) for a, d in pairs if d is not None]
        # --- systemic segment failure: one division, wrong constant divisionCode
        counts = pd.Series([a["CodeDirection"] for a, _ in live]).value_counts()
        eligible = counts[(counts >= 8) & (counts <= max(8, 0.15 * len(live)))]
        if len(eligible):
            div = int(eligible.index[0])
            for a, d in live:
                if a["CodeDirection"] == div:
                    d["divisionCode"] = 9999
                    self._gt(a, "divisionCode", "systemic_mapping_failure", C.ANOMALIE,
                             f"divisionCode constant 9999 pour toute la direction {div}")
        # --- missing assignments (non-primary)
        for i, (a, d) in enumerate(pairs):
            if d is None or a["TypeAffectation"] == "P" or a["CodePoste"] in self.injected_postes:
                continue
            if r.random() < rates["missing_assignment"]:
                pairs[i][1] = None
                self._gt(a, "__assignment__", "missing_assignment", C.A_INVESTIGUER,
                         f"affectation {a['TypeAffectation']} absente de la destination",
                         C.ANOMALIE if a["TypeAffectation"] == "A" else C.ECART_JUSTIFIE)
        live = [(a, d) for a, d in pairs if d is not None]

        def free(a, field):
            return (a["CodePoste"], field) not in self.injected and (a["CodePoste"], "__assignment__") not in self.injected

        # --- swaps between two employees
        n_swaps = int(len(live) * rates["swap"])
        for _ in range(n_swaps):
            field = r.choice(["contractTypeCode", "siteName", "weeklyHoursOverride", "payGradeId"])
            for _try in range(50):
                (a1, d1), (a2, d2) = r.sample(live, 2)
                if a1["Matricule"] != a2["Matricule"] and free(a1, field) and free(a2, field) \
                        and d1[field] is not None and d2[field] is not None and str(d1[field]) != str(d2[field]):
                    d1[field], d2[field] = d2[field], d1[field]
                    for a, other in ((a1, a2), (a2, a1)):
                        self._gt(a, field, "swapped_values", C.ANOMALIE, f"valeur intervertie avec {other['Matricule']}")
                    break
        # --- per-row injections
        for a, d in live:
            roll = r.random()
            acc = 0.0
            acc += rates["wrong_value"]
            if roll < acc:
                field = r.choice(WRONG_VALUE_FIELDS)
                if not free(a, field) or d[field] is None:
                    continue
                old = d[field]
                if field == "siteName":
                    d[field] = r.choice([v for v in self.sites.values() if v != old])
                elif field == "siteCode":
                    d[field] = r.choice([s for s in self.sites if s != old])
                elif field == "divisionCode":
                    d[field] = int(old) + r.randint(1, 50)
                elif field == "payGradeId":
                    d[field] = r.choice([g for g in (223, 260, 274, 293, 301) if g != old])
                elif field == "contractTypeCode":
                    d[field] = r.choice([c for c in ("JWN", "XFLR", "WHX", "KELH", "CEGQ") if c != old])
                elif field == "weeklyHoursOverride":
                    d[field] = 37.5
                elif field == "onboardDate":
                    d[field] = (a["DateEmbaucheRécente"] + timedelta(days=r.randint(1, 400))).strftime("%Y-%m-%dT00:00:00.000Z")
                elif field == "surname":
                    s = list(old)
                    s[1], s[2] = s[2], s[1]
                    if "".join(s).casefold() == old.casefold():
                        s.append("x")
                    d[field] = "".join(s)
                elif field == "divisionName":
                    d[field] = f"{a['CodeDirection']:05d}-UnitAdmin{r.randint(1, 99999):05d}"
                elif field == "statusReasonCode":
                    d[field] = int(old) + 1
                self._gt(a, field, "wrong_mapped_value", C.ANOMALIE, f"{old} remplacé par {d[field]}")
                continue
            acc += rates["missing_dest_value"]
            if roll < acc:
                field = r.choice(["siteName", "payGradeId", "contractTypeCode"])
                if free(a, field) and d[field] is not None:
                    d[field] = None
                    self._gt(a, field, "missing_destination_value", C.ANOMALIE, "valeur non transmise")
                continue
            acc += rates["missing_source_value"]
            if roll < acc:
                if free(a, "weeklyHoursOverride") and free(a, "dailyHoursOverride"):
                    a["HeuresNormeHebdo"], a["HeuresNormeQuotidienne"] = None, None
                    d["weeklyHoursOverride"], d["dailyHoursOverride"] = 40, 8.0
                    for f in ("weeklyHoursOverride", "dailyHoursOverride"):
                        self._gt(a, f, "missing_source_value", C.A_INVESTIGUER,
                                 "heures absentes de la source, destination = valeur par défaut du poste", C.ANOMALIE)
                continue
            acc += rates["stale_return_date"]
            if roll < acc:
                if a["CodeSuspensionAccès"] == 1 and free(a, "expectedReturnDate"):
                    a["DateRetourAnticipée"] = _rand_date(r, date(2020, 1, 1), date(2025, 12, 31))
                    self._gt(a, "expectedReturnDate", "justified_rule_active_status", C.ECART_JUSTIFIE,
                             "employé actif : la date de retour source n'est pas transmise (cf_CADP = null)")
                continue
            acc += rates["format_only"]
            if roll < acc:
                field = r.choice(["givenName", "surname", "payGradeId", "divisionId", "weeklyHoursOverride",
                                  "isPrimaryAssignment"])
                if not free(a, field) or d[field] is None:
                    continue
                if field == "givenName":
                    d[field] = f"  {d[field]} "
                elif field == "surname":
                    d[field] = str(d[field]).upper()
                elif field in ("payGradeId", "divisionId"):
                    d[field] = f"{int(d[field]):06d}"
                elif field == "weeklyHoursOverride":
                    d[field] = f"{float(d[field]):.1f}"
                elif field == "isPrimaryAssignment":
                    d[field] = str(d[field]).upper()
                expected = C.ECART_JUSTIFIE if field == "isPrimaryAssignment" else C.CONFORME  # transformed field
                self._gt(a, field, "format_only", expected, f"différence de format uniquement ({d[field]!r})")


def generate(n_employees: int = 1000, seed: int = 42, out_dir=None, rates: dict | None = None,
             fmt: str = "xlsx") -> SyntheticBundle:
    gen = Generator(n_employees, seed, rates)
    src, dst, detail, motif, gt = gen.generate()
    out = Path(out_dir) if out_dir else C.SYNTHETIC_DIR / f"synth_n{n_employees}_s{seed}"
    if out.resolve().is_relative_to(C.DATA_DIR.resolve()):
        raise ValueError("Les données synthétiques ne doivent jamais être écrites dans data/")
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        src.to_csv(out / "Employe_Source_synthetique.csv", index=False)
        dst.to_csv(out / "Employe_Destination_synthetique.csv", index=False)
    else:
        src.to_excel(out / "Employe_Source_synthetique.xlsx", sheet_name="Employe_Source", index=False)
        dst.to_excel(out / "Employe_Destination_synthetique.xlsx", sheet_name="Employe_Destination", index=False)
    detail.to_excel(out / "detail_du_poste_synthetique.xlsx", sheet_name="Feuil1", index=False)
    motif.to_excel(out / "Motif_situation_emploi_synthetique.xlsx", sheet_name="Sheet1", index=False)
    gt.to_csv(out / "ground_truth.csv", index=False, encoding="utf-8")
    meta = {"generated_at": datetime.now().isoformat(timespec="seconds"), "n_employees": n_employees, "seed": seed,
            "n_source_assignments": len(src), "n_destination_rows": len(dst), "rates": gen.rates,
            "categories": gt["category"].value_counts().to_dict() if len(gt) else {},
            "mapping": "data/Mapping.xlsx (officiel, lecture seule)",
            "simulated_expert_policy": ("affectation manquante A → ANOMALIE ; S → ECART_JUSTIFIE ; heures source "
                                        "manquantes → ANOMALIE ; doublon ambigu → ANOMALIE (simulation)")}
    (out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return SyntheticBundle(out, gt, meta)


# ======================================================================
# Evaluation
# ======================================================================
def verdict_class(v: str) -> str:
    return "OK" if v in (C.CONFORME, C.ECART_JUSTIFIE) else v


def evaluate(cases: pd.DataFrame, ground_truth: pd.DataFrame) -> dict:
    """Compare engine verdicts with injected ground truth.

    Cases without an injected label are expected to be OK (CONFORME or ECART_JUSTIFIE).
    Classes: OK / ANOMALIE / A_INVESTIGUER.
    """
    gt = ground_truth.copy()
    gt["person_id"] = gt["person_id"].astype(str)
    gt["src_poste"] = gt["src_poste"].astype(str)
    df = cases[["case_id", "person_id", "src_poste", "field", "verdict", "deterministic_verdict", "pattern_id",
                "systemic"]].copy()
    df = df.merge(gt, on=["person_id", "src_poste", "field"], how="left")
    df["category"] = df["category"].fillna("baseline")
    df["expected_class"] = df["expected_verdict"].map(lambda v: verdict_class(v) if isinstance(v, str) else "OK")
    df["predicted_class"] = df["verdict"].map(verdict_class)
    labels = ["OK", C.ANOMALIE, C.A_INVESTIGUER]
    cm = pd.crosstab(df["expected_class"], df["predicted_class"]).reindex(index=labels, columns=labels, fill_value=0)
    metrics = {}
    for lab in labels:
        tp = cm.loc[lab, lab]
        prec = tp / cm[lab].sum() if cm[lab].sum() else float("nan")
        rec = tp / cm.loc[lab].sum() if cm.loc[lab].sum() else float("nan")
        f1 = 2 * prec * rec / (prec + rec) if prec and rec and not np.isnan(prec + rec) else float("nan")
        metrics[lab] = {"precision": round(float(prec), 4), "recall": round(float(rec), 4), "f1": round(float(f1), 4),
                        "support": int(cm.loc[lab].sum())}
    injected = df[df["category"] != "baseline"]
    per_cat = injected.groupby("category")[["expected_class", "predicted_class", "expected_verdict", "verdict",
                                            "pattern_id"]].apply(lambda g: pd.Series({
        "n": len(g),
        "classe_correcte": round(float((g["expected_class"] == g["predicted_class"]).mean()), 4),
        "verdict_exact": round(float((g["expected_verdict"] == g["verdict"]).mean()), 4),
        "regroupe_en_motif": round(float((g["pattern_id"] != "").mean()), 4),
    })).reset_index()
    missing_gt = gt.merge(df[["person_id", "src_poste", "field"]].drop_duplicates(),
                          on=["person_id", "src_poste", "field"], how="left", indicator=True)
    return {
        "confusion_matrix": cm,
        "metrics": metrics,
        "per_category": per_cat,
        "accuracy_class": round(float((df["expected_class"] == df["predicted_class"]).mean()), 4),
        "n_cases": len(df),
        "ground_truth_not_found": int((missing_gt["_merge"] == "left_only").sum()),
        "errors": df[df["expected_class"] != df["predicted_class"]],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Générateur de données synthétiques CorroborAI")
    p.add_argument("--n", type=int, default=1000, help="nombre d'employés (ex. 1000 / 5000 / 10000)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=None)
    p.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    p.add_argument("--evaluate", action="store_true", help="exécuter le moteur et évaluer contre la vérité terrain")
    a = p.parse_args(argv)
    bundle = generate(a.n, a.seed, a.out, fmt=a.format)
    print(f"Données synthétiques : {bundle.directory}")
    print(json.dumps(bundle.metadata["categories"], ensure_ascii=False, indent=1))
    if a.evaluate:
        from .corroboration import run_corroboration
        res = run_corroboration(bundle.directory, use_decisions=False)
        ev = evaluate(res.cases, bundle.ground_truth)
        print(ev["confusion_matrix"])
        print(json.dumps(ev["metrics"], indent=1))
        print(ev["per_category"].to_string(index=False))
        print("vérité terrain non retrouvée :", ev["ground_truth_not_found"])


if __name__ == "__main__":
    main()
