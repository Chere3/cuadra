# -*- coding: utf-8 -*-
"""
test_r10_integridad.py — Regresión ronda 10 (auditoría del ciclo/crédito).

- R10-A-001: reingesta+commit del MISMO archivo ya committeado NO envenena la cuarentena ni bloquea close_month.
- R10-A-002: cuenta 'inverted' (crédito, fuente PDF nativa) CONCILIA (opening/closing también invertidos).
- R10-A-004: un CSV canónico a una cuenta 'inverted' NO se dobla-invierte (solo PDF nativo invierte).
"""
import os, sys, json, shutil, sqlite3, tempfile
from reportlab.pdfgen import canvas

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fresh():
    d = tempfile.mkdtemp(prefix="r10-")
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


def q(root, sql):
    c = sqlite3.connect(os.path.join(root, "data", "finance.sqlite"))
    c.row_factory = sqlite3.Row
    r = c.execute(sql).fetchall(); c.close(); return r


def make_pdf(path, lines):
    c = canvas.Canvas(path); y = 790
    for ln in lines:
        c.drawString(36, y, ln); y -= 20
    c.showPage(); c.save()


RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def case_dup_recommit_no_quarantine():
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "s.csv")
    open(p, "w").write("# account_id: bbva_debito\n# currency: MXN\n# period_start: 2026-06-01\n"
                       "# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: -50.00\n"
                       "# declared_credits: 0.00\n# declared_debits: -50.00\n# declared_count: 1\n"
                       "fecha,descripcion,monto,ref\n2026-06-07,OXXO,-50.00,A1\n")
    S.ingest([p], dry_run=True, run_id="r1"); S.commit("r1")
    # reingesta idéntica (otra sesión) + commit
    S.ingest([p], dry_run=True, run_id="r2"); S.commit("r2")
    quar = q(root, "select count(*) c from quarantine")[0]["c"]
    r = S.close_month("2026-06")
    closeable = "bbva_debito" in r["closed"] and not any(
        pp.get("status") == "cuarentena" for pp in r["pending"])
    check("R10-A-001 reingesta de duplicado no envenena cuarentena ni bloquea cierre",
          quar == 0 and closeable, f"quarantine={quar} closed={r['closed']}")


def case_inverted_credit_reconciles():
    # Tarjeta de crédito por PDF (fuente nativa): deuda 100 -> compra 50 -> pago 30 -> deuda 120.
    # Con la inversión de opening/closing (R10-A-002) debe CONCILIAR.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "vexi.pdf")
    make_pdf(p, [
        "Vexi Amex  Tarjeta de credito",
        "Periodo del 01/07/2026 al 31/07/2026",
        "Saldo inicial 100.00",
        "02/07/2026 COMPRA FARMACIA 50.00",
        "20/07/2026 Pago recibido 30.00",
        "Saldo final 120.00",
    ])
    S.ingest([p], dry_run=True, run_id="v")
    d = json.load(open(os.path.join(root, "staging", "v", "_full.json")))["statements"][0]
    sig = {t["description_norm"]: t["amount_minor"] for t in d["txns"]}
    compra_gasto = any("COMPRA" in k and v == -5000 for k, v in sig.items())
    pago_pos = any("Pago" in k and v == 3000 for k, v in sig.items())
    check("R10-A-002 TDC nativa concilia con opening/closing invertidos",
          d["account_id"] == "vexi_tdc" and compra_gasto and pago_pos and
          d["reconciliation"]["result"] == "OK",
          f"cuenta={d['account_id']} recon={d['reconciliation']['result']} signos={sig}")


def case_canonical_csv_not_double_inverted():
    # CSV canónico (gasto ya negativo) a una cuenta inverted (nu_tdc): NO debe re-invertirse.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "canon.csv")
    open(p, "w").write("# account_id: nu_tdc\n# currency: MXN\n# period_start: 2026-06-01\n"
                       "# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: -100.00\n"
                       "# declared_credits: 0.00\n# declared_debits: -100.00\n# declared_count: 1\n"
                       "fecha,descripcion,monto,ref\n2026-06-07,COMPRA CANONICA,-100.00,A1\n")
    S.ingest([p], dry_run=True, run_id="c")
    d = json.load(open(os.path.join(root, "staging", "c", "_full.json")))["statements"][0]
    amt = d["txns"][0]["amount_minor"] if d["txns"] else None
    check("R10-A-004 CSV canónico a cuenta inverted no se dobla-invierte",
          amt == -10000, f"amount_minor={amt} (esp -10000, gasto sin re-invertir)")


if __name__ == "__main__":
    for fn in (case_dup_recommit_no_quarantine, case_inverted_credit_reconciles,
               case_canonical_csv_not_double_inverted):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
