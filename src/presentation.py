"""Business-facing wording for the Streamlit app (pure functions, no Streamlit).

Translates engine outputs (codes, rule IDs, JSON evidence) into short French
explanations for functional analysts. Nothing here changes a verdict: it only
describes what the engine already decided. Technical values stay available in
the audit views.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from . import config as C

# ----------------------------------------------------------------------------- colours & labels
COLORS = {   # dark-theme accents
    C.CONFORME: "#4CAF6A", C.ECART_JUSTIFIE: "#4C9AFF", C.ANOMALIE: "#EF5350", C.A_INVESTIGUER: "#FFA040",
    "REVU": "#9AA4B2", "IA": "#B47FE0", "ACTION": "#C9D1D9", "NEUTRE": "#C9D1D9",
}
TINTS = {    # dark tinted backgrounds
    C.CONFORME: "#15261C", C.ECART_JUSTIFIE: "#14223A", C.ANOMALIE: "#33191B", C.A_INVESTIGUER: "#33240F",
    "REVU": "#20262E", "IA": "#261C33", "ACTION": "#1E252E", "NEUTRE": "#1A2028",
}
VERDICT_LABEL = {C.CONFORME: "Conforme", C.ECART_JUSTIFIE: "Écart justifié", C.ANOMALIE: "Anomalie",
                 C.A_INVESTIGUER: "À investiguer"}
VERDICT_ICON = {C.CONFORME: "🟢", C.ECART_JUSTIFIE: "🔵", C.ANOMALIE: "🔴", C.A_INVESTIGUER: "🟠"}
METHOD_LABEL = {C.RAW: "Comparaison directe", C.DETERMINISTIC: "Règle métier", C.AI_ASSISTED: "Analyse assistée"}
METHOD_HELP = {
    C.RAW: "Valeur source et destination directement comparables.",
    C.DETERMINISTIC: "Une règle documentée permet de confirmer ou d'expliquer le résultat.",
    C.AI_ASSISTED: "Le cas nécessite une analyse supplémentaire, un regroupement ou une priorisation.",
}
PRIORITY_LABEL = {"HAUTE": "Haute", "MOYENNE": "Moyenne", "BASSE": "Basse", "AUCUNE": "—"}
CERTAINTY_LABEL = {C.CERTAIN: "Certain", C.DERIVED: "Dérivé d'une règle", C.INFERRED: "Inféré",
                   C.TO_CONFIRM: "À confirmer"}
CERTAINTY_ICON = {C.CERTAIN: "🟢", C.DERIVED: "🔵", C.INFERRED: "🟣", C.TO_CONFIRM: "🟠"}
CERTAINTY_HELP = {
    C.CERTAIN: "Copie ou correspondance directe.",
    C.DERIVED: "Calculé à partir d'une règle documentée.",
    C.INFERRED: "Déduit d'un motif ou du contexte ; validation recommandée.",
    C.TO_CONFIRM: "Information insuffisante pour proposer une valeur fiable.",
}
PROPOSAL_STATUS_LABEL = {"PROPOSE": "À valider", "ACCEPTE": "Acceptée", "MODIFIE": "Modifiée", "REJETE": "Rejetée"}
ACTION_KIND_LABEL = {"CORRIGER_CHAMP": "Corriger une valeur", "CREER_AFFECTATION": "Créer une affectation",
                     "CORRIGER_GROUPE": "Corriger une valeur (groupe)", "VERIFICATION": "Vérification manuelle"}
ASSIGNMENT_TYPE_LABEL = {"P": "Principale", "A": "Temporaire", "S": "Secondaire"}
ASSIGNMENT_TYPE_ADJ = {"P": "principale", "A": "temporaire", "S": "secondaire"}

# (label, grammatical gender) — used for titles such as "Type d'emploi incohérent"
FIELD_LABEL = {
    "givenName": ("Prénom", "m"), "surname": ("Nom de famille", "m"), "contactEmail": ("Courriel", "m"),
    "onboardDate": ("Date d'embauche", "f"), "siteName": ("Nom de l'emplacement", "m"),
    "siteCode": ("Code d'emplacement", "m"), "divisionId": ("Code du département", "m"),
    "divisionName": ("Libellé du département", "m"), "divisionCode": ("Code d'imputation", "m"),
    "positionId": ("Identifiant du rôle", "m"), "positionCode": ("Code du rôle", "m"),
    "positionName": ("Nom du rôle (positionName)", "m"), "statusReasonCode": ("Motif d'absence", "m"),
    "expectedReturnDate": ("Date de retour prévue", "f"), "detailedStatus": ("Situation d'emploi", "f"),
    "contractTypeCode": ("Type d'emploi", "m"), "isPrimaryAssignment": ("Indicateur d'affectation principale", "m"),
    "isTemporaryAssignment": ("Indicateur d'affectation temporaire", "m"),
    "assignmentStartDate": ("Date de début d'affectation", "f"), "assignmentEndDate": ("Date de fin d'affectation", "f"),
    "termEndDate": ("Date de fin du détail du poste", "f"), "payGradeId": ("Groupe de rémunération", "m"),
    "weeklyHoursOverride": ("Heures par semaine", "fp"), "dailyHoursOverride": ("Heures par jour", "fp"),
    "__assignment__": ("Affectation", "f"),
}
DECISION_SHORT = {C.DECISION_CONFIRM: "Anomalie confirmée", C.DECISION_ACCEPT: "Écart considéré comme légitime",
                  C.DECISION_MODIFY: "Correction modifiée", C.DECISION_REVIEW: "Informations insuffisantes",
                  C.DECISION_REOPEN: "Décision annulée (réouverture)"}
MISSING_TYPES = ("AFFECTATION_ABSENTE_DESTINATION", "PERSONNE_ABSENTE_DESTINATION")
CLARIFICATION_SOURCE = f"Confirmation officielle Loto-Québec (canal Discord du défi) — {C.CLARIFICATION_DATE}"


def field_label(field: str) -> str:
    return FIELD_LABEL.get(field, (field, "m"))[0]


def verdict_text(v: str) -> str:
    return f"{VERDICT_ICON.get(v, '')} {VERDICT_LABEL.get(v, v)}".strip()


def fmt(value) -> str:
    """User-facing value: ISO dates as JJ/MM/AAAA, empty as '∅'."""
    s = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    if s in ("", "nan", "None"):
        return "∅"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m[3]}/{m[2]}/{m[1]}" if m else s


def parse_json(text) -> dict:
    try:
        return json.loads(text) if isinstance(text, str) and text else {}
    except ValueError:
        return {}


def confidence_level(conf: float) -> tuple[str, str]:
    if conf >= 0.85:
        return "Élevée", C.CONFORME
    if conf >= 0.55:
        return "Moyenne", C.A_INVESTIGUER
    return "Faible", C.ANOMALIE


def confidence_text(conf: float) -> str:
    return f"{confidence_level(conf)[0]} — {round(conf * 100)} %"


# ----------------------------------------------------------------------------- review state
def is_treated(decision: str) -> bool:
    """A decision closes the case, except 'informations insuffisantes' which keeps it in the queue."""
    return bool(decision) and decision != C.DECISION_REVIEW


def review_label(case) -> str:
    d = case.get("human_decision", "")
    if not d:
        return "Non revu"
    if d == C.DECISION_REVIEW:
        return "⏳ En attente d'informations"
    src = str(case.get("human_decision_source", ""))
    return "✅ Revu (décision de groupe)" if src.startswith("groupe") else "✅ Revu"


def workload(cases: pd.DataFrame, patterns: pd.DataFrame) -> dict:
    """Human decisions required vs remaining (individual cases + systemic groups)."""
    nc = cases[cases["verdict"].isin([C.ANOMALIE, C.A_INVESTIGUER])]
    individual = nc[~nc["systemic"].astype(bool)]
    groups = patterns[patterns["requires_decision"].astype(bool)] if not patterns.empty else patterns
    ind_done = int(individual["human_decision"].map(is_treated).sum())
    grp_done = int(groups["human_decision"].map(is_treated).sum()) if len(groups) and "human_decision" in groups else 0
    return {
        "individual": len(individual), "groups": len(groups), "required": len(individual) + len(groups),
        "individual_done": ind_done, "groups_done": grp_done,
        "remaining": len(individual) + len(groups) - ind_done - grp_done,
        "systemic_cases": int(nc["systemic"].astype(bool).sum()),
    }


# ----------------------------------------------------------------------------- titles
def problem_title(case) -> str:
    f, v, ct = case["field"], case["verdict"], case["case_type"]
    if ct in MISSING_TYPES:
        if ct == "PERSONNE_ABSENTE_DESTINATION":
            return "Employé absent du système Temps"
        return f"Affectation {ASSIGNMENT_TYPE_ADJ.get(case.get('src_type', ''), '')} non retrouvée".replace("  ", " ")
    if ct == "AFFECTATION_NON_ATTENDUE_DESTINATION":
        return "Affectation Temps sans origine RH"
    if ct == "PERSONNE_ABSENTE_SOURCE":
        return "Employé absent du système RH"
    if ct == "APPARIEMENT_AMBIGU":
        return "Correspondance d'affectation ambiguë"
    label, gender = FIELD_LABEL.get(f, (f, "m"))
    e = {"m": "", "f": "e", "mp": "s", "fp": "es"}.get(gender, "")
    hyp = case.get("hypothesis_code", "")
    if v == C.CONFORME:
        return f"{label} conforme{e}"
    if v == C.ECART_JUSTIFIE:
        if case.get("quality_flag"):
            return f"{label} — encodage à corriger"
        return f"{label} — transformation légitime"
    if v == C.ANOMALIE:
        if hyp == "VALEURS_INTERVERTIES":
            return f"{label} — valeurs interverties"
        if hyp == "ANONYMISATION":
            return f"{label} — identifiant incorrect"
        if hyp == "DESTINATION_MANQUANTE":
            return f"{label} absent{e} du système Temps"
        return f"{label} incohérent{e}"
    if not case.get("source_value"):
        return f"{label} — valeur absente dans RH"
    return f"{label} — à vérifier"


# ----------------------------------------------------------------------------- rule in plain words
def rule_plain(case) -> str:
    rid = case["rule_id"]
    f = field_label(case["field"])
    inp = parse_json(case.get("inputs"))
    exp = fmt(case.get("expected_value"))
    if rid == "R_DIRECT":
        src = case.get("source_fields", "")
        return f"Le champ « {f} » doit être recopié tel quel du système RH ({src}) vers le système Temps."
    if rid == "R_CONTRACT_TYPE":
        cat = inp.get("CatégorieEmploi", "")
        perm = "permanent" if "(1)" in inp.get("EstPermanent", "") else "non permanent"
        ft = "à temps plein" if "(1)" in inp.get("EstTempsPlein", "") else "à temps partiel"
        if cat.upper() == "V":
            return f"Pour un employé {perm}, {ft} et de catégorie {cat}, le code attendu dans le système Temps est {exp}."
        return f"Pour un employé de catégorie {cat}, le code attendu dans le système Temps est {exp}."
    if rid == "R_EMAIL":
        return ("Le courriel est construit ainsi : initiale du prénom + nom + 3 derniers chiffres du matricule + "
                "@loto-quebec.com (accents retirés). Un préfixe d'environnement comme « dev-08-v2_ » est autorisé.")
    if rid == "R_CONCAT_DIVISION":
        return "Le libellé attendu est « code du département - libellé du département », tel que défini au mapping."
    if rid == "R_CONCAT_POSITION":
        return "Le nom du rôle attendu est « code emploi - intitulé de l'emploi », tel que défini au mapping."
    if rid == "R_SITUATION_EMPLOI":
        acc = inp.get("CodeSuspensionAccès", "?")
        return (f"La situation d'emploi est déduite du code d'accès RH ({acc}) via la table « Règles situation "
                f"d'emploi » ; pour une absence, le motif et la date de retour sont aussi transmis.")
    if rid == "R_ASSIGNMENT_TYPE":
        t = inp.get("TypeAffectation", "?")
        return (f"Le type d'affectation RH « {t} » ({ASSIGNMENT_TYPE_LABEL.get(t, t)}) détermine les indicateurs "
                "« principale » et « temporaire » dans Temps.")
    if rid == "R_ASSIGNMENT_START":
        return ("La date de début dans Temps est la plus récente entre la date d'entrée dans le poste (RH) et la date "
                "d'effet du détail du poste courant (historique du poste).")
    if rid == "R_ASSIGNMENT_END":
        return ("La date de fin est la plus ancienne entre la date de sortie du poste et la fin de l'unité "
                "administrative courante (vide si aucune).")
    if rid == "R_MATCHING":
        return "Chaque affectation du système RH doit avoir une affectation correspondante dans le système Temps."
    return case.get("rule_name", "")


def conclusion(case) -> str:
    v = case["verdict"]
    if v == C.CONFORME:
        return "Les deux systèmes concordent."
    if v == C.ECART_JUSTIFIE:
        if case.get("quality_flag"):
            return "La valeur est correcte mais stockée avec un encodage corrompu dans Temps (à signaler)."
        return "La différence est expliquée par la règle : la valeur du système Temps est correcte."
    if v == C.ANOMALIE:
        return "La valeur présente dans le système Temps ne respecte pas la règle métier documentée."
    return "Les règles disponibles ne permettent pas de trancher : une revue humaine est nécessaire."


# ----------------------------------------------------------------------------- confidence factors
def confidence_factors(case) -> tuple[list[tuple[str, str]], str]:
    """Business factors behind the confidence score: [('+' or '−', text)], conclusion."""
    v, ct = case["verdict"], case["case_type"]
    f: list[tuple[str, str]] = []
    if ct in MISSING_TYPES:
        f += [("+", "Une affectation RH ne trouve pas de correspondance dans Temps"),
              ("+", "Les affectations RH sont bien distinctes (poste, type, date)"),
              ("−", "Aucune règle ne précise si ce type d'affectation devait être transféré"),
              ("−", "L'absence dans Temps pourrait être légitime")]
        return f, "Revue humaine nécessaire"
    if ct == "APPARIEMENT_AMBIGU":
        return [("+", "Plusieurs correspondances RH/Temps également plausibles"),
                ("−", "Aucune preuve ne permet de les départager")], "Revue humaine nécessaire"
    if ct != "COMPARAISON_CHAMP":
        return [("−", "Ligne sans correspondance identifiable")], "Revue humaine nécessaire"
    hyp = case.get("hypothesis_code", "")
    if v == C.A_INVESTIGUER:
        if not case.get("source_value"):
            f.append(("−", "Valeur absente dans le système RH"))
        if hyp.startswith("DEST_EQUALS:heures"):
            f.append(("+", "La valeur Temps correspond à la valeur par défaut du poste"))
        f.append(("−", "Aucune règle ne permet de conclure avec les données disponibles"))
        return f, "Revue humaine nécessaire"
    f.append(("+", "Comparaison directe des deux valeurs (copie attendue)" if case["rule_type"] == C.RAW
              else "Règle métier explicite (Mapping.xlsx)"))
    if case.get("certainty") in (C.CERTAIN, C.DERIVED):
        f.append(("+", "Valeur attendue calculable sans ambiguïté"))
    else:
        f.append(("−", "Valeur attendue dépendant d'une interprétation"))
    mc = case.get("match_confidence")
    if mc is not None and not pd.isna(mc):
        if float(mc) >= 0.999:
            f.append(("+", "Correspondance RH/Temps certaine (toutes les preuves concordent)"))
        else:
            f.append(("−", f"Correspondance RH/Temps partielle ({round(float(mc) * 100)} % des preuves)"))
    pid = str(case.get("pattern_id", ""))
    if hyp == "VALEURS_INTERVERTIES":
        f.append(("+", "Motif cohérent trouvé chez un autre employé (valeurs interverties)"))
    if pid.startswith("P-REC"):
        f.append(("+", "Même cause observée sur d'autres dossiers"))
    if case.get("systemic"):
        f.append(("+", "Même écart observé de façon systématique sur le jeu de données"))
    if case.get("quality_flag"):
        f.append(("−", "Encodage corrompu dans le système Temps (valeur lisible après correction)"))
    if not any(s == "−" for s, _ in f):
        f.append(("+", "Aucune preuve contradictoire"))
    concl = {C.ANOMALIE: "Anomalie fiable : correction recommandée après validation humaine.",
             C.ECART_JUSTIFIE: "Écart expliqué par une règle documentée.",
             C.CONFORME: "Valeurs concordantes."}.get(v, "")
    return f, concl


# ----------------------------------------------------------------------------- AI card
def ai_card(case, cases: pd.DataFrame, patterns: pd.DataFrame) -> dict | None:
    """Structured version of the local assistant's analysis (no internal IDs)."""
    v = case["verdict"]
    if v not in (C.ANOMALIE, C.A_INVESTIGUER):
        return None
    hyp = case.get("hypothesis_code", "")
    pid = str(case.get("pattern_id", ""))
    pat = patterns[patterns["pattern_id"] == pid].iloc[0] if pid and not patterns.empty \
        and (patterns["pattern_id"] == pid).any() else None
    members = cases[cases["case_id"].isin(str(pat["case_ids"]).split(";"))] if pat is not None else cases.iloc[0:0]
    label = field_label(case["field"])

    def obs(r):
        return f"Employé {r['person_id']} : {fmt(r['expected_value'])} attendu → {fmt(r['destination_value'])} reçu"

    card = {"hypothesis": "", "observed": [], "cause": "", "action": case.get("ai_suggested_action", ""),
            "technical": {"groupe": pid, "code d'hypothèse": hyp, "texte brut": case.get("ai_analysis", "")}}
    if hyp == "VALEURS_INTERVERTIES":
        card["hypothesis"] = "Les valeurs de deux employés semblent avoir été interverties."
        card["observed"] = [obs(r) for _, r in members.iterrows()] or [obs(case)]
        card["cause"] = "Erreur d'ordre ou de clé lors du chargement des données."
        card["action"] = "Vérifier les deux enregistrements ensemble avant correction."
    elif pid.startswith(("P-SYS", "P-SEG")) and pat is not None:
        anonym = hyp == "ANONYMISATION"
        card["hypothesis"] = ("L'identifiant utilisé dans les courriels ne correspond pas au matricule, de la même "
                              "façon pour tous les dossiers." if anonym else
                              f"Erreur dans une transformation globale de « {label} » plutôt que des erreurs "
                              "individuelles.")
        share = pat.get("share")
        card["observed"] = [f"{int(pat['n_cases'])} dossiers affectés de la même façon"
                            + (f" ({round(float(share) * 100)} % des lignes)" if share and not pd.isna(share) else "")]
        card["observed"] += [obs(r) for _, r in members.head(2).iterrows()]
        clar = C.OFFICIAL_CLARIFICATIONS.get(case["field"], {})
        card["cause"] = (f"Cause connue : {clar['known_cause']} ({CLARIFICATION_SOURCE})." if clar.get("known_cause")
                         else str(pat.get("hypothesis", "")))
        card["action"] = str(pat.get("suggested_action", card["action"]))
    elif hyp.startswith("DEST_EQUALS:heures"):
        ctx = parse_json(case.get("context"))
        default = ctx.get("heures_semaine_poste") or ctx.get("heures_jour_poste")
        card["hypothesis"] = ("Le système Temps semble avoir repris la valeur par défaut du poste au lieu de la "
                              "valeur propre à l'employé.")
        card["observed"] = [f"Valeur RH : {fmt(case['source_value'])} — valeur Temps : {fmt(case['destination_value'])}"
                            + (f" — valeur par défaut du poste : {fmt(default)}" if default is not None else "")]
        others = sorted(set(members["person_id"]) - {case["person_id"]}) if len(members) else []
        if others:
            card["observed"].append(f"Même situation chez {len(others)} autre(s) employé(s) : {', '.join(others[:5])}")
        card["cause"] = "Règle de repli de l'interface sur la valeur du détail du poste."
    elif hyp == "AFFECTATION_MANQUANTE":
        t = ASSIGNMENT_TYPE_ADJ.get(case.get("src_type", ""), "")
        card["hypothesis"] = f"L'affectation {t} n'a pas été transmise au système Temps."
        card["observed"] = [f"Affectation RH : {case['source_value']}", "Aucune ligne correspondante dans Temps"]
        card["cause"] = ("Omission de l'interface, filtrage des affectations temporaires/secondaires ou décalage "
                         "de chargement.")
        card["action"] = "Valider ou rejeter l'affectation candidate proposée."
    else:
        from .ai_assist import FAMILY_TEXT, family_of
        card["hypothesis"] = FAMILY_TEXT.get(family_of(hyp), "") or case.get("ai_hypothesis", "")
        card["observed"] = [f"Valeur attendue : {fmt(case['expected_value'])}",
                            f"Valeur Temps : {fmt(case['destination_value'])}"]
        card["cause"] = ("Erreur ponctuelle de saisie ou de chargement (aucun motif récurrent)." if v == C.ANOMALIE
                         else "Information insuffisante dans les données disponibles.")
    return card


