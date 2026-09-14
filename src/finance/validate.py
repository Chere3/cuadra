# -*- coding: utf-8 -*-
"""
validate.py — Validación real contra schemas/*.json (regla 00/22, F-03/SOL-003).

El paso VALIDATED del pipeline deja de ser decorativo: cada transacción canónica y
cada estado se validan contra su JSON Schema antes de poder pasar a READY_TO_COMMIT.
Si falta la librería jsonschema se falla de forma explícita (no se salta la validación
en silencio).
"""
from __future__ import annotations
import json
import os
import datetime as _dt
import functools

from . import config


class SchemaValidationError(ValueError):
    pass


def _bad_calendar_date(value):
    """True si `value` NO es una fecha de calendario ISO real (SOL-R2-002/SOL-003).
    El schema `format: date` valida el patrón pero no siempre la validez de calendario, y
    `2026-99-99`/`2026-02-30` deben rechazarse. `date.fromisoformat` es estricto: exige
    AAAA-MM-DD y que día/mes existan de verdad."""
    if value in (None, ""):
        return False
    try:
        _dt.date.fromisoformat(str(value))
        return False
    except (ValueError, TypeError):
        return True


@functools.lru_cache(maxsize=None)
def _schema(name):
    with open(config.path("schemas", name), encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=None)
def _validator(name):
    try:
        import jsonschema
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as e:
        raise SchemaValidationError(
            "jsonschema no está instalado; la validación de esquema es obligatoria "
            "(regla 00). Instala con: pip install jsonschema") from e
    return Draft202012Validator(_schema(name), format_checker=FormatChecker())


def validate_transaction(txn: dict):
    """Devuelve lista de errores (vacía si válida). No lanza por datos inválidos."""
    # subconjunto canónico (el dict de staging trae campos auxiliares con additionalProperties)
    errs = sorted(_validator("transaction.schema.json").iter_errors(txn),
                  key=lambda e: list(e.path))
    out = [f"{'/'.join(str(p) for p in e.path) or '(raíz)'}: {e.message}" for e in errs]
    for field in ("date_op", "date_post", "date_value"):
        if _bad_calendar_date(txn.get(field)):
            out.append(f"{field}: fecha de calendario inválida: {txn.get(field)!r}")
    return out


def validate_statement(stmt: dict):
    errs = sorted(_validator("statement.schema.json").iter_errors(stmt),
                  key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '(raíz)'}: {e.message}" for e in errs]
