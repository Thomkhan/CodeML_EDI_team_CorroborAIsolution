"""CorroborAI — assistant d'investigation RH ↔ Temps (Streamlit).

Run:  streamlit run app.py   (opens in dark mode, see .streamlit/config.toml)
Reads the input files only; human decisions and exports are written to outputs/.
Business wording lives in src/presentation.py; verdicts come unchanged from the engine.
"""
from __future__ import annotations

import copy
import html
import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src import config as C
from src import presentation as P
from src.corroboration import Corroborator, summarize
from src.decisions import DecisionStore, apply_decisions
from src.export import (audit_log, corrected_destination, export_accepted, export_corrected_destination, export_csv,
                        export_report, report_bytes)
from src.load_data import find_file, load_all
from src.normalize import display
from src.proposals import accepted_corrections

st.set_page_config(page_title="CorroborAI", page_icon="🔎", layout="wide")

MAIN_PAGES = [("dashboard", "📊 Tableau de bord"), ("investigation", "🔍 Investigation"),
              ("systemic", "🧩 Problèmes systémiques"), ("actions", "🛠️ Actions")]
REF_PAGES = [("mapping", "Mapping des champs"), ("rules", "Règles & clarifications"),
             ("matching", "Appariement des affectations"), ("export", "Export & audit")]
GUIDE = "#2EC4B6"          # guide accent (deliberately not the AI purple)
GUIDE_TINT = "#10282A"
store = DecisionStore()

st.markdown(f"""<style>
:root{{--txt:#E6EAF0;--sub:#9AA4B2;--card:#1A2028;--line:#2D3540}}
.cb-badge{{display:inline-block;padding:2px 11px;border-radius:14px;color:#0E1116;font-weight:700;font-size:.85rem;vertical-align:middle}}
.cb-title{{font-size:1.45rem;font-weight:700;color:var(--txt);margin:.1rem 0 .5rem 0}}
.cb-chip{{display:inline-block;float:right;margin-top:.35rem;padding:4px 12px;border-radius:14px;background:var(--card);border:1px solid var(--line);color:#C9D1D9;font-size:.8rem}}
.cb-kpi{{border-left:5px solid var(--c);background:var(--t);border-radius:10px;padding:10px 14px;color:var(--txt);height:100%}}
.cb-kpi .l{{font-size:.8rem;font-weight:600;color:var(--sub)}}
.cb-kpi .v{{font-size:1.8rem;font-weight:700;color:var(--c);line-height:1.2}}
.cb-kpi .s{{font-size:.75rem;color:var(--sub)}}
.cb-box{{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px;text-align:center;color:var(--txt)}}
.cb-box .l{{font-size:.78rem;font-weight:600;color:var(--sub)}}
.cb-box .v{{font-size:1.25rem;font-weight:700;color:var(--vc,#E6EAF0);word-break:break-all;margin:.15rem 0}}
.cb-note{{border-left:5px solid var(--c);background:var(--t);border-radius:8px;padding:10px 14px;color:var(--txt);margin:.4rem 0}}
.cb-tree{{display:flex;gap:10px;align-items:stretch;flex-wrap:wrap}}
.cb-node{{border:1px solid var(--line);border-top:4px solid var(--c);background:var(--card);border-radius:10px;padding:8px 10px;text-align:center;color:var(--txt);min-width:110px;flex:1}}
.cb-node .v{{font-size:1.55rem;font-weight:700;color:var(--c)}}
.cb-node .l{{font-size:.76rem;color:var(--sub)}}
.cb-branch{{border:1px dashed var(--line);border-radius:12px;padding:8px;flex:1}}
.cb-branch .h{{font-size:.8rem;color:var(--sub);margin-bottom:6px}}
.cb-eq{{font-family:monospace;color:#C9D1D9;font-size:.85rem;margin:.35rem 0}}
.cb-bar{{background:#232A33;border-radius:6px;height:10px;margin-top:4px}}
.cb-bar > div{{background:var(--c);height:10px;border-radius:6px}}
.cb-banner{{background:#33240F;border:1px dashed #FFA040;border-radius:10px;padding:8px 14px;color:#FFD9A8;font-weight:600;margin-bottom:.4rem}}
.cb-ai{{border-left:5px solid #B47FE0;background:#261C33;border-radius:10px;padding:10px 16px;color:var(--txt)}}
.cb-rev{{border-left:5px solid #9AA4B2;background:#20262E;border-radius:10px;padding:10px 16px;color:var(--txt)}}
.cb-guide{{border:1px solid {GUIDE};border-left:6px solid {GUIDE};background:{GUIDE_TINT};border-radius:10px;padding:10px 14px;color:var(--txt)}}
.cb-open{{border:1px solid {GUIDE};background:{GUIDE_TINT};border-radius:8px;padding:6px 12px;color:var(--txt);font-size:.9rem}}
</style>""", unsafe_allow_html=True)


# ============================================================================ data
def available_sources() -> dict:
    out = {"Données officielles (data/)": str(C.DATA_DIR)}
    if C.SYNTHETIC_DIR.exists():
        for d in sorted(C.SYNTHETIC_DIR.iterdir()):
            if d.is_dir() and find_file(d, "source") and find_file(d, "destination"):
                out[f"Démo synthétique — {d.name}"] = str(d)
    up = C.OUTPUT_DIR / "uploads"
    if up.exists():
        for d in sorted(up.iterdir(), reverse=True):
            if d.is_dir() and find_file(d, "source"):
                out[f"Téléversé — {d.name}"] = str(d)
    return out


def files_signature(data_dir: str) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime) for p in Path(data_dir).iterdir() if p.is_file()))


@st.cache_resource(show_spinner="Corroboration en cours…", max_entries=4)
def compute(data_dir: str, _sig: tuple):
    return Corroborator(load_all(data_dir)).run(decisions=None)


@st.cache_data(show_spinner=False, max_entries=4)
def synthetic_validation(data_dir: str, _sig: tuple) -> dict | None:
    gt_path = Path(data_dir) / "ground_truth.csv"
    if not gt_path.exists():
        return None
    from src.synthetic import evaluate
    gt = pd.read_csv(gt_path, dtype=str)
    ev = evaluate(compute(data_dir, _sig).cases, gt)
    return {"accuracy": ev["accuracy_class"], "per_category": ev["per_category"]}


def with_decisions(base):
    res = copy.copy(base)
    res.cases, res.patterns, res.proposals = base.cases.copy(), base.patterns.copy(), base.proposals.copy()
    apply_decisions(res, store)
    res.summary = summarize(res)
    return res


# ============================================================================ state
ss = st.session_state
for k, v in {"page": "dashboard", "selected_case": None, "came_from": None, "table_version": 0,
             "guide_active": True, "guide_step": 0, "result_panel": None, "toast": None, "edit_case": None,
             "selected_action": None, "action_result": None, "report_xlsx": None, "preview_xlsx": None,
             "show_preview": False}.items():
    ss.setdefault(k, v)
if ss.toast:
    st.toast(ss.toast, icon="✅")
    ss.toast = None


def goto(page: str, case_id: str | None = None, action_id: str | None = None):
    if page != ss.page:
        ss.came_from = ss.page
    ss.page = page
    if case_id:
        ss.selected_case = case_id
    if action_id:
        ss.selected_action = action_id


def bump():
    ss.table_version += 1


# ============================================================================ html helpers
def esc(x) -> str:
    return html.escape(str(x), quote=False)   # text content only (never used inside attributes)


def badge(verdict: str, text: str | None = None) -> str:
    return (f"<span class='cb-badge' style='background:{P.COLORS.get(verdict, '#9AA4B2')}'>"
            f"{esc(text or P.VERDICT_LABEL.get(verdict, verdict))}</span>")


def kpi(label, value, key, sub="") -> str:
    return (f"<div class='cb-kpi' style='--c:{P.COLORS[key]};--t:{P.TINTS[key]}'><div class='l'>{esc(label)}</div>"
            f"<div class='v'>{esc(value)}</div><div class='s'>{esc(sub)}</div></div>")


def value_box(label, value, value_key=None, tag=None) -> str:
    """Neutral container; only the value (and an optional status tag) carries colour."""
    vc = P.COLORS.get(value_key, "#E6EAF0") if value_key else "#E6EAF0"
    return (f"<div class='cb-box' style='--vc:{vc}'><div class='l'>{esc(label)}</div><div class='v'>{esc(value)}</div>"
            + (f"<div>{tag}</div>" if tag else "") + "</div>")


def note(text, key="REVU") -> None:
    st.markdown(f"<div class='cb-note' style='--c:{P.COLORS[key]};--t:{P.TINTS[key]}'>{text}</div>",
                unsafe_allow_html=True)


def ts(t) -> str:
    try:
        return datetime.fromisoformat(str(t)).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(t)


def vertical(series: pd.Series, highlight: set | None = None) -> pd.DataFrame:
    rows = []
    for k, v in series.items():
        if str(k).startswith("_") or str(k).endswith("_n"):
            continue
        val = display(v)
        if val == "" and not (highlight and k in highlight):
            continue
        rows.append({"champ": ("➤ " if highlight and k in highlight else "") + str(k), "valeur brute": val})
    return pd.DataFrame(rows)


def styled(df: pd.DataFrame, treated: list, selected: list | None = None):
    selected = selected or [False] * len(df)

    def style(row):
        if selected[row.name]:
            return ["background-color:#123236;color:#E6FFFB;font-weight:700"] * len(row)
        if treated[row.name]:
            return ["color:#7D8590;background-color:#161B22"] * len(row)
        return [""] * len(row)
    return df.style.apply(style, axis=1)


# ============================================================================ sidebar — dataset first
sources = available_sources()
st.sidebar.markdown("## 🔎 CorroborAI")
st.sidebar.caption("Assistant d'investigation RH ↔ Temps")
st.sidebar.markdown("**JEU DE DONNÉES**")
choice = st.sidebar.selectbox("Jeu de données", list(sources), label_visibility="collapsed")
with st.sidebar.expander("Téléverser des fichiers"):
    st.caption("Source, destination, détail du poste, motifs (+ mapping facultatif). Copiés dans outputs/uploads/.")
    ups = st.file_uploader("Fichiers .xlsx / .csv", type=["xlsx", "csv"], accept_multiple_files=True)
    if ups and st.button("Enregistrer et utiliser"):
        target = C.OUTPUT_DIR / "uploads" / datetime.now().strftime("%Y%m%d_%H%M%S")
        target.mkdir(parents=True, exist_ok=True)
        for f in ups:
            (target / f.name).write_bytes(f.getbuffer())
        st.rerun()
if st.sidebar.button("↻ Relancer la corroboration", width="stretch"):
    compute.clear()
    synthetic_validation.clear()

data_dir = sources[choice]
if ss.get("dataset") != data_dir:
    ss.update(dataset=data_dir, selected_case=None, selected_action=None, report_xlsx=None, preview_xlsx=None,
              result_panel=None, action_result=None)
try:
    sig = files_signature(data_dir)
    base = compute(data_dir, sig)
except Exception as exc:  # loading problems are shown, not raised
    st.error(f"Impossible de charger « {choice} » : {exc}")
    st.stop()
