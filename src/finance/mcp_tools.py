# -*- coding: utf-8 -*-
"""
mcp_tools.py — Registro de tools del MCP financiero (SOL-004 / regla 60).

Los handlers invocan la MISMA capa de servicios que la CLI (services.py); NO duplican
lógica contable. Garantías (regla 60):
- dry-run por defecto (preview_import/ingest nunca committean);
- commit y rollback separados y con CONFIRMACIÓN explícita (confirm=true);
- sin acceso arbitrario al filesystem: las rutas se limitan a la raíz del proyecto;
- no se exponen números de cuenta completos (services.mask_account) ni raw_text/documentos.

`TOOLS` mapea nombre -> {description, input_schema, handler}. El servidor stdio
(mcp_server.py) sólo hace el transporte JSON-RPC sobre este registro; las pruebas de
paridad ejercen estos handlers directamente.
"""
from __future__ import annotations
import os
import json

from . import services as S
from . import config


class MCPError(ValueError):
    pass


def _safe_project_path(p):
    """Resuelve una ruta y exige que quede DENTRO de la raíz del proyecto."""
    root = os.path.realpath(config.project_root())
    full = os.path.realpath(os.path.join(root, p) if not os.path.isabs(p) else p)
    if full != root and not full.startswith(root + os.sep):
        raise MCPError(f"ruta fuera del proyecto no permitida: {p}")
    return full


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def finance_system_status(**_):
    return S.status()


def finance_list_inbox(**_):
    inbox = _safe_project_path(os.path.join("statements", "inbox"))
    files = []
    if os.path.isdir(inbox):
        for n in sorted(os.listdir(inbox)):
            if n.lower().rsplit(".", 1)[-1] in ("pdf", "csv", "xlsx", "xls", "png", "jpg", "jpeg"):
                files.append(n)
    return {"inbox": "statements/inbox", "files": files}


def _collect_paths(path):
    full = _safe_project_path(path)
    if os.path.isdir(full):
        out = []
        for n in sorted(os.listdir(full)):
            if n.lower().rsplit(".", 1)[-1] in ("pdf", "csv", "xlsx", "xls", "png", "jpg", "jpeg"):
                out.append(os.path.join(full, n))
        return out
    return [full]


def finance_preview_import(path, period=None, **_):
    """SIEMPRE dry-run: previsualiza sin incorporar (regla 60)."""
    paths = _collect_paths(path)
    if not paths:
        raise MCPError(f"no hay archivos para importar en: {path}")
    res = S.ingest(paths, period=period, dry_run=True, actor="mcp")
    res.pop("run_dir", None)   # no exponer rutas absolutas de staging
    return res


# alias documentado en la regla 60 (mismo comportamiento: dry-run)
finance_ingest_statement = finance_preview_import


def finance_get_run(run_id, **_):
    manifest = S._read_json(S._p("staging", run_id, "manifest.json"))
    if not manifest:
        raise MCPError(f"run no encontrado: {run_id}")
    return manifest   # usa _st_public -> cuentas enmascaradas, sin raw_text


def finance_get_review_queue(run_id, **_):
    full = S._read_json(S._p("staging", run_id, "_full.json"))
    if not full:
        raise MCPError(f"run no encontrado: {run_id}")
    queue = [{"statement_id": s["statement_id"],
              "account": S.mask_account(s["account_id"]) if s["account_id"] else None,
              "state": s["state"], "why": s.get("why")}
             for s in full["statements"] if s["state"] != "READY_TO_COMMIT"]
    return {"run_id": run_id, "review_queue": queue}


def finance_apply_corrections(run_id, corrections, **_):
    full_path = S._p("staging", run_id, "_full.json")
    try:
        full = S._read_sealed_full(S._p("staging", run_id))  # SOL-R4-001: no corregir staging alterado
    except PermissionError as e:
        raise MCPError(str(e))
    if not full:
        raise MCPError(f"run no encontrado: {run_id}")
    idx = {t["transaction_id"]: t for s in full["statements"] for t in s["txns"]}
    log = full.setdefault("corrections", [])
    applied, rejected = 0, []
    for c in corrections or []:
        t = idx.get(c.get("transaction_id"))
        if not t:
            rejected.append({"transaction_id": c.get("transaction_id"), "why": "no encontrada"})
            continue
        if c.get("field") not in ("category", "review_status", "type"):
            rejected.append({"transaction_id": c.get("transaction_id"),
                             "why": f"campo no permitido: {c.get('field')}"})
            continue
        log.append({"transaction_id": c["transaction_id"], "field": c["field"],
                    "old_value": t.get(c["field"]), "new_value": c["value"],
                    "reason": c.get("reason", "corrección vía MCP"), "ts": S.now_iso()})
        t[c["field"]] = c["value"]
        if c["field"] == "category":
            t["category_source"] = "manual"  # R6-A-004: procedencia real de la corrección
        applied += 1
    full["_seal"] = S._seal_staging(full)   # vía sancionada: re-sella (SOL-R2-001/R3-001)
    S._write_json(full_path, full)
    return {"applied": applied, "rejected": rejected}


def finance_approve(run_id, statement_id, reason, **_):
    return S.approve(run_id, statement_id, reason, actor="mcp")


def finance_commit_import(run_id, confirm=False, **_):
    if not confirm:
        raise MCPError("commit requiere confirmación explícita: pasa confirm=true")
    return S.commit(run_id, actor="mcp")


