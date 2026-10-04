"""Engine configuration.

Every threshold or interpretation choice that is not written in the official
files lives here, so it is visible, documented and easy to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
SYNTHETIC_DIR = PROJECT_ROOT / "synthetic_data"

# Verdicts
CONFORME = "CONFORME"
ECART_JUSTIFIE = "ECART_JUSTIFIE"
ANOMALIE = "ANOMALIE"
A_INVESTIGUER = "A_INVESTIGUER"
VERDICTS = [CONFORME, ECART_JUSTIFIE, ANOMALIE, A_INVESTIGUER]

# Rule types (which level of the pipeline produced the final verdict)
RAW = "COMPARAISON_BRUTE"          # level 1: raw comparison / normalisation
DETERMINISTIC = "REGLE_DETERMINISTE"  # level 2: documented business rule
AI_ASSISTED = "ASSISTE_IA"          # level 3: pattern analysis on ambiguous cases

# Human decisions
DECISION_CONFIRM = "CONFIRMER_ANOMALIE"
DECISION_ACCEPT = "ECART_ACCEPTABLE"
DECISION_MODIFY = "MODIFIER_CORRECTION"
DECISION_REVIEW = "A_REVOIR"
DECISIONS = [DECISION_CONFIRM, DECISION_ACCEPT, DECISION_MODIFY, DECISION_REVIEW]
DECISION_REOPEN = "REOUVRIR"   # cancels the active decision of a target (history is kept)
DECISION_TO_VERDICT = {
    DECISION_CONFIRM: ANOMALIE,
    DECISION_ACCEPT: ECART_JUSTIFIE,
    DECISION_MODIFY: ANOMALIE,
    DECISION_REVIEW: A_INVESTIGUER,
}

# Certainty of a derived / proposed value
CERTAIN = "CERTAIN"          # copied directly from the source
DERIVED = "DERIVE"           # computed by a documented rule with complete inputs
INFERRED = "INFERE"          # depends on an interpretation or on observed data
TO_CONFIRM = "A_CONFIRMER"   # cannot be derived with confidence


@dataclass
class EngineConfig:
    # --- contactEmail ---------------------------------------------------------
    email_domain: str = "loto-quebec.com"
    # Destination-only environment prefix explicitly allowed by the challenge
    # partner (e.g. "dev-08-v2_"); it is ignored during corroboration.
    email_allowed_prefix_regex: str = r"^[a-z0-9]+(?:-[a-z0-9]+)*_"

    # --- matching -------------------------------------------------------------
    match_weights: dict = field(default_factory=lambda: {
        "position": 4.0,      # positionId == CodeEmploi
        "type": 2.0,          # isPrimary/isTemporary consistent with TypeAffectation
        "start_date": 2.0,    # assignmentStartDate == expected or raw DateEntréePoste
        "division": 1.0,      # divisionId == CodeDirection
        "site": 0.5,          # siteCode == CodeSite
        "pay_grade": 0.5,     # payGradeId == ÉchelleSalariale
    })
    match_min_score: float = 4.0
    match_tie_tolerance: float = 1e-9
    match_bruteforce_limit: int = 7   # max assignments per person for exhaustive search

    # --- systemic pattern detection (level 3) ---------------------------------
    systemic_field_min_share: float = 0.5   # share of a field's comparisons failing
    systemic_field_min_count: int = 5
    # Systemic groups keep their deterministic verdict (official clarification: systemic
    # mismatches are real errors). True = route unexplained systemic groups to A_INVESTIGUER.
    systemic_reroute_to_investigation: bool = False
    segment_min_count: int = 3
    segment_min_share: float = 0.8
    recurrent_min_count: int = 2

    # --- priority (0-100) -------------------------------------------------------
    field_weights: dict = field(default_factory=lambda: {
        "weeklyHoursOverride": 25, "dailyHoursOverride": 25,
        "contractTypeCode": 25, "detailedStatus": 25, "statusReasonCode": 25,
        "expectedReturnDate": 20, "assignmentStartDate": 20, "assignmentEndDate": 20,
        "termEndDate": 15, "isPrimaryAssignment": 20, "isTemporaryAssignment": 20,
        "payGradeId": 20, "positionId": 15, "positionCode": 15, "divisionId": 15,
        "divisionCode": 15, "siteCode": 15, "onboardDate": 15,
        "positionName": 10, "divisionName": 8, "siteName": 8,
        "givenName": 5, "surname": 5, "contactEmail": 5,
        "__assignment__": 30,
    })
    default_field_weight: int = 10
    priority_high: int = 60
    priority_medium: int = 40


DEFAULT_CONFIG = EngineConfig()

# Official clarifications received from the Loto-Québec challenge team. They are cited
# in explanations, patterns and the rule-ambiguity register (traceability).
CLARIFICATION_DATE = "2026-10-04"
OFFICIAL_CLARIFICATIONS = {
    "contactEmail": {
        "title": "Problème systémique — génération des courriels",
        "statement": ("« code » = Matricule/personID ; le préfixe destination (ex. dev-08-v2_) est autorisé et ignoré ; "
                      "l'identifiant anonymisé différent dans l'adresse est une erreur d'anonymisation, pertinente "
                      "à détecter comme anomalie."),
        "known_cause": "artefact d'anonymisation",
        "business_priority": "BASSE",
    },
    "positionName": {
        "title": "Transformation positionName incorrecte",
        "statement": "L'écart sur positionName est une erreur réelle (pas un effet de l'anonymisation).",
        "known_cause": None,
        "business_priority": None,
    },
    "assignmentStartDate": {
        "title": "Date d'effet de l'affectation — transformation confirmée",
        "statement": ("Les écarts de date des employés 9989151, 3241002 et 4402456 sont voulus : la source ne contient "
                      "que la date d'effet du poste, la destination applique la transformation documentée avec "
                      "l'historique du détail du poste."),
        "known_cause": None,
        "business_priority": None,
    },
}