res = with_decisions(base)
cases, patterns, proposals = res.cases, res.patterns, res.proposals
work = P.workload(cases, patterns)
is_official = Path(data_dir).resolve() == C.DATA_DIR.resolve()
has_gt = (Path(data_dir) / "ground_truth.csv").exists()
s_ = res.summary
if is_official:
    DATASET_KIND = "Jeu officiel"
    DATASET_DETAIL = (f"{s_['n_persons']} employés · {s_['n_source_assignments']} affectations source · "
                      f"{s_['n_destination_rows']} lignes destination")
else:
    DATASET_KIND = "Jeu synthétique" if has_gt else "Jeu téléversé"
    DATASET_DETAIL = f"{s_['n_persons']:,} employés".replace(",", " ") + (" · vérité terrain connue" if has_gt else "")
st.sidebar.caption(f"{'🟢' if is_official else '🧪'} **{DATASET_KIND}** — {DATASET_DETAIL}")
with st.sidebar.expander("Fichiers utilisés (lecture seule)"):
    for role, p in res.dataset.files.items():
        st.caption(f"• {role} : {Path(p).name}")

st.sidebar.markdown("**TRAVAIL PRINCIPAL**")
for key, label in MAIN_PAGES:
    suffix = f"  ({work['remaining']})" if key == "investigation" and work["remaining"] else ""
    st.sidebar.button(label + suffix, key=f"nav_{key}", on_click=goto, args=(key,), width="stretch",
                      type="primary" if ss.page == key else "secondary")
with st.sidebar.expander("📚 Référentiel & technique", expanded=ss.page in dict(REF_PAGES)):
    for key, label in REF_PAGES:
        st.button(label, key=f"nav_{key}", on_click=goto, args=(key,), width="stretch",
                  type="primary" if ss.page == key else "secondary")


# ============================================================================ guide (not an AI component)
def guide_steps() -> list[dict]:
    def pick(mask, prefer=None):
        sub = cases[mask]
        if prefer is not None and len(sub[prefer(sub)]):
            sub = sub[prefer(sub)]
        return sub["case_id"].iloc[0] if len(sub) else None

    fc = cases["case_type"] == "COMPARAISON_CHAMP"
    f = P.funnel(cases)
    jeu = "jeu officiel" if is_official else "jeu synthétique de démonstration"
    steps = [
        {"title": "Jeu de données et tableau de bord", "page": "dashboard", "case": None,
         "sidebar": f"Vous analysez actuellement le {jeu}. Commencez par le tableau de bord pour comprendre combien "
                    "de contrôles nécessitent réellement une intervention humaine.",
         "text": f"Voici le jeu utilisé et la réduction de la charge d'investigation : {f['raw_diff']} différences "
                 f"brutes ne signifient pas {f['raw_diff']} problèmes.",
         "look": ["le schéma « Réduction de la charge d'investigation »", "la carte « Décisions humaines »"]},
        {"title": "Un écart justifié", "page": "investigation",
         "case": pick(fc & (cases["verdict"] == C.ECART_JUSTIFIE) & (cases["field"] == "assignmentStartDate")
                      & (cases["stage1_result"] == "DIFFERENT"), lambda s: s["person_id"] == "9989151"),
         "sidebar": "Une différence brute n'est pas nécessairement une erreur.",
         "text": "La date Temps diffère de la date RH, mais la règle documentée appliquée à l'historique du poste "
                 "reproduit exactement la valeur Temps.",
         "look": ["les trois valeurs de « Ce qu'on observe »", "la conclusion « La différence est expliquée… »"]},
        {"title": "Une anomalie et son analyse", "page": "investigation",
         "case": pick(fc & (cases["verdict"] == C.ANOMALIE) & (cases["hypothesis_code"] == "VALEURS_INTERVERTIES"),
                      lambda s: s["person_id"] == "2762457"),
         "sidebar": "Une règle identifie l'erreur ; l'analyse assistée repère ensuite la probable inversion.",
         "text": "La règle métier détermine le verdict. L'analyse assistée (violet) propose ensuite une cause probable "
                 "sans remplacer ce verdict.",
         "look": ["« Ce que dit la règle »", "la carte violette « Analyse proposée par l'agent IA local »",
                  "les boutons de décision et leur effet exact"]},
        {"title": "Un cas ambigu", "page": "investigation",
         "case": pick(cases["case_type"] == "AFFECTATION_ABSENTE_DESTINATION", lambda s: s["person_id"] == "1545850"),
         "sidebar": "Quand la preuve est insuffisante, CorroborAI demande une décision humaine.",
         "text": "Une affectation temporaire RH n'a pas d'équivalent dans Temps. CorroborAI prépare une ligne candidate "
                 "et indique la certitude de chaque valeur, mais ne tranche pas seul.",
         "look": ["le tableau « Affectations dans le système RH »", "la ligne candidate et ses niveaux de certitude"]},
        {"title": "Un problème systémique", "page": "systemic", "case": None,
         "sidebar": "Une décision peut traiter un problème touchant de nombreux dossiers.",
         "text": f"{s_['n_ANOMALIE']} anomalies techniques, mais {work['required']} décisions humaines : les anomalies "
                 "de même cause se valident une seule fois.",
         "look": ["les cartes « Problèmes systémiques nécessitant une décision »",
                  "les « Indices complémentaires » plus bas"]},
    ]
    return [x for x in steps if x["case"] or x["page"] in ("dashboard", "systemic")]


def guide_go(i: int):
    steps = guide_steps()
    ss.guide_step = max(0, min(i, len(steps) - 1))
    step = steps[ss.guide_step]
    goto(step["page"], step["case"])


def guide_stop():
    ss.guide_active = False


def guide_restart():
    ss.guide_active = True
    guide_go(0)


st.sidebar.markdown("---")
if ss.guide_active:
    steps_ = guide_steps()
    gi = min(ss.guide_step, len(steps_) - 1)
    with st.sidebar.container(border=True):
        st.markdown(f"**🔎 Guide CorroborAI** · Étape {gi + 1}/{len(steps_)}")
        st.caption(steps_[gi]["sidebar"])
        a, b = st.columns(2)
        if gi < len(steps_) - 1:
            a.button("Suivant", key="g_next_sb", on_click=guide_go, args=(gi + 1,), width="stretch", type="primary")
        else:
            a.button("Terminer", key="g_end_sb", on_click=guide_stop, width="stretch", type="primary")
        b.button("Passer le guide", key="g_skip", on_click=guide_stop, width="stretch")
else:
    st.sidebar.button("🔎 Relancer le guide", key="g_restart", on_click=guide_restart, width="stretch")


def guide_callout():
    if not ss.guide_active:
        return
    steps_ = guide_steps()
    gi = min(ss.guide_step, len(steps_) - 1)
    step = steps_[gi]
    look = "".join(f"<li>{esc(x)}</li>" for x in step["look"])
    st.markdown(f"<div class='cb-guide'><b>🔎 Guide — étape {gi + 1}/{len(steps_)} : {esc(step['title'])}</b><br>"
                f"{esc(step['text'])}<br><span style='color:#9AA4B2'>À regarder :</span><ul style='margin:0'>{look}"
                "</ul></div>", unsafe_allow_html=True)
    c1, c2, c3, _ = st.columns([1, 1, 1.2, 4])
    c1.button("◀ Précédent", key="g_prev", on_click=guide_go, args=(gi - 1,), disabled=gi == 0, width="stretch")
    if gi < len(steps_) - 1:
        c2.button("Suivant ▶", key="g_next", on_click=guide_go, args=(gi + 1,), width="stretch", type="primary")
    else:
        c2.button("Terminer ✓", key="g_end", on_click=guide_stop, width="stretch", type="primary")
    c3.button("Quitter le guide", key="g_quit", on_click=guide_stop, width="stretch")


def header(title: str):
    chip = f"{'🟢' if is_official else '🧪'} <b>{DATASET_KIND}</b> · {esc(DATASET_DETAIL)}"
    st.markdown(f"<div><span class='cb-chip'>{chip}</span><div class='cb-title'>{esc(title)}</div></div>",
                unsafe_allow_html=True)
    if not is_official:
        st.markdown("<div class='cb-banner'>🧪 Jeu synthétique ou téléversé — ne représente pas les données officielles "
                    "du défi.</div>", unsafe_allow_html=True)
    guide_callout()


# ============================================================================ decision persistence
def decision_history(case_id: str, props: pd.DataFrame) -> pd.DataFrame:
    targets = [case_id] + [f"{p}::{f}" for p, f in zip(props["proposal_id"], props["field"])]
    pid = cases.loc[cases["case_id"] == case_id, "pattern_id"]
    if len(pid) and str(pid.iloc[0]).startswith(("P-SYS", "P-SEG")):
        targets.append(pid.iloc[0])
    return store.history(targets, res.dataset_key)


def history_lines(hist: pd.DataFrame, cs=None) -> list[str]:
    lines, seen = [], set()
    for _, h in hist.iterrows():
        if h["target_type"] == "case":
            what = P.decision_label(cs, h["decision"]) if cs is not None else P.DECISION_SHORT.get(h["decision"])
        elif h["target_type"] == "pattern":
            what = "Décision de groupe : " + P.DECISION_SHORT.get(h["decision"], h["decision"])
        else:
            what = "Action corrective : " + P.PROPOSAL_STATUS_LABEL.get(h["decision"], h["decision"]).lower()
        key = (h["timestamp"][:19], what)
        if key in seen:          # one line per event, even when it covers many proposal rows
            continue
        seen.add(key)
        lines.append(f"{ts(h['timestamp'])} — {what}" + (f" — « {h['comment']} »" if h["comment"] else ""))
    return lines


def action_id_for_case(cs) -> str | None:
    pid = str(cs.get("pattern_id", ""))
    if pid.startswith(("P-SYS", "P-SEG")):
        return f"G::{pid}"
    pr = proposals.loc[proposals["case_id"] == cs["case_id"], "proposal_id"]
    return pr.iloc[0] if len(pr) else None


def save_case_decision(cs, decision, comment, props, mods=None):
    store.record("case", cs["case_id"], decision, comment, mods or None, reviewer="app", dataset_key=res.dataset_key)
    lines = P.outcome_lines(cs, decision, props, mods)
    ss.result_panel = {"case_id": cs["case_id"], "lines": lines, "decision": decision,
                       "action_id": action_id_for_case(cs) if len(props) else None}
    ss.toast = "Décision enregistrée — " + lines[0]
    ss.edit_case = None
    bump()
    st.rerun()


def save_group_decision(p, decision, comment):
    store.record("pattern", p["pattern_id"], decision, comment, reviewer="app", dataset_key=res.dataset_key)
    ss.toast = ("Décision de groupe annulée" if decision == C.DECISION_REOPEN else
                f"Décision de groupe enregistrée — {P.DECISION_SHORT.get(decision, decision)}")
    bump()
    st.rerun()


# ============================================================================ dashboard
def group_title(p) -> str:
    return str(p["title"]).split(" (")[0] if p["pattern_type"] == "SYSTEMIQUE_CHAMP" else str(p["title"])