# ----------------------------------------------------------------------------- decisions
def correction_text(props: pd.DataFrame | None) -> str:
    """Short description of the proposed correction: « Type d'emploi » WHX → JWN, or the candidate row."""
    if props is None or not len(props):
        return ""
    first = props.iloc[0]
    if first["action_type"] == "CREER_AFFECTATION":
        n = int((~props["derivation"].str.startswith("champ non couvert")).sum())
        return f"la ligne candidate ({n} champs dérivés du mapping)"
    return f"« {field_label(first['field'])} » {fmt(first['current_value'])} → {fmt(first['proposed_value'])}"


def decision_options(case, props: pd.DataFrame | None = None) -> list[tuple[str, str, str]]:
    """Context-aware (decision code, label, exact effect of clicking)."""
    corr = correction_text(props)
    keep = "Le fichier officiel reste inchangé."
    review = (C.DECISION_REVIEW, "Informations insuffisantes",
              "Ne clôture pas le cas. Il reste dans la file d'investigation.")
    if case["case_type"] in MISSING_TYPES:
        return [(C.DECISION_CONFIRM, "Valider la création proposée",
                 f"Marque le cas comme traité et ajoute {corr or 'la ligne candidate'} au plan de corrections. {keep}"),
                (C.DECISION_ACCEPT, "Confirmer que l'absence est légitime",
                 "Marque le cas comme traité sans créer de ligne. Votre justification est conservée dans le journal "
                 "d'audit."),
                (C.DECISION_MODIFY, "Modifier la proposition",
                 f"Ajoute la ligne candidate au plan de corrections avec les valeurs que vous aurez ajustées. {keep}"),
                review]
    opts = [(C.DECISION_CONFIRM, "Confirmer l'anomalie et accepter la correction" if corr else "Confirmer l'anomalie",
             (f"Marque le cas comme traité et ajoute la correction {corr} au plan de corrections. {keep}" if corr else
              "Marque le cas comme traité. Aucune correction automatique n'est disponible : la donnée doit être "
              "corrigée à la source.")),
            (C.DECISION_ACCEPT, "Considérer l'écart comme légitime",
             "Marque le cas comme traité sans correction. Votre justification est conservée dans le journal d'audit.")]
    if corr:
        opts.append((C.DECISION_MODIFY, "Modifier la correction",
                     "Confirme qu'une correction est nécessaire mais vous permet de remplacer la valeur proposée "
                     "avant validation."))
    return opts + [review]


