"""Transparent matching of source assignments to destination assignment rows.

The destination has no poste number, so a source assignment is matched to a
destination row of the same person using scored evidence (job code,
primary/temporary flags, start date, division, site, pay grade).

For each person, every *optimal* matching (maximum total evidence score) is
enumerated. Pairs present in every optimal matching are confirmed; pairs that
only appear in some of them are AMBIGUOUS and are never silently forced.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import EngineConfig
from .normalize import display, norm_bool, norm_date, norm_id, values_equal
from .rules import ASSIGNMENT_TYPES

MATCHED = "APPARIE"
AMBIGUOUS = "AMBIGU"
SOURCE_ONLY = "SOURCE_SEULEMENT"
DEST_ONLY = "DESTINATION_SEULEMENT"

FEATURE_LABELS = {
    "position": "positionId = CodeEmploi",
    "type": "indicateurs primaire/temporaire cohérents avec TypeAffectation",
    "start_date": "assignmentStartDate = date attendue ou DateEntréePoste",
    "division": "divisionId = CodeDirection",
    "site": "siteCode = CodeSite",
    "pay_grade": "payGradeId = ÉchelleSalariale",
}


@dataclass
class MatchRecord:
    person_id: str
    status: str
    src_idx: int | None = None
    dst_idx: int | None = None
    score: float = 0.0
    max_score: float = 10.0
    features: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)   # [(dst_idx, score)] for ambiguous / nearest
    note: str = ""

    @property
    def confidence(self) -> float:
        return round(self.score / self.max_score, 3) if self.max_score else 0.0


def pair_features(src: pd.Series, dst: pd.Series, start_candidates: set) -> dict:
    t = str(src.get("TypeAffectation") or "").strip().upper()
    exp_flags = ASSIGNMENT_TYPES.get(t)
    dst_flags = (norm_bool(dst.get("isPrimaryAssignment")), norm_bool(dst.get("isTemporaryAssignment")))
    return {
        "position": values_equal(src.get("CodeEmploi"), dst.get("positionId"), "id")
                    and norm_id(src.get("CodeEmploi")) is not None,
        "type": exp_flags is not None and dst_flags == exp_flags,
        "start_date": norm_date(dst.get("assignmentStartDate")) in start_candidates,
        "division": norm_id(src.get("CodeDirection")) is not None
                    and values_equal(src.get("CodeDirection"), dst.get("divisionId"), "id"),
        "site": norm_id(src.get("CodeSite")) is not None and values_equal(src.get("CodeSite"), dst.get("siteCode"), "id"),
        "pay_grade": norm_id(src.get("ÉchelleSalariale")) is not None
                     and values_equal(src.get("ÉchelleSalariale"), dst.get("payGradeId"), "id"),
    }


def score_features(features: dict, weights: dict) -> float:
    return float(sum(weights[k] for k, v in features.items() if v))


def _eligible(features: dict, score: float, cfg: EngineConfig) -> bool:
    return score >= cfg.match_min_score and (features["position"] or (features["type"] and features["start_date"]))


def _optimal_matchings(srcs, dsts, scores, tol, limit):
    """Enumerate all maximum-score partial matchings (small sets) or fall back to greedy."""
    if len(srcs) <= limit and len(dsts) <= limit:
        best = [-1.0]
        found = []

        def rec(i, used, pairs, total):
            if i == len(srcs):
                if total > best[0] + tol:
                    best[0] = total
                    found.clear()
                    found.append(frozenset(pairs))
                elif abs(total - best[0]) <= tol:
                    found.append(frozenset(pairs))
                return
            s = srcs[i]
            rec(i + 1, used, pairs, total)
            for d in dsts:
                if d not in used and (s, d) in scores:
                    rec(i + 1, used | {d}, pairs + [(s, d)], total + scores[(s, d)])

        rec(0, frozenset(), [], 0.0)
        return list(set(found)), False
    # Greedy fallback for unusually large groups: ties are reported as ambiguous
    remaining = sorted(scores.items(), key=lambda kv: -kv[1])
    used_s, used_d, pairs = set(), set(), []
    for (s, d), sc in remaining:
        if s in used_s or d in used_d:
            continue
        pairs.append((s, d))
        used_s.add(s)
        used_d.add(d)
    alt = []
    for (s, d) in pairs:
        for (s2, d2), sc2 in scores.items():
            if (s2, d2) != (s, d) and abs(sc2 - scores[(s, d)]) <= tol and (s2 == s or d2 == d):
                alt.append((s2, d2))
    matchings = [frozenset(pairs)]
    if alt:
        matchings.append(frozenset(alt))
    return matchings, True


def match_assignments(source: pd.DataFrame, destination: pd.DataFrame, start_candidates_fn,
                      cfg: EngineConfig) -> list[MatchRecord]:
    weights = cfg.match_weights
    max_score = float(sum(weights.values()))
    src_pid = source["Matricule"].map(norm_id)
    dst_pid = destination["personId"].map(norm_id)
    src_groups = {k: list(v) for k, v in source.groupby(src_pid).groups.items()}
    dst_groups = {k: list(v) for k, v in destination.groupby(dst_pid).groups.items()}
    records: list[MatchRecord] = []

    for pid in sorted(set(src_groups) | set(dst_groups)):
        S = src_groups.get(pid, [])
        D = dst_groups.get(pid, [])
        if not D:
            for s in S:
                records.append(MatchRecord(pid, SOURCE_ONLY, src_idx=s, max_score=max_score,
                                           note="personne absente de l'extraction destination"))
            continue
        if not S:
            for d in D:
                records.append(MatchRecord(pid, DEST_ONLY, dst_idx=d, max_score=max_score,
                                           note="personne absente de l'extraction source"))
            continue
        feats, scores, all_scores = {}, {}, {}
        for s in S:
            cands = start_candidates_fn(s)
            for d in D:
                f = pair_features(source.loc[s], destination.loc[d], cands)
                sc = score_features(f, weights)
                feats[(s, d)] = f
                all_scores[(s, d)] = sc
                if _eligible(f, sc, cfg):
                    scores[(s, d)] = sc
        matchings, greedy = _optimal_matchings(S, D, scores, cfg.match_tie_tolerance, cfg.match_bruteforce_limit)
        if not matchings:
            matchings = [frozenset()]
        confirmed = frozenset.intersection(*matchings)
        uncertain = frozenset.union(*matchings) - confirmed
        conf_src = {s for s, _ in confirmed}
        conf_dst = {d for _, d in confirmed}
        amb_src = {s for s, _ in uncertain} - conf_src
        amb_dst = {d for _, d in uncertain} - conf_dst

        for s, d in sorted(confirmed):
            competing = sorted(((s2, sc) for (s2, d2), sc in all_scores.items()
                                if d2 == d and s2 != s and (s2, d2) in scores), key=lambda x: -x[1])
            note = "appariement unique"
            if competing:
                note = ("plusieurs affectations source candidates pour cette ligne destination ; départagées par "
                        "les preuves secondaires (" + ", ".join(f"ligne source {int(source.loc[c, '_excel_row'])}: "
                                                              f"score {sc:g}" for c, sc in competing) + ")")
            if greedy:
                note += " ; appariement glouton (groupe volumineux)"
            records.append(MatchRecord(pid, MATCHED, s, d, all_scores[(s, d)], max_score, feats[(s, d)],
                                       [(c, sc) for c, sc in competing], note))
        for s in sorted(amb_src):
            cands = sorted(((d, sc) for (s2, d), sc in scores.items() if s2 == s and d in amb_dst),
                           key=lambda x: -x[1])
            best = cands[0] if cands else (None, 0.0)
            records.append(MatchRecord(pid, AMBIGUOUS, s, best[0], best[1], max_score,
                                       feats.get((s, best[0]), {}), cands,
                                       "plusieurs appariements optimaux équivalents : aucun appariement forcé"))
        for s in S:
            if s in conf_src or s in amb_src:
                continue
            nearest = max(D, key=lambda d: all_scores[(s, d)])
            partner = next((s2 for s2, d2 in confirmed if d2 == nearest), None)
            note = "aucune ligne destination correspondante"
            if partner is not None:
                note += (f" ; la ligne destination la plus proche (ligne {int(destination.loc[nearest, '_excel_row'])}, "
                         f"score {all_scores[(s, nearest)]:g}) est déjà appariée à l'affectation source ligne "
                         f"{int(source.loc[partner, '_excel_row'])} avec un score supérieur "
                         f"({all_scores[(partner, nearest)]:g})")
            records.append(MatchRecord(pid, SOURCE_ONLY, src_idx=s, dst_idx=None, score=0.0, max_score=max_score,
                                       features=feats[(s, nearest)], candidates=[(nearest, all_scores[(s, nearest)])],
                                       note=note))
        for d in D:
            if d in conf_dst or d in amb_dst:
                continue
            records.append(MatchRecord(pid, DEST_ONLY, dst_idx=d, max_score=max_score,
                                       note="ligne destination sans affectation source correspondante"))
    return records


def matches_table(records: list[MatchRecord], source: pd.DataFrame, destination: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in records:
        rows.append({
            "person_id": r.person_id,
            "statut_appariement": r.status,
            "ligne_source": int(source.loc[r.src_idx, "_excel_row"]) if r.src_idx is not None else None,
            "ligne_destination": int(destination.loc[r.dst_idx, "_excel_row"]) if r.dst_idx is not None else None,
            "type_affectation": display(source.loc[r.src_idx, "TypeAffectation"]) if r.src_idx is not None else None,
            "code_poste": display(source.loc[r.src_idx, "CodePoste"]) if r.src_idx is not None else None,
            "code_emploi_source": display(source.loc[r.src_idx, "CodeEmploi"]) if r.src_idx is not None else None,
            "positionId_destination": display(destination.loc[r.dst_idx, "positionId"]) if r.dst_idx is not None else None,
            "score": r.score,
            "confiance_appariement": r.confidence,
            "preuves": ", ".join(FEATURE_LABELS[k] for k, v in r.features.items() if v),
            "candidats": "; ".join(f"ligne dest {int(destination.loc[d, '_excel_row'])} (score {sc:g})"
                                   for d, sc in r.candidates if d is not None and d in destination.index)
            if r.status in (AMBIGUOUS, SOURCE_ONLY) else "",
            "note": r.note,
        })
    return pd.DataFrame(rows)