def page_dashboard():
    header("Tableau de bord")
    s = res.summary
    done = work["individual_done"] + work["groups_done"]
    cards = [("Contrôles effectués", s["n_cases"], "NEUTRE", "champs × affectations"),
             ("Conformes", s["n_CONFORME"], C.CONFORME, "aucune action"),
             ("Écarts justifiés", s["n_ECART_JUSTIFIE"], C.ECART_JUSTIFIE, "expliqués par une règle"),
             ("Anomalies détectées", s["n_ANOMALIE"], C.ANOMALIE, f"dont {work['systemic_cases']} regroupées"),
             ("Cas ambigus", s["n_A_INVESTIGUER"], C.A_INVESTIGUER, "à investiguer"),
             ("Décisions humaines", f"{work['remaining']} restantes", "ACTION",
              f"sur {work['required']} initiales · {done} déjà traitée{'s' if done > 1 else ''}")]
    for col, (l, v, k, sub) in zip(st.columns(6), cards):
        col.markdown(kpi(l, v, k, sub), unsafe_allow_html=True)
    if s["n_ANOMALIE"] + s["n_A_INVESTIGUER"] > work["required"]:
        note(f"<b>{s['n_ANOMALIE']} anomalies techniques détectées, mais seulement {work['required']} décisions "
             f"humaines sont nécessaires</b> grâce au regroupement des problèmes systémiques "
             f"({work['systemic_cases']} anomalies dans {work['groups']} groupes validés en une seule fois).", "ACTION")

    st.markdown("#### Réduction de la charge d'investigation")
    f = P.funnel(cases)

    def node(v, label, key):
        return (f"<div class='cb-node' style='--c:{P.COLORS[key]}'><div class='v'>{v}</div>"
                f"<div class='l'>{esc(label)}</div></div>")

    ident_children = node(f["identical_conform"], "identiques → conformes", C.CONFORME)
    if f["identical_contradicted"]:
        ident_children += node(f["identical_contradicted"], "identiques mais contraires à une règle", C.ANOMALIE)
    diff_children = "".join([
        node(f["normalized"], "format normalisé → conformes", C.CONFORME),
        node(f["derived_conform"], "valeur dérivée → conformes", C.CONFORME),
        node(f["justified"], "expliquées par règles → écarts justifiés", C.ECART_JUSTIFIE),
        node(f["anomalies"], "anomalies", C.ANOMALIE),
        node(f["investigate"], "à investiguer", C.A_INVESTIGUER)])
    st.markdown(
        f"<div class='cb-tree'>{node(f['total'], 'contrôles effectués', 'NEUTRE')}"
        f"<div class='cb-branch' style='flex:1.3'><div class='h'>├─ {f['identical']} identiques dès la comparaison"
        f"</div><div class='cb-tree'>{ident_children}</div></div>"
        f"<div class='cb-branch' style='flex:5'><div class='h'>└─ {f['raw_diff']} différences brutes (ou valeurs à "
        f"dériver par une règle)</div><div class='cb-tree'>{diff_children}</div></div></div>",
        unsafe_allow_html=True)
    ok = "✔" if f["check_total"] and f["check_diff"] else "✖"
    ident_eq = (f"{f['identical']} = {f['identical_conform']} + {f['identical_contradicted']} · "
                if f["identical_contradicted"] else "")
    st.markdown(f"<div class='cb-eq'>{ok} {f['total']} = {f['identical']} + {f['raw_diff']} · {ident_eq}"
                f"{f['raw_diff']} = {f['normalized']} + {f['derived_conform']} + {f['justified']} + "
                f"{f['anomalies']} + {f['investigate']}</div>", unsafe_allow_html=True)
    a, arrow, b = st.columns([3, 1, 3])
    a.markdown(kpi("À examiner", f"{s['n_ANOMALIE'] + s['n_A_INVESTIGUER']} cas", C.ANOMALIE,
                   f"{s['n_ANOMALIE']} anomalies + {s['n_A_INVESTIGUER']} cas ambigus"), unsafe_allow_html=True)
    arrow.markdown("<div style='text-align:center;color:#9AA4B2;padding-top:14px'>regroupement<br>et priorisation"
                   "<br>➜</div>", unsafe_allow_html=True)
    b.markdown(kpi("Décisions humaines seulement", work["required"], "ACTION",
                   f"{work['individual']} cas individuels + {work['groups']} groupes systémiques"),
               unsafe_allow_html=True)
    st.caption(f"💡 {f['raw_diff']} différences brutes ne signifient pas {f['raw_diff']} problèmes.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Comment le verdict moteur a été obtenu")
        parts = [("Comparaison / normalisation", int((cases["rule_type"] == C.RAW).sum()), C.CONFORME,
                  "valeurs directement comparables"),
                 ("Règles métier", int((cases["rule_type"] == C.DETERMINISTIC).sum()), C.ECART_JUSTIFIE,
                  "une règle documentée confirme ou explique")]
        n_ai = int((cases["rule_type"] == C.AI_ASSISTED).sum())
        if n_ai:
            parts.append(("Analyse assistée (verdict)", n_ai, "IA", "regroupement ayant modifié le verdict"))
        for col, (l, n, k, sub) in zip(st.columns(len(parts)), parts):
            col.markdown(kpi(l, n, k, sub), unsafe_allow_html=True)
        st.caption(f"{' + '.join(str(p[1]) for p in parts)} = {sum(p[1] for p in parts)} contrôles : chaque contrôle "
                   "appartient à une seule catégorie.")
    with right:
        st.markdown("#### Où l'analyse assistée apporte une valeur supplémentaire")
        nc = cases[cases["verdict"].isin([C.ANOMALIE, C.A_INVESTIGUER])]
        st.markdown(kpi("Cas enrichis par l'analyse assistée", len(nc), "IA",
                        "sous-ensemble des contrôles ci-contre"), unsafe_allow_html=True)
        st.caption(f"Ces {len(nc)} cas font partie des contrôles ci-contre. L'analyse assistée ajoute regroupement, "
                   "hypothèse de cause, priorité ou explication ; elle ne remplace pas le verdict déterministe.")
        ex = []
        for ptype, label in (("VALEURS_INTERVERTIES", "valeurs interverties"),
                             ("SYSTEMIQUE_CHAMP", "problèmes systémiques"),
                             ("SYSTEMIQUE_SEGMENT", "défaillances par segment"),
                             ("RECURRENT", "repli vers la valeur par défaut / cause commune"),
                             ("QUALITE_DONNEES", "qualité de données (écarts justifiés signalés)")):
            sub = patterns[patterns["pattern_type"] == ptype] if not patterns.empty else patterns
            n = int(sub["n_cases"].sum()) if len(sub) else 0
            if n:
                ex.append(f"{label} : {n} cas")
        if ex:
            st.markdown("\n".join(f"- {x}" for x in ex))

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Où reste l'intervention humaine ?")
        st.caption("Ces cas ne peuvent pas être clôturés automatiquement et nécessitent une décision fonctionnelle.")
        rem = P.remaining_by_category(cases, patterns)
        if rem.empty:
            st.success("🎉 Toutes les décisions humaines ont été prises.")
        else:
            mx = rem["decisions"].max()
            for _, r in rem.iterrows():
                color = (P.COLORS["ACTION"] if r["categorie"].startswith("Problème systémique") else
                         P.COLORS[C.A_INVESTIGUER] if "manquante" in r["categorie"].lower() else P.COLORS[C.ANOMALIE])
                st.markdown(f"<div style='margin:.35rem 0'><b>{esc(r['categorie'])}</b> — {r['decisions']} "
                            f"décision{'s' if r['decisions'] > 1 else ''} <span style='color:#9AA4B2'>"
                            f"({esc(r['type'])})</span><div class='cb-bar' style='--c:{color}'><div style='width:"
                            f"{100 * r['decisions'] / mx:.0f}%'></div></div></div>", unsafe_allow_html=True)
        top = cases[cases["verdict"].isin([C.ANOMALIE, C.A_INVESTIGUER]) & ~cases["systemic"].astype(bool)
                    & ~cases["human_decision"].map(P.is_treated)].sort_values("priority_score", ascending=False).head(5)
        if len(top):
            st.markdown("**À traiter en priorité**")
            for _, r in top.iterrows():
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"{badge(r['verdict'])} &nbsp; {esc(P.problem_title(r))} — employé {esc(r['person_id'])}",
                            unsafe_allow_html=True)
                c2.button("Ouvrir", key=f"open_{r['case_id']}", on_click=goto, args=("investigation", r["case_id"]))
    with right:
        st.markdown("#### Problèmes systémiques à valider une seule fois")
        st.caption("Plusieurs anomalies partagent la même cause : une seule validation humaine traite tout le groupe.")
        groups = patterns[patterns["requires_decision"].astype(bool)] if not patterns.empty else patterns
        for _, p in groups.sort_values("priority_score", ascending=False).iterrows():
            with st.container(border=True):
                done_ = P.is_treated(p.get("human_decision", ""))
                clar = C.OFFICIAL_CLARIFICATIONS.get(str(p["field"]), {})
                members = cases[cases["case_id"].isin(str(p["case_ids"]).split(";"))]
                st.markdown(f"**{'✅ ' if done_ else ''}{esc(group_title(p))}**")
                lines = [P.rows_employees(len(members), members["person_id"].nunique()),
                         f"priorité {P.PRIORITY_LABEL.get(p['priority'], '').lower()}"]
                if clar.get("known_cause"):
                    lines.insert(1, f"cause connue : {clar['known_cause']}")
                lines.append("groupe traité" if done_ else "1 décision humaine")
                st.caption(" · ".join(lines))
        st.button("Voir les problèmes systémiques", on_click=goto, args=("systemic",), type="primary")

    if not is_official:
        val = synthetic_validation(data_dir, sig)
        with st.expander("🧪 Jeu synthétique : catégories injectées et validation"):
            if val:
                st.write(f"Exactitude par rapport à la vérité terrain : **{val['accuracy'] * 100:.1f} %**")
                st.dataframe(val["per_category"].rename(columns={"category": "catégorie injectée"}),
                             hide_index=True, width="stretch")
            else:
                st.caption("Pas de vérité terrain disponible pour ce jeu.")


