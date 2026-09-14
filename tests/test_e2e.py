# -*- coding: utf-8 -*-
"""
test_e2e.py — Importación ficticia INTEGRAL de principio a fin:
dry-run -> auto-commit -> export -> idempotencia -> rollback -> doctor.
No usa datos reales: monta su propio FINANCE_ROOT temporal.
"""
import os
import sys
import glob
import shutil
import tempfile

SUBSYS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SUBSYS, "src"))

# R12-A-003: el E2E corría sobre el proyecto real y `clean()` borra data/finance.sqlite*, así que
# CUALQUIER corrida de la suite destruía la base canónica del usuario. Ahora monta un root temporal
# (copia de config/schemas/fixtures) e IGNORA a propósito FINANCE_ROOT del entorno: ninguna prueba
# debe poder escribir sobre datos reales.
ROOT = tempfile.mkdtemp(prefix="fin_e2e_")
for _d in ("config", "schemas", "fixtures/sanitized"):
    shutil.copytree(os.path.join(SUBSYS, _d), os.path.join(ROOT, _d))
for _d in ("data/exports", "data/backups", "staging", "statements/raw", "statements/inbox"):
    os.makedirs(os.path.join(ROOT, _d), exist_ok=True)
os.environ["FINANCE_ROOT"] = ROOT

from finance import services as S, db, config  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  {'✔' if cond else '✘'} {name}")
    if not cond:
        FAILS.append(name)


def clean():
    for p in glob.glob(os.path.join(ROOT, "data", "finance.sqlite*")):
        os.remove(p)
    for d in glob.glob(os.path.join(ROOT, "staging", "run_*")):
        shutil.rmtree(d, ignore_errors=True)
    inbox = os.path.join(ROOT, "statements", "inbox")
    os.makedirs(inbox, exist_ok=True)
    for f in glob.glob(os.path.join(inbox, "*")):
        os.remove(f)
    for f in glob.glob(os.path.join(ROOT, "fixtures", "sanitized", "*.csv")):
        shutil.copy(f, inbox)
    config.reset_cache()


def counts():
    return S.status()["counts"]


def main():
    print("== E2E: importación ficticia integral ==\n")
    clean()
    inbox = os.path.join(ROOT, "statements", "inbox")

    # 1) DRY-RUN
    print("[1] dry-run")
    dry = S.ingest(S.cli_paths(inbox) if hasattr(S, "cli_paths") else _paths(inbox),
                   period="2026-06", dry_run=True)
    states = list(dry["estados"].values())
    check("2 estados detectados", dry["files"] == 2)
    check("ambos READY_TO_COMMIT", all(v == "READY_TO_COMMIT" for v in states))
    check("6 movimientos", dry["n_transactions"] == 6)
    check("1 transferencia detectada", dry["transferencias"] == 1)
    check("sin dudosos", dry["movimientos_dudosos"] == 0)
    check("no committeado (dry-run)", dry.get("auto_committed") is False)
    check("BD sigue vacía", counts()["transactions"] == 0)

    # 2) INGEST + COMMIT (modo revisión: commit explícito; agnóstico a la política)
    print("[2] ingest + commit")
    real = S.ingest(_paths(inbox), period="2026-06", dry_run=False)
    if not real.get("auto_committed"):
        S.commit(real["run_id"])
    c = counts()
    check("2 statements en BD", c["statements"] == 2)
    check("6 transacciones en BD", c["transactions"] == 6)
    check("1 transfer_link", c["transfer_links"] == 1)
    check("reconciliation_checks=2", c["reconciliation_checks"] == 2)

    # 3) EXPORT
    print("[3] export a CSV (contrato Power Query)")
    exp = S.export_excel()
    check("CSV export existe", os.path.exists(exp["path"]))
    check("export 6 filas", exp["n_rows"] == 6)
    with open(exp["path"], encoding="utf-8") as f:
        head = f.readline().strip()
    check("encabezado = contrato", head == ",".join(S.EXPORT_COLUMNS))

    # 4) IDEMPOTENCIA
    print("[4] idempotencia (re-importar lo mismo)")
    r4 = S.ingest(_paths(inbox), period="2026-06", dry_run=False)
    if not r4.get("auto_committed"):
        S.commit(r4["run_id"])
    c2 = counts()
    check("transacciones sin cambio (6)", c2["transactions"] == 6)
    check("statements sin cambio (2)", c2["statements"] == 2)

    # 5) ROLLBACK
    print("[5] rollback de una importación")
    con = db.connect(S.db_path())
    imp = con.execute("SELECT import_id, account_id FROM imports WHERE rolled_back=0 AND account_id='nu_debito' LIMIT 1").fetchone()
    con.close()
    rb = S.rollback(imp["import_id"])
    c3 = counts()
    check("rollback quitó la transacción de Nu", c3["transactions"] == 5)
    con = db.connect(S.db_path())
    committed_stmts = con.execute("SELECT COUNT(*) c FROM statements WHERE status='COMMITTED'").fetchone()["c"]
    con.close()
    check("statement de Nu marcado ROLLED_BACK (1 committed)", committed_stmts == 1)
    check("transfer_link eliminado con el rollback", c3["transfer_links"] == 0)

    # 6) DOCTOR
    print("[6] doctor")
    doc = S.doctor()
    check("integridad OK", doc["ok"])

    print("\n== RESULTADO ==", "✔ E2E COMPLETO SIN FALLOS" if not FAILS else f"✘ FALLOS: {FAILS}")
    shutil.rmtree(ROOT, ignore_errors=True)
    return 0 if not FAILS else 1


def _paths(d):
    out = []
    for ext in ("csv", "xlsx", "pdf", "png", "jpg", "jpeg"):
        out += glob.glob(os.path.join(d, f"*.{ext}"))
    return sorted(out)


if __name__ == "__main__":
    raise SystemExit(main())
