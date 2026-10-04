"""Level 2: deterministic business rules.

Each rule is bound to destination field(s) listed in Mapping.xlsx and keeps a
reference to the mapping rows it implements. A rule does two things:

* ``derive``  : compute the expected destination value from the source row and
                the joined extracts (détail du poste, motifs, situation table);
* ``compare`` : compare the destination value with that expectation and return
                MATCH / MATCH_JUSTIFIED / MISMATCH / UNDETERMINED.

Rules never guess: when an input is missing, a code is undocumented or the
documentation is ambiguous, the outcome is UNDETERMINED (-> A_INVESTIGUER).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

import pandas as pd

from .config import CERTAIN, DERIVED, INFERRED, EngineConfig
from .mapping import (MappingEntry, parse_contract_rules, parse_mapping,
                      parse_situation_rules, KEY_FIELDS)
from .normalize import (Unparseable, canon_key, display, is_blank, norm_bool,
                        norm_date, norm_id, norm_number, norm_text, normalize,
                        strip_accents, values_equal)

MATCH = "MATCH"
MATCH_JUSTIFIED = "MATCH_JUSTIFIE"
MISMATCH = "MISMATCH"
UNDETERMINED = "INDETERMINE"

AMB_START = "AMB-01"   # assignmentStartDate wording — resolved by official clarification (2026-10-04)
# Détail du poste columns compared to detect a change of record (date and row excluded)
DETAIL_VALUE_COLUMNS = ["IdentifiantEmploi", "CodeDirectionAffectée", "CodeBudget", "IndicateurGestion",
                        "CodePosteSecondaire", "MatriculeGestionnaire", "HeuresSemaineContrat",
                        "HeuresJourContrat", "JoursTravailléesSemaine"]

ASSIGNMENT_TYPES = {"P": (True, False), "A": (False, True), "S": (False, False)}

KIND_BY_FIELD = {
    "givenName": "name", "surname": "name", "onboardDate": "date", "siteName": "text",
    "siteCode": "id", "divisionId": "id", "divisionCode": "id", "positionId": "id",
    "positionCode": "id", "payGradeId": "id", "weeklyHoursOverride": "number",
    "dailyHoursOverride": "number", "contactEmail": "email", "divisionName": "text",
    "positionName": "text", "statusReasonCode": "id", "expectedReturnDate": "date",
    "detailedStatus": "text", "contractTypeCode": "text", "isPrimaryAssignment": "bool",
    "isTemporaryAssignment": "bool", "assignmentStartDate": "date",
    "assignmentEndDate": "date", "termEndDate": "date",
}
# Contextual value from détail du poste used to explain a missing/different value
POSTE_DEFAULTS = {"weeklyHoursOverride": "heures_semaine_poste",
                  "dailyHoursOverride": "heures_jour_poste"}


def infer_kind(dest_field: str, source_col: str | None) -> str:
    if dest_field in KIND_BY_FIELD:
        return KIND_BY_FIELD[dest_field]
    key = canon_key(source_col or dest_field)
    if "date" in key:
        return "date"
    if key.startswith(("code", "matricule", "identifiant", "echelle")) or key.endswith("id"):
        return "id"
    if "heure" in key or "hours" in key:
        return "number"
    return "text"


@dataclass
class Derivation:
    expected: Any = None
    kind: str = "text"
    ok: bool = True                      # False: the rule cannot be applied
    certainty: str = DERIVED
    inputs: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    explanation: str = ""
    alt_expected: Any = None
    alt_label: str = ""
    ambiguity_id: str | None = None
    issue: str = ""
    expected_display: str | None = None

    def shown(self) -> str:
        return self.expected_display if self.expected_display is not None else display(self.expected)


@dataclass
class Outcome:
    status: str
    note: str = ""
    hypothesis: str | None = None        # machine-readable hint for level 3


@dataclass
class Rule:
    rule_id: str
    name: str
    derive: Callable
    compare: Callable
    raw_source_field: str | None
    is_direct: bool = False


@dataclass
class FieldSpec:
    dest_field: str
    entry: MappingEntry
    rule: Rule
    kind: str


# --------------------------------------------------------------------------
# Détail du poste: current record
# --------------------------------------------------------------------------
def current_record_effective(history: list) -> dict:
    """Effective date of the *current* détail-du-poste record: walk back from the
    most recent record while it is identical to the previous one ("changement de
    valeur par rapport à l'enregistrement précédent"); if no record ever differs,
    MIN(EFFDT). This reading reproduces every destination assignmentStartDate of
    the official sample and is confirmed by the challenge team's clarification."""
    out = {"date": None, "index": None, "excel_row": None, "changed": [], "method": ""}
    if not history:
        out["method"] = "aucun historique 'détail du poste'"
        return out
    i = len(history) - 1
    while i > 0 and history[i]["values"] == history[i - 1]["values"]:
        i -= 1
    out.update(date=history[i]["date"], index=i, excel_row=history[i]["excel_row"])
    if i == 0:
        out["method"] = ("aucun changement dans l'historique → MIN(EFFDT)" if len(history) > 1
                         else "enregistrement unique du poste")
    else:
        prev, cur = history[i - 1]["values"], history[i]["values"]
        out["changed"] = [c for c, a, b in zip(DETAIL_VALUE_COLUMNS, cur, prev) if a != b]
        out["method"] = (f"enregistrement courant (ligne {history[i]['excel_row']}) différent du précédent "
                         f"(ligne {history[i - 1]['excel_row']}) sur : {', '.join(out['changed'])}")
    return out


# --------------------------------------------------------------------------
# Unit (unité administrative) history analysis — détail du poste
# --------------------------------------------------------------------------
def analyse_unit_history(history: list, current_unit: str | None) -> dict:
    """Apply the mapping text for 'Date d'effet de l'unité administrative':
    the effective date at which the *current* admin unit became applicable for
    the poste, detected as a change of value versus the previous record; if
    the history never changes unit, MIN(EFFDT). Also computes the end of the
    current unit (next record with a different unit, minus one day)."""
    out = {"unit_effective_date": None, "method": "", "change_index": None,
           "unit_end_date": None, "min_effdt": None, "max_effdt": None,
           "units": [], "n_records": len(history), "issue": ""}
    if not history:
        out["issue"] = "Aucun historique 'détail du poste' pour ce poste"
        return out
    units = [h["unit"] for h in history]
    dates = [h["date"] for h in history]
    out.update(units=units, min_effdt=min(dates), max_effdt=max(dates))
    if current_unit is None:
        out["issue"] = "CodeDirection source vide"
        return out
    if current_unit not in units:
        out["issue"] = (f"L'unité administrative courante {current_unit} n'apparaît pas dans "
                        f"l'historique du poste ({sorted(set(units))})")
        return out
    if len(set(units)) == 1:
        idx = 0
        out["unit_effective_date"] = min(dates)
        out["method"] = "aucun changement d'unité dans l'historique → MIN(EFFDT)"
    else:
        idx = max(i for i, u in enumerate(units)
                  if u == current_unit and (i == 0 or units[i - 1] != current_unit))
        out["unit_effective_date"] = dates[idx]
        prev = units[idx - 1] if idx > 0 else None
        out["method"] = (f"changement d'unité détecté {prev} → {current_unit} le {dates[idx]}"
                         if prev is not None else "unité courante présente dès le premier enregistrement")
    out["change_index"] = idx
    for j in range(idx + 1, len(units)):
        if units[j] != current_unit:
            out["unit_end_date"] = dates[j] - timedelta(days=1)
            out["method"] += f" ; unité modifiée ensuite le {dates[j]} (fin = veille)"
            break
    return out


class RuleEngine:
    """Binds Mapping.xlsx entries to rule implementations and evaluates them."""

    def __init__(self, ds, config: EngineConfig):
        self.ds = ds
        self.cfg = config
        self.entries = parse_mapping(ds.mapping_sheets, list(ds.source.columns))
        self.situation_rules = parse_situation_rules(ds.mapping_sheets)
        contract_entry = next((e for e in self.entries if "contractTypeCode" in e.dest_fields), None)
        self.contract_entry = contract_entry
        self.contract_rules = parse_contract_rules(contract_entry.rule_text) if contract_entry else []
        self._build_motif()
        self._build_history()
        self.rules = self._build_rules()
        self.field_specs = self._build_field_specs()
        self._ctx_cache: dict = {}

    # ---------------------------------------------------------------- joins
    def _build_motif(self):
        self.motif = {}
        self.motif_duplicates = set()
        m = self.ds.motif
        for _, r in m.iterrows():
            code = norm_id(r.get("CodeCatégorieStatut"))
            if code is None:
                continue
            if code in self.motif:
                self.motif_duplicates.add(code)
            self.motif[code] = {"remphor": norm_id(r.get("CodeStatutSystèmeExterne")),
                                "access": norm_number(r.get("CodeGestionAccès")),
                                "excel_row": int(r["_excel_row"])}

    def _build_history(self):
        self.history = {}
        jd = self.ds.job_detail
        for _, r in jd.iterrows():
            poste = norm_id(r.get("IdentifiantPoste"))
            d = r.get("DateEffet")
            if poste is None or d is None or isinstance(d, Unparseable):
                continue
            self.history.setdefault(poste, []).append({
                "date": d, "unit": norm_id(r.get("CodeDirectionAffectée")),
                "emploi": norm_id(r.get("IdentifiantEmploi")),
                "hours_week": norm_number(r.get("HeuresSemaineContrat")),
                "hours_day": norm_number(r.get("HeuresJourContrat")),
                "values": tuple(norm_id(r.get(c)) for c in DETAIL_VALUE_COLUMNS),
                "excel_row": int(r["_excel_row"])})
        for h in self.history.values():
            h.sort(key=lambda x: (x["date"], x["excel_row"]))

    # ---------------------------------------------------------------- context
    def context(self, src_idx: int) -> dict:
        if src_idx in self._ctx_cache:
            return self._ctx_cache[src_idx]
        src = self.ds.source.loc[src_idx]
        poste = norm_id(src.get("CodePoste"))
        hist = self.history.get(poste, [])
        unit = analyse_unit_history(hist, norm_id(src.get("CodeDirection")))
        latest = hist[-1] if hist else {}
        ctx = {"src": src, "src_idx": src_idx, "poste": poste, "history": hist, "unit": unit,
               "current_record": current_record_effective(hist),
               "poste_context": {
                   "heures_semaine_poste": latest.get("hours_week"),
                   "heures_jour_poste": latest.get("hours_day"),
                   "emploi_detail_poste": latest.get("emploi"),
                   "EFFDT_min": unit["min_effdt"], "EFFDT_max": unit["max_effdt"],
                   "lignes_detail_poste": [h["excel_row"] for h in hist],
               }}
        ctx["situation"] = self._situation(src)
        self._ctx_cache[src_idx] = ctx
        return ctx

    def expected_start_candidates(self, src_idx: int) -> set:
        """Dates accepted as 'start date evidence' when matching assignments."""
        ctx = self.context(src_idx)
        d = self._derive_start(ctx, "assignmentStartDate")
        raw = norm_date(ctx["src"].get("DateEntréePoste"))
        return {x for x in (d.expected, d.alt_expected, raw) if x is not None and not isinstance(x, Unparseable)}

    # ---------------------------------------------------------------- registry
    def _build_rules(self) -> dict:
        r = {}
        r["contactEmail"] = Rule("R_EMAIL", "Courriel : initiale prénom + nom + 3 derniers chiffres du matricule + @domaine, sans accents",
                                 self._derive_email, self._compare_email, None)
        r["divisionName"] = Rule("R_CONCAT_DIVISION", "Concaténation CodeDirection + '-' + LibelléDirection",
                                 self._derive_concat("CodeDirection", "LibelléDirection"), self._compare_concat, "LibelléDirection")
        r["positionName"] = Rule("R_CONCAT_POSITION", "Concaténation CodeEmploi + '-' + IntituléEmploi",
                                 self._derive_concat("CodeEmploi", "IntituléEmploi"), self._compare_concat, "IntituléEmploi")
        for f, raw in (("detailedStatus", None), ("statusReasonCode", "CodeRaisonStatut"),
                       ("expectedReturnDate", "DateRetourAnticipée")):
            r[f] = Rule("R_SITUATION_EMPLOI", "Table 'Règles situation d'emploi' + jointure motifs",
                        self._derive_situation_field, self._compare_default, raw)
        r["contractTypeCode"] = Rule("R_CONTRACT_TYPE", "Type d'employé : table SI PERM_IND / FT_IND / EMPTP_CD",
                                     self._derive_contract, self._compare_default, "CatégorieEmploi")
        for f in ("isPrimaryAssignment", "isTemporaryAssignment"):
            r[f] = Rule("R_ASSIGNMENT_TYPE", "TypeAffectation P/A/S → indicateurs primaire/temporaire",
                        self._derive_assignment_type, self._compare_default, "TypeAffectation")
        r["assignmentStartDate"] = Rule("R_ASSIGNMENT_START", "Date d'effet : DateEntréePoste vs date d'effet de l'unité adm. (détail du poste)",
                                        self._derive_start, self._compare_start, "DateEntréePoste")
        for f in ("assignmentEndDate", "termEndDate"):
            r[f] = Rule("R_ASSIGNMENT_END", "Date de fin : MIN(DateSortiePoste, fin de l'unité adm. courante)",
                        self._derive_end, self._compare_end, "DateSortiePoste")
        return r

    def _build_field_specs(self) -> list[FieldSpec]:
        specs = []
        dest_cols = set(self.ds.destination.columns)
        for e in self.entries:
            for f in e.dest_fields:
                if f in KEY_FIELDS:
                    continue
                if f not in dest_cols:
                    continue
                rule = self.rules.get(f)
                src_col = e.source_columns[0] if e.source_columns else None
                if rule is None:
                    if e.is_direct and src_col:
                        rule = Rule("R_DIRECT", "Copie directe (mapping N/A)", self._derive_direct,
                                    self._compare_direct, src_col, is_direct=True)
                    else:
                        rule = Rule("R_NON_IMPLEMENTEE", "Règle du mapping non implémentée : revue humaine",
                                    self._derive_unsupported, self._compare_unsupported, src_col)
                specs.append(FieldSpec(f, e, rule, infer_kind(f, src_col)))
        return specs

    def missing_dest_fields(self) -> list[str]:
        dest_cols = set(self.ds.destination.columns)
        return [f for e in self.entries for f in e.dest_fields if f not in dest_cols and f not in KEY_FIELDS]

    # ---------------------------------------------------------------- evaluate
    def evaluate(self, spec: FieldSpec, src_idx: int, dest_value) -> tuple[Derivation, Outcome]:
        ctx = self.context(src_idx)
        deriv = spec.rule.derive(ctx, spec.dest_field, spec)
        if not deriv.ok:
            outcome = Outcome(UNDETERMINED, deriv.issue)
            # a rule that cannot be applied is still informative when dest is blank/equal
            return deriv, outcome
        return deriv, spec.rule.compare(deriv, dest_value, ctx, spec)

    # ================================================================ rules
    def _src(self, ctx, col):
        return ctx["src"].get(col)

    # --- direct ------------------------------------------------------------
    def _derive_direct(self, ctx, f, spec):
        col = spec.rule.raw_source_field
        val = self._src(ctx, col)
        context = {}
        if f in POSTE_DEFAULTS:
            context[POSTE_DEFAULTS[f]] = ctx["poste_context"][POSTE_DEFAULTS[f]]
            if "CodeQuart" in ctx["src"].index:
                context["CodeQuart"] = display(self._src(ctx, "CodeQuart"))
        return Derivation(expected=val, kind=spec.kind, certainty=CERTAIN, inputs={col: display(val)},
                          context=context, explanation=f"copie directe de {col}")

    def _compare_direct(self, deriv, dest_value, ctx, spec):
        s_blank, d_blank = is_blank(deriv.expected), is_blank(dest_value)
        if s_blank and d_blank:
            return Outcome(MATCH)
        if s_blank:
            hyp = self._context_equality(deriv.context, dest_value, spec.kind)
            note = f"valeur source absente alors que la destination contient '{display(dest_value)}'"
            if hyp:
                note += f" (égale à {hyp} du détail du poste)"
            return Outcome(UNDETERMINED, note, hypothesis=f"DEST_EQUALS:{hyp}" if hyp else "SOURCE_MANQUANTE")
        if d_blank:
            return Outcome(MISMATCH, "valeur non transmise à la destination", hypothesis="DESTINATION_MANQUANTE")
        if values_equal(deriv.expected, dest_value, spec.kind):
            return Outcome(MATCH)
        hyp = self._context_equality(deriv.context, dest_value, spec.kind)
        return Outcome(MISMATCH, f"valeur destination égale à {hyp}" if hyp else "",
                       hypothesis=f"DEST_EQUALS:{hyp}" if hyp else None)

    @staticmethod
    def _context_equality(context: dict, dest_value, kind) -> str | None:
        for k, v in (context or {}).items():
            if v is None or isinstance(v, (list, dict)):
                continue
            if values_equal(v, dest_value, kind):
                return k
        return None

    def _compare_default(self, deriv, dest_value, ctx, spec):
        if values_equal(deriv.expected, dest_value, spec.kind):
            return Outcome(MATCH)
        if is_blank(dest_value):
            return Outcome(MISMATCH, "valeur attendue non transmise à la destination", hypothesis="DESTINATION_MANQUANTE")
        return Outcome(MISMATCH)

    # --- unsupported ---------------------------------------------------------
    def _derive_unsupported(self, ctx, f, spec):
        col = spec.rule.raw_source_field
        return Derivation(expected=self._src(ctx, col) if col else None, kind=spec.kind, ok=False,
                          certainty=INFERRED, issue="règle du mapping non implémentée dans V1")

    def _compare_unsupported(self, deriv, dest_value, ctx, spec):
        return Outcome(UNDETERMINED, deriv.issue)

    # --- email ---------------------------------------------------------------
    def _derive_email(self, ctx, f, spec):
        given, surname, mat = (self._src(ctx, c) for c in ("PrénomUsuel", "NomFamille", "Matricule"))
        inputs = {"PrénomUsuel": display(given), "NomFamille": display(surname), "Matricule": display(mat)}
        if any(is_blank(v) for v in (given, surname, mat)):
            return Derivation(kind="email", ok=False, inputs=inputs,
                              issue="prénom, nom ou matricule absent : courriel non dérivable")
        digits = re.sub(r"\D", "", display(mat))
        g = strip_accents(str(given).strip())
        s = strip_accents(str(surname).strip())
        certainty = DERIVED
        note = ""
        if re.search(r"[\s'\-]", s + g):
            certainty = INFERRED
            note = " ; nom/prénom composé : traitement des espaces/traits d'union non documenté"
        local = g[:1] + re.sub(r"\s+", "", s) + digits[-3:]
        expected = f"{local}@{self.cfg.email_domain}"
        return Derivation(expected=expected, kind="email", certainty=certainty, inputs=inputs,
                          explanation=f"initiale '{g[:1]}' + nom '{s}' + '{digits[-3:]}' + '@{self.cfg.email_domain}' (accents retirés){note}")

    def _compare_email(self, deriv, dest_value, ctx, spec):
        if is_blank(dest_value):
            return Outcome(MISMATCH, "courriel absent à la destination", hypothesis="DESTINATION_MANQUANTE")
        d = str(dest_value).strip().lower()
        d = strip_accents(d)
        prefix = ""
        m = re.match(self.cfg.email_allowed_prefix_regex, d)
        if m and d[m.end():] and "@" in d[m.end():]:
            prefix, d = m.group(0), d[m.end():]
        expected = deriv.expected.lower()
        if d == expected:
            if prefix:
                return Outcome(MATCH_JUSTIFIED, f"préfixe d'environnement '{prefix}' ignoré (autorisé côté destination)")
            return Outcome(MATCH)
        if "@" not in d:
            return Outcome(MISMATCH, "adresse destination invalide (pas de '@')")
        local, domain = d.rsplit("@", 1)
        exp_local = expected.split("@")[0]
        if domain != self.cfg.email_domain:
            return Outcome(MISMATCH, f"domaine '{domain}' différent de '{self.cfg.email_domain}'")
        if local[:1] != exp_local[:1]:
            return Outcome(MISMATCH, "l'initiale du prénom ne correspond pas")
        # Structure check: initial + identity token + last 3 digits of the code embedded in the token
        tail, body = local[-3:], local[1:-3]
        if tail.isdigit() and body.endswith(tail) and re.search(r"\d", body):
            p = f" ; préfixe '{prefix}' ignoré" if prefix else ""
            return Outcome(MISMATCH,
                           "structure conforme à la règle (initiale + nom + 3 derniers chiffres du code), mais "
                           f"l'identifiant '{body}' ne correspond pas au Matricule/personID (attendu "
                           f"'{exp_local[1:-3]}'){p} ; profil caractéristique d'un artefact d'anonymisation",
                           hypothesis="ANONYMISATION")
        return Outcome(MISMATCH, "structure du courriel non conforme à la règle")

    # --- concatenation ---------------------------------------------------------
    def _derive_concat(self, code_col, label_col):
        def derive(ctx, f, spec):
            code, label = self._src(ctx, code_col), self._src(ctx, label_col)
            inputs = {code_col: display(code), label_col: display(label)}
            if is_blank(code) or is_blank(label):
                return Derivation(kind="text", ok=False, inputs=inputs,
                                  issue=f"{code_col} ou {label_col} absent : concaténation impossible")
            return Derivation(expected=(norm_id(code), norm_text(label)), kind="text", inputs=inputs,
                              expected_display=f"{display(code)}-{display(label)}",
                              explanation=f"{code_col} + '-' + {label_col}")
        return derive

    def _compare_concat(self, deriv, dest_value, ctx, spec):
        if is_blank(dest_value):
            return Outcome(MISMATCH, "valeur absente à la destination", hypothesis="DESTINATION_MANQUANTE")
        text = str(dest_value).strip()
        if "-" not in text:
            return Outcome(MISMATCH, "la valeur destination n'a pas le format 'code-libellé'")
        left, right = text.split("-", 1)
        exp_code, exp_label = deriv.expected
        code_ok = norm_id(left) == exp_code
        label_ok = norm_text(right) == exp_label
        deriv.context["code_destination"] = left.strip()
        deriv.context["libelle_destination"] = right.strip()
        if code_ok and label_ok:
            note = "zéros non significatifs du code ignorés" if left.strip() != exp_code else ""
            return Outcome(MATCH, note)
        parts = [p for p, ok in (("code", code_ok), ("libellé", label_ok)) if not ok]
        return Outcome(MISMATCH, f"{' et '.join(parts)} différent(s) de la concaténation attendue",
                       hypothesis="SUBSTITUTION_CODE" if not code_ok else None)

    # --- employment situation ---------------------------------------------------
    def _find_situation_rule(self, code):
        if code is None:
            return None
        for rule in self.situation_rules:
            if int(code) in rule.access_codes:
                return rule
        return None

    def _situation(self, src) -> dict:
        access = norm_number(src.get("CodeSuspensionAccès"))
        statut = norm_number(src.get("CodeStatutEmploi"))
        reason = norm_id(src.get("CodeRaisonStatut"))
        motif = self.motif.get(reason)
        rule = self._find_situation_rule(access) if isinstance(access, int) else None
        issues = []
        if access is None:
            issues.append("CodeSuspensionAccès (code de traitement des accès) vide")
        elif rule is None:
            issues.append(f"code de traitement des accès {access} non couvert par la table 'Règles situation d'emploi'")
        if rule is not None and isinstance(statut, int):
            rule_statut = self._find_situation_rule(statut)
            if rule_statut is not None and rule_statut is not rule:
                issues.append(f"CodeStatutEmploi={statut} et CodeSuspensionAccès={access} mènent à des situations différentes")
        if motif is not None and access is not None and motif["access"] is not None and motif["access"] != access:
            issues.append(f"le motif {reason} porte le code d'accès {motif['access']} (fichier motifs, ligne "
                          f"{motif['excel_row']}) ≠ CodeSuspensionAccès {access}")
        if reason in self.motif_duplicates:
            issues.append(f"motif {reason} présent plusieurs fois dans le fichier motifs")
        return {"access": access, "statut": statut, "reason": reason, "motif": motif, "rule": rule,
                "issues": issues,
                "inputs": {"CodeSuspensionAccès": display(src.get("CodeSuspensionAccès")),
                           "CodeStatutEmploi": display(src.get("CodeStatutEmploi")),
                           "CodeRaisonStatut": display(src.get("CodeRaisonStatut")),
                           "DateEffetRaison": display(src.get("DateEffetRaison")),
                           "DateRetourAnticipée": display(src.get("DateRetourAnticipée"))}}

    def _derive_situation_field(self, ctx, f, spec):
        sit = ctx["situation"]
        rule = sit["rule"]
        context = {"motif_trouve": sit["motif"] is not None}
        if sit["motif"]:
            context.update({"code_Remphor": sit["motif"]["remphor"], "acces_motif": sit["motif"]["access"],
                            "ligne_motif": sit["motif"]["excel_row"]})
        base = dict(kind=spec.kind, inputs=sit["inputs"], context=context)
        if sit["issues"]:
            return Derivation(ok=False, certainty=INFERRED, issue=" ; ".join(sit["issues"]), **base)
        sit_label = f"situation '{rule.specific_status}' (codes d'accès {sorted(rule.access_codes)}, ligne {rule.excel_row})"
        if f == "detailedStatus":
            return Derivation(expected=rule.specific_status, explanation=sit_label, **base)
        if f == "statusReasonCode":
            if rule.reason_code_rule == "NULL":
                return Derivation(expected=None, explanation=f"{sit_label} → cf_CAD = null", **base)
            if sit["motif"] is None:
                return Derivation(ok=False, issue=f"{sit_label} exige le code Remphor du motif {sit['reason']}, "
                                  "absent du fichier motifs", **base)
            return Derivation(expected=sit["motif"]["remphor"],
                              explanation=f"{sit_label} → code Remphor du motif {sit['reason']} = {sit['motif']['remphor']}",
                              **base)
        if f == "expectedReturnDate":
            if rule.return_date_rule == "NULL":
                return Derivation(expected=None, explanation=f"{sit_label} → cf_CADP = null", **base)
            d = norm_date(ctx["src"].get("DateRetourAnticipée"))
            return Derivation(expected=d, explanation=f"{sit_label} → cf_CADP = DateRetourAnticipée", **base)
        return Derivation(ok=False, issue="champ de situation inconnu", **base)

    # --- contract type ------------------------------------------------------------
    def _derive_contract(self, ctx, f, spec):
        src = ctx["src"]
        cat = norm_text(src.get("CatégorieEmploi"), casefold=False)
        perm, ft = norm_bool(src.get("EstPermanent")), norm_bool(src.get("EstTempsPlein"))
        values = {"CatégorieEmploi": cat.upper() if cat else None,
                  "EstPermanent": None if perm is None or isinstance(perm, Unparseable) else str(int(perm)),
                  "EstTempsPlein": None if ft is None or isinstance(ft, Unparseable) else str(int(ft))}
        inputs = {"CatégorieEmploi": display(src.get("CatégorieEmploi")),
                  "EstPermanent": f"{display(src.get('EstPermanent'))} ({values['EstPermanent']})",
                  "EstTempsPlein": f"{display(src.get('EstTempsPlein'))} ({values['EstTempsPlein']})"}
        for rule in self.contract_rules:
            if all(values.get(col) == val for col, val in rule.conditions.items()):
                return Derivation(expected=rule.result, kind="text", inputs=inputs,
                                  explanation=f"ligne de règle appliquée : {rule.line}")
        return Derivation(kind="text", ok=False, inputs=inputs, certainty=INFERRED,
                          issue=f"combinaison {values} non couverte par la table du mapping")

    # --- assignment type ------------------------------------------------------------
    def _derive_assignment_type(self, ctx, f, spec):
        t = norm_text(ctx["src"].get("TypeAffectation"), casefold=False)
        t = t.upper() if t else None
        inputs = {"TypeAffectation": display(ctx["src"].get("TypeAffectation"))}
        if t not in ASSIGNMENT_TYPES:
            return Derivation(kind="bool", ok=False, inputs=inputs,
                              issue=f"TypeAffectation '{t}' non documenté (P/A/S attendus)")
        prim, temp = ASSIGNMENT_TYPES[t]
        return Derivation(expected=prim if f == "isPrimaryAssignment" else temp, kind="bool", inputs=inputs,
                          explanation=f"Type aff = {t} → primaire={str(prim).lower()}, temporaire={str(temp).lower()}")

    # --- assignment start ---------------------------------------------------------------
    def _derive_start(self, ctx, f, spec=None):
        """assignmentStartDate = MAX(DateEntréePoste, effective date of the current détail-du-poste record).
        The source only holds the job effective date; the destination applies the documented transformation
        with the job history (official clarification 2026-10-04, see AMB-01)."""
        src, unit, cur = ctx["src"], ctx["unit"], ctx["current_record"]
        entry = norm_date(src.get("DateEntréePoste"))
        entry = None if isinstance(entry, Unparseable) else entry
        eff = cur["date"]
        hist = ctx["history"]
        inputs = {"DateEntréePoste": display(entry), "CodePoste": display(src.get("CodePoste")),
                  "CodeDirection": display(src.get("CodeDirection"))}
        context = {"date_effet_detail_courant": display(eff), "ligne_detail_courant": cur["excel_row"],
                   "methode_detail_poste": cur["method"],
                   "historique": " → ".join(f"{display(h['date'])} (unité {h['unit']})" for h in hist[-6:]),
                   "EFFDT_min": display(unit["min_effdt"]), "EFFDT_max": display(unit["max_effdt"]),
                   "lignes_detail_poste": ctx["poste_context"]["lignes_detail_poste"]}
        if eff is None:
            return Derivation(expected=entry, kind="date", ok=False, certainty=INFERRED, inputs=inputs,
                              context=context, issue="aucun historique 'détail du poste' : transformation non calculable")
        cur_unit = norm_id(src.get("CodeDirection"))
        if cur_unit is not None and hist[cur["index"]]["unit"] != cur_unit:
            return Derivation(expected=None, kind="date", ok=False, certainty=INFERRED, inputs=inputs, context=context,
                              issue=(f"l'unité adm. de l'enregistrement courant du détail du poste "
                                     f"({hist[cur['index']]['unit']}) diffère du CodeDirection source ({cur_unit})"))
        if entry is None:
            return Derivation(expected=eff, kind="date", certainty=INFERRED, inputs=inputs, context=context,
                              explanation="DateEntréePoste absente : seule la date du détail du poste est disponible")
        exp = max(entry, eff)
        context["valeur_lecture_litterale_MIN"] = display(min(entry, eff))
        source_part = "date d'effet du poste (source)" if exp == entry else "date du détail du poste courant"
        return Derivation(expected=exp, kind="date", certainty=DERIVED, inputs=inputs, context=context,
                          explanation=(f"MAX(DateEntréePoste {entry}, date d'effet du détail du poste courant {eff}) = "
                                       f"{exp} → {source_part} [{cur['method']}]"))

    def _compare_start(self, deriv, dest_value, ctx, spec):
        d = norm_date(dest_value)
        if d == deriv.expected:
            return Outcome(MATCH)
        entry = norm_date(ctx["src"].get("DateEntréePoste"))
        unit = ctx["unit"]
        if d is not None and d == entry:
            return Outcome(MISMATCH, "la destination reprend la date source sans appliquer la transformation "
                           "(historique du détail du poste ignoré)", hypothesis="DEST_EQUALS:DateEntréePoste")
        if d is not None and d == unit["min_effdt"]:
            return Outcome(MISMATCH, "égale à la plus ancienne date d'effet du détail du poste",
                           hypothesis="DEST_EQUALS:EFFDT_min")
        return Outcome(MISMATCH, "ne correspond pas à la valeur transformée attendue")

    # --- assignment end -------------------------------------------------------------------
    def _derive_end(self, ctx, f, spec):
        src, unit = ctx["src"], ctx["unit"]
        exit_d = norm_date(src.get("DateSortiePoste"))
        exit_d = None if isinstance(exit_d, Unparseable) else exit_d
        unit_end = unit["unit_end_date"]
        inputs = {"DateSortiePoste": display(exit_d), "CodePoste": display(src.get("CodePoste"))}
        context = {"fin_unite_adm": display(unit_end), "methode_unite_adm": unit["method"] or unit["issue"]}
        cands = [x for x in (exit_d, unit_end) if x is not None]
        expected = min(cands) if cands else None
        certainty = DERIVED if not unit["issue"] else INFERRED
        if unit["issue"]:
            context["limite"] = unit["issue"]
        return Derivation(expected=expected, kind="date", certainty=certainty, inputs=inputs, context=context,
                          explanation=(f"MIN(DateSortiePoste={display(exit_d) or 'NULL'}, fin unité adm.="
                                       f"{display(unit_end) or 'NULL'}) = {display(expected) or 'NULL'}"))

    def _compare_end(self, deriv, dest_value, ctx, spec):
        if values_equal(deriv.expected, dest_value, "date"):
            return Outcome(MATCH)
        if deriv.certainty == INFERRED:
            return Outcome(UNDETERMINED, "historique du poste incomplet : fin d'unité adm. non vérifiable")
        if is_blank(dest_value):
            return Outcome(MISMATCH, "date de fin attendue non transmise", hypothesis="DESTINATION_MANQUANTE")
        return Outcome(MISMATCH)

    # ================================================================ proposals
    def derive_all(self, src_idx: int) -> dict:
        """Expected value for every corroborated destination field (used for
        proposed corrections, e.g. a missing destination assignment)."""
        out = {}
        for spec in self.field_specs:
            ctx = self.context(src_idx)
            out[spec.dest_field] = (spec, spec.rule.derive(ctx, spec.dest_field, spec))
        return out