# ============================================================================ investigation
def page_investigation():
    header("Investigation")
    sel = cases[cases["case_id"] == ss.selected_case]
    if len(sel):
        c1, c2 = st.columns([5, 1.4])
        c1.markdown(f"<div class='cb-open'>📂 <b>Cas ouvert :</b> {esc(P.problem_title(sel.iloc[0]))} — Employé "
                    f"{esc(sel.iloc[0]['person_id'])}</div>", unsafe_allow_html=True)
        if ss.came_from and ss.came_from != "investigation":
            back = dict(MAIN_PAGES + REF_PAGES).get(ss.came_from, "page précédente")
            c2.button(f"← Retour : {back.split(' ', 1)[-1]}", on_click=goto, args=(ss.came_from,), width="stretch")
    with st.container(border=True):
        f = st.columns([2, 1.4, 2, 1.2, 1.6, 1.2])
        statuses = f[0].multiselect("Statut", list(P.VERDICT_LABEL.values()), default=["Anomalie", "À investiguer"])
        prios = f[1].multiselect("Priorité", ["Haute", "Moyenne", "Basse"])
        fields = f[2].multiselect("Champ contrôlé", sorted({P.field_label(x) for x in cases["field"]}))
        employee = f[3].text_input("Employé / matricule")
        methods = f[4].multiselect("Méthode de validation", list(P.METHOD_LABEL.values()))
        reviewed = f[5].selectbox("Revue", ["Tous", "Non revus", "Revus"])
        t1, t2 = st.columns(2)
        hide_done = t1.toggle("Masquer les cas déjà traités", value=False)
        incl_sys = t2.toggle("Inclure les cas rattachés à un problème systémique", value=False,
                             help="Ces cas sont normalement traités en une seule décision de groupe.")
    inv = {v: k for k, v in P.VERDICT_LABEL.items()}
    view = cases
    if statuses:
        view = view[view["verdict"].isin([inv[x] for x in statuses])]
    if prios:
        view = view[view["priority"].map(P.PRIORITY_LABEL).isin(prios)]
    if fields:
        view = view[view["field"].map(P.field_label).isin(fields)]
    if employee:
        view = view[view["person_id"].str.contains(employee.strip(), regex=False)]
    if methods:
        view = view[view["rule_type"].map(P.METHOD_LABEL).isin(methods)]
    treated = view["human_decision"].map(P.is_treated)
    if reviewed == "Non revus":
        view = view[~treated]
    elif reviewed == "Revus":
        view = view[treated]
    if hide_done:
        view = view[~view["human_decision"].map(P.is_treated)]
    if not incl_sys:
        view = view[~view["systemic"].astype(bool)]
    n_total = len(view)
    view = view.sort_values(["priority_score", "person_id"], ascending=[False, True]).head(1000)
    priority_order = view["case_id"].tolist()            # stable order used by Précédent / Suivant
    if ss.selected_case in set(priority_order):          # the opened case is shown first, marked ▶
        view = pd.concat([view[view["case_id"] == ss.selected_case], view[view["case_id"] != ss.selected_case]])
    view = view.reset_index(drop=True)

    if len(view):
        is_sel = (view["case_id"] == ss.selected_case).tolist()
        table = pd.DataFrame({
            " ": ["▶ Ouvert" if x else "" for x in is_sel],
            "Statut": view["verdict"].map(P.verdict_text),
            "Employé": view["person_id"],
            "Problème": view.apply(P.problem_title, axis=1),
            "Priorité": view["priority"].map(P.PRIORITY_LABEL),
            "Confiance": view["confidence"].map(P.confidence_text),
            "Revue humaine": view.apply(P.review_label, axis=1),
        })
        event = st.dataframe(styled(table, view["human_decision"].map(P.is_treated).tolist(), is_sel),
                             hide_index=True, width="stretch", height=min(320, 38 + 35 * len(table)),
                             on_select="rerun", selection_mode="single-row", lazy=False,
                             key=f"queue_{ss.table_version}_{ss.selected_case}")
        if event and event.selection.rows:
            new = view.loc[event.selection.rows[0], "case_id"]
            if new != ss.selected_case:
                ss.selected_case = new
                st.rerun()
        st.caption(f"{n_total} cas" + (" (1 000 plus prioritaires affichés)" if n_total > len(view) else "")
                   + " · cliquez sur une ligne pour l'ouvrir · ▶ = cas ouvert · lignes grisées = déjà revues.")
    else:
        st.success("🎉 Aucun cas dans la file pour ces filtres.")

    if ss.selected_case not in set(cases["case_id"]):
        ss.selected_case = priority_order[0] if priority_order else None
    if ss.selected_case is None:
        return
    if priority_order:
        pos = priority_order.index(ss.selected_case) if ss.selected_case in priority_order else -1
        prev_id = priority_order[pos - 1] if pos > 0 else None
        next_id = priority_order[pos + 1] if 0 <= pos < len(priority_order) - 1 else (
            priority_order[0] if pos == -1 else None)
        n1, n2, _ = st.columns([1.2, 1.2, 6])
        n1.button("◀ Cas précédent", disabled=prev_id is None, width="stretch",
                  on_click=lambda: ss.update(selected_case=prev_id))
        n2.button("Cas suivant ▶", disabled=next_id is None, width="stretch",
                  on_click=lambda: ss.update(selected_case=next_id))
    st.markdown("---")
    render_case(cases[cases["case_id"] == ss.selected_case].iloc[0], in_queue=ss.selected_case in priority_order)


def render_case(cs, in_queue=True):
    v = cs["verdict"]
    props = proposals[proposals["case_id"] == cs["case_id"]]
    panel = ss.result_panel
    if panel and panel["case_id"] == cs["case_id"]:
        result_panel(cs, panel)
    st.markdown(f"<div class='cb-title'>{badge(v)} &nbsp;{esc(P.problem_title(cs))} — Employé {esc(cs['person_id'])}"
                "</div>", unsafe_allow_html=True)
    if not in_queue:
        st.caption("Ce cas n'apparaît pas dans la file actuelle (filtres, déjà traité ou rattaché à un groupe).")
    lvl, lvl_key = P.confidence_level(cs["confidence"])
    c = st.columns(4)
    c[0].markdown(kpi("Verdict moteur", P.VERDICT_LABEL[v], v), unsafe_allow_html=True)
    prio_key = {"HAUTE": C.ANOMALIE, "MOYENNE": C.A_INVESTIGUER, "BASSE": C.CONFORME}.get(cs["priority"], "NEUTRE")
    c[1].markdown(kpi("Priorité", P.PRIORITY_LABEL.get(cs["priority"], "—"), prio_key), unsafe_allow_html=True)
    c[2].markdown(kpi("Confiance", f"{lvl} — {round(cs['confidence'] * 100)} %", lvl_key), unsafe_allow_html=True)
    c[3].markdown(kpi("Revue humaine", P.review_label(cs), "REVU"), unsafe_allow_html=True)

    hist = decision_history(cs["case_id"], props)
    if cs["human_decision"]:
        human_resolution(cs, props, hist)
        st.markdown("### Analyse originale")
        st.caption("Le verdict moteur ne change pas : il documente l'écart détecté dans les données officielles. "
                   "La résolution humaine ci-dessus indique ce qui en a été fait.")
    elif len(hist):
        with st.expander("Historique des décisions"):
            for line in history_lines(hist, cs):
                st.markdown(f"- {line}")

    if cs["case_type"] in P.MISSING_TYPES:
        missing_assignment_section(cs)
    elif cs["case_type"] == "COMPARAISON_CHAMP":
        why_section(cs)
    else:
        st.markdown("### Pourquoi ce cas est signalé")
        st.write(P.rule_plain(cs))
        note(esc(cs["explanation"]), v)
    evidence_expander(cs)
    card = P.ai_card(cs, cases, patterns)
    if card:
        ai_section(card)
    confidence_section(cs)
    action_section(cs, props)
    if v in (C.ANOMALIE, C.A_INVESTIGUER):
        if cs["human_decision"]:
            with st.expander("✏️ Modifier la décision", expanded=ss.edit_case == cs["case_id"]):
                st.caption("La nouvelle décision remplacera l'actuelle ; l'historique complet est conservé.")
                decision_panel(cs, props)
        elif cs["systemic"]:
            note("🧩 Ce cas fait partie d'un problème systémique : la décision se prend une seule fois pour tout le "
                 "groupe.", "ACTION")
            st.button("Aller au problème systémique", on_click=goto, args=("systemic",), key=f"gs_{cs['case_id']}")
            with st.expander("Décider quand même pour ce cas seulement"):
                decision_panel(cs, props)
        else:
            decision_panel(cs, props)
    with st.expander("🔧 Voir les détails techniques / audit"):
        st.caption("Enregistrement complet du cas tel que produit par le moteur (valeurs internes).")
        st.dataframe(pd.DataFrame({"attribut": cs.index, "valeur": [str(x) for x in cs.values]}), hide_index=True,
                     width="stretch", height=400)


def result_panel(cs, panel):
    body = "<br>".join(esc(x) for x in panel["lines"])
    icon = "⏳" if panel["decision"] == C.DECISION_REVIEW else "↩️" if panel["decision"] == C.DECISION_REOPEN else "✅"
    st.markdown(f"<div class='cb-note' style='--c:{P.COLORS[C.CONFORME]};--t:{P.TINTS[C.CONFORME]}'>"
                f"<b>{icon} Décision enregistrée</b><br>{body}</div>", unsafe_allow_html=True)
    b = st.columns([1.4, 1.4, 1, 4])
    if panel.get("action_id") and panel["decision"] not in (C.DECISION_REVIEW, C.DECISION_REOPEN):
        b[0].button("Voir l'action acceptée" if panel["decision"] != C.DECISION_ACCEPT else "Voir l'action",
                    on_click=goto, args=("actions", None, panel["action_id"]), key="rp_act", width="stretch")
    if panel["decision"] != C.DECISION_REOPEN:
        b[1].button("Modifier cette décision", key="rp_edit", width="stretch",
                    on_click=lambda: ss.update(edit_case=cs["case_id"]))
    b[2].button("Fermer", key="rp_close", on_click=lambda: ss.update(result_panel=None), width="stretch")


def human_resolution(cs, props, hist):
    d = cs["human_decision"]
    src = str(cs.get("human_decision_source", ""))
    st.markdown("### Résolution humaine")
    head = ("⏳ En attente d'informations" if d == C.DECISION_REVIEW else
            "🧩 Cas traité via une décision de groupe" if src.startswith("groupe") else "✅ Cas traité")
    status, before, after = "", None, None
    if len(props):
        first = props.iloc[0]
        status = P.PROPOSAL_STATUS_LABEL.get(first["status"], first["status"])
        if first["action_type"] == "CORRIGER_CHAMP":
            before = P.fmt(first["current_value"])
            after = {"ACCEPTE": P.fmt(first["proposed_value"]), "MODIFIE": P.fmt(first["human_value"])}.get(
                first["status"], "— (aucune correction)")
        else:
            before = "aucune ligne"
            after = {"ACCEPTE": "ligne candidate ajoutée", "MODIFIE": "ligne candidate (valeurs modifiées)"}.get(
                first["status"], "— (aucune ligne créée)")
    accepted = status in ("Acceptée", "Modifiée")
    c = st.columns(3)
    c[0].markdown(kpi("Verdict moteur (inchangé)", P.VERDICT_LABEL[cs["verdict"]], cs["verdict"]),
                  unsafe_allow_html=True)
    c[1].markdown(kpi("Décision de l'expert", P.decision_label(cs, d), "REVU",
                      ts(cs["human_decided_at"]) + (" · décision de groupe" if src.startswith("groupe") else "")),
                  unsafe_allow_html=True)
    c[2].markdown(kpi("Correction", (f"{status}" if status else "aucune proposée"),
                      C.CONFORME if accepted else "REVU"), unsafe_allow_html=True)
    if before is not None:
        b1, b2 = st.columns(2)
        b1.markdown(value_box("Valeur actuelle dans le fichier officiel", before), unsafe_allow_html=True)
        b2.markdown(value_box("Valeur validée pour correction", after, C.CONFORME if accepted else None),
                    unsafe_allow_html=True)
    if accepted:
        impact = "La correction a été ajoutée au plan de corrections. Le fichier officiel n'a pas été modifié."
    elif d == C.DECISION_REVIEW:
        impact = "Le cas reste dans la file d'investigation en attendant des informations."
    else:
        impact = "Aucune correction ne sera appliquée. La justification est conservée dans le journal d'audit."
    note(f"<b>{head}</b> — {esc(impact)}" + (f"<br>Commentaire : {esc(cs['human_comment'])}"
                                             if cs["human_comment"] else ""), "REVU")
    lines = history_lines(hist, cs)
    with st.expander(f"Historique des décisions ({len(lines)})"):
        for line in lines:
            st.markdown(f"- {line}")
    b = st.columns([1.3, 1.3, 4])
    b[0].button("✏️ Modifier la décision", key=f"edit_{cs['case_id']}", width="stretch",
                on_click=lambda: ss.update(edit_case=cs["case_id"]))
    if not src.startswith("groupe"):
        if b[1].button("↩️ Réouvrir le cas", key=f"reopen_{cs['case_id']}", width="stretch"):
            save_case_decision(cs, C.DECISION_REOPEN, "", props)