def group_decision_options(pattern, n_corrections: int = 0) -> list[tuple[str, str, str]]:
    n = int(pattern.get("n_cases", 0))
    known = bool(C.OFFICIAL_CLARIFICATIONS.get(str(pattern.get("field")), {}).get("known_cause"))
    corr = (f" et ajoute les {n_corrections} corrections proposées au plan de corrections" if n_corrections else "")
    first = ("Accepter la clarification pour le groupe" if known else "Confirmer le problème systémique",
             f"Marque les {n} cas comme traités{corr}. Les fichiers officiels restent inchangés.")
    return [(C.DECISION_CONFIRM, *first),
            (C.DECISION_ACCEPT, "Considérer les écarts comme légitimes",
             f"Marque les {n} cas comme traités sans aucune correction. Justification conservée dans l'audit."),
            (C.DECISION_REVIEW, "Informations insuffisantes", "Ne clôture pas le groupe : il reste à décider.")]


def decision_label(case, decision: str) -> str:
    if case["case_type"] in MISSING_TYPES and decision != C.DECISION_REOPEN:
        return {C.DECISION_CONFIRM: "Création validée", C.DECISION_ACCEPT: "Absence confirmée légitime",
                C.DECISION_MODIFY: "Proposition modifiée", C.DECISION_REVIEW: "Informations insuffisantes"}[decision]
    return DECISION_SHORT.get(decision, decision)


