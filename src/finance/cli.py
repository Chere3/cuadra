# -*- coding: utf-8 -*-
"""
cli.py — Interfaz de línea de comandos. Delega TODO en services (misma capa que el MCP).

Comandos:
  finance ingest <ruta> [--period YYYY-MM] [--commit]
      Sin --commit es dry-run: previsualiza y NO incorpora nada. --commit es el
      interruptor real para ir en vivo (F-02). Omitir --dry-run no basta para
      incorporar: --dry-run es el default y existe solo como alias explícito.
  finance review <run_id>
  finance correct <run_id> <archivo_correcciones.json>
  finance commit <run_id>
  finance rollback <import_id>
  finance audit-month <YYYY-MM>
  finance refresh-excel
  finance status
  finance doctor
"""
from __future__ import annotations
import sys
import os
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finance import services as S  # noqa: E402


def _paths(arg):
    if os.path.isdir(arg):
        out = []
        for ext in ("pdf", "csv", "xlsx", "xls", "png", "jpg", "jpeg"):
            out += glob.glob(os.path.join(arg, f"*.{ext}"))
        return sorted(out)
    return [arg]


def cmd_ingest(a):
    paths = _paths(a.path)
    if not paths:
        print("No hay archivos para importar en:", a.path)
        return 2
    dry = not a.commit   # F-02: seguro por defecto (dry-run); --commit para ir en vivo
    res = S.ingest(paths, period=a.period, dry_run=dry)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(f"\nVista previa: {res['run_dir']}/preview.md")
    if dry:
        print(f"Para incorporar: finance commit {res['run_id']}   (o re-corre con --commit)")
    return 0


def cmd_review(a):
    run_dir = S._p("staging", a.run_id)
    pv = os.path.join(run_dir, "preview.md")
    if os.path.exists(pv):
        print(open(pv, encoding="utf-8").read())
        return 0
    print("run no encontrado:", a.run_id)
    return 2


_CORRECT_ALLOWED = ("category", "review_status", "type")


def cmd_correct(a):
    # aplica correcciones de campos NO contables al staging y deja rastro auditable (SOL-005).
    # NUNCA fecha/importe/moneda/id; campos no permitidos (p. ej. 'tag') se RECHAZAN explícito.
    full_path = S._p("staging", a.run_id, "_full.json")
    try:
        full = S._read_sealed_full(S._p("staging", a.run_id))  # SOL-R4-001: no corregir staging alterado
    except PermissionError as e:
        print(str(e))
        return 2
    corr = S._read_json(a.file)
    if not full or not corr:
        print("falta run o archivo de correcciones")
        return 2
    idx = {t["transaction_id"]: t for s in full["statements"] for t in s["txns"]}
    log = full.setdefault("corrections", [])
    applied, rejected = 0, []
    for c in corr.get("corrections", []):
        tid = c.get("transaction_id")
        t = idx.get(tid)
        if not t:
            rejected.append((tid, "transacción no encontrada en el run"))
            continue
        field = c.get("field")
        if field not in _CORRECT_ALLOWED:
            rejected.append((tid, f"campo no permitido: {field!r}"))
            continue
        old = t.get(field)
        t[field] = c["value"]
        # R6-A-004: una categoría corregida a mano es procedencia 'manual' (y así entra al
        # histórico verificado; antes quedaba con el source viejo -> no se "aprendía" y mentía).
        if field == "category":
            t["category_source"] = "manual"
        log.append({"transaction_id": tid, "field": field, "old_value": old,
                    "new_value": c["value"], "reason": c.get("reason", "corrección manual"),
                    "ts": S.now_iso()})
        applied += 1
    full["_seal"] = S._seal_staging(full)   # vía sancionada: re-sella (SOL-R2-001/R3-001)
    S._write_json(full_path, full)
    print(f"correcciones aplicadas: {applied}")
    if rejected:
        print("rechazadas (no se aplican en silencio):")
        for tid, why in rejected:
            print(f"  - {tid}: {why}")
    return 0


def cmd_commit(a):
    res = S.commit(a.run_id)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    if res.get("skipped"):
        ids = ", ".join(x["statement_id"] for x in res["skipped"])
        print(f"\n⚠ {len(res['skipped'])} statement(s) NO incorporados (requieren revisión/aprobación): {ids}")
    return 0


def cmd_approve(a):
    res = S.approve(a.run_id, a.statement_id, a.reason)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_cerrar_mes(a):
    res = S.close_month(a.month)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0 if res["all_closed"] else 1


def cmd_rollback(a):
    res = S.rollback(a.import_id)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_resolver_cuarentena(a):
    # R9-OBS-01: vía ligera y auditada para resolver una cuarentena a nivel movimiento ya
    # committeada, sin rollback+reimportación del estado completo.
    res = S.resolve_quarantine_txn(a.transaction_id, a.reason)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_audit_month(a):
    print(json.dumps(S.audit_month(a.month), ensure_ascii=False, indent=2))
    return 0


def cmd_refresh_excel(a):
    print(json.dumps(S.export_excel(), ensure_ascii=False, indent=2))
    return 0


def cmd_status(a):
    print(json.dumps(S.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(a):
    res = S.doctor()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


def build_parser():
    p = argparse.ArgumentParser(prog="finance", description="Ingestión de estados de cuenta (canónico + Excel)")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("ingest"); g.add_argument("path"); g.add_argument("--period")
    g.add_argument("--commit", action="store_true", help="incorpora en vivo (por defecto: dry-run)")
    # Redundante por diseño: nada lo lee (cmd_ingest decide con `not a.commit`). Se conserva como
    # alias explícito del comportamiento por defecto para no romper scripts que ya lo pasan.
    g.add_argument("--dry-run", action="store_true", help="(por defecto, redundante) solo previsualiza, no incorpora")
    g.set_defaults(fn=cmd_ingest)
    g = sub.add_parser("review"); g.add_argument("run_id"); g.set_defaults(fn=cmd_review)
    g = sub.add_parser("correct"); g.add_argument("run_id"); g.add_argument("file"); g.set_defaults(fn=cmd_correct)
    g = sub.add_parser("approve"); g.add_argument("run_id"); g.add_argument("statement_id")
    g.add_argument("--reason", required=True, help="razón de la aprobación (auditada)"); g.set_defaults(fn=cmd_approve)
    g = sub.add_parser("commit"); g.add_argument("run_id"); g.set_defaults(fn=cmd_commit)
    g = sub.add_parser("rollback"); g.add_argument("import_id"); g.set_defaults(fn=cmd_rollback)
    g = sub.add_parser("resolver-cuarentena"); g.add_argument("transaction_id")
    g.add_argument("--reason", required=True, help="razón de la resolución (auditada)")
    g.set_defaults(fn=cmd_resolver_cuarentena)
    g = sub.add_parser("audit-month"); g.add_argument("month"); g.set_defaults(fn=cmd_audit_month)
    g = sub.add_parser("cerrar-mes"); g.add_argument("month"); g.set_defaults(fn=cmd_cerrar_mes)
    sub.add_parser("refresh-excel").set_defaults(fn=cmd_refresh_excel)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