def why_section(cs):
    v = cs["verdict"]
    st.markdown("### " + ("Pourquoi ce cas est signalé" if v in (C.ANOMALIE, C.A_INVESTIGUER)
                          else "Pourquoi ce cas est " + ("conforme" if v == C.CONFORME else "justifié")))
    st.markdown("**Ce que dit la règle**")
    st.write(P.rule_plain(cs))
    st.markdown("**Ce qu'on observe**")
    tag = {C.CONFORME: badge(C.CONFORME, "✓ Conforme"), C.ECART_JUSTIFIE: badge(C.ECART_JUSTIFIE, "✓ Justifié"),
           C.ANOMALIE: badge(C.ANOMALIE, "✗ Non conforme"), C.A_INVESTIGUER: badge(C.A_INVESTIGUER, "? À confirmer")}[v]
    boxes = [value_box("Système RH", P.fmt(cs["source_value"]))]
    if cs["rule_id"] != "R_DIRECT":
        boxes.append(value_box("Valeur attendue selon la règle", P.fmt(cs["expected_value"]), C.ECART_JUSTIFIE))
    boxes.append(value_box("Système Temps", P.fmt(cs["destination_value"]), v, tag))
    for col, html_ in zip(st.columns(len(boxes)), boxes):
        col.markdown(html_, unsafe_allow_html=True)
    st.caption("Légende : bleu = valeur attendue calculée par la règle ; la couleur de la valeur Temps et l'étiquette "
               "indiquent son statut.")
    if cs["rule_id"] == "R_ASSIGNMENT_START":
        ctx = P.parse_json(cs["context"])
        st.caption(f"Historique du détail du poste : {ctx.get('historique', '')} · "
                   f"enregistrement courant : {ctx.get('methode_detail_poste', '')}")
    st.markdown("**Conclusion**")
    note(f"<b>{esc(P.conclusion(cs))}</b>", v)


def missing_assignment_section(cs):
    st.markdown("### Affectations dans le système RH")
    src = res.dataset.source
    rows = src[src["Matricule"].map(display) == cs["person_id"]]
    m = res.matches.set_index("ligne_source")
    state = {"APPARIE": "✅ Retrouvée", "SOURCE_SEULEMENT": "⚠ Non retrouvée", "AMBIGU": "🟠 Ambiguë"}
    table = pd.DataFrame({
        "Type": rows["TypeAffectation"].map(lambda t: P.ASSIGNMENT_TYPE_LABEL.get(str(t), t)),
        "Début": rows["DateEntréePoste"].map(lambda d: P.fmt(display(d))),
        "Poste": rows["CodePoste"].map(display), "Code emploi": rows["CodeEmploi"].map(display),
        "Correspondance Temps": rows["_excel_row"].map(lambda r: state.get(m.loc[r, "statut_appariement"], "?")
                                                       if r in m.index else "?")})
    st.dataframe(table, hide_index=True, width="stretch")
    n_ok = int((table["Correspondance Temps"] == "✅ Retrouvée").sum())
    t = P.ASSIGNMENT_TYPE_ADJ.get(cs["src_type"], "")
    st.markdown("### Pourquoi c'est ambigu")
    note(f"{len(rows)} affectations distinctes existent dans le système RH, mais seulement {n_ok} correspondance(s) "
         f"claire(s) dans le système Temps. Les données disponibles ne permettent pas de déterminer si l'affectation "
         f"{t} devait être transférée ou si son absence est légitime.", C.A_INVESTIGUER)


def evidence_expander(cs):
    with st.expander("📄 Voir la règle complète et les sources"):
        a, b = st.columns(2)
        a.markdown(f"**Règle :** `{cs['rule_id']}` — {cs['rule_name']}  \n**Mapping :** {cs['mapping_ref']}  \n"
                   f"**Méthode :** {P.METHOD_LABEL.get(cs['rule_type'], cs['rule_type'])} (`{cs['rule_type']}`)  \n"
                   f"**Comparaison brute :** `{cs['stage1_result']}` · **Issue de la règle :** `{cs['rule_outcome']}`"
                   f" · **Certitude :** {P.CERTAINTY_LABEL.get(cs['certainty'], cs['certainty'])}")
        if cs["mapping_rule_text"]:
            a.markdown("**Texte de la règle (Mapping.xlsx)**")
            a.code(cs["mapping_rule_text"], language=None)
        b.markdown(f"**Explication du moteur :** {cs['explanation']}")
        b.markdown(f"**Valeurs normalisées :** RH `{cs['source_normalized'] or '∅'}` · Temps "
                   f"`{cs['destination_normalized'] or '∅'}`")
        b.markdown(f"**Sources :** {cs['evidence']}")
        ctx = {f"entrée · {k}": v for k, v in P.parse_json(cs["inputs"]).items()}
        ctx.update({f"contexte · {k}": v for k, v in P.parse_json(cs["context"]).items()})
        ctx["appariement"] = f"{cs['match_status']} — {cs['match_note']}"
        st.dataframe(pd.DataFrame({"élément": list(ctx), "valeur": [str(x) for x in ctx.values()]}), hide_index=True,
                     width="stretch")
        s1, s2 = st.columns(2)
        src, dst = res.dataset.source, res.dataset.destination
        if pd.notna(cs["src_row"]):
            s1.markdown(f"**Ligne RH {cs['src_row']}** ({Path(res.dataset.files['source']).name})")
            s1.dataframe(vertical(src[src["_excel_row"] == cs["src_row"]].iloc[0],
                                  set(str(cs["source_fields"]).split(", "))), hide_index=True, width="stretch",
                         height=260)
        if pd.notna(cs["dst_row"]):
            s2.markdown(f"**Ligne Temps {cs['dst_row']}** ({Path(res.dataset.files['destination']).name})")
            s2.dataframe(vertical(dst[dst["_excel_row"] == cs["dst_row"]].iloc[0], {cs["field"]}), hide_index=True,
                         width="stretch", height=260)


def ai_section(card):
    st.markdown("### 🤖 Analyse proposée par l'agent IA local")
    obs = "".join(f"<li>{esc(o)}</li>" for o in card["observed"])
    st.markdown(f"<div class='cb-ai'><i>Cette analyse aide l'investigation mais ne remplace pas le verdict "
                f"déterministe.</i><br><br><b>Hypothèse</b><br>{esc(card['hypothesis'])}<br><br><b>Éléments "
                f"observés</b><ul>{obs}</ul><b>Cause possible</b><br>{esc(card['cause'])}<br><br><b>Action "
                f"suggérée</b><br>{esc(card['action'])}</div>", unsafe_allow_html=True)
    with st.expander("🔧 Voir détails techniques de l'analyse IA"):
        st.json(card["technical"])


def confidence_section(cs):
    factors, concl = P.confidence_factors(cs)
    with st.expander(f"Pourquoi ce niveau de confiance ? ({P.confidence_text(cs['confidence'])})"):
        for sign, text in factors:
            st.markdown(f"{'🟢 +' if sign == '+' else '🔴 −'} {text}")
        if concl:
            st.markdown(f"**→ {concl}**")
        st.caption(f"Calcul détaillé : {cs['confidence_detail']}")
        st.caption(f"Priorité : {cs['priority_detail'] or '—'}")


def action_section(cs, props):
    st.markdown("### Action proposée")
    if not len(props):
        if cs["verdict"] in (C.CONFORME, C.ECART_JUSTIFIE):
            st.caption("Aucune action requise.")
        else:
            note(esc(cs["ai_suggested_action"] or cs["proposed_action"]), C.A_INVESTIGUER)
        return
    first = props.iloc[0]
    with st.container(border=True):
        status = P.PROPOSAL_STATUS_LABEL.get(first["status"], first["status"])
        if first["action_type"] == "CORRIGER_CHAMP":
            st.markdown(f"**Corriger « {P.field_label(first['field'])} »** &nbsp; `{P.fmt(first['current_value'])}` → "
                        f"`{P.fmt(first['human_value'] or first['proposed_value'])}`")
            origin = "Copie directe du système RH" if cs["rule_type"] == C.RAW else "Règle métier documentée"
            st.caption(f"Origine : {origin} · Certitude : {P.CERTAINTY_ICON[first['certainty']]} "
                       f"{P.CERTAINTY_LABEL[first['certainty']]} — {P.CERTAINTY_HELP[first['certainty']]} · "
                       f"Statut : {status}")
        else:
            t = P.ASSIGNMENT_TYPE_ADJ.get(cs["src_type"], "")
            st.markdown(f"**Créer une affectation {t} candidate** — statut : {status}")
            st.caption("Préparée à partir du mapping ; une validation fonctionnelle est requise avant toute correction.")
            key = props[~props["derivation"].str.startswith("champ non couvert")]
            st.dataframe(certainty_table(key), hide_index=True, width="stretch", height=min(420, 38 + 35 * len(key)))
            st.caption(" · ".join(f"{P.CERTAINTY_ICON[k]} {P.CERTAINTY_LABEL[k]} : {P.CERTAINTY_HELP[k]}"
                                  for k in P.CERTAINTY_LABEL))
            with st.expander("Autres champs du système Temps (non couverts par le mapping)"):
                st.dataframe(certainty_table(props[props["derivation"].str.startswith("champ non couvert")]),
                             hide_index=True, width="stretch")
        if not cs["human_decision"]:
            st.caption("⚠ Validation humaine requise.")


def certainty_table(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=["Champ Temps", "Valeur proposée", "Certitude", "Justification"])
    return pd.DataFrame({"Champ Temps": rows["field"].map(lambda f: f"{P.field_label(f)} ({f})" if f in P.FIELD_LABEL
                                                          else f),
                         "Valeur proposée": rows.apply(lambda r: P.fmt(r["human_value"] or r["proposed_value"]), axis=1),
                         "Certitude": rows["certainty"].map(lambda c: f"{P.CERTAINTY_ICON[c]} {P.CERTAINTY_LABEL[c]}"),
                         "Justification": rows["derivation"]})