def outcome_lines(case, decision: str, props: pd.DataFrame | None = None, mods: dict | None = None) -> list[str]:
    """What actually happened after a decision (persistent feedback panel)."""
    corr = correction_text(props)
    missing = case["case_type"] in MISSING_TYPES
    if decision == C.DECISION_REOPEN:
        return ["Décision annulée : le cas est de nouveau ouvert dans la file d'investigation.",
                "L'historique complet reste dans le journal d'audit."]
    if decision == C.DECISION_REVIEW:
        return ["Décision enregistrée. Le cas reste en attente d'informations dans la file d'investigation."]
    if decision == C.DECISION_ACCEPT:
        return [decision_label(case, decision) + ".",
                "Aucune ligne ne sera créée." if missing else "Aucune correction ne sera appliquée.",
                "Justification conservée dans le journal d'audit."]
    lines = [decision_label(case, decision) + "."]
    if decision == C.DECISION_MODIFY and mods:
        lines.append("Valeurs ajustées ajoutées au plan de corrections : "
                     + ", ".join(f"{field_label(k)} = {v}" for k, v in mods.items()) + ".")
    elif corr:
        lines.append(("Ligne candidate ajoutée" if missing else f"Correction {corr} ajoutée") +
                     " au plan de corrections.")
    else:
        lines.append("Aucune correction automatique : la donnée doit être corrigée à la source.")
    lines.append("Le fichier officiel n'a pas été modifié.")
    return lines


