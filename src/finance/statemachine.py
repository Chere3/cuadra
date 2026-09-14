# -*- coding: utf-8 -*-
"""statemachine.py — Estados explícitos de importación. Sin saltos arbitrarios."""
from __future__ import annotations

STATES = [
    "DISCOVERED", "HASHED", "EXTRACTED", "NORMALIZED", "VALIDATED",
    "REVIEW_REQUIRED", "READY_TO_COMMIT", "COMMITTED", "EXPORTED",
    "EXPORT_FAILED", "EXCEL_VERIFIED", "FAILED", "ROLLED_BACK",
]

ALLOWED = {
    "DISCOVERED": {"HASHED", "FAILED"},
    "HASHED": {"EXTRACTED", "FAILED"},
    "EXTRACTED": {"NORMALIZED", "FAILED"},
    "NORMALIZED": {"VALIDATED", "FAILED"},
    "VALIDATED": {"REVIEW_REQUIRED", "READY_TO_COMMIT", "FAILED"},
    "REVIEW_REQUIRED": {"READY_TO_COMMIT", "FAILED"},
    "READY_TO_COMMIT": {"COMMITTED", "FAILED"},
    "COMMITTED": {"EXPORTED", "EXPORT_FAILED", "ROLLED_BACK", "FAILED"},
    "EXPORT_FAILED": {"EXPORTED", "ROLLED_BACK", "FAILED"},
    "EXPORTED": {"EXCEL_VERIFIED", "ROLLED_BACK", "FAILED"},
    "EXCEL_VERIFIED": {"ROLLED_BACK"},
    "FAILED": set(),
    "ROLLED_BACK": set(),
}


class IllegalTransition(Exception):
    pass


def check(src: str, dst: str):
    if dst not in ALLOWED.get(src, set()):
        raise IllegalTransition(f"transición no permitida: {src} -> {dst}")
    return dst