def decision_panel(cs, props):
    st.markdown("### Décision de l'expert")
    st.write("Comment souhaitez-vous traiter ce cas ? Chaque option indique précisément son effet.")
    cid = cs["case_id"]
    comment = st.text_input("Commentaire / justification (conservé dans le journal d'audit)",
                            key=f"cm_{cid}_{ss.table_version}")
    mods = {}
    if len(props):
        key = props[~props["derivation"].str.startswith("champ non couvert")] \
            if props.iloc[0]["action_type"] == "CREER_AFFECTATION" else props
        with st.expander("Ajuster les valeurs proposées (pour « Modifier »)"):
            ed = st.data_editor(pd.DataFrame({"Champ": key["field"].values,
                                              "Valeur proposée": key["proposed_value"].map(P.fmt).values,
                                              "Votre valeur": [""] * len(key)}),
                                disabled=["Champ", "Valeur proposée"], hide_index=True, width="stretch",
                                key=f"ed_{cid}_{ss.table_version}")
            mods = {r["Champ"]: r["Votre valeur"] for _, r in ed.iterrows() if str(r["Votre valeur"] or "").strip()}
    options = P.decision_options(cs, props)
    clicked_code = None
    for col, (code, label, expl) in zip(st.columns(len(options)), options):
        with col.container(border=True):
            if st.button(label, key=f"dec_{code}_{cid}_{ss.table_version}", width="stretch",
                         type="primary" if code == C.DECISION_CONFIRM else "secondary"):
                clicked_code = code
            st.caption(expl)
    if clicked_code:
        if clicked_code == C.DECISION_MODIFY and not mods:
            st.warning("Renseignez au moins une « Votre valeur » dans « Ajuster les valeurs proposées ».")
        else:
            save_case_decision(cs, clicked_code, comment, props, mods if clicked_code == C.DECISION_MODIFY else None)


# ============================================================================ systemic
def page_systemic():
    header("Problèmes systémiques")
    st.markdown("### Problèmes systémiques nécessitant une décision")
    st.caption("Groupes pour lesquels une seule décision humaine traite de nombreux cas. Chaque cas reste consultable "
               "individuellement pour l'audit.")
    groups = patterns[patterns["requires_decision"].astype(bool)] if not patterns.empty else patterns
    if groups.empty:
        st.info("Aucun problème systémique détecté.")
    for _, p in groups.sort_values("priority_score", ascending=False).iterrows():
        systemic_card(p)
    others = patterns[~patterns["requires_decision"].astype(bool)] if not patterns.empty else patterns
    if len(others):
        st.markdown("### Indices complémentaires détectés pendant l'analyse")
        st.caption("Ces indices n'entraînent pas de décision de groupe. Ils aident l'analyste à comprendre la cause "
                   "possible de certains cas individuels, qui se décident un par un dans Investigation.")
        for _, p in others.iterrows():
            members = cases[cases["case_id"].isin(str(p["case_ids"]).split(";"))]
            info = P.clue_info(p, members)
            with st.expander(f"🔎 {info['title']}"):
                st.markdown(f"**Observation :** {info['observation']}  \n**Pourquoi c'est utile :** {info['why']}  \n"
                            f"**Investigation suggérée :** {info['suggestion']}")
                st.markdown(f"**Cas concernés** — "
                            f"{P.rows_employees(len(members), members['person_id'].nunique(), 'cas')}")
                member_table(members)


def systemic_card(p):
    members = cases[cases["case_id"].isin(str(p["case_ids"]).split(";"))]
    n, n_emp = len(members), members["person_id"].nunique()
    clar = C.OFFICIAL_CLARIFICATIONS.get(str(p["field"]), {})
    anonym = bool(members["hypothesis_code"].eq("ANONYMISATION").any())
    bp = p.get("business_priority")
    bp = bp if isinstance(bp, str) else ""
    n_anom = int((members["verdict"] == C.ANOMALIE).sum())
    done = P.is_treated(p.get("human_decision", ""))
    mprops = proposals[proposals["case_id"].isin(members["case_id"])]
    n_corr = mprops["case_id"].nunique()
    n_acc = mprops.loc[mprops["status"].isin(["ACCEPTE", "MODIFIE"]), "case_id"].nunique()
    icon = "✅" if done else ("🟡" if bp == "BASSE" else "🔴" if n_anom else "🟠")
    share = p.get("share")
    total = round(n / float(share)) if share and not pd.isna(share) and float(share) > 0 else n
    with st.container(border=True):
        st.markdown(f"### {icon} {group_title(p)}")
        c = st.columns(3)
        c[0].markdown(kpi("Dossiers concernés", n, C.ANOMALIE if n_anom else C.A_INVESTIGUER,
                          P.rows_employees(n, n_emp)), unsafe_allow_html=True)
        c[1].markdown(kpi("Priorité" + (" métier" if bp else ""), P.PRIORITY_LABEL.get(p["priority"], ""),
                          {"HAUTE": C.ANOMALIE, "MOYENNE": C.A_INVESTIGUER}.get(p["priority"], C.CONFORME)),
                      unsafe_allow_html=True)
        c[2].markdown(kpi("Impact", "1 décision", "ACTION", f"au lieu de {n} dossiers individuels"),
                      unsafe_allow_html=True)
        if done:
            dec = P.DECISION_SHORT.get(p["human_decision"], p["human_decision"])
            st.markdown(f"<div class='cb-rev'><b>✅ Groupe traité</b> — {n_anom} anomalies couvertes<br>Décision : "
                        f"<b>{esc(dec)}</b> · {esc(ts(p.get('human_decided_at', '')))}"
                        + (f"<br>Commentaire : {esc(p['human_comment'])}" if p.get("human_comment") else "")
                        + f"<br>Plan de corrections : <b>{n_acc}/{n_corr} corrections acceptées</b> · les {n} cas "
                          "affichent « Résolu via décision de groupe » ; leurs verdicts moteur restent dans l'audit."
                          "</div>", unsafe_allow_html=True)
            b = st.columns([1.5, 1.5, 4])
            b[0].button(f"Voir les {n_corr} corrections", key=f"gv_{p['pattern_id']}", width="stretch",
                        on_click=goto, args=("actions", None, f"G::{p['pattern_id']}"))
            if b[1].button("↩️ Réouvrir le groupe", key=f"gr_{p['pattern_id']}", width="stretch"):
                save_group_decision(p, C.DECISION_REOPEN, "")
        if anonym:
            obs = ("Le préfixe destination (ex. dev-08-v2_) est autorisé, mais l'identifiant restant ne correspond pas "
                   "au Matricule/personID.")
        elif p["pattern_type"] == "SYSTEMIQUE_SEGMENT":
            obs = str(p["description"])
        else:
            obs = "La valeur générée ne respecte pas la transformation définie dans le mapping."
        bullets = [f"**Observation :** {obs}",
                   f"**Répétition :** {n}/{total} lignes affectées de la même façon ({P.rows_employees(n, n_emp)})."]
        if clar.get("known_cause"):
            bullets.append(f"**Cause connue :** {clar['known_cause']}.")
        else:
            bullets.append(f"**Hypothèse :** erreur dans une transformation globale plutôt que {n} erreurs "
                           f"individuelles ({p['hypothesis']}).")
        if clar:
            bullets.append(f"**Clarification officielle :** {clar['statement']} _({P.CLARIFICATION_SOURCE})_")
        bullets.append(f"**Impact :** {n_anom} anomalies techniques auditables — 1 seule décision humaine.")
        bullets.append(f"**Action suggérée :** {p['suggested_action']}")
        st.markdown("\n".join(f"- {b}" for b in bullets))
        with st.expander(f"Voir les {n} cas"):
            member_table(members)
        with st.expander("✏️ Modifier la décision de groupe" if done else "Prendre une décision pour ce groupe",
                         expanded=not done):
            comment = st.text_input("Commentaire / justification", key=f"gc_{p['pattern_id']}_{ss.table_version}")
            opts = P.group_decision_options(p, n_corr)
            for col, (code, label, expl) in zip(st.columns(len(opts)), opts):
                with col.container(border=True):
                    if st.button(label, key=f"gd_{code}_{p['pattern_id']}_{ss.table_version}", width="stretch",
                                 type="primary" if code == C.DECISION_CONFIRM else "secondary"):
                        save_group_decision(p, code, comment)
                    st.caption(expl)
            hist = store.history([p["pattern_id"]], res.dataset_key)
            if len(hist):
                st.caption("Historique : " + " · ".join(history_lines(hist)))


def member_table(members: pd.DataFrame):
    st.dataframe(pd.DataFrame({
        "Employé": members["person_id"], "Problème": members.apply(P.problem_title, axis=1),
        "Valeur Temps": members["destination_value"].map(P.fmt),
        "Valeur attendue": members["expected_value"].map(P.fmt),
        "Verdict moteur": members["verdict"].map(P.verdict_text),
        "Revue humaine": members.apply(P.review_label, axis=1)}), hide_index=True, width="stretch")


# ============================================================================ actions
def action_rows(a) -> pd.DataFrame:
    if a["kind"] == "CORRIGER_GROUPE":
        p = patterns[patterns["pattern_id"] == a["pattern_id"]].iloc[0]
        return proposals[proposals["case_id"].isin(str(p["case_ids"]).split(";"))]
    if a["kind"] == "VERIFICATION":
        return proposals.iloc[0:0]
    return proposals[proposals["proposal_id"] == a["action_id"]]


def action_history(a, rows) -> pd.DataFrame:
    targets = [f"{p}::{f}" for p, f in zip(rows["proposal_id"], rows["field"])]
    if a["pattern_id"]:
        targets.append(a["pattern_id"])
    targets += list(rows["case_id"].unique()) + ([a["case_id"]] if a["case_id"] else [])
    return store.history(targets, res.dataset_key)