def rows_employees(n_rows: int, n_emp: int, noun: str = "dossiers") -> str:
    """'22 dossiers affectés, correspondant à 20 employés uniques' when rows ≠ employees."""
    if n_rows == n_emp:
        return f"{n_rows} {noun} ({n_emp} employés)"
    return f"{n_rows} {noun}, correspondant à {n_emp} employés uniques"


# ----------------------------------------------------------------------------- dashboard figures
def funnel(cases: pd.DataFrame) -> dict:
    """Exact partition of all controls (always reconciles by construction)."""
    identical = cases["stage1_result"].isin(["IDENTIQUE", "VIDE_DES_DEUX_COTES"])
    conf = cases["verdict"] == C.CONFORME
    diff = ~identical
    out = {
        "total": len(cases),
        "identical": int(identical.sum()),
        "identical_conform": int((identical & conf).sum()),
        "identical_contradicted": int((identical & ~conf).sum()),   # raw-identical but a rule says otherwise
        "raw_diff": int(diff.sum()),
        "normalized": int((diff & conf & (cases["stage1_result"] == "IDENTIQUE_APRES_NORMALISATION")).sum()),
        "derived_conform": int((diff & conf & (cases["stage1_result"] != "IDENTIQUE_APRES_NORMALISATION")).sum()),
        "justified": int((diff & (cases["verdict"] == C.ECART_JUSTIFIE)).sum()),
        "anomalies": int((diff & (cases["verdict"] == C.ANOMALIE)).sum()),
        "investigate": int((diff & (cases["verdict"] == C.A_INVESTIGUER)).sum()),
    }
    out["check_total"] = out["identical"] + out["raw_diff"] == out["total"]
    out["check_diff"] = (out["normalized"] + out["derived_conform"] + out["justified"] + out["anomalies"]
                         + out["investigate"]) == out["raw_diff"]
    return out


