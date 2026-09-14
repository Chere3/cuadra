# -*- coding: utf-8 -*-
"""
textparse.py — Parser heurístico de líneas de texto (para PDF de texto y OCR).

LÍMITE HONESTO: el layout de cada banco varía; este parser genérico extrae
(fecha, descripción, importe) por línea con expresiones regulares. Para producción
por banco conviene una plantilla específica. Las líneas no parseadas se reportan
como warnings, NUNCA se inventan importes.
"""
from __future__ import annotations
import re

_DATE = r"(\d{1,2}[/\-.][a-zA-Z0-9]{1,4}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-zA-Záéíóú]{3,}\.?\s*\d{0,4})"
_AMOUNT = r"\(?-?\$?\s?[\d,\. ]*\d[\d,\.]*\)?"
_LINE = re.compile(rf"^\s*(?P<date>{_DATE})\s+(?P<desc>.+?)\s+(?P<amount>{_AMOUNT})\s*$")

_META = {
    "period_start": re.compile(r"per[ií]odo.*?(\d{4}-\d{2}-\d{2}|\d{1,2}[/\-][a-z0-9]{1,4}[/\-]\d{2,4})", re.I),
    # SOL-009: el token de importe EXIGE empezar con dígito — '[\d,\.]+' aceptaba una coma sola
    # (p. ej. 'saldo al corte, no incluye…' → closing_raw=',', que rompía la conciliación).
    "opening": re.compile(r"(saldo\s+(?:inicial|anterior))\D{0,15}(\(?[-+]?\$?\s?\d[\d,\.]*\)?)", re.I),
    # incluye variantes por banco: 'saldo final/actual/nuevo', 'saldo al corte', y el 'saldo al
    # generar este estado de cuenta' de Nu.
    "closing": re.compile(
        r"(saldo\s+(?:final|actual|nuevo|al\s+corte|al\s+generar[\w\sáéíóú]*?estado[\w\sáéíóú]*?cuenta))"
        r"\D{0,3}(\(?[-+]?\$?\s?\d[\d,\.]*\)?)", re.I),
}


def parse_lines(text, page=None, line_conf=None, min_conf=60):
    """Devuelve (rows, meta_hints, warnings).

    line_conf: lista opcional de confianzas OCR (0-100) alineada a text.splitlines().
    Cuando la confianza de la línea de un movimiento es baja (< min_conf), el importe se
    marca 'importe_dudoso' (bloqueante) en vez de aceptarse: no se fabrican importes a
    partir de OCR ilegible (regla 00/21, SOL-009).
    """
    rows, warnings = [], []
    meta = {"declared": {}}
    for m_key, rx in (("opening", _META["opening"]), ("closing", _META["closing"])):
        mm = rx.search(text)
        if mm:
            meta["opening_raw" if m_key == "opening" else "closing_raw"] = mm.group(2).strip()
    ridx = 0
    for i, ln in enumerate(text.splitlines()):
        if not ln.strip():
            continue
        m = _LINE.match(ln)
        if m and _plausible_amount(m.group("amount")):
            ridx += 1
            conf = line_conf[i] if (line_conf and i < len(line_conf)) else None
            flags = []
            if conf is not None and conf < min_conf:
                flags.append("importe_dudoso")
                warnings.append(f"OCR baja confianza ({conf:.0f}<{min_conf}) en línea: {ln.strip()[:60]}")
            rows.append({
                "locator": (f"page:{page}|" if page else "") + f"line:{ridx}",
                "raw_text": ln.strip(), "page": page, "row_idx": ridx,
                "date_op": m.group("date").strip(),
                "description": m.group("desc").strip(),
                "amount": m.group("amount").strip(),
                "_flags": flags,
            })
        else:
            # solo advertir de líneas que parecen movimientos (tienen dígitos y fecha)
            if re.search(_DATE, ln) and re.search(r"\d", ln):
                warnings.append(f"línea no parseada (revisar): {ln.strip()[:80]}")
    return rows, meta, warnings


def _plausible_amount(s):
    return bool(re.search(r"\d", s)) and len(re.sub(r"[^\d]", "", s)) >= 1