def finance_rollback_import(import_id, confirm=False, **_):
    if not confirm:
        raise MCPError("rollback requiere confirmación explícita: pasa confirm=true")
    return S.rollback(import_id, actor="mcp")


def finance_audit_month(month, **_):
    return S.audit_month(month, actor="mcp")


def finance_close_month(month, **_):
    return S.close_month(month, actor="mcp")


def finance_refresh_excel(**_):
    return S.export_excel()


def finance_resolve_quarantine(transaction_id, reason, confirm=False, **_):
    # R9-OBS-01: paridad CLI/MCP (SOL-004). Muta la base -> confirmación explícita como commit/rollback.
    if not confirm:
        raise MCPError("resolver cuarentena requiere confirmación explícita: pasa confirm=true")
    return S.resolve_quarantine_txn(transaction_id, reason, actor="mcp")


# --------------------------------------------------------------------------
# Registro (name -> {description, input_schema, handler})
# --------------------------------------------------------------------------
def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


TOOLS = {
    "finance_system_status": {
        "description": "Estado e integridad de la base canónica (conteos, versión de esquema).",
        "input_schema": _schema({}), "handler": finance_system_status},
    "finance_list_inbox": {
        "description": "Lista los estados de cuenta pendientes en statements/inbox.",
        "input_schema": _schema({}), "handler": finance_list_inbox},
    "finance_preview_import": {
        "description": "Previsualiza (DRY-RUN) la importación de un archivo o carpeta; no incorpora.",
        "input_schema": _schema({"path": {"type": "string"}, "period": {"type": ["string", "null"]}},
                                ["path"]), "handler": finance_preview_import},
    "finance_ingest_statement": {
        "description": "Alias de preview_import (dry-run): extrae, concilia y deja vista previa.",
        "input_schema": _schema({"path": {"type": "string"}, "period": {"type": ["string", "null"]}},
                                ["path"]), "handler": finance_ingest_statement},
    "finance_get_run": {
        "description": "Manifiesto de un run de staging (cuentas enmascaradas, sin texto original).",
        "input_schema": _schema({"run_id": {"type": "string"}}, ["run_id"]),
        "handler": finance_get_run},
    "finance_get_review_queue": {
        "description": "Statements de un run que requieren revisión/aprobación y por qué.",
        "input_schema": _schema({"run_id": {"type": "string"}}, ["run_id"]),
        "handler": finance_get_review_queue},
    "finance_apply_corrections": {
        "description": "Aplica correcciones de categoría/tipo/review_status al staging (auditadas).",
        "input_schema": _schema({"run_id": {"type": "string"},
                                 "corrections": {"type": "array", "items": {"type": "object"}}},
                                ["run_id", "corrections"]), "handler": finance_apply_corrections},
    "finance_approve": {
        "description": "Aprueba un statement REVIEW_REQUIRED (bloqueos blandos) con razón auditada.",
        "input_schema": _schema({"run_id": {"type": "string"}, "statement_id": {"type": "string"},
                                 "reason": {"type": "string"}},
                                ["run_id", "statement_id", "reason"]), "handler": finance_approve},
    "finance_commit_import": {
        "description": "Incorpora atómicamente un run a la base. REQUIERE confirm=true.",
        "input_schema": _schema({"run_id": {"type": "string"}, "confirm": {"type": "boolean"}},
                                ["run_id"]), "handler": finance_commit_import},
    "finance_rollback_import": {
        "description": "Revierte por completo una importación. REQUIERE confirm=true.",
        "input_schema": _schema({"import_id": {"type": "string"}, "confirm": {"type": "boolean"}},
                                ["import_id"]), "handler": finance_rollback_import},
    "finance_audit_month": {
        "description": "Cobertura y conciliación de un mes (YYYY-MM).",
        "input_schema": _schema({"month": {"type": "string"}}, ["month"]),
        "handler": finance_audit_month},
    "finance_close_month": {
        "description": "Marca un mes como cerrado de forma persistente y auditable.",
        "input_schema": _schema({"month": {"type": "string"}}, ["month"]),
        "handler": finance_close_month},
    "finance_refresh_excel": {
        "description": "Regenera el CSV canónico que consume Excel.",
        "input_schema": _schema({}), "handler": finance_refresh_excel},
    "finance_resolve_quarantine": {
        "description": "Resuelve una cuarentena a nivel movimiento (review_status→ok) con razón "
                       "auditada. REQUIERE confirm=true.",
        "input_schema": _schema({"transaction_id": {"type": "string"}, "reason": {"type": "string"},
                                 "confirm": {"type": "boolean"}},
                                ["transaction_id", "reason"]), "handler": finance_resolve_quarantine},
}


def call_tool(name, arguments):
    """Ejecuta un tool por nombre con argumentos (dict). Devuelve el resultado (dict).

    SOL-R2-005 (enforcement): TODO resultado pasa por services.sanitize_out en este chokepoint —
    ningún tool (presente o futuro) puede devolver bytes de control C0/ANSI ni backticks capaces de
    romper el encapsulado de datos no confiables; el enforcement no es opt-in por handler."""
    spec = TOOLS.get(name)
    if not spec:
        raise MCPError(f"tool desconocido: {name}")
    return S.sanitize_out(spec["handler"](**(arguments or {})))


def list_tools():
    return [{"name": n, "description": t["description"], "inputSchema": t["input_schema"]}
            for n, t in TOOLS.items()]
