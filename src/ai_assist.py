"""Level 3: assisted analysis of the cases that remain non-conforming.

This module never overrides a deterministic verdict silently. What it does:

* detects recurring discrepancy patterns and groups similar anomalies
  (swapped values, systemic field failures, segment clusters, common root causes);
* routes *field-wide systemic* mismatches to a single investigation group
  (the per-case deterministic verdict stays in ``deterministic_verdict``);
* computes a transparent priority score and confidence score (formula shown);
* writes a concise investigation explanation and a suggested next action;
* optionally learns from expert decisions (``FeedbackModel``) to *suggest* a
  verdict for A_INVESTIGUER cases.

Everything runs locally. ``LLMAssistant`` is the plug-in point for a future
language model; it only ever receives redacted evidence.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import defaultdict

import numpy as np
import pandas as pd

from . import config as C
from .config import EngineConfig

FIELD_CASE = "COMPARAISON_CHAMP"
NON_CONFORM = (C.ANOMALIE, C.A_INVESTIGUER)

HYPOTHESIS_FAMILY = {
    "DEST_EQUALS:heures_semaine_poste": "REPLI_VALEUR_DEFAUT_POSTE",
    "DEST_EQUALS:heures_jour_poste": "REPLI_VALEUR_DEFAUT_POSTE",
    "DEST_EQUALS:EFFDT_min": "DATE_EFFET_LA_PLUS_ANCIENNE",
    "DEST_EQUALS:DateEntréePoste": "TRANSFORMATION_NON_APPLIQUEE",
}
FAMILY_TEXT = {
    "REPLI_VALEUR_DEFAUT_POSTE": ("La destination semble avoir repris la valeur par défaut du poste (détail du poste) "
                                  "au lieu de la valeur propre à l'employé."),
    "DATE_EFFET_LA_PLUS_ANCIENNE": "La destination semble utiliser la plus ancienne date d'effet du détail du poste.",
    "TRANSFORMATION_NON_APPLIQUEE": ("La destination reprend la date source sans appliquer la transformation avec "
                                     "l'historique du détail du poste."),
    "DESTINATION_MANQUANTE": "La valeur n'a pas été transmise au système B (champ vide à la destination).",
    "SOURCE_MANQUANTE": "La valeur est absente du système A : la destination a été alimentée par une autre source.",
    "AFFECTATION_MANQUANTE": ("Affectation présente dans RH mais absente du système Temps : omission de l'interface, "
                              "filtrage des affectations temporaires/secondaires ou décalage de chargement."),
    "PERSONNE_MANQUANTE": "Employé absent du système Temps : périmètre d'interface ou échec de chargement.",
    "APPARIEMENT_AMBIGU": "Affectations indiscernables avec les preuves disponibles.",
    "INTERPRETATION_ALTERNATIVE": "La destination applique l'interprétation littérale de la règle.",
    "ANONYMISATION": ("Identifiant du courriel différent du Matricule/personID : cause connue dans le jeu fourni = "
                      "artefact d'anonymisation."),
    "SUBSTITUTION_CODE": "Le code inscrit dans la valeur destination diffère du code source.",
    "LIGNE_DESTINATION_ORPHELINE": "Ligne destination sans origine RH identifiable.",
}
FAMILY_ACTION = {
    "REPLI_VALEUR_DEFAUT_POSTE": ("Vérifier pourquoi l'interface n'a pas transmis la valeur employé (règle de repli "
                                  "sur le poste ?) puis corriger la valeur destination."),
    "DESTINATION_MANQUANTE": "Vérifier le flux d'interface pour ce champ et retransmettre la valeur.",
    "TRANSFORMATION_NON_APPLIQUEE": "Vérifier que l'interface applique la règle de date avec le détail du poste.",
    "SOURCE_MANQUANTE": "Compléter la donnée dans le système RH, ou confirmer la valeur par défaut utilisée.",
    "AFFECTATION_MANQUANTE": "Confirmer avec l'équipe d'intégration si l'affectation doit exister ; valider la ligne proposée.",
    "PERSONNE_MANQUANTE": "Vérifier le périmètre de l'interface pour cet employé.",
    "APPARIEMENT_AMBIGU": "Désigner manuellement la correspondance entre affectations.",
    "INTERPRETATION_ALTERNATIVE": "Confirmer l'interprétation de la règle avec le responsable fonctionnel.",
    "ANONYMISATION": "Traiter au niveau du groupe systémique (génération des courriels / anonymisation).",
    "SUBSTITUTION_CODE": "Vérifier la table de correspondance utilisée par l'interface.",
    "LIGNE_DESTINATION_ORPHELINE": "Vérifier si la ligne doit être fermée ou supprimée dans le système B.",
}


def family_of(code: str) -> str:
    if not code:
        return ""
    return HYPOTHESIS_FAMILY.get(code, code)


class AnalysisAssistant(ABC):
    """Interface of a level-3 assistant. ``analyze`` returns (cases, patterns)."""
    name = "abstract"

    @abstractmethod
    def analyze(self, cases: pd.DataFrame, cfg: EngineConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
        ...


class LocalAssistant(AnalysisAssistant):
    """Transparent local heuristics + statistics (no external call)."""
    name = "assistant-local-v1"

    def __init__(self, feedback_model: "FeedbackModel | None" = None):
        self.feedback_model = feedback_model

    def analyze(self, cases: pd.DataFrame, cfg: EngineConfig):
        cases = cases.copy()
        for col, default in (("systemic", False), ("pattern_id", ""), ("ai_hypothesis", ""),
                             ("ai_suggested_action", ""), ("ai_suggested_verdict", ""),
                             ("ai_suggestion_confidence", np.nan)):
            cases[col] = default
        patterns: list[dict] = []
        if not cases.empty:
            patterns += self._swaps(cases)
            patterns += self._systemic_fields(cases, cfg)
            patterns += self._segments(cases, cfg)
            patterns += self._recurrent(cases, cfg)
            patterns += self._quality(cases)
            self._fill_hypotheses(cases)
            self._score(cases, cfg)
            self._summaries(cases)
            if self.feedback_model is not None and self.feedback_model.is_trained:
                self.feedback_model.suggest(cases)
        pats = pd.DataFrame(patterns)
        if pats.empty:
            pats = pd.DataFrame(columns=["pattern_id", "pattern_type", "field", "title", "description", "n_cases",
                                         "n_persons", "share", "case_ids", "hypothesis", "suggested_action",
                                         "verdict_effect", "priority_score", "priority", "requires_decision"])
        else:
            pats = self._pattern_priority(pats, cases, cfg)
        return cases, pats

    # ------------------------------------------------------------ patterns
    def _swaps(self, cases):
        m = (cases["case_type"] == FIELD_CASE) & (cases["rule_outcome"] == "MISMATCH") & \
            (cases["expected_key"] != "") & (cases["destination_normalized"] != "")
        fc = cases[m]
        index = defaultdict(list)
        for i, r in fc.iterrows():
            index[(r["field"], r["expected_key"], r["destination_normalized"])].append(i)
        used, patterns = set(), []
        for i, r in fc.iterrows():
            if i in used or r["expected_key"] == r["destination_normalized"]:
                continue
            for j in index.get((r["field"], r["destination_normalized"], r["expected_key"]), []):
                if j in used or j == i or cases.at[j, "person_id"] == r["person_id"]:
                    continue
                used.update({i, j})
                a, b = r, cases.loc[j]
                pid = f"P-SWAP-{r['field']}-{a['person_id']}-{b['person_id']}"
                for x, y in ((i, b), (j, a)):
                    cases.at[x, "pattern_id"] = pid
                    cases.at[x, "hypothesis_code"] = "VALEURS_INTERVERTIES"
                    cases.at[x, "ai_hypothesis"] = (
                        f"Valeurs probablement interverties avec l'employé {y['person_id']} (ligne destination "
                        f"{y['dst_row']}) : cet enregistrement a reçu '{cases.at[x, 'destination_value']}' "
                        f"attendu chez {y['person_id']}, qui a reçu la valeur attendue ici "
                        f"('{cases.at[x, 'expected_value']}').")
                    cases.at[x, "ai_suggested_action"] = (
                        f"Corriger les deux enregistrements ({a['person_id']} et {b['person_id']}) ; "
                        "vérifier l'ordre/la clé de chargement de l'interface.")
                patterns.append({
                    "pattern_id": pid, "pattern_type": "VALEURS_INTERVERTIES", "field": r["field"],
                    "title": f"Valeurs interverties — {r['field']} ({a['person_id']} ↔ {b['person_id']})",
                    "description": (f"{a['person_id']} attend '{a['expected_value']}' et a reçu "
                                    f"'{a['destination_value']}' ; {b['person_id']} attend '{b['expected_value']}' "
                                    f"et a reçu '{b['destination_value']}'."),
                    "n_cases": 2, "n_persons": 2, "share": None, "case_ids": f"{a['case_id']};{b['case_id']}",
                    "hypothesis": "Inversion de valeurs entre deux enregistrements lors du chargement.",
                    "suggested_action": "Corriger les deux enregistrements ; vérifier la clé de jointure de l'interface.",
                    "verdict_effect": "aucun (ANOMALIE confirmée, confiance renforcée)", "requires_decision": False,
                })
                break
        return patterns

    def _systemic_fields(self, cases, cfg):
        """A field failing on a large share of rows for the same reason is ONE systemic issue for the
        human workflow. Individual cases keep their deterministic verdict (audit trail); the group
        receives a single decision."""
        fc = cases[cases["case_type"] == FIELD_CASE]
        totals = fc.groupby("field").size()
        fails = fc[fc["rule_outcome"].isin(["MISMATCH", "INDETERMINE"]) & (fc["pattern_id"] == "")]
        patterns = []
        for field_name, grp in fails.groupby("field"):
            n_tot = totals[field_name]
            share = len(grp) / n_tot
            if share < cfg.systemic_field_min_share or len(grp) < cfg.systemic_field_min_count:
                continue
            anonym = (grp["hypothesis_code"] == "ANONYMISATION").mean() > 0.5
            consistency = self._substitution(grp)
            clar = C.OFFICIAL_CLARIFICATIONS.get(field_name)
            pid = f"P-SYS-{field_name}"
            n_anom = int((grp["verdict"] == C.ANOMALIE).sum())
            if anonym:
                hyp = (f"{len(grp)}/{n_tot} dossiers affectés ({share:.0%}). Le préfixe d'environnement est autorisé, "
                       "mais l'identifiant utilisé après normalisation ne correspond pas au Matricule/personID.")
                action = ("Une seule décision / investigation : corriger la génération des courriels (ou "
                          "l'anonymisation de l'extraction) pour tous les dossiers.")
            else:
                hyp = (f"{len(grp)}/{n_tot} affectations concernées ({share:.0%}). La valeur cible ne respecte pas la "
                       f"transformation définie par le mapping ; {consistency['text']}. Cause probablement "
                       "systémique (règle d'interface), pas propre à chaque employé.")
                action = ("Vérifier la transformation une seule fois avec l'équipe d'intégration plutôt que "
                          f"{len(grp)} dossiers ; corriger l'interface puis recharger.")
            if clar and clar.get("known_cause"):
                hyp += f" Cause connue dans le jeu fourni : {clar['known_cause']}."
            if clar:
                hyp += f" [Clarification officielle {C.CLARIFICATION_DATE} : {clar['statement']}]"
            reroute = cfg.systemic_reroute_to_investigation and not clar
            for i in grp.index:
                cases.at[i, "systemic"] = True
                cases.at[i, "pattern_id"] = pid
                cases.at[i, "ai_hypothesis"] = hyp
                cases.at[i, "ai_suggested_action"] = action
                if reroute and cases.at[i, "verdict"] == C.ANOMALIE:
                    cases.at[i, "verdict"] = C.A_INVESTIGUER
                    cases.at[i, "rule_type"] = C.AI_ASSISTED
            title = (clar or {}).get("title") or f"Problème systémique — {field_name}"
            patterns.append({
                "pattern_id": pid, "pattern_type": "SYSTEMIQUE_CHAMP", "field": field_name,
                "title": f"{title} ({len(grp)}/{n_tot})",
                "description": hyp, "n_cases": len(grp), "n_persons": grp["person_id"].nunique(),
                "share": round(share, 3), "case_ids": ";".join(grp["case_id"]),
                "hypothesis": (clar or {}).get("known_cause") or consistency["text"],
                "suggested_action": action,
                "verdict_effect": (f"{n_anom} ANOMALIE conservées individuellement pour l'audit ; une seule "
                                   "décision humaine pour le groupe"),
                "requires_decision": True, "anonymisation": anonym,
                "business_priority": (clar or {}).get("business_priority") or "",
            })
        return patterns

    @staticmethod
    def _substitution(grp) -> dict:
        pairs = grp[["source_normalized", "destination_normalized"]]
        pairs = pairs[(pairs["source_normalized"] != "") & (pairs["destination_normalized"] != "")]
        if pairs.empty:
            return {"functional": False, "injective": False, "text": "aucune correspondance source→destination exploitable"}
        by_src = pairs.groupby("source_normalized")["destination_normalized"].nunique()
        functional = bool((by_src == 1).all())
        n_src = pairs["source_normalized"].nunique()
        n_dst = pairs["destination_normalized"].nunique()
        injective = functional and n_src == n_dst
        ex = pairs.drop_duplicates().head(3)
        examples = ", ".join(f"'{a}' → '{b}'" for a, b in ex.itertuples(index=False))
        if injective:
            text = f"substitution cohérente 1:1 sur {n_src} valeurs distinctes (ex. {examples})"
        elif functional:
            text = f"substitution cohérente (fonctionnelle) de {n_src} valeurs vers {n_dst} (ex. {examples})"
        else:
            text = f"correspondances incohérentes ({n_src} valeurs source, {n_dst} valeurs destination)"
        return {"functional": functional, "injective": injective, "text": text}

    def _segments(self, cases, cfg):
        fc = cases[cases["case_type"] == FIELD_CASE]
        rem = fc[(fc["verdict"] == C.ANOMALIE) & (fc["pattern_id"] == "")]
        candidates = []
        for seg in ("src_division", "src_emploi", "src_site"):
            totals = fc.groupby(["field", seg]).size()
            for (f, sv, dv), grp in rem.groupby(["field", seg, "destination_normalized"]):
                if len(grp) < cfg.segment_min_count:
                    continue
                share = len(grp) / totals[(f, sv)]
                if share >= cfg.segment_min_share:
                    candidates.append((len(grp), f, seg, sv, dv, share, grp.index))
        patterns, used = [], set()
        seg_label = {"src_division": "CodeDirection", "src_emploi": "CodeEmploi", "src_site": "CodeSite"}
        for n, f, seg, sv, dv, share, idx in sorted(candidates, key=lambda x: -x[0]):
            idx = [i for i in idx if i not in used]
            if len(idx) < cfg.segment_min_count:
                continue
            used.update(idx)
            pid = f"P-SEG-{f}-{seg_label[seg]}-{sv}"
            hyp = (f"{len(idx)} affectations du segment {seg_label[seg]}={sv} ({share:.0%} du segment) reçoivent la "
                   f"même valeur erronée '{dv}' pour {f} : défaillance de mapping concentrée sur ce segment.")
            for i in idx:
                cases.at[i, "systemic"] = True
                cases.at[i, "pattern_id"] = pid
                cases.at[i, "ai_hypothesis"] = hyp
                cases.at[i, "ai_suggested_action"] = (f"Corriger la règle/table d'interface pour {seg_label[seg]}={sv} "
                                                      "puis recharger le segment (correction groupée).")
            patterns.append({
                "pattern_id": pid, "pattern_type": "SYSTEMIQUE_SEGMENT", "field": f,
                "title": f"Défaillance systémique — {f} pour {seg_label[seg]}={sv}", "description": hyp,
                "n_cases": len(idx), "n_persons": cases.loc[idx, "person_id"].nunique(), "share": round(share, 3),
                "case_ids": ";".join(cases.loc[idx, "case_id"]), "hypothesis": hyp,
                "suggested_action": "Correction groupée au niveau de l'interface plutôt qu'enregistrement par enregistrement.",
                "verdict_effect": "aucun (ANOMALIE conservées, regroupées pour une correction unique)",
                "requires_decision": True,
            })
        return patterns

    def _recurrent(self, cases, cfg):
        rem = cases[cases["verdict"].isin(NON_CONFORM) & (cases["pattern_id"] == "") & (cases["hypothesis_code"] != "")]
        if rem.empty:
            return []
        fam = rem["hypothesis_code"].map(family_of)
        patterns = []
        for family, grp in rem.groupby(fam):
            if len(grp) < cfg.recurrent_min_count or family in ("APPARIEMENT_AMBIGU",):
                continue
            pid = f"P-REC-{family}"
            fields = ", ".join(sorted(grp["field"].unique()))
            text = FAMILY_TEXT.get(family, family)
            for i in grp.index:
                cases.at[i, "pattern_id"] = pid
            patterns.append({
                "pattern_id": pid, "pattern_type": "RECURRENT", "field": fields,
                "title": f"Motif récurrent — {family.replace('_', ' ').lower()} ({len(grp)} cas)",
                "description": f"{text} Champs : {fields} ; employés : {', '.join(sorted(grp['person_id'].unique())[:8])}.",
                "n_cases": len(grp), "n_persons": grp["person_id"].nunique(), "share": None,
                "case_ids": ";".join(grp["case_id"]), "hypothesis": text,
                "suggested_action": FAMILY_ACTION.get(family, "Investiguer la cause commune."),
                "verdict_effect": "aucun (cas traités individuellement, cause commune signalée)",
                "requires_decision": False,
            })
        return patterns

    @staticmethod
    def _quality(cases):
        if "quality_flag" not in cases.columns:
            return []
        q = cases[cases["quality_flag"] != ""]
        patterns = []
        for flag, grp in q.groupby("quality_flag"):
            pid = f"P-QUAL-{flag}"
            cases.loc[grp.index[cases.loc[grp.index, "pattern_id"] == ""], "pattern_id"] = pid
            patterns.append({
                "pattern_id": pid, "pattern_type": "QUALITE_DONNEES", "field": ", ".join(sorted(grp["field"].unique())),
                "title": f"Qualité de données — {flag.replace('_', ' ').lower()} ({len(grp)} cas)",
                "description": ("Valeurs sémantiquement conformes mais stockées avec un encodage corrompu dans le "
                                "système B (UTF-8 relu en Latin-1) : " +
                                ", ".join(f"{a} → '{b}'" for a, b in grp[["person_id", "destination_value"]]
                                          .head(5).itertuples(index=False))),
                "n_cases": len(grp), "n_persons": grp["person_id"].nunique(), "share": None,
                "case_ids": ";".join(grp["case_id"]), "hypothesis": "Problème d'encodage dans l'interface ou l'extraction.",
                "suggested_action": "Signaler au responsable du système B ; vérifier l'encodage de l'interface (UTF-8).",
                "verdict_effect": "ECART_JUSTIFIE (différence d'encodage uniquement)", "requires_decision": False,
                "priority_score": 25,
            })
        return patterns

    # ------------------------------------------------------------ hypotheses
    @staticmethod
    def _fill_hypotheses(cases):
        mask = cases["verdict"].isin(NON_CONFORM) & (cases["ai_hypothesis"] == "")
        for i in cases.index[mask]:
            code = cases.at[i, "hypothesis_code"]
            fam = family_of(code)
            text = FAMILY_TEXT.get(fam, "")
            if fam == "REPLI_VALEUR_DEFAUT_POSTE":
                text += f" (valeur destination '{cases.at[i, 'destination_value']}' = valeur du poste)"
            if fam == "AFFECTATION_MANQUANTE":
                text += f" Affectation concernée : {cases.at[i, 'source_value']}."
            if not text and cases.at[i, "verdict"] == C.ANOMALIE:
                text = "Écart isolé sans motif récurrent détecté : erreur de saisie ou de chargement ponctuelle probable."
            cases.at[i, "ai_hypothesis"] = text
            if not cases.at[i, "ai_suggested_action"]:
                cases.at[i, "ai_suggested_action"] = FAMILY_ACTION.get(fam, cases.at[i, "proposed_action"])

    # ------------------------------------------------------------ scores
    @staticmethod
    def _score(cases, cfg):
        conf, conf_d, prio, prio_d, level = [], [], [], [], []
        for r in cases.itertuples():
            # ----- confidence in the verdict
            if r.verdict == C.CONFORME:
                base = 0.99 if r.stage1_result == "IDENTIQUE" else 0.95
            elif r.verdict == C.ECART_JUSTIFIE:
                base = 0.92
            elif r.verdict == C.ANOMALIE:
                base = 0.90
            else:
                base = 0.60
            parts = [f"base {r.verdict}={base:.2f}"]
            c = base
            if r.certainty == C.INFERRED and r.verdict != C.A_INVESTIGUER:
                c -= 0.10
                parts.append("valeur inférée −0.10")
            if r.case_type == FIELD_CASE and not pd.isna(r.match_confidence):
                f = 0.7 + 0.3 * float(r.match_confidence)
                c *= f
                parts.append(f"×appariement {f:.2f}")
            if r.ambiguity_id:
                c -= 0.05
                parts.append("ambiguïté de règle −0.05")
            if r.hypothesis_code == "VALEURS_INTERVERTIES" or (r.pattern_id and r.pattern_id.startswith(("P-REC", "P-SEG"))):
                c += 0.05
                parts.append("motif corroborant +0.05")
            c = float(min(max(c, 0.05), 0.99))
            conf.append(round(c, 2))
            conf_d.append(" ; ".join(parts) + f" = {c:.2f}")
            # ----- priority
            if r.verdict not in NON_CONFORM:
                prio.append(0)
                prio_d.append("")
                level.append("AUCUNE")
                continue
            base_p = 45 if r.verdict == C.ANOMALIE else 30
            fw = cfg.field_weights.get(r.field, cfg.default_field_weight)
            p = base_p + fw
            pd_parts = [f"verdict {base_p}", f"impact champ {fw}"]
            if r.verdict == C.ANOMALIE:
                b = round(10 * c)
                p += b
                pd_parts.append(f"confiance +{b}")
            if r.case_type == "PERSONNE_ABSENTE_DESTINATION":
                p += 20
                pd_parts.append("personne absente +20")
            if r.case_type == "APPARIEMENT_AMBIGU":
                p += 10
                pd_parts.append("appariement ambigu +10")
            if r.hypothesis_code == "VALEURS_INTERVERTIES" or (r.pattern_id and r.pattern_id.startswith("P-REC")):
                p += 5
                pd_parts.append("motif récurrent +5")
            if r.systemic:
                p -= 25
                pd_parts.append("traité au niveau du groupe systémique −25")
            p = int(min(max(p, 1), 100))
            prio.append(p)
            prio_d.append(" + ".join(pd_parts) + f" = {p}")
            level.append("HAUTE" if p >= cfg.priority_high else "MOYENNE" if p >= cfg.priority_medium else "BASSE")
        cases["confidence"] = conf
        cases["confidence_detail"] = conf_d
        cases["priority_score"] = prio
        cases["priority"] = level
        cases["priority_detail"] = prio_d

    @staticmethod
    def _summaries(cases):
        out, ai = [], []
        for r in cases.itertuples():
            if r.verdict not in NON_CONFORM:
                out.append("")
                ai.append("")
                continue
            if r.case_type == FIELD_CASE:
                head = (f"{r.field} : attendu '{r.expected_value or '∅'}' ({r.rule_id}), reçu "
                        f"'{r.destination_value or '∅'}'.")
            else:
                head = f"{r.case_type.replace('_', ' ').capitalize()} : {r.source_value or r.destination_value}."
            hyp = f" Hypothèse : {r.ai_hypothesis}" if r.ai_hypothesis else ""
            nxt = f" Prochaine étape : {r.ai_suggested_action}" if r.ai_suggested_action else ""
            out.append(head + hyp + nxt)
            ai.append(f"[Assistance IA locale — ne modifie pas le verdict déterministe]{hyp}{nxt}"
                      + (f" Groupe : {r.pattern_id}." if r.pattern_id else ""))
        cases["investigation_summary"] = out
        cases["ai_analysis"] = ai

    @staticmethod
    def _pattern_priority(pats, cases, cfg):
        scores, levels = [], []
        for p in pats.itertuples():
            if getattr(p, "priority_score", None) and not pd.isna(p.priority_score):
                scores.append(int(p.priority_score))
            elif p.pattern_type == "SYSTEMIQUE_CHAMP":
                fw = cfg.field_weights.get(p.field, cfg.default_field_weight)
                ids = set(str(p.case_ids).split(";"))
                anomalies = (cases.loc[cases["case_id"].isin(ids), "verdict"] == C.ANOMALIE).any()
                s = (45 if anomalies else 30) + fw + int(10 * (p.share or 0))
                bp = getattr(p, "business_priority", "")
                if bp == "BASSE":
                    s = min(s, cfg.priority_medium - 1)      # known cause, low business priority
                elif bp == "MOYENNE":
                    s = min(s, cfg.priority_high - 1)
                scores.append(s)
            else:
                ids = set(str(p.case_ids).split(";"))
                member = cases.loc[cases["case_id"].isin(ids), "priority_score"]
                s = int(member.max()) if len(member) else 30
                if p.pattern_type == "SYSTEMIQUE_SEGMENT":
                    s = max(s + 25, 60)  # members were discounted; the group itself is high value
                scores.append(min(s, 100))
            levels.append("HAUTE" if scores[-1] >= cfg.priority_high else
                          "MOYENNE" if scores[-1] >= cfg.priority_medium else "BASSE")
        pats = pats.copy()
        pats["priority_score"] = scores
        pats["priority"] = levels
        return pats.drop(columns=[c for c in pats.columns if c.startswith("_")])


# ======================================================================
# Learning from expert feedback (optional, local scikit-learn model)
# ======================================================================
FEATURES = ["field", "rule_id", "case_type", "stage1_result", "rule_outcome", "certainty", "hypothesis_code",
            "src_type"]


def _feature_dicts(cases: pd.DataFrame) -> list[dict]:
    rows = []
    for r in cases[FEATURES + ["systemic", "ambiguity_id", "source_value", "destination_value"]].itertuples(index=False):
        d = {f"{k}={getattr(r, k)}": 1 for k in FEATURES}
        d["family=" + family_of(r.hypothesis_code)] = 1
        d["systemic"] = int(bool(r.systemic))
        d["ambiguity"] = int(bool(r.ambiguity_id))
        d["source_empty"] = int(not r.source_value)
        d["dest_empty"] = int(not r.destination_value)
        rows.append(d)
    return rows


class FeedbackModel:
    """Small explainable classifier (decision tree) trained on expert decisions
    (or simulated labels). It only *suggests* a verdict for A_INVESTIGUER cases."""

    def __init__(self, max_depth: int = 4, min_samples: int = 5):
        self.max_depth = max_depth
        self.min_samples = min_samples
        self.model = None
        self.vectorizer = None
        self.is_trained = False
        self.n_train = 0

    def fit(self, cases: pd.DataFrame, labels: pd.Series) -> "FeedbackModel":
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.tree import DecisionTreeClassifier
        mask = labels.notna() & (labels != "")
        if mask.sum() < self.min_samples or labels[mask].nunique() < 2:
            self.is_trained = False
            return self
        self.vectorizer = DictVectorizer(sparse=False)
        X = self.vectorizer.fit_transform(_feature_dicts(cases[mask]))
        self.model = DecisionTreeClassifier(max_depth=self.max_depth, random_state=0, class_weight="balanced")
        self.model.fit(X, labels[mask])
        self.is_trained = True
        self.n_train = int(mask.sum())
        return self

    def predict(self, cases: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = self.vectorizer.transform(_feature_dicts(cases))
        proba = self.model.predict_proba(X)
        return self.model.classes_[proba.argmax(1)], proba.max(1)

    def suggest(self, cases: pd.DataFrame) -> None:
        mask = cases["verdict"] == C.A_INVESTIGUER
        if not mask.any():
            return
        labels, conf = self.predict(cases[mask])
        cases.loc[mask, "ai_suggested_verdict"] = labels
        cases.loc[mask, "ai_suggestion_confidence"] = np.round(conf, 2)

    def rules_text(self) -> str:
        from sklearn.tree import export_text
        if not self.is_trained:
            return "(modèle non entraîné)"
        return export_text(self.model, feature_names=list(self.vectorizer.get_feature_names_out()))


def train_from_decisions(cases: pd.DataFrame) -> FeedbackModel:
    """Train on cases that received a human decision (reviewed_verdict)."""
    if "human_decision" not in cases.columns:
        return FeedbackModel()
    labels = cases["reviewed_verdict"].where(cases["human_decision"] != "")
    return FeedbackModel().fit(cases, labels)


# ======================================================================
# Plug-in point for a future language model (no external call in V1)
# ======================================================================
REDACT_COLUMNS = ["nom"]


def redact(text: str, case: pd.Series) -> str:
    out = str(text)
    for col in REDACT_COLUMNS:
        val = str(case.get(col, "") or "")
        for token in val.split():
            if len(token) > 2:
                out = out.replace(token, "[NOM]")
    out = re.sub(r"[\w.\-]+@[\w.\-]+", "[COURRIEL]", out)
    return out


class LLMAssistant(AnalysisAssistant):
    """Future LLM integration. V1 runs the local assistant and prepares redacted
    prompts for A_INVESTIGUER cases; no data leaves the machine unless a client
    is explicitly provided (and it must be an approved, internal endpoint)."""
    name = "llm-placeholder"

    def __init__(self, client=None, fallback: AnalysisAssistant | None = None):
        self.client = client
        self.fallback = fallback or LocalAssistant()

    @staticmethod
    def build_prompt(case: pd.Series) -> str:
        body = (f"Champ: {case['field']}\nRègle: {case['rule_id']} ({case['mapping_ref']})\n"
                f"Valeur source: {case['source_value']}\nValeur destination: {case['destination_value']}\n"
                f"Valeur attendue: {case['expected_value']}\nNote de règle: {case['rule_note']}\n"
                f"Contexte: {case['context']}\nHypothèse locale: {case.get('ai_hypothesis', '')}\n"
                "Question: proposer une explication et une action d'investigation, sans inventer de règle métier.")
        return redact(body, case)

    def analyze(self, cases, cfg):
        cases, patterns = self.fallback.analyze(cases, cfg)
        mask = cases["verdict"] == C.A_INVESTIGUER
        cases["llm_prompt_preview"] = ""
        cases.loc[mask, "llm_prompt_preview"] = cases[mask].apply(self.build_prompt, axis=1)
        if self.client is not None:
            raise NotImplementedError("Intégration LLM non activée en V1 (aucun appel externe).")
        return cases, patterns
