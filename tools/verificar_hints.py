#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verificar_hints.py — comprueba que los `match_hints` de `config/accounts.yml` distinguen de verdad.

Por qué existe: los hints se SUMAN y se pesan por número de palabras, sin exigir una marca de
institución y sin margen mínimo sobre el segundo lugar. Basta con que una frase aparezca en las
descripciones de los movimientos de OTRA cuenta para que le robe el documento, o para empatar y
dejarlo sin identificar. Nos pasó dos veces con Nu, en las dos direcciones: los dos productos se
nombran mutuamente ("disposición de saldo en Cuenta Nu" en la tarjeta, "Pago a tu tarjeta de
crédito Nu" en la cuenta). Un empate exacto tumbó un estado YA INCORPORADO, y no lo vimos porque
la verificación se hizo solo contra los documentos nuevos.

Este script pasa TODO el acervo —`statements/raw/` (lo ya incorporado, con la cuenta que dice la
base) y `statements/inbox/`— por `identify_account`, y para los raw compara contra la verdad de
campo. Córrelo cada vez que toques `match_hints`.

No es un test de la suite a propósito: necesita los estados reales, y la regla 70 exige que los
fixtures versionados sean ficticios. El script es código; los datos se quedan fuera de git.

    python3 tools/verificar_hints.py
"""
import glob
import hashlib
import os
import re
import sqlite3
import sys
from collections import Counter

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
os.environ.setdefault("FINANCE_ROOT", RAIZ)

from finance import config, services as S  # noqa: E402
from finance.extractors.pdf_ext import _texto_inservible  # noqa: E402

# Debajo de este margen el ganador lo decide una sola aparición: un estado con una mención más de
# la cuenta hermana volcaría la identificación. 0 es el empate que ya nos tumbó un documento.
MARGEN_MINIMO = 4


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _texto(path):
    import pdfplumber
    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    except Exception:
        return ""


def main():
    db = os.path.join(RAIZ, "data", "finance.sqlite")
    verdad = {}
    if os.path.exists(db):
        con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        verdad = {s: a for s, a in con.execute(
            "select file_sha256, account_id from statements where status='COMMITTED'")}

    fallos, frágiles, sin_texto, revisados = [], [], 0, 0
    hits_por_cuenta = Counter()
    for path in sorted(glob.glob(os.path.join(RAIZ, "statements", "raw", "**", "*.pdf"),
                                 recursive=True)) + \
            sorted(glob.glob(os.path.join(RAIZ, "statements", "inbox", "*.pdf"))):
        texto = _texto(path)
        if _texto_inservible(texto):
            # PDF sin texto aprovechable: en el pipeline real la cuenta se identifica DESPUÉS del
            # OCR, así que aquí no hay nada que juzgar. Mismo criterio que usa `extract_pdf`.
            sin_texto += 1
            continue
        revisados += 1
        cabecera = texto[:2000]
        cuenta, _via = S.identify_account({}, texto, header=cabecera)
        nombre = re.sub(r"[0-9]{5,}", "#", os.path.basename(path))
        esperada = verdad.get(_sha(path))
        if cuenta is None:
            fallos.append((nombre, "SIN IDENTIFICAR", esperada, S._account_hits(cabecera)))
            continue
        if esperada and cuenta != esperada:
            fallos.append((nombre, f"ruteado a {cuenta}", esperada, S._account_hits(cabecera)))
            continue
        hits_por_cuenta[cuenta] += 1
        # Margen del ganador: es lo que de verdad mide la robustez. Los nombres de banco aparecen
        # legítimamente en las descripciones de traspasos de OTROS bancos, así que la coincidencia
        # cruzada por sí sola no es un defecto; que decida el resultado, sí.
        marcador = S._account_hits(cabecera) or S._account_hits(texto)
        orden = sorted(marcador.values(), reverse=True)
        # Solo es frágil si OTRA cuenta queda cerca. Que la ganadora tenga pocos puntos siendo la
        # única que puntúa no es debilidad: es exactamente lo que se busca.
        if len(orden) > 1 and orden[0] - orden[1] < MARGEN_MINIMO:
            frágiles.append((nombre, cuenta, orden[0] - orden[1], marcador))

    print(f"documentos con texto aprovechable revisados: {revisados}"
          f"   (omitidos por requerir OCR: {sin_texto})")
    print(f"identificados: {dict(hits_por_cuenta)}")

    if frágiles:
        print(f"\nMARGEN ESTRECHO (< {MARGEN_MINIMO} puntos sobre la segunda cuenta):")
        for nombre, cuenta, margen, marcador in frágiles:
            print(f"   {nombre[:40]:42} -> {cuenta:14} margen={margen}  {marcador}")

    if fallos:
        print("\nFALLOS DE IDENTIFICACIÓN:")
        for nombre, que, esperada, hits in fallos:
            print(f"   {nombre[:40]:42} {que:24} esperada={esperada}  hits={hits}")

    if fallos:
        print(f"\n=== {len(fallos)} fallo(s) de identificación ===")
        return 1
    if frágiles:
        print(f"\n=== identificación correcta, pero {len(frágiles)} documento(s) con margen "
              f"estrecho: revisa antes de tocar los hints ===")
        return 2
    print("\n=== TODO VERDE: cada documento se identifica con margen suficiente ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
