# CorroborAI

Here is the link of the video demo : https://youtu.be/JZxD_AKfgSI

**CorroborAI is an investigation assistant for the reconciliation between Système A — RH and Système B — Temps**
(Loto-Québec *CorroborIA* challenge).

It compares the two extracts and applies the business rules documented in `Mapping.xlsx`. Only the discrepancies that
genuinely need a person reach the analyst, each with a traceable justification.

**Core principle:**

```
raw comparison  →  deterministic business rules  →  assisted analysis of residual / ambiguous cases  →  human validation
```

The goal is to **minimise unnecessary manual investigation while preserving full traceability**. Every verdict points
to the source file and row it came from, the rule that produced it, and the pipeline level that decided it. The
official files are **never modified**.

---

## 1. Final results on the official files

| Scope | |
|---|---|
| Employees | 20 |
| Source assignments (Système A — RH) | 23 |
| Destination rows (Système B — Temps) | 22 |
| Corroborated fields (from `Mapping.xlsx`) | 24 |
| **Total controls** (field × assignment + assignment-level cases) | **529** |

| CONFORME | ECART_JUSTIFIE | ANOMALIE | A_INVESTIGUER |
|---|---|---|---|
| 359 | 111 | 56 | 3 |

**Raw-comparison breakdown (always reconciles):**

```
529 controls   = 317 identical at first comparison
               + 212 raw differences

212 raw differences = 22  resolved by normalisation / formatting       → CONFORME
                    + 20  values correctly derived by a rule           → CONFORME
                    + 111 justified differences                        → ECART_JUSTIFIE
                    + 56  anomalies                                    → ANOMALIE
                    + 3   cases to investigate                         → A_INVESTIGUER
```

### Workload reduction: 212 raw differences → 17 human decisions

56 anomalies plus 3 ambiguous cases do **not** mean 59 independent human tasks:

* **44 anomaly cases belong to 2 systemic groups**: `contactEmail` (22) and `positionName` (22). They remain
  `ANOMALIE` in the audit trail, but each group is validated **once**.
* **17 human decisions are required**: 15 individual decisions plus 2 systemic-group decisions.

Details:

* **Systemic groups (2 decisions, 44 audit cases)**
  * *Génération des courriels* (`contactEmail`, 22/22). The `dev-08-v2_` prefix is allowed, but the identifier after
    normalisation does not match the Matricule/personID. Known cause: an anonymisation artefact (low business priority).
  * *Transformation positionName incorrecte* (`positionName`, 22/22). The target code-label does not follow the
    mapping; the substitution is a consistent 1:1 mapping across 9 job codes. This is a confirmed real error.
* **Individual anomalies (12)**
  * `contractTypeCode` values swapped between 2762457 ↔ 4625374 and between 3712987 ↔ 7254364.
  * `siteName` values swapped between 3241002 ↔ 6035643.
  * Weekly/daily hours on 3 assignments: the source has 35/7 or 36/7.2, the destination has 40/8, which is the job
    default (common root cause detected).
* **To investigate (3)**
  * Employee **1545850**: the temporary assignment (A) has no clear match in Temps. A candidate destination row is
    proposed, but a person must validate it.
  * Employee 3712987: hours are missing in the source (weekly and daily).
* **Justified by the date transformation**: `assignmentStartDate` for 9989151 (2009-03-30 → 2021-03-30), 3241002
  (2009-03-30 → 2022-10-05) and 4402456 (2001-02-05 → 2013-10-20). "Different" is not "error".
* **Data quality**: `detailedStatus` is stored with broken encoding (`Absence complÃ¨te`) in 2 destination rows.
  These are `ECART_JUSTIFIE` with a quality flag.

---

## 2. Three-level methodology

```
            ┌─────────────────────────────────────────────────────────────┐
 data/ ───► │ LEVEL 1  Raw comparison / normalisation        normalize.py │  identical? identical after
 (read-only)│          blanks, dates, booleans, ids, spaces, encoding     │  normalisation? different?
            ├─────────────────────────────────────────────────────────────┤
            │ LEVEL 2  Deterministic business rules   mapping.py rules.py │  the rule derives the expected
            │          Mapping.xlsx + situation table + motifs +          │  destination value from the
            │          détail du poste; transparent assignment matching   │  source (matching.py)
            ├─────────────────────────────────────────────────────────────┤
            │ LEVEL 3  Assisted analysis of what remains     ai_assist.py │  patterns, systemic groups,
            │          (local, no external call)                          │  priority, confidence, advice
            └─────────────────────────────────────────────────────────────┘
                     │                       │                       │
               cases + evidence     proposed corrections     human decisions (separate log)
                     └──────────► Streamlit app / Excel report / notebook ◄───────┘
```

