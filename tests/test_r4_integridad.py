# -*- coding: utf-8 -*-
"""
test_r4_integridad.py — Regresión de integridad ronda 4.

Oráculos (auditores externos):
- SOL-R4-001: reseal legitima staging alterado -> approve sobre _full.json manipulado
  debe BLOQUEAR (no lavar la alteración recomputando el sello).
- SOL-R4-002: colisión de bank_ref elimina cargo legítimo -> dos movimientos DISTINTOS
  (misma cuenta/periodo/ref, descripción y monto distintos) deben CONSERVARSE ambos.
- F5-R3-01: corrección SIN bank_ref (mismo día/desc, monto distinto, periodo solapado)
  debe SUPERSEDER a 1 fila.
- Regresión: corrección CON bank_ref sigue superseando a 1 fila.

Se ejecuta como script (sin pytest). Cada caso corre en una copia fresca del árbol.
"""
import os, sys, json, csv, shutil, sqlite3, tempfile
import hashlib as _hl

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fresh():
    d = tempfile.mkdtemp(prefix="r4-")
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


def _csv(root, name, rows, account="bbva_debito", close="0.00",
         credits="0.00", debits="0.00", n=1):
    p = os.path.join(root, "fixtures", name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# account_id: {account}\n# currency: MXN\n# period_start: 2026-06-01\n"
                f"# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: {close}\n"
                f"# declared_credits: {credits}\n# declared_debits: {debits}\n# declared_count: {n}\n")
        f.write("fecha,descripcion,monto,ref\n")
        for (fecha, desc, monto, ref) in rows:
            f.write(f"{fecha},{desc},{monto},{ref}\n")
    return p


