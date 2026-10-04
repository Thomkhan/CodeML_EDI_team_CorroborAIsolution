"""Level 1 helpers: value normalisation and raw comparison.

Raw values are never modified: every function returns a *new* normalised value
so that the original cell content stays available for audit.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

BLANK_TOKENS = {"", "nan", "nat", "none", "null"}
TRUE_TOKENS = {"true", "vrai", "oui", "o", "yes", "y", "1", "t"}
FALSE_TOKENS = {"false", "faux", "non", "n", "no", "0", "f"}
EXCEL_EPOCH = datetime(1899, 12, 30)

# Stage-1 outcomes
IDENTICAL = "IDENTIQUE"
IDENTICAL_NORMALIZED = "IDENTIQUE_APRES_NORMALISATION"
DIFFERENT = "DIFFERENT"
BOTH_EMPTY = "VIDE_DES_DEUX_COTES"
SOURCE_EMPTY = "SOURCE_VIDE"
DEST_EMPTY = "DESTINATION_VIDE"
NOT_COMPARABLE = "NON_COMPARABLE"


class Unparseable(str):
    """Marker for a value that could not be parsed into the requested kind."""


def is_blank(value: Any) -> bool:
    if value is None or value is pd.NaT:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return value.strip().lower() in BLANK_TOKENS
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def canon_key(text: Any) -> str:
    """Accent/case/punctuation-insensitive key used to match column names."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(str(text)).lower())


MOJIBAKE_MARKERS = ("Ã", "Â", "â€")


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as Latin-1/CP1252 ('complÃ¨te' -> 'complète')."""
    if not isinstance(text, str) or not any(m in text for m in MOJIBAKE_MARKERS):
        return text
    for enc in ("cp1252", "latin-1"):
        try:
            return text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def has_mojibake(value: Any) -> bool:
    return isinstance(value, str) and fix_mojibake(value) != value


def norm_text(value: Any, casefold: bool = True, accents: bool = False) -> str | None:
    if is_blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = re.sub(r"\s+", " ", fix_mojibake(str(value))).strip()
    if accents:
        s = strip_accents(s)
    return s.casefold() if casefold else s


def excel_serial_to_date(n: float) -> date:
    return (EXCEL_EPOCH + timedelta(days=float(n))).date()


def norm_date(value: Any, allow_serial: bool = True) -> date | Unparseable | None:
    if is_blank(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        if allow_serial and 1 <= float(value) <= 2958465:
            return excel_serial_to_date(value)
        return Unparseable(str(value))
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return Unparseable(s)
    if allow_serial and re.fullmatch(r"\d{4,6}(\.0+)?", s):
        return excel_serial_to_date(float(s))
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", s)
    if m:  # Québec/French convention: day first
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return Unparseable(s)
    return Unparseable(s)


def norm_bool(value: Any) -> bool | Unparseable | None:
    if is_blank(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        if float(value) in (0.0, 1.0):
            return bool(int(value))
        return Unparseable(str(value))
    s = str(value).strip().lower()
    if s in TRUE_TOKENS:
        return True
    if s in FALSE_TOKENS:
        return False
    return Unparseable(str(value))


def norm_number(value: Any) -> int | float | Unparseable | None:
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
    else:
        s = str(value).strip().replace(" ", "").replace(" ", "")
        if re.fullmatch(r"-?\d+,\d+", s):
            s = s.replace(",", ".")
        try:
            f = float(s)
        except ValueError:
            return Unparseable(str(value))
    if math.isnan(f):
        return None
    f = round(f, 6)
    return int(f) if f.is_integer() else f


def norm_id(value: Any) -> str | None:
    """Identifiers: '00397', 397, 397.0 and ' 397 ' are all the same identifier."""
    if is_blank(value):
        return None
    n = norm_number(value)
    if isinstance(n, int):
        return str(n)
    return norm_text(value, casefold=True)


def norm_email(value: Any) -> str | None:
    return norm_text(value, casefold=True, accents=True)


NORMALIZERS = {
    "text": lambda v: norm_text(v, casefold=True),
    "name": lambda v: norm_text(v, casefold=True),
    "email": norm_email,
    "id": norm_id,
    "number": norm_number,
    "date": norm_date,
    "bool": norm_bool,
}


def normalize(value: Any, kind: str):
    return NORMALIZERS.get(kind, NORMALIZERS["text"])(value)


def raw_repr(value: Any) -> str:
    """Text form of the raw cell value, used for the strict raw comparison."""
    if is_blank(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def display(value: Any) -> str:
    """Human-readable form of a raw or normalised value."""
    if value is None or is_blank(value):
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "true" if value else "false"
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def compare_values(src: Any, dst: Any, kind: str) -> dict:
    """Level 1: strict raw comparison, then comparison after normalisation."""
    s_blank, d_blank = is_blank(src), is_blank(dst)
    s_norm = normalize(src, kind)
    d_norm = normalize(dst, kind)
    if s_blank and d_blank:
        result = BOTH_EMPTY
    elif s_blank:
        result = SOURCE_EMPTY
    elif d_blank:
        result = DEST_EMPTY
    elif raw_repr(src) == raw_repr(dst):
        result = IDENTICAL
    elif s_norm == d_norm and not isinstance(s_norm, Unparseable):
        result = IDENTICAL_NORMALIZED
    else:
        result = DIFFERENT
    return {"stage1": result, "src_norm": s_norm, "dst_norm": d_norm}


def values_equal(a: Any, b: Any, kind: str) -> bool:
    """Equality of two values after normalisation (blank == blank)."""
    na, nb = normalize(a, kind), normalize(b, kind)
    if na is None and nb is None:
        return True
    return na == nb