| Verdict | Meaning |
|---|---|
| `CONFORME` | identical, identical after normalisation, or equal to the value derived by the rule |
| `ECART_JUSTIFIE` | raw values differ, but a documented rule (or an allowed format/encoding difference) explains it |
| `ANOMALIE` | a deterministic rule applies with sufficient evidence and the destination contradicts it |
| `A_INVESTIGUER` | the rules cannot conclude: missing input, undocumented code, conflicting evidence, ambiguous or missing assignment |

Each case records `rule_type`, the level that produced the engine verdict: `COMPARAISON_BRUTE` or
`REGLE_DETERMINISTE` (on the official data, 262 + 267 = 529). Level 3 enriches, groups and prioritises cases. It does
not replace the deterministic verdict, which is always kept in `deterministic_verdict`.

---

## 3. The application (final UI)

`python -m streamlit run app.py` opens a French-language investigation assistant (dark theme, set in
`.streamlit/config.toml`). Every screen gives the business explanation first. Technical and audit details (rule IDs,
rows, scores, normalised values, formulas, internal IDs, file fingerprints) are one click away in the
"🔧 détails techniques / audit" sections.

### Sidebar, top to bottom

1. **Jeu de données** (dataset context first): the official data in `data/`, a synthetic demo set, or uploaded
   files. The current dataset is also shown as a chip on every page ("Jeu officiel · 20 employés · …"). Synthetic
   data shows a warning banner.
2. **Travail principal**
   * **Tableau de bord**:
     * summary cards; on first opening, "17 restantes · sur 17 initiales · 0 déjà traitée";
     * the reconciling breakdown above;
     * how the engine verdict was obtained;
     * the 59 cases enriched by the assisted analysis (a subset of the controls, not an alternative to the rules);
     * the remaining human decisions by business category, and the systemic problems.
   * **Investigation**:
     * the work queue; reviewed cases stay **visible by default** (greyed, "✅ Revu") and can optionally be hidden;
     * the opened case is **pinned and highlighted** (▶, "Cas ouvert" banner);
     * each case shows *what the rule says / what we observe / conclusion*, the local AI analysis card, an explained
       confidence level, the proposed action, and decision buttons that state their exact effect.
   * **Problèmes systémiques**:
     * groups that need **one** decision;
     * separately, *indices complémentaires* (swaps, a shared root cause, data quality) that help the investigation
       without requiring a group decision.
   * **Actions**:
     * every corrective action, with status counters and history;
     * accepted actions **remain visible**, and any action can be reopened or modified;
     * **Aperçu après corrections** simulates the destination with all accepted corrections applied, and can export a
       separate *corrected candidate copy*.
3. **📚 Référentiel & technique**: Mapping des champs, Règles & clarifications, Appariement des affectations,
   Export & audit.
4. **🔎 Guide CorroborAI**:
   * a 5-step contextual guide, **on by default** in a new session, that can be skipped and relaunched;
   * steps: dashboard → a justified difference (9989151) → an anomaly with AI root cause (2762457) → the ambiguous
     case (1545850) → systemic problems;
   * it is a navigation aid, not an AI component.

### Behaviour of human decisions in the UI

* Every decision gives immediate feedback (toast plus a result panel). The case shows a **Résolution humaine** block:
  engine verdict (unchanged), expert decision, official value next to the validated correction, and the impact.
* Decisions can be **revised or reopened** without deleting anything. Each change is appended as a new event, and the
  history is shown on the case and on the action.
* A **group decision** closes the workflow for all its cases at once ("Résolu via décision de groupe") while keeping
  every individual engine verdict.
* **Official source files are never modified.** Accepted corrections form a correction plan that is exported
  separately. The optional corrected candidate copy is a separate file in `outputs/`.

---

## 4. Human-in-the-loop

The **engine verdict is immutable**. The **human decision is stored separately**. Example:

| | |
|---|---|
| Engine verdict | `ANOMALIE` (contractTypeCode, employee 2762457) |
| Human decision | Anomalie confirmée |
| Accepted correction | `WHX → JWN` (added to the correction plan) |
| Official destination file | unchanged |

* Decisions target a **case**, a **systemic group** or a **proposed action**:
  * `CONFIRMER_ANOMALIE` — confirm the anomaly;
  * `ECART_ACCEPTABLE` — accept the difference as legitimate;
  * `MODIFIER_CORRECTION` — modify the proposed correction;
  * `A_REVOIR` — not enough information; keeps the case open;
  * `REOUVRIR` — cancels the active decision.
