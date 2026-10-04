"""CorroborAI — corroboration Système A (RH) ↔ Système B (Temps).

Three-level pipeline: raw comparison / normalisation → deterministic business
rules from Mapping.xlsx → assisted analysis of what remains ambiguous.
"""
__version__ = "0.1.0"
__all__ = ["run_corroboration", "Corroborator", "CorroborationResult", "load_all"]


def __getattr__(name):  # lazy imports keep `python -m src.corroboration` clean
    if name in ("run_corroboration", "Corroborator", "CorroborationResult"):
        from . import corroboration
        return getattr(corroboration, name)
    if name == "load_all":
        from .load_data import load_all
        return load_all
    raise AttributeError(name)
