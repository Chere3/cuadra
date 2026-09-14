#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bbva_pdf_to_csv.py — Estados de cuenta BBVA (PDF) → CSV, uno por estado más un consolidado.

Reutiliza la plantilla BBVA de la ingesta (`src/finance/extractors`): aquí no hay lógica contable
propia, solo formato de salida. Cada estado se VALIDA antes de reportarse como bueno:
  · saldo_anterior + Σ importes == saldo_final (identidad de saldo, al centavo);
  · número de cargos/abonos y sus totales == los que el propio estado declara en su resumen.
Si algo no cuadra, el CSV se escribe igual (sirve para inspeccionar), pero el estado sale marcado
NO_CUADRA y el script termina con código 1. Nunca se toca un importe para cuadrar.

Uso:
  python3 scripts/bbva_pdf_to_csv.py <pdf|carpeta> [<pdf|carpeta> ...] [--out reports/bbva_csv]

Privacidad (regla 50): en la columna `detalle` toda secuencia de 10+ dígitos (CLABE, clave de
rastreo, cuenta) se enmascara a sus últimos 4 dígitos. El resto del texto se conserva tal cual.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
logging.getLogger("pdfminer").setLevel(logging.ERROR)   # "Could not get FontBBox" en cada página

from finance.extractors import pdf_ext            # noqa: E402
from finance.extractors import bank_templates as BT   # noqa: E402

COLUMNAS = ["fecha_operacion", "fecha_liquidacion", "descripcion", "concepto", "contraparte",
            "referencia", "tarjeta", "cargo", "abono", "importe", "saldo",
            "periodo_inicio", "periodo_fin", "archivo", "locator", "detalle", "flags"]

_REFERENCIA = re.compile(r"\bReferencia\s+(\d{6,})")
_TARJETA = re.compile(r"\*{4,}(\d{4})")
# concepto que escribió el ordenante: SPEI lo antepone con un prefijo de 7 dígitos
# ('0506260Pago TDC Referencia …'); las transferencias BNET van tras el número de cuenta
# ('BNET 0162238802 Transf a MARIA SAL Referencia …').
_CONCEPTO_SPEI = re.compile(r"^\d{7}(.+?)\s+Referencia\b")
_CONCEPTO_BNET = re.compile(r"^BNET\s+\d+\s+(.+?)\s+Referencia\b")
_NOMBRE = re.compile(r"^[A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]+){1,}$")
_DIGITOS_LARGOS = re.compile(r"\d{10,}")


def _centavos(txt):
    if txt in (None, ""):
        return None
    return round(float(str(txt).replace("$", "").replace(",", "").replace(" ", "")) * 100)


def _detalle(detail):
    """(concepto, contraparte, referencia, tarjeta, detalle_enmascarado) a partir de los renglones
    de detalle que BBVA imprime bajo el movimiento. Lo que no aparece queda vacío: no se infiere."""
    partes = [p.strip() for p in (detail or "").split(" | ") if p.strip()]
    concepto = contraparte = referencia = tarjeta = ""
    for p in partes:
        m = _CONCEPTO_SPEI.match(p) or _CONCEPTO_BNET.match(p)
        if m and not concepto:
            concepto = m.group(1).strip()
        m = _REFERENCIA.search(p)
        if m and not referencia:
            referencia = m.group(1)
        m = _TARJETA.search(p)
        if m and not tarjeta:
            tarjeta = m.group(1)
        if _NOMBRE.match(p) and not p.upper().startswith("RFC"):
            contraparte = p            # el nombre va al final del bloque: gana el último
    enmascarado = _DIGITOS_LARGOS.sub(lambda m: "…" + m.group(0)[-4:], " | ".join(partes))
    return concepto, contraparte, referencia, tarjeta, enmascarado


def _fila(row, meta, archivo):
    concepto, contraparte, referencia, tarjeta, detalle = _detalle(row.get("detail"))
    imp = row.get("amount") or ""
    val = float(imp) if imp else None
    return {
        "fecha_operacion": row.get("date_op") or "",
        "fecha_liquidacion": row.get("date_post") or "",
        "descripcion": row.get("description") or "",
        "concepto": concepto,
        "contraparte": contraparte,
        "referencia": referencia,
        "tarjeta": tarjeta,
        "cargo": f"{-val:.2f}" if val is not None and val < 0 else "",
        "abono": f"{val:.2f}" if val is not None and val > 0 else "",
        "importe": f"{val:.2f}" if val is not None else "",
        "saldo": row.get("balance") or "",
        "periodo_inicio": meta.get("period_start") or "",
        "periodo_fin": meta.get("period_end") or "",
        "archivo": archivo,
        "locator": row.get("locator") or "",
        "detalle": detalle,
        "flags": ";".join(row.get("_flags") or []),
    }