* The log `outputs/human_decisions.csv` is **append-only**. The latest event per target is the active one, across
  case, group and action decisions, and the full history stays auditable. Tests and demos can point to an isolated
  log through the environment variable `CORROBORAI_DECISIONS`.
* Proposed corrections are never applied to the official files. Accepted or modified ones are exported to
  `outputs/accepted_corrections.csv/.xlsx`, and the simulated destination to
  `outputs/destination_corrigee_candidate.xlsx`.
* Every proposed value carries a certainty level:
  * `CERTAIN` — direct copy;
  * `DERIVE` — computed by a documented rule;
  * `INFERE` — depends on an interpretation or an observed relation;
  * `A_CONFIRMER` — information insufficient.

The submitted repository opens in the **baseline state**: no human decision recorded, 17/17 decisions remaining.

---

## 5. AI contribution (what it does and does not do)

**The deterministic rules are not replaced by AI.** Every verdict comes from level 1 (comparison/normalisation) or
level 2 (documented rules). The assisted analysis runs **after** the deterministic evidence and only adds
investigation value:

| Assisted analysis (level 3, `ai_assist.py`) | Example on the official data |
|---|---|
| Detection of swapped values between records | `contractTypeCode` 2762457 ↔ 4625374 |
| Detection of recurring / systemic discrepancy patterns | 22/22 `positionName` with a consistent 1:1 substitution |
| Grouping of repeated anomalies into one decision | 44 anomaly records → 2 group decisions |
| Likely root-cause suggestion | hours on 4 employees (8 cases): destination = job default (détail du poste) |
| Priority scoring (0–100, formula stored per case) | swapped contract types ranked high, known anonymisation artefact low |
| Confidence scoring with business factors | "+ explicit rule, + unambiguous expected value, + swap found elsewhere…" |
| Investigation explanation and suggested next action | "Vérifier les deux enregistrements ensemble avant correction" |
| `FeedbackModel`: learns from expert decisions on ambiguous cases | an explainable decision tree that only *suggests* a verdict for `A_INVESTIGUER` cases |

* **Deterministic verdict** (`verdict`, `deterministic_verdict`) is distinct from the **AI-assisted investigation
  suggestion** (`ai_hypothesis`, `ai_suggested_action`, `ai_suggested_verdict`). The UI shows the AI suggestion in a
  separate purple card stating that it does not replace the verdict.
* Basic equality checks are **not** called AI.
* **No external LLM or API is called in V1.** `LLMAssistant` is only an optional future plug-in: it prepares redacted
  prompts (names and emails masked) and makes no call.
* `FeedbackModel` on synthetic data with **simulated** expert decisions recovers the hidden policy on the held-out
  half (notebook §9). This demonstrates the mechanism, not real expert behaviour.

---

## 6. Official clarifications (Loto-Québec, 2026-10-04)

Stored in `src/config.py` (`OFFICIAL_CLARIFICATIONS`) and cited in explanations, patterns and the register.

* **contactEmail**:
  * "code" = Matricule/personID;
  * a destination-only environment prefix such as `dev-08-v2_` is allowed and normalised away;
  * the remaining anonymised identifier mismatch in the supplied data is a known anonymisation error, and it is still
    valid to detect it as an anomaly in this challenge → `ANOMALIE`, grouped systemically.
* **positionName**: the mismatch is a confirmed real error → `ANOMALIE`, grouped systemically.
* **assignmentStartDate**:
  * the destination applies a transformation using job-history information (détail du poste);
  * the implementation `MAX(DateEntréePoste, effective date of the current détail record)` reproduces all
    **22/22** destination values;
  * the three initially suspicious cases (9989151, 3241002, 4402456) are therefore `ECART_JUSTIFIE`;
  * the written mapping wording alone is ambiguous: "plus ancienne", and a change detected on the unit code only.
    It is the official clarification that validates the retained interpretation.

---

## 7. Supported rules

Only destination fields listed in `Mapping.xlsx` are corroborated (24 fields). `personId` is the matching key.
`LibelléImputation` has no target. The contract-type table is parsed from the rule text, and the situation table
from its sheet.

