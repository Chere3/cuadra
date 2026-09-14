# -*- coding: utf-8 -*-
"""
test_r12_rollback_correcciones.py — R12-D-001: una importación CON correcciones debe poder
deshacerse.

`rollback` limpiaba `transfer_links`, `transaction_sources` y las supersesiones (`dup_of`) antes
de borrar los movimientos, pero NO `corrections`, que también referencia `transactions`. Con una
sola corrección aplicada el DELETE reventaba con FOREIGN KEY constraint failed y la importación
quedaba IRREVERSIBLE — lo contrario de la invariante 00. La transacción hacía ROLLBACK, así que
no había corrupción, pero tampoco había forma de deshacer.

Cifras y datos FICTICIOS.
"""
import os, sys, json, shutil, sqlite3, tempfile

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def fresh():
    d = tempfile.mkdtemp(prefix="r12rb-")
    for sub in ("src", "config", "schemas"):
        shutil.copytree(os.path.join(SRC_ROOT, sub), os.path.join(d, sub))
    shutil.copytree(os.path.join(SRC_ROOT, "fixtures", "sanitized"),
                    os.path.join(d, "fixtures", "sanitized"))
    for sub in ("data/exports", "data/backups", "staging", "statements/inbox", "statements/raw"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def load(root):
    os.environ["FINANCE_ROOT"] = root
    for n in list(sys.modules):
        if n == "finance" or n.startswith("finance."):
            del sys.modules[n]
    sys.path.insert(0, os.path.join(root, "src"))
    from finance import services as S, config as C
    C.reset_cache()
    return S


def _corregir(S, run_id, tid, campo, valor):
    """Vía sancionada (cli.cmd_correct): verifica el sello previo y re-sella."""
    run_dir = S._p("staging", run_id)
    full = S._read_sealed_full(run_dir)
    for st in full["statements"]:
        for t in st["txns"]:
            if t["transaction_id"] == tid:
                old = t.get(campo)
                t[campo] = valor
                if campo == "category":
                    t["category_source"] = "manual"
                full.setdefault("corrections", []).append(
                    {"transaction_id": tid, "field": campo, "old_value": old,
                     "new_value": valor, "reason": "prueba", "ts": S.now_iso()})
    full["_seal"] = S._seal_staging(full)
    S._write_json(os.path.join(run_dir, "_full.json"), full)


def case_rollback_con_correcciones():
    root = fresh(); S = load(root)
    # UN solo estado: cada archivo genera su propia importación, y aquí se revierte una.
    fx = os.path.join(root, "fixtures", "sanitized")
    paths = [os.path.join(fx, sorted(os.listdir(fx))[0])]
    S.ingest(paths, dry_run=True, run_id="rb1")
    full = json.load(open(os.path.join(root, "staging", "rb1", "_full.json")))
    tid = next(t["transaction_id"] for st in full["statements"] for t in st["txns"]
               if t["amount_minor"])
    _corregir(S, "rb1", tid, "category", "Otros gastos")
    S.commit("rb1")

    con = sqlite3.connect(S.db_path()); con.row_factory = sqlite3.Row
    antes_tx = con.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    antes_corr = con.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"]
    imp = con.execute("SELECT import_id FROM imports").fetchone()["import_id"]
    con.close()

    try:
        S.rollback(imp)
        err = ""
    except Exception as e:
        err = repr(e)

    con = sqlite3.connect(S.db_path()); con.row_factory = sqlite3.Row
    tx = con.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    corr = con.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"]
    est = con.execute("SELECT state FROM imports WHERE import_id=?", (imp,)).fetchone()["state"]
    con.close()
    doc = S.doctor()
    check("R12-18 una importación con correcciones se puede deshacer",
          not err and tx == 0 and corr == 0 and est == "ROLLED_BACK" and doc["ok"],
          f"error={err} tx={antes_tx}->{tx} corr={antes_corr}->{corr} estado={est} doctor={doc['ok']}")


def case_auditoria_sobrevive():
    """El rastro no se pierde: el evento de auditoría de la corrección sigue registrado."""
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    paths = [os.path.join(fx, sorted(os.listdir(fx))[0])]
    S.ingest(paths, dry_run=True, run_id="rb2")
    full = json.load(open(os.path.join(root, "staging", "rb2", "_full.json")))
    tid = next(t["transaction_id"] for st in full["statements"] for t in st["txns"]
               if t["amount_minor"])
    _corregir(S, "rb2", tid, "category", "Otros gastos")
    S.commit("rb2")
    con = sqlite3.connect(S.db_path()); con.row_factory = sqlite3.Row
    imp = con.execute("SELECT import_id FROM imports").fetchone()["import_id"]
    con.close()
    S.rollback(imp)
    con = sqlite3.connect(S.db_path()); con.row_factory = sqlite3.Row
    # R12-E-010: antes se contaban eventos de ROLLBACK y `total > 1`, así que el test pasaba
    # aunque se borrase la auditoría de la corrección. Ahora se exige el evento de la CORRECCIÓN
    # (que es lo que el nombre promete) además del de rollback.
    ev_rollback = con.execute("SELECT COUNT(*) c FROM audit_events WHERE action LIKE '%rollback%'").fetchone()["c"]
    ev_correct = con.execute("SELECT COUNT(*) c FROM audit_events WHERE action='correct'").fetchone()["c"]
    con.close()
    check("R12-19 tras el rollback sobrevive la auditoría de la corrección aplicada",
          ev_rollback >= 1 and ev_correct >= 1,
          f"eventos_rollback={ev_rollback} eventos_correct={ev_correct}")


if __name__ == "__main__":
    for fn in (case_rollback_con_correcciones, case_auditoria_sobrevive):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