def page_actions():
    header("Actions correctives")
    st.caption("Propositions dérivées du mapping. Rien n'est appliqué aux fichiers officiels : les actions acceptées "
               "forment un plan de corrections exportable séparément.")
    if ss.action_result:
        st.markdown(f"<div class='cb-note' style='--c:{P.COLORS[C.CONFORME]};--t:{P.TINTS[C.CONFORME]}'>"
                    f"<b>✅ {esc(ss.action_result)}</b></div>", unsafe_allow_html=True)
        st.button("Fermer", key="ar_close", on_click=lambda: ss.update(action_result=None))
    grouped = st.toggle("Regrouper les corrections des problèmes systémiques", value=True)
    acts = P.build_actions(cases, proposals, patterns, group_systemic=grouped)
    for col, (code, key) in zip(st.columns(4), [("PROPOSE", C.A_INVESTIGUER), ("ACCEPTE", C.CONFORME),
                                                ("MODIFIE", C.ECART_JUSTIFIE), ("REJETE", "REVU")]):
        col.markdown(kpi(P.PROPOSAL_STATUS_LABEL[code], int((acts["status"] == code).sum()), key),
                     unsafe_allow_html=True)
    preview_section()
    with st.container(border=True):
        f = st.columns([2.2, 2, 2, 1.2])
        vue = f[0].radio("Afficher", ["Toutes", "À valider", "Traitées (historique)"], horizontal=True)
        kinds = f[1].multiselect("Type", ["Corriger une valeur", "Créer une affectation", "Vérification manuelle"])
        certs = f[2].multiselect("Certitude", list(P.CERTAINTY_LABEL.values()))
        emp = f[3].text_input("Employé", key="act_emp")
    view = acts
    if vue == "À valider":
        view = view[view["status"] == "PROPOSE"]
    elif vue.startswith("Traitées"):
        view = view[view["status"] != "PROPOSE"]
    if kinds:
        kmap = {"Corriger une valeur": ["CORRIGER_CHAMP", "CORRIGER_GROUPE"],
                "Créer une affectation": ["CREER_AFFECTATION"], "Vérification manuelle": ["VERIFICATION"]}
        view = view[view["kind"].isin(sum((kmap[x] for x in kinds), []))]
    if certs:
        inv_c = {v: k for k, v in P.CERTAINTY_LABEL.items()}
        view = view[view["certainty"].isin([inv_c[c] for c in certs])]
    if emp:
        view = view[view["person_id"].astype(str).str.contains(emp.strip(), regex=False)]
    if ss.selected_action in set(view["action_id"]):
        view = pd.concat([view[view["action_id"] == ss.selected_action], view[view["action_id"] != ss.selected_action]])
    view = view.reset_index(drop=True)
    if view.empty:
        st.info("Aucune action pour ces filtres.")
        return
    is_sel = (view["action_id"] == ss.selected_action).tolist()
    icon = {"PROPOSE": "⏳", "ACCEPTE": "✅", "MODIFIE": "✏️", "REJETE": "❌"}
    table = pd.DataFrame({
        " ": ["▶" if x else "" for x in is_sel],
        "Statut": view["status"].map(lambda s: f"{icon[s]} {P.PROPOSAL_STATUS_LABEL[s]}"),
        "Type": view["kind"].map(P.ACTION_KIND_LABEL), "Employé": view["person_id"], "Objet": view["objet"],
        "Actuel": view["current"], "Proposé": view["proposed"],
        "Certitude": view["certainty"].map(lambda c: f"{P.CERTAINTY_ICON[c]} {P.CERTAINTY_LABEL[c]}")})
    ev = st.dataframe(styled(table, (view["status"] != "PROPOSE").tolist(), is_sel), hide_index=True,
                      width="stretch", height=min(330, 38 + 35 * len(table)), on_select="rerun",
                      selection_mode="single-row", lazy=False, key=f"acts_{ss.table_version}_{ss.selected_action}")
    if ev and ev.selection.rows:
        new = view.loc[ev.selection.rows[0], "action_id"]
        if new != ss.selected_action:
            ss.selected_action = new
            st.rerun()
    if ss.selected_action not in set(view["action_id"]):
        ss.selected_action = view["action_id"].iloc[0]
    action_detail(view[view["action_id"] == ss.selected_action].iloc[0])