| Rule | Destination field(s) | Logic (source: Mapping.xlsx) |
|---|---|---|
| `R_DIRECT` | givenName, surname, onboardDate, siteName, siteCode, divisionId, divisionCode, positionId, positionCode, payGradeId, weekly/dailyHoursOverride | mapping "N/A": direct copy, compared after normalisation |
| `R_EMAIL` | contactEmail | first letter of first name + last name + last 3 digits of the code (= Matricule/personID) + `@loto-quebec.com`, accents removed; destination-only prefix ignored; identity mismatch → `ANOMALIE` |
| `R_CONCAT_DIVISION` / `R_CONCAT_POSITION` | divisionName / positionName | `code + "-" + label` (leading zeros of the code are treated as format) |
| `R_SITUATION_EMPLOI` | detailedStatus, statusReasonCode, expectedReturnDate | access code (CodeSuspensionAccès) → "Règles situation d'emploi"; absence → Remphor code from the motif file and DateRetourAnticipée; active → null; conflicts → `A_INVESTIGUER` |
| `R_CONTRACT_TYPE` | contractTypeCode | table `SI PERM_IND / FT_IND / EMPTP_CD → code`; Oui/Non = 1/0; uncovered combination → `A_INVESTIGUER` |
| `R_ASSIGNMENT_TYPE` | isPrimaryAssignment, isTemporaryAssignment | P → (true, false), A → (false, true), S → (false, false) |
| `R_ASSIGNMENT_START` | assignmentStartDate | `MAX(DateEntréePoste, effective date of the current détail record)`; the current record is the latest one that differs from its predecessor, else MIN(EFFDT) |
| `R_ASSIGNMENT_END` | assignmentEndDate, termEndDate | MIN(DateSortiePoste, end of current admin unit = next different-unit record − 1 day) |
| `R_MATCHING` | assignment level | missing, unexpected or ambiguous assignments |

**Matching.** The destination has no poste number. Assignments are matched per person with weighted evidence: job
code 4, primary/temporary flags 2, start date 2, division 1, site 0.5, pay grade 0.5. All optimal matchings are
enumerated. A pair that is not present in every optimal matching is `AMBIGU` and is never forced.

---

## 8. Installation and running (Windows, from the repository root)

> **Official challenge files are NOT included in the public repository.** Place the official files **unchanged** in
> `data/` before running. The engine finds them by name: `*Source*.xlsx`, `*Destination*.xlsx`, `détail_du_poste.xlsx`,
> `Motif*.xlsx` and `Mapping.xlsx`. The PDF and PPTX are not needed by the engine.

Requires Python 3.10+.

```powershell
python -m venv VenvCorroborAI              # or reuse an existing virtual environment
.\VenvCorroborAI\Scripts\Activate.ps1
pip install -r requirements.txt
```

With the environment activated:

```powershell
python -m streamlit run app.py                                     # application
python -m src.corroboration --no-decisions                          # engine → outputs/corroboration_report.xlsx + CSVs
python -m jupyter lab notebooks/analysis.ipynb                      # notebook (interactive)
jupyter nbconvert --to notebook --execute --inplace notebooks/analysis.ipynb   # notebook (headless re-run)
python -m unittest discover -s tests -v                             # tests (pytest also works if installed)
python -m src.synthetic --n 1000 --seed 42 --evaluate               # synthetic data + evaluation (also 5000 / 10000)
```

On macOS/Linux, use `source VenvCorroborAI/bin/activate`; the other commands are identical.

---

## 9. Architecture

```
corroborai/
  data/                  official files — read-only, NOT in the public repository
  src/
    config.py            thresholds, interpretation choices, official clarifications
    load_data.py         read-only loaders (CSV-in-cell détail du poste, Excel serial dates, header matching)
    mapping.py           Mapping.xlsx parser (merged cells), situation table, contract-type table, join sheets
    normalize.py         level 1: normalisation and raw comparison
    rules.py             level 2: rule registry bound to mapping entries (derive + compare)
    matching.py          transparent source↔destination assignment matching, ambiguity detection
    corroboration.py     orchestration, case building, explanations, rule-ambiguity register, CLI
    ai_assist.py         level 3: patterns, systemic grouping, priority/confidence, FeedbackModel, LLM plug-in
    proposals.py         proposed corrections (new row / field update) with certainty per value
    decisions.py         append-only human decision log
    presentation.py      business-facing French wording for the app
    synthetic.py         synthetic data generator, ground truth, evaluation
    export.py            Excel / CSV report, audit log, corrected-destination simulation
  app.py                 Streamlit investigation assistant
  notebooks/analysis.ipynb   jury-facing report (calls src/, no duplicated logic)
  tests/test_basic.py    unit and end-to-end tests
  outputs/               baseline report and CSV exports (runtime decision files are git-ignored)
  synthetic_data/        generated datasets (git-ignored; regenerate with src.synthetic)
```

---

## 10. Synthetic validation

