# -*- coding: utf-8 -*-
"""
reconcile.py — Conciliación DETERMINISTA (no depende de la 'confianza' del modelo).

saldo_final_calculado = saldo_inicial + créditos + débitos  (con signo)
diff = calculado - declarado.  Resultado: OK | WARNING | BLOCKED | REVIEW_REQUIRED.
Si faltan saldos suficientes -> REVIEW_REQUIRED (nunca se inventan).
"""
from __future__ import annotations


def reconcile(opening_minor, closing_minor, txns, declared_totals, policy, currency,
              account_id=None):
    rec = policy.get("reconciliation", {})
    # Precedencia: cuenta > moneda > global. La excepción POR CUENTA existe para emisores cuyo
    # propio documento no cuadra consigo mismo (residuo de redondeo declarado por el banco), sin
    # relajar la exigencia de cuadre exacto para los demás. La regla 25 lo admite: tolerancia
    # ≤ 1 unidad mínima «salvo justificación documentada» — y la justificación va en el YAML,
    # junto al valor, no en la cabeza de nadie.
    por_cuenta = rec.get("tolerance_by_account", {}) or {}
    if account_id and account_id in por_cuenta:
        tol = por_cuenta[account_id]
    else:
        tol = rec.get("tolerance_by_currency", {}).get(currency.upper(),
                                                       rec.get("default_tolerance_minor", 1))
    credits = sum(t["amount_minor"] for t in txns if t["amount_minor"] > 0)
    debits = sum(t["amount_minor"] for t in txns if t["amount_minor"] < 0)
    n = len(txns)
    declared = declared_totals or {}
    declared_count = declared.get("n")

    out = {
        "opening_minor": opening_minor,
        "credits_minor": credits,
        "debits_minor": debits,
        "computed_closing_minor": None,
        "declared_closing_minor": closing_minor,
        "diff_minor": None,
        "tolerance_minor": tol,
        "n_transactions": n,
        "declared_count": declared_count,
        "coverage_ok": None,
        "result": None,
        "details": {},
    }

    # cobertura por número de movimientos declarado
    if declared_count is not None:
        out["coverage_ok"] = (declared_count == n)

    # ¿hay saldos suficientes?
    require = rec.get("require_opening_and_closing", True)
    if opening_minor is None or closing_minor is None:
        if require:
            out["result"] = "REVIEW_REQUIRED"
            out["details"]["reason"] = "faltan saldo inicial y/o final; no se inventan"
            # validación alternativa: totales declarados de créditos/débitos
            _check_declared_totals(out, declared, credits, debits, tol)
            return out

    if opening_minor is not None and closing_minor is not None:
        computed = opening_minor + credits + debits
        out["computed_closing_minor"] = computed
        diff = computed - closing_minor
        out["diff_minor"] = diff
        _check_declared_totals(out, declared, credits, debits, tol)
        cov_fail = out["coverage_ok"] is False
        if abs(diff) <= tol and not cov_fail and not out["details"].get("totals_mismatch"):
            out["result"] = "OK"
        elif abs(diff) <= tol:
            out["result"] = "WARNING"
            out["details"].setdefault("reason", "saldo cuadra pero hay advertencias (cobertura/totales)")
        else:
            out["result"] = "BLOCKED"
            out["details"]["reason"] = f"diferencia de saldo {diff} > tolerancia {tol}"
        return out

    # sin saldos y sin require estricto
    out["result"] = "REVIEW_REQUIRED"
    out["details"]["reason"] = "sin saldos para conciliar"
    _check_declared_totals(out, declared, credits, debits, tol)
    return out


def _check_declared_totals(out, declared, credits, debits, tol):
    dc = declared.get("credits_minor")
    dd = declared.get("debits_minor")
    mismatch = False
    if dc is not None and abs(dc - credits) > tol:
        out["details"]["declared_credits_diff"] = credits - dc
        mismatch = True
    if dd is not None and abs(dd - debits) > tol:
        out["details"]["declared_debits_diff"] = debits - dd
        mismatch = True
    if mismatch:
        out["details"]["totals_mismatch"] = True