def preview_section():
    dest, changes = corrected_destination(res)
    with st.container(border=True):
        st.markdown("### Aperçu après corrections")
        st.caption("Simulation uniquement — aucun fichier officiel n'est modifié.")
        mod = changes[changes["type"] == "MODIFICATION"]
        cre = changes[changes["type"] == "CREATION"]
        c = st.columns(4)
        c[0].markdown(kpi("Corrections acceptées", len(mod) + len(cre), C.CONFORME), unsafe_allow_html=True)
        c[1].markdown(kpi("Valeurs qui changeraient", len(mod), C.ECART_JUSTIFIE), unsafe_allow_html=True)
        c[2].markdown(kpi("Lignes qui seraient créées", len(cre), C.ECART_JUSTIFIE), unsafe_allow_html=True)
        c[3].markdown(kpi("Décisions en attente", work["remaining"], "ACTION"), unsafe_allow_html=True)
        b = st.columns([1.4, 1.8, 4])
        b[0].button("Masquer l'aperçu" if ss.show_preview else "Voir l'aperçu corrigé", key="pv_toggle",
                    on_click=lambda: ss.update(show_preview=not ss.show_preview), width="stretch")
        if b[1].button("Préparer une copie corrigée candidate", key="pv_export", width="stretch",
                       disabled=changes.empty):
            out = C.OUTPUT_DIR if is_official else C.OUTPUT_DIR / Path(data_dir).name
            path = export_corrected_destination(res, out / "destination_corrigee_candidate.xlsx")
            ss.preview_xlsx = path.read_bytes()
            ss.action_result = f"Copie corrigée candidate écrite dans {path} (fichier séparé ; data/ inchangé)."
            st.rerun()
        if ss.preview_xlsx:
            b[2].download_button("⬇️ Télécharger la copie corrigée candidate", ss.preview_xlsx,
                                 "destination_corrigee_candidate.xlsx",
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if ss.show_preview:
            if changes.empty:
                st.info("Aucune correction acceptée pour l'instant : l'aperçu est identique au fichier officiel.")
            else:
                st.dataframe(pd.DataFrame({
                    "Type": changes["type"].map({"MODIFICATION": "Valeur modifiée",
                                                 "CREATION": "Nouvelle ligne candidate"}),
                    "Employé": changes["person_id"], "Ligne Temps": changes["ligne_destination"],
                    "Champ": changes["champ"].map(lambda f: P.field_label(f) if f in P.FIELD_LABEL else f),
                    "Avant (fichier officiel)": changes["avant"].map(P.fmt),
                    "Correction acceptée": changes["apres"].map(P.fmt),
                    "Provenance": changes["provenance"]}), hide_index=True, width="stretch")
                with st.expander("Voir la destination corrigée complète (simulation)"):
                    st.dataframe(dest.fillna("").astype(str), hide_index=True, width="stretch", height=300)


def action_detail(a):
    rows = action_rows(a)
    treated = a["status"] != "PROPOSE"
    icon = {"PROPOSE": "⏳ À valider", "ACCEPTE": "✅ Acceptée", "MODIFIE": "✏️ Modifiée", "REJETE": "❌ Rejetée"}
    with st.container(border=True):
        st.markdown(f"#### {icon[a['status']]} — {P.ACTION_KIND_LABEL[a['kind']]} · {a['objet']} · {a['person_id']}")
        final = a["proposed"]
        if a["kind"] == "CORRIGER_CHAMP" and len(rows):
            r = rows.iloc[0]
            final = P.fmt(r["human_value"] if r["status"] == "MODIFIE" and r["human_value"] else r["proposed_value"])
        validated = a["status"] in ("ACCEPTE", "MODIFIE")
        c = st.columns(3)
        c[0].markdown(value_box("Fichier officiel (actuel)", a["current"]), unsafe_allow_html=True)
        c[1].markdown(value_box("Correction validée" if validated else "Valeur proposée", final,
                                C.CONFORME if validated else C.ECART_JUSTIFIE), unsafe_allow_html=True)
        c[2].markdown(value_box("Certitude", P.CERTAINTY_LABEL[a["certainty"]]), unsafe_allow_html=True)
        st.caption(P.CERTAINTY_HELP[a["certainty"]])
        st.markdown(f"**Raison :** {a['reason']}")
        hist = action_history(a, rows)
        if len(hist):
            st.caption(f"Dernière décision : {ts(hist.iloc[-1]['timestamp'])}")
            with st.expander("Historique des décisions"):
                for line in history_lines(hist):
                    st.markdown(f"- {line}")
        with st.expander("Voir la preuve"):
            st.write(a["evidence"])
            if a["case_id"]:
                st.button("Ouvrir le cas dans Investigation", key=f"oc_{a['action_id']}", on_click=goto,
                          args=("investigation", a["case_id"]))
        if a["kind"] == "VERIFICATION":
            st.button("Traiter ce cas dans Investigation", on_click=goto, args=("investigation", a["case_id"]),
                      type="primary")
            return
        if treated:
            if st.button("↩️ Modifier / réouvrir", key=f"ro_{a['action_id']}", type="primary"):
                reopen_action(a, rows)
            return
        values = {}
        if a["kind"] == "CREER_AFFECTATION":
            key = rows[~rows["derivation"].str.startswith("champ non couvert")]
            with st.expander("Modifier des valeurs avant d'accepter"):
                ed = st.data_editor(pd.DataFrame({"Champ": key["field"].values,
                                                  "Valeur proposée": key["proposed_value"].map(P.fmt).values,
                                                  "Votre valeur": [""] * len(key)}),
                                    disabled=["Champ", "Valeur proposée"], hide_index=True, width="stretch",
                                    key=f"aed_{a['action_id']}_{ss.table_version}")
                values = {r["Champ"]: r["Votre valeur"] for _, r in ed.iterrows() if str(r["Votre valeur"]).strip()}
        elif a["kind"] == "CORRIGER_CHAMP":
            v = st.text_input("Nouvelle valeur (pour « Modifier »)", key=f"av_{a['action_id']}_{ss.table_version}")
            if v.strip():
                values = {rows.iloc[0]["field"]: v.strip()}
        comment = st.text_input("Commentaire (facultatif)", key=f"ac_{a['action_id']}_{ss.table_version}")
        st.caption("Accepter ou modifier ajoute la correction au plan de corrections et marque le cas comme traité. "
                   "Rejeter écarte la correction ; le cas reste à trancher dans Investigation.")
        b = st.columns(3)
        if b[0].button("✅ Accepter", type="primary", width="stretch", key=f"aa_{a['action_id']}"):
            decide_action(a, rows, "accept", comment)
        if b[1].button("✏️ Modifier", width="stretch", key=f"am_{a['action_id']}",
                       disabled=a["kind"] == "CORRIGER_GROUPE"):
            if values:
                decide_action(a, rows, "modify", comment, values)
            else:
                st.warning("Saisissez d'abord une nouvelle valeur.")
        if b[2].button("❌ Rejeter", width="stretch", key=f"ar_{a['action_id']}"):
            decide_action(a, rows, "reject", comment)


def decide_action(a, rows, how, comment, values=None):
    """Accept/modify = the underlying case (or group) decision; reject = proposal-level rejection."""
    if how == "reject":
        store.record_many([{"target_type": "proposal", "target_id": f"{r['proposal_id']}::{r['field']}",
                            "decision": "REJETE", "comment": comment} for _, r in rows.iterrows()],
                          reviewer="app", dataset_key=res.dataset_key)
        msg = "Correction rejetée. Le cas reste ouvert dans Investigation : indiquez s'il s'agit d'un écart légitime."
    elif a["kind"] == "CORRIGER_GROUPE":
        store.record("pattern", a["pattern_id"], C.DECISION_CONFIRM, comment, reviewer="app",
                     dataset_key=res.dataset_key)
        msg = f"Groupe confirmé : {rows['case_id'].nunique()} corrections ajoutées au plan de corrections."
    else:
        decision = C.DECISION_MODIFY if how == "modify" else C.DECISION_CONFIRM
        store.record("case", a["case_id"], decision, comment, values or None, reviewer="app",
                     dataset_key=res.dataset_key)
        msg = ("Correction modifiée et ajoutée" if how == "modify" else "Correction acceptée et ajoutée") + \
            " au plan de corrections ; le cas est marqué comme traité."
    ss.action_result = msg + " Le fichier officiel n'a pas été modifié."
    ss.toast = msg.split(".")[0]
    bump()
    st.rerun()


def reopen_action(a, rows):
    items = [{"target_type": "proposal", "target_id": f"{r['proposal_id']}::{r['field']}", "decision": "PROPOSE"}
             for _, r in rows.iterrows()]
    if a["kind"] == "CORRIGER_GROUPE":
        items.append({"target_type": "pattern", "target_id": a["pattern_id"], "decision": C.DECISION_REOPEN})
    elif a["case_id"] and cases.loc[cases["case_id"] == a["case_id"], "human_decision_source"].iloc[0] == "cas":
        items.append({"target_type": "case", "target_id": a["case_id"], "decision": C.DECISION_REOPEN})
    store.record_many(items, reviewer="app", dataset_key=res.dataset_key)
    ss.action_result = "Action réouverte : elle est de nouveau « À valider ». L'historique complet est conservé."
    ss.toast = "Action réouverte"
    bump()
    st.rerun()


# ============================================================================ reference pages
def page_mapping():
    header("Mapping des champs")
    st.caption("Comment chaque donnée du système RH est reliée au système Temps. Sert de référence pour reproduire "
               "chaque verdict. Seuls les champs listés ici sont corroborés.")
    entries = res.engine.entries
    q = st.text_input("Rechercher (champ source, champ destination ou mot-clé)")

    def match(e):
        blob = " ".join([e.description, *e.source_fields, *e.dest_fields, e.rule_text]).casefold()
        return q.casefold() in blob if q else True
    sel = [e for e in entries if match(e)]
    if not sel:
        st.info("Aucune entrée.")
        return
    labels = [f"{e.description} → {', '.join(e.dest_fields) or '(aucun champ cible)'}" for e in sel]
    i = st.selectbox("Entrée du mapping", range(len(sel)), format_func=lambda k: labels[k])
    e = sel[i]
    with st.container(border=True):
        st.markdown(f"### {e.description}")
        a, b = st.columns(2)
        a.markdown(f"**Champ(s) RH :** {', '.join(e.source_fields) or '—'}")
        if e.unresolved_sources:
            a.caption(f"Absent(s) de l'extraction : {', '.join(e.unresolved_sources)} (valeur dérivée par la règle)")
        a.markdown(f"**Champ(s) Temps :** {', '.join(e.dest_fields) or '— (non corroboré)'}")
        a.markdown(f"**Transformation :** {'copie directe' if e.is_direct else 'règle métier'}")
        a.caption(e.evidence)
        b.markdown("**Règle documentée**")
        b.code(e.rule_text or "N/A", language=None)
        for f in e.dest_fields:
            clar = C.OFFICIAL_CLARIFICATIONS.get(f)
            if clar:
                note(f"<b>Clarification officielle</b> ({esc(P.CLARIFICATION_SOURCE)}) : {esc(clar['statement'])}",
                     C.ECART_JUSTIFIE)
        ex = cases[cases["field"].isin(e.dest_fields)].head(5)
        if len(ex):
            st.markdown("**Exemples tirés des données**")
            st.dataframe(pd.DataFrame({"Employé": ex["person_id"], "Valeur RH": ex["source_value"].map(P.fmt),
                                       "Valeur Temps": ex["destination_value"].map(P.fmt),
                                       "Statut": ex["verdict"].map(P.verdict_text)}), hide_index=True, width="stretch")
    with st.expander("🔧 Voir le tableau complet"):
        st.dataframe(res.mapping, hide_index=True, width="stretch")


def page_rules():
    header("Règles & clarifications")
    st.caption("Points de la documentation officielle qui demandaient une interprétation, et ce que CorroborAI "
               "applique. Aucune règle n'est inventée.")
    amb = res.ambiguities.reset_index(drop=True)
    listing = pd.DataFrame({
        "Statut": amb.apply(lambda r: " ".join(P.ambiguity_status(r)), axis=1),
        "Règle": amb["ambiguity_id"].map(lambda a: P.AMBIGUITY_TITLES.get(a, (a, []))[0])})
    left, right = st.columns([2, 3])
    with left:
        ev = st.dataframe(listing, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                          lazy=False, key="rules_tbl", height=min(470, 38 + 35 * len(listing)))
        i = ev.selection.rows[0] if ev and ev.selection.rows else 0
    r = amb.iloc[i]
    title, fields = P.AMBIGUITY_TITLES.get(r["ambiguity_id"], (r["ambiguity_id"], []))
    icon, status = P.ambiguity_status(r)
    with right, st.container(border=True):
        st.markdown(f"### {icon} {title}")
        st.markdown("**Documentation initiale**")
        st.write(r["description"])
        for e in res.engine.entries:
            if set(e.dest_fields) & set(fields) and e.rule_text and e.rule_text != "N/A":
                st.code(e.rule_text, language=None)
                break
        if r.get("statistiques"):
            st.markdown("**Observation**")
            st.write(r["statistiques"])
        cf = P.AMBIGUITY_CLARIFIED_FIELD.get(r["ambiguity_id"])
        if cf and status != "Hypothèse documentée":
            clar = C.OFFICIAL_CLARIFICATIONS[cf]
            st.markdown("**Clarification**")
            bullets = "".join(f"<li>{esc(x.strip())}</li>" for x in clar["statement"].split(";") if x.strip())
            note(f"<b>{esc(P.CLARIFICATION_SOURCE)}</b><ul>{bullets}</ul>", C.CONFORME)
        st.markdown("**Interprétation utilisée**")
        st.write(r["interpretation_retenue"])
        sub = cases[cases["field"].isin(fields)]
        if len(sub):
            st.markdown("**Impact**")
            counts = sub["verdict"].value_counts()
            st.write(" · ".join(f"{P.verdict_text(k)} : {int(counts[k])}" for k in C.VERDICTS if k in counts))
        st.markdown(f"**Statut :** {icon} {'Clarifiée' if status != 'Hypothèse documentée' else 'Hypothèse documentée'}")


def page_matching():
    header("Appariement des affectations")
    st.caption("Cette étape détermine quelle affectation RH correspond à quelle affectation Temps. CorroborAI ne force "
               "jamais un appariement lorsqu'il existe plusieurs correspondances plausibles.")
    s = res.summary
    for col, (l, v, k) in zip(st.columns(6), [("Affectations RH", s["n_source_assignments"], "NEUTRE"),
                                              ("Affectations Temps", s["n_destination_rows"], "NEUTRE"),
                                              ("Appariées", s["n_matched"], C.CONFORME),
                                              ("Ambiguës", s["n_ambiguous_match"], C.A_INVESTIGUER),
                                              ("RH sans correspondance", s["n_source_only"], C.A_INVESTIGUER),
                                              ("Temps sans origine RH", s["n_dest_only"], C.A_INVESTIGUER)]):
        col.markdown(kpi(l, v, k), unsafe_allow_html=True)
    m = res.matches
    state = {"APPARIE": "✅ Appariée", "AMBIGU": "🟠 Ambiguë", "SOURCE_SEULEMENT": "⚠ RH sans correspondance",
             "DESTINATION_SEULEMENT": "⚠ Temps sans origine RH"}
    emps = ["Tous"] + sorted(m["person_id"].unique())
    default = emps.index("1545850") if "1545850" in emps else 0
    pick = st.selectbox("Employé", emps, index=default)
    v = m if pick == "Tous" else m[m["person_id"] == pick]
    st.dataframe(pd.DataFrame({
        "Employé": v["person_id"], "Type": v["type_affectation"].map(lambda t: P.ASSIGNMENT_TYPE_LABEL.get(t, t or "—")),
        "Poste": v["code_poste"], "Code emploi": v["code_emploi_source"], "Ligne RH": v["ligne_source"],
        "Ligne Temps": v["ligne_destination"], "État": v["statut_appariement"].map(state)}),
        hide_index=True, width="stretch")
    with st.expander("🔧 Détails techniques (scores et preuves)"):
        st.dataframe(v, hide_index=True, width="stretch")


def page_export():
    header("Export & audit")
    note("🔒 Les fichiers officiels ne sont jamais modifiés. Les décisions humaines sont conservées dans un journal "
         "séparé (outputs/human_decisions.csv), sans jamais effacer l'historique.", C.CONFORME)
    c1, c2 = st.columns(2)
    with c1:
        if ss.report_xlsx is None:
            if st.button("Préparer le rapport complet Excel", type="primary", width="stretch"):
                with st.spinner("Génération du rapport…"):
                    ss.report_xlsx = report_bytes(res)
                st.rerun()
        else:
            st.download_button("⬇️ Télécharger le rapport complet Excel", ss.report_xlsx, "corroboration_report.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary",
                               width="stretch")
            st.caption("Rapport préparé avec les décisions connues à ce moment ; régénérez-le après de nouvelles décisions.")
            st.button("Régénérer", on_click=lambda: ss.update(report_xlsx=None))
        anomalies = cases[cases["verdict"] == C.ANOMALIE]
        st.download_button("⬇️ Télécharger les anomalies (CSV)", anomalies.to_csv(index=False).encode("utf-8-sig"),
                           "anomalies.csv", "text/csv", width="stretch")
        st.download_button("⬇️ Télécharger les décisions humaines (CSV)",
                           store.load().to_csv(index=False).encode("utf-8-sig"), "human_decisions.csv", "text/csv",
                           width="stretch")
    with c2:
        acc = accepted_corrections(proposals)
        st.download_button("⬇️ Télécharger les corrections acceptées (CSV)", acc.to_csv(index=False).encode("utf-8-sig"),
                           "accepted_corrections.csv", "text/csv", width="stretch")
        st.download_button("⬇️ Télécharger le journal d'audit (CSV)",
                           audit_log(res).to_csv(index=False).encode("utf-8-sig"), "audit_log.csv", "text/csv",
                           width="stretch")
        _, changes = corrected_destination(res)
        buf = io.BytesIO()
        changes.to_csv(buf, index=False, encoding="utf-8-sig")
        st.download_button("⬇️ Télécharger l'aperçu des corrections (CSV)", buf.getvalue(),
                           "apercu_corrections.csv", "text/csv", width="stretch")
    if st.button("Écrire le rapport et les CSV dans outputs/"):
        out = C.OUTPUT_DIR if is_official else C.OUTPUT_DIR / Path(data_dir).name
        p = export_report(res, out / "corroboration_report.xlsx")
        export_csv(res, out)
        export_accepted(res, out)
        st.success(f"Écrit : {p} (+ CSV et corrections acceptées)")
    with st.expander("🔧 Empreintes des fichiers d'entrée (SHA-256)"):
        st.dataframe(pd.DataFrame([{"rôle": k, "fichier": Path(res.dataset.files[k]).name, "sha256": v}
                                   for k, v in res.dataset.fingerprints.items()]), hide_index=True, width="stretch")


PAGES = {"dashboard": page_dashboard, "investigation": page_investigation, "systemic": page_systemic,
         "actions": page_actions, "mapping": page_mapping, "rules": page_rules, "matching": page_matching,
         "export": page_export}
PAGES.get(ss.page, page_dashboard)()