`src/synthetic.py` generates datasets of any size with the same schema (source, destination, détail du poste as
CSV-in-cell, motifs). It reuses the official mapping read-only. The destination is built by an **independent**
implementation of the documented rules, then known cases are injected and saved to `ground_truth.csv`:

* conforming records and justified differences (admin-unit change, job-history update, stale return date for an
  active employee);
* formatting-only differences;
* wrong mapped values and swapped values;
* missing assignments, and missing source or destination values;
* a systemic segment failure;
* date transformation not applied;
* ambiguous cases: indistinguishable duplicate assignments, an undocumented access code, an uncovered contract
  combination.

On `synth_n1000_s42` (1,000 employees, 1,162 assignments, 27,037 controls, 761 injected labels), every injected
category is classified correctly: precision and recall are 1.00 for OK / ANOMALIE / A_INVESTIGUER. At 10,000
employees (about 266k controls), the engine runs in about 1.5 minutes on a laptop.

> **This 100 % on generated ground truth demonstrates a faithful implementation of the documented rules and injected
> patterns. It does not prove complete real-world HR coverage**: the generator encodes the same reading of the
> documentation as the engine.

---

## 11. Rule-ambiguity register (assumptions)

Shown in the app under *Référentiel & technique → Règles & clarifications*, and in the report sheet `Rule_Ambiguities`.

| ID | Field | Interpretation retained |
|---|---|---|
| AMB-01 | assignmentStartDate | **Resolved by official clarification** (see §6); 22/22 destination values reproduced |
| AMB-02 | situation fields | cf_specificStatus / cf_CAD / cf_CADP ↔ detailedStatus / statusReasonCode / expectedReturnDate |
| AMB-03 | detailedStatus | CodeSuspensionAccès is the situation code; CodeStatutEmploi and the motif access code are cross-checks |
| AMB-04 | detailedStatus | "cessation" mentioned but has no rule; undocumented access code → `A_INVESTIGUER` |
| AMB-05 | statusReasonCode | DateEffetRaison listed but unused by any rule; kept as context |
| AMB-06 | motifs | motif file columns mapped to the join sheet by position |
| AMB-07 | contractTypeCode | EMPT_CD / EMPTP_CD = CatégorieEmploi; V with PERM_IND = 0 not covered → `A_INVESTIGUER` |
| AMB-08 | assignment type | role_term_1_primary / role_term_1_temporary read as isPrimary / isTemporaryAssignment |
| AMB-09 | contactEmail | **Resolved by official clarification** (see §6) |
| AMB-10 | concatenations | code zero-padding treated as a format difference |
| AMB-11 | termEndDate | same rule as assignmentEndDate |
| AMB-12 | positionName | **Resolved by official clarification**: real error |

AMB-02 to AMB-08, AMB-10 and AMB-11 remain documented, unconfirmed hypotheses. Other choices:

* emails are compared case-insensitively;
* `dd/mm/yyyy` dates are read day-first;
* a missing assignment is never automatically an error;
* when source hours are empty and the destination holds the job default, the case is `A_INVESTIGUER`; when the
  source explicitly differs, it is `ANOMALIE`.

---

## 12. Privacy and read-only behaviour

* `data/` is opened **read-only**. Nothing is ever written, renamed or deleted there, and writers refuse paths inside
  `data/`.
* **No external data transmission, no external LLM call.**
* Generated files go only to `outputs/` and `synthetic_data/`.
* The original challenge files are **excluded from the public repository** (`.gitignore`). Per the official
  clarification, reports generated from the anonymised extracts (`outputs/corroboration_report.xlsx`, CSVs, executed
  notebook) may be published.
* Accepted corrections are exported separately. The corrected preview and candidate copy **never overwrite** the
  official destination.
* Each run records the SHA-256 fingerprint of every input file.

---

## 13. Limitations and next steps

* The official sample is very small; level-3 thresholds (`src/config.py`) should be calibrated on real volumes.
* Person-level fields are compared on every matched assignment row, so a person-level error on a multi-assignment
  employee appears once per row.
* The email rule does not define compound names (spaces, hyphens); such cases are marked `INFERE`.
* Proposed rows copy unmapped destination columns only when a relation is observable; otherwise they are left
  `A_CONFIRMER`.
* `FeedbackModel` needs at least 5 recorded decisions covering at least 2 classes before suggesting anything.
* Next steps:
  * plug an approved internal LLM into `LLMAssistant` for richer explanations of `A_INVESTIGUER` cases;
  * turn accepted systemic decisions into versioned rule exceptions;
  * add reviewer identity and roles.
