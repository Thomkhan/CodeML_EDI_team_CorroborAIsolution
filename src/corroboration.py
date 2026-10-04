"""Corroboration engine: orchestrates the three-level pipeline.

    Level 1  raw comparison / normalisation        (normalize.py)
    Level 2  deterministic business rules           (rules.py, matching.py)
    Level 3  assisted analysis of what remains      (ai_assist.py)

Produces one traceable *case* per (matched assignment x mapped field), plus
assignment-level cases (missing / unexpected / ambiguous assignments).
Official input files are only read; results live in memory or in outputs/.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config as C
from .config import EngineConfig
from .load_data import Dataset, load_all
from .mapping import mapping_table
from .matching import (AMBIGUOUS, DEST_ONLY, MATCHED, SOURCE_ONLY, FEATURE_LABELS,
                       match_assignments, matches_table)
from .normalize import (BOTH_EMPTY, DIFFERENT, IDENTICAL, IDENTICAL_NORMALIZED,
                        NOT_COMPARABLE, compare_values, display, fix_mojibake, has_mojibake,
                        is_blank, normalize)
from .rules import (AMB_START, MATCH, MATCH_JUSTIFIED, MISMATCH, UNDETERMINED,
                    RuleEngine)

ASSIGNMENT_FIELD = "__assignment__"
CT_FIELD = "COMPARAISON_CHAMP"
CT_MISSING_ASSIGNMENT = "AFFECTATION_ABSENTE_DESTINATION"
CT_MISSING_PERSON = "PERSONNE_ABSENTE_DESTINATION"
CT_UNEXPECTED_ASSIGNMENT = "AFFECTATION_NON_ATTENDUE_DESTINATION"
CT_UNKNOWN_PERSON = "PERSONNE_ABSENTE_SOURCE"
CT_AMBIGUOUS = "APPARIEMENT_AMBIGU"

# Documentation ambiguities found while reading the official files.
HYP = "hypothèse retenue (non confirmée)"
RESOLVED = f"RÉSOLUE ({C.CLARIFICATION_DATE})"
DOC_AMBIGUITIES = [
    ("AMB-02", "detailedStatus / statusReasonCode / expectedReturnDate",
     "La feuille 'Règles situation d'emploi' nomme ses colonnes cf_specificStatus, cf_CAD, cf_CADP sans les relier "
     "explicitement aux champs destination.",
     "Association retenue : cf_specificStatus→detailedStatus, cf_CAD→statusReasonCode, cf_CADP→expectedReturnDate "
     "(cohérente avec les valeurs observées).", HYP),
    ("AMB-03", "detailedStatus",
     "Le code de situation peut provenir de CodeSuspensionAccès (= 'Code de traitement des accès' selon la feuille "
     "de jointure), de CodeStatutEmploi (listé au mapping) ou du CodeGestionAccès du motif.",
     "CodeSuspensionAccès est utilisé ; CodeStatutEmploi et le motif servent de contre-vérification. Tout conflit → A_INVESTIGUER.", HYP),
    ("AMB-04", "detailedStatus",
     "La description mentionne 'actif / cessation / absence complète' mais la table ne définit que les codes 00-01 et 02,03,06,07.",
     "Tout autre code d'accès est non documenté → A_INVESTIGUER (aucune règle inventée).", HYP),
    ("AMB-05", "statusReasonCode",
     "DateEffetRaison est listée comme source de statusReasonCode mais aucune règle ne l'utilise.",
     "Conservée comme valeur contextuelle dans la preuve uniquement.", HYP),
    ("AMB-06", "Motifs",
     "Les colonnes du fichier motifs (CodeCatégorieStatut, CodeStatutSystèmeExterne, CodeGestionAccès) ne portent pas "
     "les noms de la feuille 'Jointure - Motif des situations'.",
     "Correspondance par position : code situation ↔ CodeRaisonStatut, code Remphor, code de traitement des accès.",
     HYP),
    ("AMB-07", "contractTypeCode",
     "La règle utilise EMPTP_CD, la colonne B indique EMPT_CD, l'extraction contient CatégorieEmploi ; "
     "la combinaison EMPTP_CD='V' avec PERM_IND=0 n'est pas couverte.",
     "Alias EMPT_CD/EMPTP_CD = CatégorieEmploi ; Oui/Non = 1/0 ; combinaison non couverte → A_INVESTIGUER.", HYP),
    ("AMB-08", "isPrimaryAssignment / isTemporaryAssignment",
     "La règle parle de role_term_1_primary / role_term_1_temporary.",
     "Interprétés comme isPrimaryAssignment / isTemporaryAssignment.", HYP),
    ("AMB-09", "contactEmail",
     "Le champ source 'Adresse courriel travail' n'existe pas dans l'extraction ; la destination porte un préfixe "
     "d'environnement et un identifiant anonymisé.",
     "Clarification officielle : code = Matricule/personID ; préfixe 'xxx_' autorisé et ignoré ; identifiant "
     "divergent = erreur d'anonymisation → ANOMALIE, regroupée en un seul problème systémique (cause connue).",
     RESOLVED),
    ("AMB-10", "divisionName / positionName",
     "La concaténation ne précise pas le formatage du code (la destination montre '00397').",
     "Zéros non significatifs ignorés (différence de format).", HYP),
    ("AMB-11", "termEndDate",
     "Décrit comme 'Date d'effet du détail du poste' mais la règle est identique à assignmentEndDate.",
     "Même règle appliquée que pour assignmentEndDate.", HYP),
    ("AMB-12", "positionName",
     "La destination suit une substitution de code cohérente non décrite par le mapping (22/22 lignes) : "
     "anonymisation ou erreur ?",
     "Clarification officielle : erreur réelle → ANOMALIE, regroupée en un seul problème systémique.", RESOLVED),
]


@dataclass
class CorroborationResult:
    cases: pd.DataFrame
    matches: pd.DataFrame
    patterns: pd.DataFrame
    proposals: pd.DataFrame
    ambiguities: pd.DataFrame
    mapping: pd.DataFrame
    summary: dict
    dataset: Dataset
    config: EngineConfig
    engine: RuleEngine = field(repr=False)
    dataset_key: str = ""


def _json(d) -> str:
    if not d:
        return ""
    return json.dumps({k: v if v is None or isinstance(v, (bool, int, float, list)) else display(v)
                       for k, v in d.items()}, ensure_ascii=False, default=str)


def _assignment_label(src) -> str:
    return (f"{display(src.get('TypeAffectation'))} · poste {display(src.get('CodePoste'))} · emploi "
            f"{display(src.get('CodeEmploi'))} · début {display(src.get('DateEntréePoste'))}")


def _key(value, kind) -> str:
    n = normalize(value, kind)
    return "" if n is None else display(n) if not isinstance(n, bool) else str(n).lower()


def decide(stage1: str, outcome: str, is_direct: bool) -> tuple[str, str]:
    if outcome == MATCH:
        if stage1 in (IDENTICAL, BOTH_EMPTY):
            return C.CONFORME, C.RAW if is_direct else C.DETERMINISTIC
        if stage1 == IDENTICAL_NORMALIZED:
            return C.CONFORME, C.RAW
        if stage1 == NOT_COMPARABLE:
            return C.CONFORME, C.DETERMINISTIC
        return C.ECART_JUSTIFIE, C.DETERMINISTIC
    if outcome == MATCH_JUSTIFIED:
        return C.ECART_JUSTIFIE, C.DETERMINISTIC
    if outcome == MISMATCH:
        return C.ANOMALIE, C.RAW if is_direct else C.DETERMINISTIC
    return C.A_INVESTIGUER, C.DETERMINISTIC


class Corroborator:
    def __init__(self, ds: Dataset, config: EngineConfig | None = None):
        self.ds = ds
        self.cfg = config or EngineConfig()
        self.engine = RuleEngine(ds, self.cfg)
        self.src_name = Path(ds.files["source"]).name
        self.dst_name = Path(ds.files["destination"]).name

    # ------------------------------------------------------------ helpers
    def _base_case(self, pid, src_idx, dst_idx, rec) -> dict:
        src = self.ds.source.loc[src_idx] if src_idx is not None else None
        dst = self.ds.destination.loc[dst_idx] if dst_idx is not None else None
        if src is not None:
            name = f"{display(src.get('PrénomUsuel'))} {display(src.get('NomFamille'))}".strip()
        else:
            name = f"{display(dst.get('givenName'))} {display(dst.get('surname'))}".strip()
        return {
            "person_id": pid,
            "nom": name,
            "src_row": int(src["_excel_row"]) if src is not None else None,
            "dst_row": int(dst["_excel_row"]) if dst is not None else None,
            "assignment": _assignment_label(src) if src is not None else
            f"dest · positionId {display(dst.get('positionId'))} · début {display(dst.get('assignmentStartDate'))}",
            "src_type": display(src.get("TypeAffectation")) if src is not None else "",
            "src_poste": display(src.get("CodePoste")) if src is not None else "",
            "src_emploi": display(src.get("CodeEmploi")) if src is not None else "",
            "src_division": display(src.get("CodeDirection")) if src is not None else "",
            "src_site": display(src.get("CodeSite")) if src is not None else "",
            "match_status": rec.status,
            "match_confidence": rec.confidence,
            "match_evidence": ", ".join(FEATURE_LABELS[k] for k, v in rec.features.items() if v),
            "match_note": rec.note,
        }

    def _evidence(self, src_row, dst_row, extra: list[str]) -> str:
        parts = []
        if src_row:
            parts.append(f"{self.src_name} ligne {src_row}")
        if dst_row:
            parts.append(f"{self.dst_name} ligne {dst_row}")
        return " ; ".join(parts + [e for e in extra if e])

    # ------------------------------------------------------------ field case
    def _field_case(self, spec, rec) -> dict:
        src = self.ds.source.loc[rec.src_idx]
        dst = self.ds.destination.loc[rec.dst_idx]
        dest_value = dst.get(spec.dest_field)
        deriv, outcome = self.engine.evaluate(spec, rec.src_idx, dest_value)
        raw_field = spec.rule.raw_source_field
        src_value = src.get(raw_field) if raw_field else None
        if raw_field:
            l1 = compare_values(src_value, dest_value, spec.kind)
            stage1 = l1["stage1"]
        else:
            stage1 = NOT_COMPARABLE
        verdict, rule_type = decide(stage1, outcome.status, spec.rule.is_direct)
        quality_flag = ""
        if has_mojibake(dest_value):
            quality_flag = "ENCODAGE_CORROMPU_DESTINATION"
            outcome.note = ((outcome.note + " ; ") if outcome.note else "") +                 f"encodage corrompu dans la destination ('{dest_value}' = '{fix_mojibake(dest_value)}')"
            if verdict == C.CONFORME:
                verdict = C.ECART_JUSTIFIE
        expected_shown = deriv.shown() if deriv.ok or deriv.expected is not None else ""
        ctx = self.engine.context(rec.src_idx)

        extra_ev = [spec.entry.evidence]
        if spec.rule.rule_id in ("R_ASSIGNMENT_START", "R_ASSIGNMENT_END") and ctx["poste_context"]["lignes_detail_poste"]:
            rows = ctx["poste_context"]["lignes_detail_poste"]
            extra_ev.append(f"{Path(self.ds.files['detail']).name} lignes {rows[0]}-{rows[-1]}")
        if spec.rule.rule_id == "R_SITUATION_EMPLOI":
            extra_ev.append("Mapping.xlsx › Règles situation d'emploi")
            if deriv.context.get("ligne_motif"):
                extra_ev.append(f"{Path(self.ds.files['motif']).name} ligne {deriv.context['ligne_motif']}")
        if spec.dest_field in ("weeklyHoursOverride", "dailyHoursOverride") and ctx["poste_context"]["lignes_detail_poste"]:
            extra_ev.append(f"{Path(self.ds.files['detail']).name} (contexte) lignes "
                            f"{ctx['poste_context']['lignes_detail_poste'][-1]}")

        explanation = self._explain(spec, stage1, outcome, deriv, verdict, src_value, dest_value, raw_field)
        case = self._base_case(rec.person_id, rec.src_idx, rec.dst_idx, rec)
        case.update({
            "case_type": CT_FIELD,
            "field": spec.dest_field,
            "source_fields": ", ".join(spec.entry.source_fields) or "-",
            "source_value": display(src_value) if raw_field else "",
            "destination_value": display(dest_value),
            "source_normalized": _key(src_value, spec.kind) if raw_field else "",
            "destination_normalized": _key(dest_value, spec.kind),
            "expected_value": expected_shown,
            "alt_expected_value": display(deriv.alt_expected) if deriv.alt_expected is not None else "",
            "expected_key": _key(deriv.expected, spec.kind) if not isinstance(deriv.expected, tuple)
            else (deriv.expected_display or "").casefold(),
            "stage1_result": stage1,
            "rule_id": spec.rule.rule_id,
            "rule_name": spec.rule.name,
            "rule_outcome": outcome.status,
            "rule_note": outcome.note or deriv.issue,
            "certainty": deriv.certainty,
            "mapping_ref": spec.entry.evidence,
            "mapping_rule_text": spec.entry.rule_text[:500],
            "inputs": _json(deriv.inputs),
            "context": _json(deriv.context),
            "hypothesis_code": outcome.hypothesis or "",
            "ambiguity_id": deriv.ambiguity_id or "",
            "quality_flag": quality_flag,
            "deterministic_verdict": verdict,
            "verdict": verdict,
            "rule_type": rule_type,
            "explanation": explanation,
            "proposed_action": self._action(spec.dest_field, verdict, expected_shown, dest_value, deriv),
            "evidence": self._evidence(case["src_row"], case["dst_row"], extra_ev),
        })
        return case

    def _explain(self, spec, stage1, outcome, deriv, verdict, src_value, dest_value, raw_field) -> str:
        f = spec.dest_field
        d = display(dest_value) or "∅"
        s = display(src_value) or "∅"
        note = f" ({outcome.note})" if outcome.note else ""
        rule = f"{spec.rule.rule_id} [{spec.entry.evidence}]"
        if verdict == C.CONFORME:
            if stage1 == IDENTICAL:
                if spec.rule.is_direct:
                    return f"Niveau 1 : valeurs identiques ({raw_field}='{s}', {f}='{d}')."
                return (f"Niveau 1 : valeurs identiques ({raw_field}='{s}', {f}='{d}') ; Niveau 2 : {rule} confirme "
                        f"la valeur attendue '{deriv.shown()}' ({deriv.explanation}){note}.")
            if stage1 == BOTH_EMPTY:
                return f"Niveau 1 : valeur vide des deux côtés ({raw_field} / {f}){note}."
            if stage1 == IDENTICAL_NORMALIZED:
                return (f"Niveau 1 : différence de format uniquement ('{s}' vs '{d}' → "
                        f"'{_key(src_value, spec.kind)}'){note}.")
            return f"Niveau 2 : {rule} dérive '{deriv.shown()}' ({deriv.explanation}) ; identique à la destination{note}."
        if verdict == C.ECART_JUSTIFIE and outcome.status == MATCH and stage1 in (IDENTICAL_NORMALIZED, NOT_COMPARABLE,
                                                                              IDENTICAL, BOTH_EMPTY):
            return (f"Niveau 1 : valeur conforme après normalisation de l'encodage ('{d}') ; attendu "
                    f"'{deriv.shown()}' ({spec.rule.rule_id}){note}. Qualité de donnée à signaler au système B.")
        if verdict == C.ECART_JUSTIFIE:
            lvl1 = f"Niveau 1 : {raw_field}='{s}' ≠ {f}='{d}'. " if raw_field else ""
            return (f"{lvl1}Niveau 2 : écart expliqué par {rule} : {deriv.explanation} → attendu "
                    f"'{deriv.shown() or '∅'}'{note}.")
        if verdict == C.ANOMALIE:
            if spec.rule.is_direct:
                return (f"Niveau 1 : copie directe attendue (mapping N/A) mais {raw_field}='{s}' ≠ {f}='{d}'{note}. "
                        "Aucune règle documentée n'explique l'écart.")
            return (f"Niveau 2 : {rule} attend '{deriv.shown() or '∅'}' ({deriv.explanation}) ; la destination "
                    f"contient '{d}'{note}. Aucune règle documentée n'explique l'écart.")
        reason = outcome.note or deriv.issue or "règle non concluante"
        return f"Niveau 2 : {rule} ne permet pas de conclure : {reason}. Destination = '{d}'."

    @staticmethod
    def _action(field_name, verdict, expected, dest_value, deriv) -> str:
        if verdict in (C.CONFORME, C.ECART_JUSTIFIE):
            return "Aucune action."
        if verdict == C.ANOMALIE:
            return (f"Corriger {field_name} dans le système B : '{display(dest_value) or '∅'}' → "
                    f"'{expected or '∅'}' (valeur dérivée de la source). Validation humaine requise.")
        return "Investiguer : la règle ne permet pas de trancher (voir explication)."

    # ------------------------------------------------------------ assignment cases
    def _assignment_case(self, rec) -> dict:
        case = self._base_case(rec.person_id, rec.src_idx, rec.dst_idx, rec)
        src = self.ds.source.loc[rec.src_idx] if rec.src_idx is not None else None
        common = {"field": ASSIGNMENT_FIELD, "source_fields": "TypeAffectation, CodePoste, CodeEmploi, DateEntréePoste",
                  "source_normalized": "", "destination_normalized": "", "alt_expected_value": "", "expected_key": "",
                  "certainty": C.INFERRED, "mapping_ref": "Mapping.xlsx › Mapping, lignes 41-45 (type d'affectation)",
                  "mapping_rule_text": "", "inputs": "", "context": "", "ambiguity_id": "", "quality_flag": "",
                  "rule_type": C.DETERMINISTIC}
        case.update(common)
        if rec.status == SOURCE_ONLY:
            missing_person = "personne absente" in rec.note
            ctype = CT_MISSING_PERSON if missing_person else CT_MISSING_ASSIGNMENT
            expl = (f"Niveau 2 (appariement) : l'affectation source {_assignment_label(src)} n'a aucune ligne "
                    f"correspondante dans le système B ; {rec.note}.")
            if not missing_person:
                expl += (" Une affectation manquante n'est pas automatiquement une erreur (affectation temporaire / "
                         "secondaire, décalage de chargement) : cas soumis à investigation.")
            case.update({
                "case_type": ctype,
                "source_value": _assignment_label(src),
                "destination_value": "",
                "expected_value": "ligne destination attendue (voir action proposée)",
                "stage1_result": NOT_COMPARABLE,
                "rule_id": "R_MATCHING",
                "rule_name": "Appariement des affectations (preuves pondérées)",
                "rule_outcome": UNDETERMINED,
                "rule_note": rec.note,
                "hypothesis_code": "AFFECTATION_MANQUANTE" if not missing_person else "PERSONNE_MANQUANTE",
                "deterministic_verdict": C.A_INVESTIGUER,
                "verdict": C.A_INVESTIGUER,
                "explanation": expl,
                "proposed_action": "Valider (ou modifier/rejeter) la création de la ligne destination proposée selon le mapping.",
                "evidence": self._evidence(case["src_row"], None, [common["mapping_ref"]]),
            })
        elif rec.status == DEST_ONLY:
            unknown = "personne absente" in rec.note
            dst = self.ds.destination.loc[rec.dst_idx]
            case.update({
                "case_type": CT_UNKNOWN_PERSON if unknown else CT_UNEXPECTED_ASSIGNMENT,
                "source_value": "",
                "destination_value": f"positionId {display(dst.get('positionId'))} · début {display(dst.get('assignmentStartDate'))}",
                "expected_value": "",
                "stage1_result": NOT_COMPARABLE,
                "rule_id": "R_MATCHING",
                "rule_name": "Appariement des affectations (preuves pondérées)",
                "rule_outcome": UNDETERMINED,
                "rule_note": rec.note,
                "hypothesis_code": "LIGNE_DESTINATION_ORPHELINE",
                "deterministic_verdict": C.A_INVESTIGUER,
                "verdict": C.A_INVESTIGUER,
                "explanation": f"Niveau 2 (appariement) : ligne destination sans affectation source correspondante ; {rec.note}.",
                "proposed_action": "Vérifier l'origine de cette ligne dans le système B (affectation terminée ? doublon ?).",
                "evidence": self._evidence(None, case["dst_row"], []),
            })
        else:  # AMBIGUOUS
            cands = ", ".join(f"ligne dest {int(self.ds.destination.loc[d, '_excel_row'])} (score {sc:g})"
                              for d, sc in rec.candidates if d is not None)
            case.update({
                "case_type": CT_AMBIGUOUS,
                "source_value": _assignment_label(src),
                "destination_value": cands,
                "expected_value": "",
                "stage1_result": NOT_COMPARABLE,
                "rule_id": "R_MATCHING",
                "rule_name": "Appariement des affectations (preuves pondérées)",
                "rule_outcome": UNDETERMINED,
                "rule_note": rec.note,
                "hypothesis_code": "APPARIEMENT_AMBIGU",
                "deterministic_verdict": C.A_INVESTIGUER,
                "verdict": C.A_INVESTIGUER,
                "explanation": (f"Niveau 2 (appariement) : plusieurs affectations source/destination sont "
                                f"équivalentes selon les preuves disponibles ({cands}) ; aucun appariement n'est "
                                "forcé et les champs ne sont pas comparés."),
                "proposed_action": "Identifier manuellement la ligne destination correspondant à chaque affectation source.",
                "evidence": self._evidence(case["src_row"], None, []),
            })
        return case

    # ------------------------------------------------------------ run
    def run(self, assistant=None, decisions=None) -> CorroborationResult:
        from .ai_assist import LocalAssistant
        from .proposals import build_proposals

        records = match_assignments(self.ds.source, self.ds.destination,
                                    self.engine.expected_start_candidates, self.cfg)
        cases = []
        for rec in records:
            if rec.status == MATCHED:
                for spec in self.engine.field_specs:
                    cases.append(self._field_case(spec, rec))
            else:
                cases.append(self._assignment_case(rec))
        cases_df = pd.DataFrame(cases)
        cases_df.insert(0, "case_id", [
            f"C-{r.person_id}-S{r.src_row if pd.notna(r.src_row) else 0:.0f}-D{r.dst_row if pd.notna(r.dst_row) else 0:.0f}-{r.field}"
            for r in cases_df.itertuples()])
        cases_df["src_row"] = cases_df["src_row"].astype("Int64")
        cases_df["dst_row"] = cases_df["dst_row"].astype("Int64")

        ambiguities, amb_patterns = self._ambiguity_register(cases_df)
        assistant = assistant or LocalAssistant()
        cases_df, patterns = assistant.analyze(cases_df, self.cfg)
        if not amb_patterns.empty:
            patterns = pd.concat([amb_patterns, patterns], ignore_index=True)
        cases_df = _attach_rule_ambiguity_patterns(cases_df, amb_patterns)

        proposals = build_proposals(self, cases_df, records)
        matches = matches_table(records, self.ds.source, self.ds.destination)
        result = CorroborationResult(
            cases=cases_df, matches=matches, patterns=patterns, proposals=proposals,
            ambiguities=ambiguities, mapping=mapping_table(self.engine.entries), summary={},
            dataset=self.ds, config=self.cfg, engine=self.engine,
            dataset_key=(self.ds.fingerprints["source"][:8] + self.ds.fingerprints["destination"][:8]))
        if decisions is not None:
            from .decisions import apply_decisions
            apply_decisions(result, decisions)
        else:
            for col in ("human_decision", "human_comment", "human_decided_at", "human_decision_source"):
                result.cases[col] = ""
            result.cases["reviewed_verdict"] = result.cases["verdict"]
        result.summary = summarize(result)
        return result

    def _ambiguity_register(self, cases: pd.DataFrame):
        rows = []
        start = cases[(cases["field"] == "assignmentStartDate")]
        n_total = len(start)
        n_ok = int((start["rule_outcome"] == MATCH).sum())
        n_lit = int((start["destination_normalized"] ==
                     start["context"].map(lambda c: _ctx(c, "valeur_lecture_litterale_MIN"))).sum())
        n_transformed = int(((start["rule_outcome"] == MATCH) & (start["stage1_result"] == DIFFERENT)).sum())
        clar = C.OFFICIAL_CLARIFICATIONS["assignmentStartDate"]
        rows.append({
            "ambiguity_id": AMB_START, "champ": "assignmentStartDate", "statut": f"RÉSOLUE ({C.CLARIFICATION_DATE})",
            "description": ("Libellé du mapping : 'date la plus ancienne' entre la date d'effet de l'unité adm. "
                            "(détection du changement de valeur vs l'enregistrement précédent, sinon MIN EFFDT) et la "
                            "date d'effet du poste. Lu strictement (changement du seul code d'unité + MIN), il ne "
                            "reproduit pas la destination."),
            "interpretation_retenue": ("MAX(DateEntréePoste, date d'effet de l'enregistrement courant du détail du "
                                       "poste, i.e. dernier enregistrement différent du précédent ; sinon MIN EFFDT). "
                                       "Clarification officielle : " + clar["statement"]),
            "statistiques": (f"transformation reproduite : {n_ok}/{n_total} lignes destination ({n_transformed} "
                             f"différentes de la date source, dont 9989151, 3241002, 4402456) ; lecture littérale "
                             f"MIN : {n_lit}/{n_total}"),
            "enregistrements_concernes": n_transformed,
            "type": "dynamique (calculée sur les données)",
        })
        for amb_id, fld, desc, choice, status in DOC_AMBIGUITIES:
            rows.append({"ambiguity_id": amb_id, "champ": fld, "statut": status, "description": desc,
                         "interpretation_retenue": choice, "statistiques": "", "enregistrements_concernes": None,
                         "type": "documentation"})
        return pd.DataFrame(rows), pd.DataFrame()


def _ctx(ctx_json: str, key: str) -> str:
    if not ctx_json:
        return ""
    try:
        return str(json.loads(ctx_json).get(key, "") or "")
    except (ValueError, TypeError):
        return ""


def _attach_rule_ambiguity_patterns(cases: pd.DataFrame, amb_patterns: pd.DataFrame) -> pd.DataFrame:
    if amb_patterns.empty:
        return cases
    for _, p in amb_patterns.iterrows():
        ids = set(p["case_ids"].split(";"))
        mask = cases["case_id"].isin(ids) & (cases["pattern_id"] == "")
        cases.loc[mask, "pattern_id"] = p["pattern_id"]
    return cases


def summarize(result: CorroborationResult) -> dict:
    cases = result.cases
    counts = cases["verdict"].value_counts().to_dict()
    systemic_mask = cases["systemic"].astype(bool)
    attention_individual = int(cases[cases["verdict"].isin([C.ANOMALIE, C.A_INVESTIGUER]) & ~systemic_mask].shape[0])
    pats = result.patterns
    group_types = {"SYSTEMIQUE_CHAMP", "SYSTEMIQUE_SEGMENT", "AMBIGUITE_REGLE_SYSTEMIQUE"}
    n_groups = int(pats["pattern_type"].isin(group_types).sum()) if not pats.empty else 0
    m = result.matches["statut_appariement"].value_counts().to_dict()
    return {
        "dataset": result.dataset.label,
        "n_cases": int(len(cases)),
        "n_field_comparisons": int((cases["case_type"] == CT_FIELD).sum()),
        "n_persons": int(cases["person_id"].nunique()),
        "n_source_assignments": int(len(result.dataset.source)),
        "n_destination_rows": int(len(result.dataset.destination)),
        "n_fields_corroborated": len(result.engine.field_specs),
        **{f"n_{v}": int(counts.get(v, 0)) for v in C.VERDICTS},
        "n_matched": int(m.get(MATCHED, 0)), "n_ambiguous_match": int(m.get(AMBIGUOUS, 0)),
        "n_source_only": int(m.get(SOURCE_ONLY, 0)), "n_dest_only": int(m.get(DEST_ONLY, 0)),
        "n_systemic_cases": int(systemic_mask.sum()),
        "n_systemic_groups": n_groups,
        "n_attention_individual": attention_individual,
        "n_human_attention": attention_individual + n_groups,
        "n_proposals": int(result.proposals["proposal_id"].nunique()) if not result.proposals.empty else 0,
        "n_reviewed": int((cases["human_decision"] != "").sum()),
        "n_rule_type_" + C.RAW: int((cases["rule_type"] == C.RAW).sum()),
        "n_rule_type_" + C.DETERMINISTIC: int((cases["rule_type"] == C.DETERMINISTIC).sum()),
        "n_rule_type_" + C.AI_ASSISTED: int((cases["rule_type"] == C.AI_ASSISTED).sum()),
    }


def run_corroboration(data_dir=C.DATA_DIR, config: EngineConfig | None = None, use_decisions: bool = True,
                      decisions_path=None, **load_kwargs) -> CorroborationResult:
    """One-call entry point used by the app, the notebook and the CLI."""
    ds = load_all(data_dir, **load_kwargs)
    store = None
    if use_decisions:
        from .decisions import DecisionStore
        store = DecisionStore(decisions_path)
    return Corroborator(ds, config).run(decisions=store)


def main(argv=None):
    import argparse
    from .export import export_csv, export_report
    p = argparse.ArgumentParser(description="CorroborAI — corroboration Système A (RH) ↔ Système B (Temps)")
    p.add_argument("--data", default=str(C.DATA_DIR), help="dossier des fichiers d'entrée (lecture seule)")
    p.add_argument("--out", default=str(C.OUTPUT_DIR), help="dossier de sortie")
    p.add_argument("--no-decisions", action="store_true", help="ignorer les décisions humaines enregistrées")
    a = p.parse_args(argv)
    res = run_corroboration(a.data, use_decisions=not a.no_decisions)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = export_report(res, out / "corroboration_report.xlsx")
    export_csv(res, out)
    s = res.summary
    print(f"Cas: {s['n_cases']} | CONFORME {s['n_CONFORME']} | ECART_JUSTIFIE {s['n_ECART_JUSTIFIE']} | "
          f"ANOMALIE {s['n_ANOMALIE']} | A_INVESTIGUER {s['n_A_INVESTIGUER']} | attention humaine {s['n_human_attention']}")
    print(f"Rapport : {path}")


if __name__ == "__main__":
    main()
