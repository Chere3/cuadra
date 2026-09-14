# -*- coding: utf-8 -*-
"""
normalize.py — Normalización de fechas, descripciones y comercios.
Regla dura: NUNCA inferir silenciosamente una fecha ilegible; se marca dudosa.
"""
from __future__ import annotations
import re
import unicodedata
import datetime as _dt

# R6-A-005: plegado de homóglifos comunes (cirílico/griego que imitan al latín) para que las reglas
# de comercio no se evadan con "CаSINO" (а cirílica U+0430). NFKC cubre fullwidth/compatibilidad;
# este mapa cubre confundibles que NFKC no une. Solo se usa en merchant_norm (categorización), NO en
# description_norm (identidad/fingerprint), para no alterar la huella del movimiento.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "в": "b",
    "м": "m", "т": "t", "н": "h", "і": "i", "ѕ": "s", "ј": "j", "ԛ": "q", "ԝ": "w", "ё": "e",
    "α": "a", "ο": "o", "ν": "v", "ρ": "p", "τ": "t", "ε": "e", "ι": "i", "κ": "k", "χ": "x",
})


def _fold_confusables(s: str) -> str:
    return unicodedata.normalize("NFKC", s).translate(_CONFUSABLES)

MESES = {
    "ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6,
    "jul": 7, "julio": 7, "ago": 8, "agosto": 8, "sep": 9, "sept": 9, "septiembre": 9,
    "oct": 10, "octubre": 10, "nov": 11, "noviembre": 11, "dic": 12, "diciembre": 12,
}


class DateParseError(ValueError):
    pass


def normalize_date(text, year_hint=None):
    """Devuelve ISO 'YYYY-MM-DD'. Lanza DateParseError si es ilegible/ambigua."""
    if text is None:
        raise DateParseError("fecha vacía")
    s = str(text).strip().lower()
    if not s:
        raise DateParseError("fecha vacía")
    # ISO ya
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # dd/mm/yyyy o dd-mm-yyyy o dd.mm.yyyy
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if mo > 12 and d <= 12:  # formato mm/dd -> corrige solo si es inequívoco
            d, mo = mo, d
        return _mk(y, mo, d)
    # dd-mmm-yyyy / dd mmm yyyy / "15 jun 2026"
    m = re.match(r"^(\d{1,2})[\s\-/]*([a-záéíóú]{3,})[\s\-/]*(\d{2,4})?$", s)
    if m and m.group(2)[:3] in MESES or (m and m.group(2) in MESES):
        d = int(m.group(1))
        mo = MESES.get(m.group(2)) or MESES.get(m.group(2)[:3])
        y = int(m.group(3)) if m.group(3) else year_hint
        if not y:
            raise DateParseError(f"fecha sin año y sin year_hint: {text!r}")
        if y < 100:
            y += 2000
        return _mk(y, mo, d)
    raise DateParseError(f"fecha ilegible: {text!r}")


def _mk(y, mo, d):
    try:
        return _dt.date(y, mo, d).isoformat()
    except ValueError:
        raise DateParseError(f"fecha inválida: {y}-{mo}-{d}")


_WS = re.compile(r"\s+")
_REF = re.compile(r"\b(ref|folio|aut|autoriz\w*|no\.?|num\.?|clave|rfc)\b[:\s#]*[a-z0-9\-]+", re.I)
_LONGNUM = re.compile(r"\b\d{6,}\b")


def normalize_description(raw: str) -> str:
    if raw is None:
        return ""
    s = _WS.sub(" ", str(raw).strip())
    return s


def normalize_merchant(raw: str) -> str:
    """Token de comercio en minúsculas, sin referencias/folios/números largos ni ciudad genérica."""
    if raw is None:
        return ""
    s = _fold_confusables(str(raw)).lower()  # R6-A-005: homóglifos no evaden reglas
    s = _REF.sub(" ", s)
    s = _LONGNUM.sub(" ", s)
    s = re.sub(r"[*#]+", " ", s)
    s = re.sub(r"\b(mexico|mx|cdmx|gdl|mty|compra|pago|tarjeta|debito|credito)\b", " ", s)
    s = _WS.sub(" ", s).strip()
    return s