def attention_category(case) -> str:
    if case["case_type"] in MISSING_TYPES:
        return "Affectation manquante dans Temps"
    if case["case_type"] == "APPARIEMENT_AMBIGU":
        return "Correspondance d'affectation ambiguë"
    if case["case_type"] != "COMPARAISON_CHAMP":
        return "Affectation sans origine"
    if case["verdict"] == C.A_INVESTIGUER and not case.get("source_value"):
        return "Donnée source manquante (RH)"
    return field_label(case["field"])


def remaining_by_category(cases: pd.DataFrame, patterns: pd.DataFrame) -> pd.DataFrame:
    """Open human decisions grouped by business category (individual cases + systemic groups)."""
    nc = cases[cases["verdict"].isin([C.ANOMALIE, C.A_INVESTIGUER]) & ~cases["systemic"].astype(bool)
               & ~cases["human_decision"].map(is_treated)]
    rows = [{"categorie": k, "decisions": len(g), "type": "cas individuels"}
            for k, g in nc.groupby(nc.apply(attention_category, axis=1))]
    if not patterns.empty:
        g = patterns[patterns["requires_decision"].astype(bool) & ~patterns["human_decision"].map(is_treated)]
        for _, p in g.iterrows():
            name = str(p["title"]).split(" (")[0].replace("Problème systémique — ", "")
            rows.append({"categorie": f"Problème systémique : {name[:1].upper() + name[1:]}", "decisions": 1,
                         "type": f"groupe de {int(p['n_cases'])} cas"})
    return pd.DataFrame(rows, columns=["categorie", "decisions", "type"]).sort_values("decisions", ascending=False)


