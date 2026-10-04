"""Human-in-the-loop: expert decisions stored separately from the data.

Decisions are appended to ``outputs/human_decisions.csv`` (an audit log: the
latest decision per target wins). They never modify the input files nor the
engine's verdict columns; they populate ``human_decision`` / ``reviewed_verdict``.

Targets:
  case      a single case (case_id)
  pattern   a systemic group or rule ambiguity (pattern_id) -> applies to all members
  proposal  one line of a proposed correction (proposal_id::field)
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config as C

COLUMNS = ["decision_id", "timestamp", "dataset_key", "target_type", "target_id", "decision", "comment",
           "modified_values", "reviewer"]
PROPOSAL_STATUSES = ["PROPOSE", "ACCEPTE", "MODIFIE", "REJETE"]
CASE_DECISION_TO_PROPOSAL = {C.DECISION_CONFIRM: "ACCEPTE", C.DECISION_ACCEPT: "REJETE",
                             C.DECISION_MODIFY: "MODIFIE", C.DECISION_REVIEW: "PROPOSE"}


class DecisionStore:
    def __init__(self, path=None):
        # CORROBORAI_DECISIONS lets tests/demos use an isolated log instead of outputs/human_decisions.csv
        env = os.environ.get("CORROBORAI_DECISIONS")
        self.path = Path(path) if path else Path(env) if env else C.OUTPUT_DIR / "human_decisions.csv"

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=COLUMNS)
        return pd.read_csv(self.path, dtype=str, keep_default_na=False)

    def record(self, target_type: str, target_id: str, decision: str, comment: str = "",
               modified_values: dict | None = None, reviewer: str = "", dataset_key: str = "") -> dict:
        if target_type not in ("case", "pattern", "proposal"):
            raise ValueError(target_type)
        if target_type != "proposal" and decision not in C.DECISIONS + [C.DECISION_REOPEN]:
            raise ValueError(f"décision inconnue : {decision}")
        if target_type == "proposal" and decision not in PROPOSAL_STATUSES:
            raise ValueError(f"statut de proposition inconnu : {decision}")
        row = {"decision_id": uuid.uuid4().hex[:12], "timestamp": datetime.now().isoformat(timespec="milliseconds"),
               "dataset_key": dataset_key, "target_type": target_type, "target_id": target_id,
               "decision": decision, "comment": comment,
               "modified_values": json.dumps(modified_values, ensure_ascii=False) if modified_values else "",
               "reviewer": reviewer}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([row], columns=COLUMNS)
        df.to_csv(self.path, mode="a", header=not self.path.exists(), index=False, encoding="utf-8")
        return row

    def record_many(self, items: list[dict], reviewer: str = "", dataset_key: str = "") -> int:
        """Append several decisions at once. Each item: target_type, target_id, decision, [comment, modified_values]."""
        rows = []
        for it in items:
            mv = it.get("modified_values")
            rows.append({"decision_id": uuid.uuid4().hex[:12],
                         "timestamp": datetime.now().isoformat(timespec="milliseconds"), "dataset_key": dataset_key,
                         "target_type": it["target_type"], "target_id": it["target_id"], "decision": it["decision"],
                         "comment": it.get("comment", ""),
                         "modified_values": json.dumps(mv, ensure_ascii=False) if mv else "", "reviewer": reviewer})
        if rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows, columns=COLUMNS).to_csv(self.path, mode="a", header=not self.path.exists(),
                                                       index=False, encoding="utf-8")
        return len(rows)

    def latest(self, dataset_key: str | None = None) -> pd.DataFrame:
        df = self.load()
        if df.empty:
            return df
        if dataset_key:
            df = df[(df["dataset_key"] == dataset_key) | (df["dataset_key"] == "")]
        return df.sort_values("timestamp", kind="stable").groupby(["target_type", "target_id"], as_index=False).last()

    def history(self, target_ids, dataset_key: str | None = None) -> pd.DataFrame:
        """Every decision ever recorded for these targets (append-only audit trail), oldest first."""
        df = self.load()
        if df.empty:
            return df
        if dataset_key:
            df = df[(df["dataset_key"] == dataset_key) | (df["dataset_key"] == "")]
        return df[df["target_id"].isin(list(target_ids))].sort_values("timestamp", kind="stable")


def apply_decisions(result, store: DecisionStore) -> None:
    """Populate human-decision columns from the decision log (latest decision per target is active;
    REOUVRIR cancels the active decision of its target without deleting history)."""
    cases, patterns, proposals = result.cases, result.patterns, result.proposals
    for col in ("human_decision", "human_comment", "human_decided_at", "human_decision_source"):
        cases[col] = ""
    cases["reviewed_verdict"] = cases["verdict"]
    if not patterns.empty:
        patterns["human_decision"] = ""
        patterns["human_comment"] = ""
        patterns["human_decided_at"] = ""
    lat = store.latest(result.dataset_key)
    if lat.empty:
        return
    pos = {cid: i for i, cid in zip(cases.index, cases["case_id"])}
    pattern_ids = set(patterns["pattern_id"]) if not patterns.empty else set()

    def apply(idx_list, d, rule_ambiguity=False, source="cas"):
        for i in idx_list:
            cases.at[i, "human_decision_source"] = source
            cases.at[i, "human_decision"] = d["decision"]
            cases.at[i, "human_comment"] = d["comment"]
            cases.at[i, "human_decided_at"] = d["timestamp"]
            if rule_ambiguity and d["decision"] == C.DECISION_ACCEPT:
                cases.at[i, "reviewed_verdict"] = cases.at[i, "verdict"]   # interpretation confirmed
            else:
                cases.at[i, "reviewed_verdict"] = C.DECISION_TO_VERDICT[d["decision"]]

    # 1) pattern-level decisions
    pat_dec = lat[(lat["target_type"] == "pattern") & lat["target_id"].isin(pattern_ids)]
    group_status, case_group = {}, {}
    for _, p in patterns.iterrows():
        for cid in str(p["case_ids"]).split(";"):
            case_group.setdefault(cid, p["pattern_id"])
    for _, d in pat_dec.iterrows():
        if d["decision"] == C.DECISION_REOPEN:
            continue
        p = patterns[patterns["pattern_id"] == d["target_id"]].iloc[0]
        pi = patterns.index[patterns["pattern_id"] == d["target_id"]][0]
        patterns.at[pi, "human_decision"] = d["decision"]
        patterns.at[pi, "human_comment"] = d["comment"]
        patterns.at[pi, "human_decided_at"] = d["timestamp"]
        group_status[d["target_id"]] = CASE_DECISION_TO_PROPOSAL[d["decision"]]
        ids = [pos[c] for c in str(p["case_ids"]).split(";") if c in pos]
        apply(ids, d, rule_ambiguity=p["pattern_type"] == "AMBIGUITE_REGLE_SYSTEMIQUE",
              source=f"groupe:{d['target_id']}")
    # 2) case-level decisions override pattern-level ones (REOUVRIR falls back to the group decision, if any)
    case_dec = lat[(lat["target_type"] == "case") & lat["target_id"].isin(pos)]
    for _, d in case_dec.iterrows():
        if d["decision"] != C.DECISION_REOPEN:
            apply([pos[d["target_id"]]], d)

    # 3) proposals: every event applied in chronological order (latest decision wins)
    if proposals.empty:
        return
    events = []
    for _, d in pat_dec.iterrows():
        if d["decision"] == C.DECISION_REOPEN:
            continue
        members = str(patterns.loc[patterns["pattern_id"] == d["target_id"], "case_ids"].iloc[0]).split(";")
        events.append((d["timestamp"], 0, proposals["case_id"].isin(members),
                       CASE_DECISION_TO_PROPOSAL[d["decision"]], None, None, d["comment"]))
    for _, d in case_dec.iterrows():
        mask = proposals["case_id"] == d["target_id"]
        if d["decision"] == C.DECISION_REOPEN:
            status = group_status.get(case_group.get(d["target_id"], ""), "PROPOSE")
            events.append((d["timestamp"], 1, mask, status, None, None, ""))
            continue
        mods = json.loads(d["modified_values"]) if d["decision"] == C.DECISION_MODIFY and d["modified_values"] else None
        events.append((d["timestamp"], 1, mask, CASE_DECISION_TO_PROPOSAL[d["decision"]], mods, None, d["comment"]))
    for _, d in lat[lat["target_type"] == "proposal"].iterrows():
        pid, _, f = d["target_id"].partition("::")
        mask = (proposals["proposal_id"] == pid) & ((proposals["field"] == f) if f else True)
        value = json.loads(d["modified_values"]).get("value", "") if d["modified_values"] else None
        events.append((d["timestamp"], 2, mask, d["decision"], None, value, d["comment"]))
    for _, _, mask, status, mods, value, comment in sorted(events, key=lambda e: (e[0], e[1])):
        if not mask.any():
            continue
        proposals.loc[mask, "status"] = status
        if status != "MODIFIE":
            proposals.loc[mask, "human_value"] = ""
        if mods:
            for f, v in mods.items():
                proposals.loc[mask & (proposals["field"] == f), "human_value"] = str(v)
        if value not in (None, ""):
            proposals.loc[mask, "human_value"] = str(value)
        proposals.loc[mask, "human_comment"] = comment or ""
