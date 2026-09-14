# -*- coding: utf-8 -*-
"""
test_r7_cuarentena.py — SOL-R2-006: la cuarentena (lo retenido/no committeado) debe ser VISIBLE.

- Un estado bloqueado queda en la tabla quarantine con conteo/monto.
- export_excel propaga quarantine_txns/amount al contrato (_contract.json) y a cuarentena.csv.
- close_month se BLOQUEA (queda 'pending' con detalle) mientras haya cuarentena para el mes.
- Al committear luego el estado (aprobado), la cuarentena se limpia.
"""
import os, sys, json, csv, shutil, sqlite3, tempfile

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fresh():
    d = tempfile.mkdtemp(prefix="r7-")
    for sub in ("src", "config", "fixtures", "schemas"):
        shutil.copytree(os.path.join(SRC_ROOT, sub), os.path.join(d, sub))
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
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


def _csv(root, name, rows, account="bbva_debito", close="0.00", credits="0.00", debits="0.00", n=1):
    p = os.path.join(root, "fixtures", name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# account_id: {account}\n# currency: MXN\n# period_start: 2026-06-01\n"
                f"# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: {close}\n"
                f"# declared_credits: {credits}\n# declared_debits: {debits}\n# declared_count: {n}\n")
        f.write("fecha,descripcion,monto,ref\n")
        for (fecha, desc, monto, ref) in rows:
            f.write(f"{fecha},{desc},{monto},{ref}\n")
    return p


def q(root, sql):
    c = sqlite3.connect(os.path.join(root, "data", "finance.sqlite"))
    c.row_factory = sqlite3.Row
    r = c.execute(sql).fetchall(); c.close(); return r


RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def case_quarantine_visible():
    root = fresh(); S = load(root)
    # estado con importe ilegible -> importe_dudoso -> bloquea (REVIEW_REQUIRED) -> cuarentena
    v = _csv(root, "dud.csv", [("2026-06-07", "COMPRA", "ILEGIBLE", "A1")],
             close="0.00", debits="0.00", n=1)
    S.ingest([v], dry_run=True, run_id="d"); res = S.commit("d")
    # tabla quarantine
    qz = q(root, "select statement_id, n_txns, amount_minor, reason from quarantine")
    in_table = len(qz) == 1
    # contrato
    contract = json.load(open(os.path.join(root, "data", "exports", "_contract.json")))
    in_contract = contract.get("quarantine_statements", 0) >= 1
    # csv
    csv_exists = os.path.exists(os.path.join(root, "data", "exports", "cuarentena.csv"))
    check("SOL-R2-006 cuarentena visible (tabla+contrato+csv)",
          in_table and in_contract and csv_exists,
          f"tabla={len(qz)} contrato={contract.get('quarantine_statements')} csv={csv_exists}")


def case_close_month_blocked():
    root = fresh(); S = load(root)
    # un estado bueno (committea) + otro bloqueado (cuarentena) en el mismo mes/cuenta
    ok = _csv(root, "ok.csv", [("2026-06-05", "NOMINA", "1000.00", "N1")],
              close="1000.00", credits="1000.00", n=1)
    S.ingest([ok], dry_run=True, run_id="ok"); S.commit("ok")
    bad = _csv(root, "bad.csv", [("2026-06-10", "COMPRA", "ILEGIBLE", "B1")], n=1)
    S.ingest([bad], dry_run=True, run_id="bad"); S.commit("bad")
    r = S.close_month("2026-06")
    # el mes NO debe quedar all_closed; bbva_debito queda pending por cuarentena
    blocked = (not r["all_closed"]) and any(p.get("status") == "cuarentena" for p in r["pending"])
    status = q(root, "select status from month_close where account_id='bbva_debito' and month='2026-06'")
    not_closed = not status or status[0]["status"] != "closed"
    check("SOL-R2-006 close_month bloquea con cuarentena", blocked and not_closed,
          f"all_closed={r['all_closed']} pending={r['pending']}")


def case_quarantine_cleared_on_commit():
    root = fresh(); S = load(root)
    # WARNING de cobertura (declared_count=2 pero 1 movimiento) -> REVIEW_REQUIRED (APROBABLE) -> cuarentena.
    v = _csv(root, "rev.csv", [("2026-06-07", "COMPRA OXXO", "-100.00", "A1")],
             close="-100.00", debits="-100.00", n=2)
    S.ingest([v], dry_run=True, run_id="r")
    st = json.load(open(os.path.join(root, "staging", "r", "_full.json")))["statements"][0]
    assert st["state"] == "REVIEW_REQUIRED", f"precondición: se esperaba REVIEW_REQUIRED, no {st['state']}"
    S.commit("r")   # bloqueado -> queda en cuarentena, 0 transacciones
    before = q(root, "select count(*) c from quarantine")[0]["c"]
    committed_before = q(root, "select count(*) c from transactions")[0]["c"]
    # aprobar y committear el MISMO estado -> debe entrar y LIMPIAR su cuarentena
    S.approve("r", st["statement_id"], "revisado")
    S.commit("r")
    after = q(root, "select count(*) c from quarantine")[0]["c"]
    committed = q(root, "select count(*) c from transactions")[0]["c"]
    check("SOL-R2-006 cuarentena se limpia SOLO al committear (test no trivial)",
          before == 1 and committed_before == 0 and after == 0 and committed == 1,
          f"before={before} committed_before={committed_before} after={after} committed={committed}")


if __name__ == "__main__":
    for fn in (case_quarantine_visible, case_close_month_blocked, case_quarantine_cleared_on_commit):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