# ----------------------------------------------------------------------------- investigation clues
def clue_info(pattern, members: pd.DataFrame) -> dict:
    """Plain-French description of a non-decisional pattern (investigation clue)."""
    t = pattern["pattern_type"]
    label = field_label(str(pattern["field"]).split(",")[0].strip())
    people = sorted(members["person_id"].unique())
    if t == "VALEURS_INTERVERTIES":
        return {"title": f"Valeurs probablement interverties entre deux employés ({label} : {' ↔ '.join(people)})",
                "observation": "Chaque employé a reçu la valeur attendue chez l'autre.",
                "why": "Indique une erreur de chargement (ordre ou clé) plutôt que deux erreurs indépendantes.",
                "suggestion": "Corriger les deux enregistrements ensemble et vérifier la clé de chargement."}
    if t == "QUALITE_DONNEES":
        return {"title": f"{len(members)} valeurs contiennent un problème d'encodage",
                "observation": "La valeur est correcte une fois l'encodage réparé (ex. « complÃ¨te » au lieu de "
                               "« complète »).",
                "why": "Sans conséquence sur le verdict, mais l'affichage dans le système Temps est dégradé.",
                "suggestion": "Signaler au responsable de l'interface (encodage UTF-8)."}
    if "REPLI_VALEUR_DEFAUT_POSTE" in str(pattern["pattern_id"]):
        return {"title": "Plusieurs écarts d'heures correspondent à la valeur par défaut du poste",
                "observation": f"{len(members)} cas ({len(people)} employés) : la valeur Temps égale la valeur "
                               "par défaut du détail du poste au lieu de la valeur de l'employé.",
                "why": "Suggère une règle de repli dans l'interface : une seule cause pour plusieurs cas.",
                "suggestion": "Vérifier pourquoi l'interface n'utilise pas les heures propres à l'employé."}
    return {"title": f"Même cause probable sur {len(members)} cas ({label})",
            "observation": str(pattern.get("description", "")),
            "why": "Plusieurs cas semblent partager la même origine.",
            "suggestion": str(pattern.get("suggested_action", ""))}


