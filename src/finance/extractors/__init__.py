# -*- coding: utf-8 -*-
"""
extractors — Adaptadores de extracción. Contrato común:

extract(path, account_hint, policy) -> dict:
{
  "extraction_method": "csv|xlsx|pdf_text|ocr|image",
  "extractor_version": "1.0",
  "meta": {"account_id"?, "currency"?, "period_start"?, "period_end"?,
           "opening_raw"?, "closing_raw"?, "declared": {"credits","debits","n"}},
  "rows": [ {"locator","raw_text","page"?,"table_idx"?,"row_idx"?,
             "date_op","date_post"?,"date_value"?,"description","amount","balance"?,"bank_ref"?} ],
  "warnings": [ ... ],
}
Preferir texto nativo; OCR solo si no hay texto (regla 21). Nunca inferir importes ilegibles.
"""
from __future__ import annotations
import os

# Se sube cuando cambia la SEMÁNTICA de lo extraído, no cuando se corrige un detalle interno: la
# base guarda esta versión por statement y es lo único que permite saber después con qué reglas se
# leyó cada documento.
#   1.1 — el periodo puede derivarse de la fecha de corte (antes: solo rango explícito o mes
#         natural), de modo que un mismo PDF puede dar un periodo distinto al de la versión 1.0;
#         Nu recompone las fechas partidas en dos renglones y excluye congelar/descongelar de
#         Cajita; HSBC restituye los miles separados por espacio y admite renglones que empiezan
#         por el número de autorización.
EXTRACTOR_VERSION = "1.1"


def extract(path: str, account_hint=None, policy=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        from .csv_ext import extract_csv
        return extract_csv(path, account_hint, policy)
    if ext in (".xlsx", ".xls"):
        from .xlsx_ext import extract_xlsx
        return extract_xlsx(path, account_hint, policy)
    if ext == ".pdf":
        from .pdf_ext import extract_pdf
        return extract_pdf(path, account_hint, policy)
    if ext in (".png", ".jpg", ".jpeg"):
        from .image_ext import extract_image
        return extract_image(path, account_hint, policy)
    raise ValueError(f"extensión no soportada: {ext}")
