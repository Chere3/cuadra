# -*- coding: utf-8 -*-
"""
money.py — Dinero SIEMPRE en unidades menores enteras (centavos). Nunca float binario.

Regla dura (00-invariantes): jamás se usa coma flotante binaria para cantidades
monetarias. Todo importe vive como INTEGER de unidades menores + código ISO 4217.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import re

# Exponente decimal por moneda (ISO 4217). Amplía según necesites.
CURRENCY_EXPONENT = {
    "MXN": 2, "USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "BRL": 2, "ARS": 2,
    "CLP": 0, "JPY": 0, "COP": 2, "PEN": 2,
}


class AmountParseError(ValueError):
    """Importe ilegible o ambiguo. NUNCA se infiere silenciosamente (regla 21)."""


# R5-A-005: cota de magnitud. Un importe > int64 desbordaba el INSERT (OverflowError crudo);
# un valor absurdo pero dentro de int64 entraba como basura. 10^15 centavos = 10 billones de
# pesos: generoso para finanzas personales, bloquea forjas/errores como `importe_dudoso`.
MAX_AMOUNT_MINOR = 10 ** 15


def exponent(currency: str) -> int:
    return CURRENCY_EXPONENT.get((currency or "").upper(), 2)


def to_minor(amount, currency: str) -> int:
    """Convierte Decimal/str/int a unidades menores enteras. Rechaza float binario directo."""
    if isinstance(amount, float):
        raise TypeError("No se aceptan float binarios para dinero; usa str o Decimal.")
    exp = exponent(currency)
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    q = d.scaleb(exp).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    minor = int(q)
    if abs(minor) > MAX_AMOUNT_MINOR:
        raise AmountParseError(f"importe fuera de rango (magnitud > {MAX_AMOUNT_MINOR}): {amount!r}")
    return minor


def from_minor(minor: int, currency: str) -> Decimal:
    """Unidades menores -> importe con la ESCALA de la moneda (MXN: 2 decimales siempre).
    La división sola deja el Decimal sin escala fija —20000 daba Decimal('200') y 300050
    daba Decimal('3000.5')—, y el CSV-contrato de exportación heredaba ese formato variable:
    un consumidor que espere dos decimales tropieza. Cuantizar es exacto (dividir un entero
    entre 10^exp no puede tener más de `exp` decimales), así que no redondea ningún importe."""
    exp = exponent(currency)
    d = Decimal(int(minor)) / (Decimal(10) ** exp)
    return d.quantize(Decimal(1).scaleb(-exp))


def format_minor(minor: int, currency: str) -> str:
    exp = exponent(currency)
    d = from_minor(minor, currency)
    return f"{d:,.{exp}f} {currency.upper()}"


_CLEAN = re.compile(r"[^\d.,\-()]")


def parse_amount(text, currency: str) -> int:
    """
    Convierte un importe de texto de estado de cuenta a unidades menores (int, con signo).
    Soporta: "$1,234.56", "1.234,56", "-123.45", "(123.45)" (paréntesis = negativo),
    "1 234,56", "MXN 1,000.00". Lanza AmountParseError si es ilegible/ambiguo.
    """
    if text is None:
        raise AmountParseError("importe vacío")
    if isinstance(text, (int,)):
        return to_minor(Decimal(text), currency)
    if isinstance(text, Decimal):
        return to_minor(text, currency)
    s = str(text).strip()
    if s == "" or s in {"-", "—", "N/A", "n/a"}:
        raise AmountParseError(f"importe no numérico: {text!r}")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = _CLEAN.sub("", s).replace(" ", "")
    if s.count("-") > 1:
        raise AmountParseError(f"importe ambiguo (varios signos): {text!r}")
    if s.startswith("-"):
        neg = not neg
        s = s[1:]
    if s == "":
        raise AmountParseError(f"importe vacío tras limpiar: {text!r}")

    has_dot, has_com = "." in s, "," in s
    if has_dot and has_com:
        # el separador MÁS A LA DERECHA es el decimal
        dec_sep = "." if s.rfind(".") > s.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        s = s.replace(thou_sep, "").replace(dec_sep, ".")
    elif has_com and not has_dot:
        # una sola coma: decimal si hay <=2 dígitos después, si no separador de miles
        after = s.split(",")[-1]
        s = s.replace(",", ".") if len(after) <= 2 else s.replace(",", "")
    elif has_dot and not has_com:
        after = s.split(".")[-1]
        if s.count(".") > 1:
            # varios puntos ("1.234.567") => separadores de miles, inequívoco
            s = s.replace(".", "")
        elif len(after) == 3 and len(s.replace(".", "")) > 3 and _looks_thousands(s):
            # F-14: un solo punto con grupo de 3 y sin decimales ("5.000") es AMBIGUO
            # (miles vs decimal). No se asume: se marca dudoso (regla 00: no inferir).
            raise AmountParseError(f"importe ambiguo (miles vs decimal): {text!r}")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise AmountParseError(f"importe ilegible: {text!r}")
    if neg:
        d = -d
    return to_minor(d, currency)


def _looks_thousands(s: str) -> bool:
    # "1.234" -> miles ; "12.34" -> decimal. Heurística conservadora.
    parts = s.split(".")
    return len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3