# ----------------------------------------------------------------------------- actions# ----------------------------------------------------------------------------- actions
def build_actions(cases: pd.DataFrame, proposals: pd.DataFrame, patterns: pd.DataFrame,
                  group_systemic: bool = True) -> pd.DataFrame:
    """One row per corrective action: field correction, assignment creation, systemic group correction or
    manual verification (non-conforming case without a proposal)."""
    rows = []
    by_case = cases.set_index("case_id")
    systemic_members = {}
    if group_systemic and not patterns.empty:
        for _, p in patterns[patterns["pattern_type"].isin(["SYSTEMIQUE_CHAMP", "SYSTEMIQUE_SEGMENT"])].iterrows():
            for cid in str(p["case_ids"]).split(";"):
                systemic_members[cid] = p
    done_groups = set()

    def agg_status(st):
        st = set(st)
        if st == {"ACCEPTE"}:
            return "ACCEPTE"
        if "MODIFIE" in st:
            return "MODIFIE"
        if st == {"REJETE"}:
            return "REJETE"
        return "PROPOSE"

    rank = {C.TO_CONFIRM: 0, C.INFERRED: 1, C.DERIVED: 2, C.CERTAIN: 3}
    for pid, grp in proposals.groupby("proposal_id", sort=False):
        first = grp.iloc[0]
        cid = first["case_id"]
        case = by_case.loc[cid] if cid in by_case.index else None
        if cid in systemic_members:
            p = systemic_members[cid]
            if p["pattern_id"] in done_groups:
                continue
            done_groups.add(p["pattern_id"])
            members = proposals[proposals["case_id"].isin(str(p["case_ids"]).split(";"))]
            rows.append({"action_id": f"G::{p['pattern_id']}", "kind": "CORRIGER_GROUPE", "case_id": "",
                         "pattern_id": p["pattern_id"],
                         "person_id": rows_employees(members["case_id"].nunique(), members["person_id"].nunique()),
                         "objet": field_label(str(p["field"])),
                         "current": f"{len(members)} valeurs incorrectes", "proposed": "valeurs dérivées du mapping",
                         "certainty": min(members["certainty"], key=lambda c: rank.get(c, 0)),
                         "status": agg_status(members["status"]),
                         "reason": str(p["title"]), "evidence": str(p["description"])})
            continue
        if first["action_type"] == "CREER_AFFECTATION":
            key = grp[~grp["derivation"].str.startswith("champ non couvert")]
            rows.append({"action_id": pid, "kind": "CREER_AFFECTATION", "case_id": cid, "pattern_id": "",
                         "person_id": first["person_id"],
                         "objet": f"Affectation {ASSIGNMENT_TYPE_ADJ.get(case['src_type'], '') if case is not None else ''}",
                         "current": "absente", "proposed": f"{len(key)} champs dérivés du mapping",
                         "certainty": min(key["certainty"], key=lambda c: rank.get(c, 0)) if len(key) else C.TO_CONFIRM,
                         "status": agg_status(grp["status"]),
                         "reason": "Affectation RH sans correspondance dans Temps",
                         "evidence": first["evidence"]})
        else:
            rows.append({"action_id": pid, "kind": "CORRIGER_CHAMP", "case_id": cid, "pattern_id": "",
                         "person_id": first["person_id"], "objet": field_label(first["field"]),
                         "current": fmt(first["current_value"]),
                         "proposed": fmt(first["human_value"] or first["proposed_value"]),
                         "certainty": first["certainty"], "status": first["status"],
                         "reason": rule_plain(case) if case is not None else "", "evidence": first["evidence"]})
    with_prop = set(proposals["case_id"])
    manual = cases[(cases["verdict"] == C.A_INVESTIGUER) & ~cases["case_id"].isin(with_prop)
                   & ~cases["systemic"].astype(bool)]
    to_status = {C.DECISION_CONFIRM: "ACCEPTE", C.DECISION_ACCEPT: "REJETE", C.DECISION_MODIFY: "MODIFIE",
                 C.DECISION_REVIEW: "PROPOSE", "": "PROPOSE"}
    for _, c in manual.iterrows():
        rows.append({"action_id": f"V::{c['case_id']}", "kind": "VERIFICATION", "case_id": c["case_id"],
                     "pattern_id": "", "person_id": c["person_id"], "objet": field_label(c["field"]),
                     "current": fmt(c["destination_value"]), "proposed": "vérification humaine",
                     "certainty": C.TO_CONFIRM, "status": to_status.get(c["human_decision"], "PROPOSE"),
                     "reason": c["ai_suggested_action"] or c["proposed_action"], "evidence": c["evidence"]})
    return pd.DataFrame(rows, columns=["action_id", "kind", "case_id", "pattern_id", "person_id", "objet", "current",
                                       "proposed", "certainty", "status", "reason", "evidence"])


# ----------------------------------------------------------------------------- rules register
AMBIGUITY_TITLES = {
    "AMB-01": ("Date de début d'affectation (assignmentStartDate)", ["assignmentStartDate"]),
    "AMB-02": ("Situation d'emploi — correspondance des colonnes", ["detailedStatus", "statusReasonCode",
                                                                   "expectedReturnDate"]),
    "AMB-03": ("Situation d'emploi — code utilisé", ["detailedStatus"]),
    "AMB-04": ("Situation d'emploi — cessation non documentée", ["detailedStatus"]),
    "AMB-05": ("Motif d'absence — date d'effet du motif", ["statusReasonCode"]),
    "AMB-06": ("Fichier des motifs — noms de colonnes", ["statusReasonCode"]),
    "AMB-07": ("Type d'emploi — table de codes", ["contractTypeCode"]),
    "AMB-08": ("Type d'affectation — noms des indicateurs", ["isPrimaryAssignment", "isTemporaryAssignment"]),
    "AMB-09": ("Courriel (contactEmail)", ["contactEmail"]),
    "AMB-10": ("Concaténations — zéros du code", ["divisionName", "positionName"]),
    "AMB-11": ("Date de fin du détail du poste (termEndDate)", ["termEndDate"]),
    "AMB-12": ("Nom du rôle (positionName)", ["positionName"]),
}
AMBIGUITY_CLARIFIED_FIELD = {"AMB-01": "assignmentStartDate", "AMB-09": "contactEmail", "AMB-12": "positionName"}


def ambiguity_status(row) -> tuple[str, str]:
    s = str(row.get("statut", ""))
    if s.startswith("RÉSOLUE"):
        return ("✅", "Erreur confirmée" if row["ambiguity_id"] == "AMB-12" else "Clarification obtenue")
    return ("⚠️", "Hypothèse documentée")
