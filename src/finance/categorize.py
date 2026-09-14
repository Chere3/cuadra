# -*- coding: utf-8 -*-
"""
categorize.py — Orden: reglas aprobadas -> histórico verificado -> IA (opcional) -> "Por revisar".
La categoría NUNCA altera fecha, importe o saldo (regla 24). Ante incertidumbre: "Por revisar".
"""
from __future__ import annotations
import re

FALLBACK = "Por revisar"


def _match(rule, txn) -> bool:
    field = rule.get("field", "merchant_norm")
    val = (txn.get(field) or "").lower()
    op = rule.get("op", "contains")
    target = str(rule.get("value", "")).lower()
    if op == "equals":
        return val == target
    if op == "contains":
        return target in val
    if op == "regex":
        try:
            return re.search(target, val) is not None
        except re.error:
            return False
    return False


def categorize(txn: dict, rules, history: dict, policy: dict):
    """
    Devuelve (category, source, review_status).
    - rules: lista de reglas aprobadas (config.merchant_rules()), ya ordenadas por prioridad.
    - history: {merchant_norm: category} de correcciones/histórico verificado.
    """
    # 1) reglas aprobadas (mayor prioridad = menor número), primera que casa
    for r in sorted(rules, key=lambda x: x.get("priority", 100)):
        if _match(r, txn):
            return r["category"], "rule", "ok"
    # 2) histórico verificado por comercio exacto
    m = txn.get("merchant_norm")
    if m and m in history and history[m]:
        return history[m], "history", "ok"
    # 3) IA (opcional; desactivada por defecto en fixtures) — nunca toca importes/fechas
    ai = policy.get("ai_categorization", {})
    if ai.get("enabled"):
        # Interfaz para conectar un categorizador local; aquí no adivina.
        pass
    # 4) fallback
    return FALLBACK, "none", "por_revisar"