def export_rows(root):
    p = os.path.join(root, "data", "exports", "transacciones_excel.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def q(root, sql):
    c = sqlite3.connect(os.path.join(root, "data", "finance.sqlite"))
    c.row_factory = sqlite3.Row
    r = c.execute(sql).fetchall()
    c.close()
    return r


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


# ---------------------------------------------------------------------------
def case_approve_reseal():
    root = fresh(); S = load(root)
    fs = [os.path.join(root, "fixtures", "sanitized", "bbva_2026-06.csv"),
          os.path.join(root, "fixtures", "sanitized", "nu_2026-06.csv")]
    S.ingest(fs, dry_run=True, run_id="x")
    p = os.path.join(root, "staging", "x", "_full.json")
    d = json.load(open(p))
    ids = [s["txns"][0]["transaction_id"] for s in d["statements"]]
    # forja: link falso + statement a REVIEW_REQUIRED, SIN recomputar el sello
    d["transfer_links"] = [{"from": ids[0], "to": ids[1], "kind": "transfer", "confidence": 1.0}]
    d["statements"][0]["state"] = "REVIEW_REQUIRED"
    d["statements"][0]["why"] = []
    open(p, "w").write(json.dumps(d))
    sid = d["statements"][0]["statement_id"]
    blocked = False
    try:
        S.approve("x", sid, "reseal")
        S.commit("x")
    except Exception:
        blocked = True
    links = q(root, "select * from transfer_links")
    check("SOL-R4-001 reseal bloquea", blocked and len(links) == 0,
          f"blocked={blocked} links={len(links)}")


def case_bankref_collision():
    root = fresh(); S = load(root)
    a = _csv(root, "a.csv", [("2026-06-10", "BONO UNO", "7.00", "REUSED")],
             close="7.00", credits="7.00", n=1)
    b = _csv(root, "b.csv", [("2026-06-10", "BONO DOS", "9.00", "REUSED")],
             close="9.00", credits="9.00", n=1)
    S.ingest([a], dry_run=True, run_id="a"); S.commit("a")
    S.ingest([b], dry_run=True, run_id="b"); S.commit("b")
    txns = q(root, "select description_raw, amount_minor, dup_of from transactions order by amount_minor")
    rows = export_rows(root)
    no_dup = all(t["dup_of"] is None for t in txns)
    check("SOL-R4-002 colisión conserva ambos", len(txns) == 2 and no_dup and len(rows) == 2,
          f"txns={len(txns)} dup_of={[t['dup_of'] for t in txns]} export={len(rows)}")


def _correction(ref):
    root = fresh(); S = load(root)
    v1 = _csv(root, "v1.csv", [("2026-06-07", "OXXO TIENDA 1234", "-250.00", ref)],
              close="-250.00", debits="-250.00", n=1)
    S.ingest([v1], dry_run=True, run_id="v1"); S.commit("v1")
    # estado RE-EMITIDO: mismo día/desc, monto corregido -> -350 (statement distinto)
    v2 = _csv(root, "v2.csv", [("2026-06-07", "OXXO TIENDA 1234", "-350.00", ref)],
              close="-350.00", debits="-350.00", n=1)
    S.ingest([v2], dry_run=True, run_id="v2"); S.commit("v2")
    rows = export_rows(root)
    total = sum(int(round(float(r["importe"]) * 100)) for r in rows)
    return rows, total


def case_correction_with_ref():
    rows, total = _correction("A1")
    check("Regresión corrección CON ref -> 1 fila/-350", len(rows) == 1 and total == 35000,
          f"export={len(rows)} total_abs_cent={total}")


def case_correction_no_ref():
    rows, total = _correction("")
    check("F5-R3-01 corrección SIN ref -> 1 fila/-350", len(rows) == 1 and total == 35000,
          f"export={len(rows)} total_abs_cent={total}")


def case_rollback_supersede():
    # SOL-R5-002: rollback de la corrección (que superseded al original) NO debe violar FK,
    # y debe RESTAURAR el movimiento previo como canónico.
    root = fresh(); S = load(root)
    v1 = _csv(root, "v1.csv", [("2026-06-07", "OXXO TIENDA 1234", "-250.00", "A1")],
              close="-250.00", debits="-250.00", n=1)
    S.ingest([v1], dry_run=True, run_id="v1"); S.commit("v1")
    v2 = _csv(root, "v2.csv", [("2026-06-07", "OXXO TIENDA 1234", "-350.00", "A1")],
              close="-350.00", debits="-350.00", n=1)
    S.ingest([v2], dry_run=True, run_id="v2"); S.commit("v2")
    imp_b = q(root, "select import_id from imports where run_id='v2'")[0]["import_id"]
    ok, err = True, ""
    try:
        S.rollback(imp_b)
    except Exception as e:
        ok, err = False, repr(e)
    rows = export_rows(root)
    total = sum(int(round(float(r["importe"]) * 100)) for r in rows)
    active = q(root, "select transaction_id from transactions where dup_of is null")
    check("SOL-R5-002 rollback de corrección reversible + restaura original",
          ok and len(rows) == 1 and total == 25000 and len(active) == 1,
          f"ok={ok} err={err} export={len(rows)} total={total} activos={len(active)}")


def case_false_transfer_prefix():
    # SOL-R5-003: refs cortas '700'/'7' NO deben vincular transferencia por prefijo.
    root = fresh(); S = load(root)
    a = _csv(root, "inc.csv", [("2026-06-10", "REEMBOLSO CLIENTE ACME", "500.00", "700")],
             account="bbva_debito", close="500.00", credits="500.00", n=1)
    b = _csv(root, "exp.csv", [("2026-06-10", "COMPRA FARMACIA GUADALAJARA", "-500.00", "7")],
             account="nu_debito", close="-500.00", debits="-500.00", n=1)
    S.ingest([a, b], dry_run=True, run_id="t"); S.commit("t")
    links = q(root, "select * from transfer_links")
    rows = export_rows(root)
    n_transfer = sum(1 for r in rows if r["es_transferencia"] == "1")
    check("SOL-R5-003 ref corta no vincula transferencia falsa",
          len(links) == 0 and n_transfer == 0,
          f"links={len(links)} export_transfer={n_transfer}")


def case_rollback_month_close():
    # SOL-R5-004: rollback total revierte month_close (no deja fantasma received/OK).
    root = fresh(); S = load(root)
    v1 = _csv(root, "m.csv", [("2026-06-07", "OXXO TIENDA 1234", "-250.00", "A1")],
              close="-250.00", debits="-250.00", n=1)
    S.ingest([v1], dry_run=True, run_id="m"); S.commit("m")
    imp = q(root, "select import_id from imports where run_id='m'")[0]["import_id"]
    S.rollback(imp)
    ntx = q(root, "select count(*) c from transactions")[0]["c"]
    mc = q(root, "select received from month_close where account_id='bbva_debito' and month='2026-06'")
    ghost = bool(mc) and mc[0]["received"] == 1
    check("SOL-R5-004 rollback revierte month_close", ntx == 0 and not ghost,
          f"transactions={ntx} month_close_received={(mc[0]['received'] if mc else 'sin fila')}")


def case_seal_hmac_forgery():
    # SOL-R5-001: forja AUTO-CONSISTENTE (id/fingerprint/sign/reconciliation coherentes, pasa
    # _revalidate) con sello recomputado por el algoritmo PÚBLICO (sha256 sin clave). El sello
    # HMAC con clave la bloquea: no basta conocer el código para re-sellar.
    root = fresh(); S = load(root)
    from finance import ids as I, reconcile as Rr
    v1 = _csv(root, "h.csv", [("2026-06-07", "OXXO TIENDA 1234", "-250.00", "A1")],
              close="-250.00", debits="-250.00", n=1)
    S.ingest([v1], dry_run=True, run_id="h")
    p = os.path.join(root, "staging", "h", "_full.json")
    d = json.load(open(p))
    s = d["statements"][0]; t = s["txns"][0]
    t["amount_minor"] = -2500; t["sign"] = -1  # -250.00 -> -25.00 (subvaluar el gasto)
    t["transaction_id"] = I.transaction_id(t["account_id"], t["statement_id"], t["date_op"],
                                            t["amount_minor"], t["description_raw"],
                                            t.get("bank_ref"), t.get("locator"))
    t["fingerprint"] = I.fingerprint(t["account_id"], t["date_op"], t["amount_minor"],
                                     t.get("description_norm"), t.get("bank_ref"))
    s["closing_minor"] = -2500; s["declared"]["debits_minor"] = -2500
    s["reconciliation"] = Rr.reconcile(s["opening_minor"], s["closing_minor"],
                                        [x for x in s["txns"] if x["amount_minor"] is not None],
                                        s["declared"], S.config.policy(), s["currency"])
    payload = {"statements": d["statements"], "transfer_links": d.get("transfer_links", []),
               "corrections": d.get("corrections", [])}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    d["_seal"] = "seal_" + _hl.sha256(blob.encode("utf-8")).hexdigest()  # sello algoritmo público
    open(p, "w").write(json.dumps(d))
    blocked, err = False, ""
    try:
        S.commit("h")
    except Exception as e:
        blocked, err = True, type(e).__name__
    persisted = q(root, "select amount_minor from transactions")
    got = persisted[0]["amount_minor"] if persisted else None
    check("SOL-R5-001 HMAC bloquea forja auto-consistente", blocked and got != -2500,
          f"blocked={blocked}({err}) persisted={got}")


if __name__ == "__main__":
    for fn in (case_approve_reseal, case_bankref_collision,
               case_correction_with_ref, case_correction_no_ref,
               case_rollback_supersede, case_false_transfer_prefix,
               case_rollback_month_close, case_seal_hmac_forgery):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
