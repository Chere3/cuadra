# -*- coding: utf-8 -*-
"""
ids.py — Identificadores estables y fingerprints deterministas.

Invariantes:
- El transaction_id NUNCA depende de la categoría (regla 08/22).
- Reimportar el MISMO documento produce los MISMOS ids (idempotencia).
- El fingerprint sirve para detectar el MISMO movimiento en estados solapados,
  pero NO se usa por sí solo para declarar duplicado (regla 26).
"""
from __future__ import annotations
import hashlib


def _h(*parts, n=16) -> str:
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:n]


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def statement_id(account_id: str, file_hash: str) -> str:
    """Identidad del documento: mismo archivo + misma cuenta => mismo statement_id."""
    return "stmt_" + _h(account_id, file_hash)


def transaction_id(account_id, statement_id, date_op, amount_minor, description_raw,
                   bank_ref=None, locator=None) -> str:
    """Estable e independiente de la categoría. Incluye locator para no colapsar
    dos renglones idénticos legítimos dentro del mismo estado."""
    return "txn_" + _h(account_id, statement_id, date_op, amount_minor,
                       description_raw, bank_ref, locator)


def fingerprint(account_id, date_op, amount_minor, description_norm, bank_ref=None) -> str:
    """Huella de MOVIMIENTO (no de documento) para detectar el mismo cargo en estados
    solapados. Se combina con origen/localizador/contexto antes de declarar duplicado."""
    return "fp_" + _h(account_id, date_op, amount_minor, description_norm, bank_ref)


def run_id(ts: str, rand: str = "") -> str:
    """ts debe venir del llamador (no se usa reloj interno aquí para trazabilidad)."""
    return "run_" + _h(ts, rand, n=12)


def import_id(run_id_: str, account_id: str, statement_id: str = "") -> str:
    """R12-E-005: la clave era solo (run, cuenta). Dos estados de la MISMA cuenta en un run
    —un mes partido en dos archivos, o un estado reemitido junto al original— generaban el mismo
    `import_id` y el INSERT reventaba con UNIQUE constraint: el commit ENTERO abortaba, incluidos
    los estados de las demás cuentas del run. Incluir el statement_id los separa. El tercer
    argumento es opcional para no romper llamadas antiguas."""
    return "imp_" + _h(run_id_, account_id, statement_id, n=12)
