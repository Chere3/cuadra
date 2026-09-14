# -*- coding: utf-8 -*-
"""
csv_ext.py — Extractor de estados en CSV. Formato:

  # account_id: bbva_debito
  # currency: MXN
  # period_start: 2026-06-01
  # period_end: 2026-06-30
  # opening_balance: 10000.00
  # closing_balance: 12345.67
  # declared_credits: 30000.00
  # declared_debits: -27654.33
  # declared_count: 5
  fecha,fecha_cargo,descripcion,monto,saldo,ref
  2026-06-01,2026-06-01,DEPOSITO NOMINA,30000.00,40000.00,NOM123
  ...
Las líneas '# k: v' son metadatos. La tabla es CSV estándar con encabezados flexibles.
"""
from __future__ import annotations
import csv
from . import EXTRACTOR_VERSION

# mapeo de encabezados -> campo canónico
HEADERS = {
    "date_op": ["date_op", "fecha", "fecha_operacion", "fecha operación", "fecha_op"],
    "date_post": ["date_post", "fecha_cargo", "fecha_procesamiento", "fecha proceso"],
    "date_value": ["date_value", "fecha_valor"],
    "description": ["description", "descripcion", "descripción", "concepto", "detalle"],
    "amount": ["amount", "monto", "importe", "cargo_abono", "movimiento"],
    "balance": ["balance", "saldo"],
    "bank_ref": ["bank_ref", "ref", "folio", "referencia", "autorizacion", "autorización"],
}


def _canon(header):
    h = (header or "").strip().lower()
    for canon, alts in HEADERS.items():
        if h in alts:
            return canon
    return None


def extract_csv(path, account_hint=None, policy=None):
    meta = {"declared": {}}
    data_lines = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.rstrip("\n")
            if s.strip().startswith("#"):
                body = s.strip()[1:].strip()
                if ":" in body:
                    k, v = body.split(":", 1)
                    k, v = k.strip().lower(), v.strip()
                    if k == "account_id":
                        meta["account_id"] = v
                    elif k == "currency":
                        meta["currency"] = v
                    elif k == "period_start":
                        meta["period_start"] = v
                    elif k == "period_end":
                        meta["period_end"] = v
                    elif k == "opening_balance":
                        meta["opening_raw"] = v
                    elif k == "closing_balance":
                        meta["closing_raw"] = v
                    elif k == "declared_credits":
                        meta["declared"]["credits"] = v
                    elif k == "declared_debits":
                        meta["declared"]["debits"] = v
                    elif k == "declared_count":
                        meta["declared"]["n"] = v
            else:
                data_lines.append(s)

    rows = []
    warnings = []
    if data_lines:
        reader = csv.reader(data_lines)
        header = next(reader)
        cmap = {i: _canon(h) for i, h in enumerate(header)}
        for ridx, raw in enumerate(reader, start=1):
            if not any(cell.strip() for cell in raw):
                continue
            rec = {"locator": f"row:{ridx}", "raw_text": ",".join(raw),
                   "row_idx": ridx, "table_idx": 0}
            for i, cell in enumerate(raw):
                field = cmap.get(i)
                if field:
                    rec[field] = cell.strip()
            if "date_op" not in rec or "amount" not in rec:
                warnings.append(f"row:{ridx} sin fecha o importe")
            rows.append(rec)
    if account_hint and "account_id" not in meta:
        meta["account_id"] = account_hint
    return {
        "extraction_method": "csv",
        "extractor_version": EXTRACTOR_VERSION,
        "meta": meta,
        "rows": rows,
        "warnings": warnings,
    }