def _validar(rows, meta):
    """Devuelve (ok, problemas[]) comparando lo extraído con lo que el estado declara."""
    problemas = []
    importes = [_centavos(r["amount"]) for r in rows if r.get("amount")]
    if len(importes) != len(rows):
        problemas.append(f"{len(rows) - len(importes)} fila(s) sin importe (dudosas)")
    dudosas = sum(1 for r in rows if "importe_dudoso" in (r.get("_flags") or []))
    if dudosas:
        problemas.append(f"{dudosas} fila(s) con importe_dudoso")
    op, cl = _centavos(meta.get("opening_raw")), _centavos(meta.get("closing_raw"))
    if op is None or cl is None:
        problemas.append("el estado no trae saldo anterior/final legible")
    else:
        diff = op + sum(importes) - cl
        if diff != 0:
            problemas.append(f"saldo_anterior + Σ ≠ saldo_final (diferencia {diff / 100:.2f})")
    dec = meta.get("declared") or {}
    if dec.get("n") is not None and dec["n"] != len(rows):
        problemas.append(f"el estado declara {dec['n']} movimientos y se extrajeron {len(rows)}")
    abonos = sum(x for x in importes if x > 0)
    cargos = -sum(x for x in importes if x < 0)
    if dec.get("credits") is not None and _centavos(dec["credits"]) != abonos:
        problemas.append(f"abonos declarados {dec['credits']} ≠ extraídos {abonos / 100:.2f}")
    if dec.get("debits") is not None and -_centavos(dec["debits"]) != cargos:
        problemas.append(f"cargos declarados {dec['debits']} ≠ extraídos {cargos / 100:.2f}")
    return not problemas, problemas


def _pdfs(paths):
    for p in paths:
        if os.path.isdir(p):
            for n in sorted(os.listdir(p)):
                if n.lower().endswith(".pdf"):
                    yield os.path.join(p, n)
        elif p.lower().endswith(".pdf"):
            yield p


def _banco_rapido(path):
    import pdfplumber
    try:
        with pdfplumber.open(path) as pdf:
            texto = "\n".join((pg.extract_text() or "") for pg in pdf.pages[:2])
    except Exception:
        return None
    return BT.detect_bank(texto)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="PDF(s) de BBVA o carpeta(s) que los contengan")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "bbva_csv"),
                    help="carpeta de salida (default: reports/bbva_csv)")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    vistos, periodos, todas, resumen, algun_fallo = {}, {}, [], [], False
    for pdf in _pdfs(args.paths):
        nombre = os.path.basename(pdf)
        h = _sha(pdf)
        if h in vistos:
            resumen.append((nombre, "DUPLICADO", f"mismo archivo que {vistos[h]}", 0))
            continue
        vistos[h] = nombre
        # Se mira el emisor en las dos primeras páginas ANTES de la extracción completa: una carpeta
        # mezclada trae estados de otros bancos (y PDFs sin texto que dispararían el OCR) y no
        # es un error que estén ahí; simplemente no son de este exportador.
        banco = _banco_rapido(pdf)
        if banco != "bbva":
            resumen.append((nombre, "OMITIDO", f"no es BBVA (detectado: {banco or 'ninguno'})", 0))
            continue
        ext = pdf_ext.extract_pdf(pdf, account_hint="bbva")
        if not ext["extraction_method"].endswith(":bbva"):
            resumen.append((nombre, "NO_PARSEADO", f"método {ext['extraction_method']}", 0))
            algun_fallo = True
            continue
        meta, rows = ext["meta"], ext["rows"]
        ok, problemas = _validar(rows, meta)
        ps, pe = meta.get("period_start") or "sin-periodo", meta.get("period_end") or ""
        # El mismo estado bajado dos veces NO es idéntico byte a byte (BBVA regenera el PDF), así
        # que la identidad útil es el periodo + su contenido: si coincide, es el mismo estado y no
        # se duplica; si el periodo coincide pero el contenido no, se conservan los dos y se avisa.
        firma = (ps, pe, tuple((r.get("date_op"), r.get("amount"), r.get("description")) for r in rows))
        previo = periodos.get((ps, pe))
        if previo and previo[0] == firma:
            resumen.append((nombre, "DUPLICADO", f"mismo periodo y contenido que {previo[1]}", 0))
            continue
        sufijo = ""
        if previo:
            sufijo = "_bis"
            problemas.append(f"mismo periodo que {previo[1]} pero distinto contenido: revisar cuál vale")
            ok = False
        periodos.setdefault((ps, pe), (firma, nombre))
        salida = os.path.join(args.out, f"bbva_{ps}_{pe}{sufijo}.csv")
        filas = [_fila(r, meta, nombre) for r in rows]
        with open(salida, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS)
            w.writeheader()
            w.writerows(filas)
        todas.extend(filas)
        estado = "OK" if ok else "NO_CUADRA"
        algun_fallo = algun_fallo or not ok
        detalle = (f"{len(rows)} mov · {ps}→{pe} · saldo {meta.get('opening_raw')}→{meta.get('closing_raw')}"
                   + ("" if ok else " · " + "; ".join(problemas)))
        for wmsg in ext.get("warnings") or []:
            detalle += f" · aviso: {wmsg}"
        resumen.append((nombre, estado, detalle, len(rows)))

    if todas:
        todas.sort(key=lambda r: (r["periodo_inicio"], r["locator"].rsplit(":", 1)[-1].zfill(4)))
        consolidado = os.path.join(args.out, "bbva_todos.csv")
        with open(consolidado, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS)
            w.writeheader()
            w.writerows(todas)

    ancho = max((len(n) for n, *_ in resumen), default=10)
    for nombre, estado, detalle, _n in resumen:
        print(f"{estado:10} {nombre:{ancho}}  {detalle}")
    if todas:
        print(f"\n{len(todas)} movimientos en {args.out}/ (uno por estado + bbva_todos.csv)")
    return 1 if algun_fallo else 0


if __name__ == "__main__":
    sys.exit(main())
