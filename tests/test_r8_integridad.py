# -*- coding: utf-8 -*-
"""
test_r8_integridad.py — Regresión de integridad ronda 8 (código nuevo de ronda 7).

- R8-A-001: conciliación NO tautológica en estrategia saldo (saldo corrupto / fila perdida -> dudoso/BLOCKED).
- R8-A-002: identificación de cuenta por límite de palabra + ambigüedad (no 'Nu' en 'anual').
- R8-A-003: cuarentena por mes de date_op -> close_month del mes real la ve.
- R8-A-004: monto de cuarentena estimado (no $0) cuando el importe es dudoso.
- R8-A-005: _untrust quita controles C0/ANSI.
"""
import os, sys, json, shutil, sqlite3, tempfile

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fresh():
    d = tempfile.mkdtemp(prefix="r8-")
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


RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def case_saldo_not_tautological():
    # R8-A-001: saldo intermedio corrupto -> importe declarado no cuadra con el delta -> dudoso.
    root = fresh(); load(root)
    from finance.extractors.bank_templates import parse_bank
    # estrategia 'saldo' (nu): -100 -100 -100 (saldos 900,800,700 desde 1000). Corrompemos el saldo
    # intermedio 800->850 -> el importe declarado no cuadra con el delta -> importe_dudoso.
    text = ("GBM\n2026-06-01 A 100.00 900.00\n2026-06-02 B 100.00 850.00\n2026-06-03 C 100.00 700.00")
    rows, meta, warn = parse_bank(text, "gbm", opening=1000.0)
    dud = any("importe_dudoso" in (r.get("_flags") or []) for r in rows)
    check("R8-A-001 saldo corrupto -> importe_dudoso (no telescopia)", dud,
          f"flags={[r.get('_flags') for r in rows]}")


def case_saldo_missing_row_blocks():
    # R8-A-001: una fila perdida (sin decimales, no capturada) hace que la conciliación NO cuadre.
    root = fresh(); S = load(root)
    import openpyxl  # noqa
    from reportlab.pdfgen import canvas
    p = os.path.join(root, "fixtures", "bbva.pdf"); c = canvas.Canvas(p); y = 780
    # opening 1000; 3 movimientos reales pero el 2º sin decimales (se pierde). closing declarado 700.
    for ln in ["BBVA Bancomer estado", "Saldo inicial 1,000.00",
               "2026-06-01 COMPRA A 100.00 900.00",
               "2026-06-02 RETIRO 100 800.00",     # sin decimales en el importe
               "2026-06-03 COMPRA C 100.00 700.00",
               "Saldo final 700.00"]:
        c.drawString(40, y, ln); y -= 22
    c.showPage(); c.save()
    S.ingest([p], dry_run=True, run_id="m")
    d = json.load(open(os.path.join(root, "staging", "m", "_full.json")))["statements"][0]
    # sin la fila perdida, Σimportes != closing-opening -> conciliación no OK
    blocked = d["reconciliation"]["result"] != "OK" or d["state"] != "READY_TO_COMMIT"
    check("R8-A-001 fila perdida -> conciliación no cuadra (bloquea)", blocked,
          f"recon={d['reconciliation']['result']} state={d['state']}")


def case_account_word_boundary():
    # R8-A-002: 'Nu' no debe casar dentro de 'anual'; un estado Ualá no debe ir a nu_debito.
    root = fresh(); S = load(root)
    acc, how = S.identify_account({}, "PAGO ANUAL SERVICIOS\nCOMPRA MANUAL", header="Ualá estado de cuenta")
    check("R8-A-002 identificación por límite de palabra + cabecera", acc == "uala_tdc",
          f"cuenta={acc} how={how}")


def case_account_ambiguous_blocks():
    # R8-A-002: empate REAL al tope (dos bancos igual de presentes) -> no identifica (bloquea).
    root = fresh(); S = load(root)
    # R21: 'BBVA' a secas dejó de ser hint del débito (lo comparte con la tarjeta del mismo emisor);
    # el empate se arma con 'Bancomer', que sigue siéndolo.
    acc, how = S.identify_account({}, "", header="Estado Bancomer y tambien Ualá")  # bbva=1, uala=1
    check("R8-A-002 empate al tope -> account_not_identified", acc is None,
          f"cuenta={acc} how={how}")


def case_quarantine_by_dateop():
    # R8-A-003: estado con period_start en mayo pero movimiento con date_op en junio -> cuarentena en junio;
    # close_month('2026-06') debe verla.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "q.csv")
    open(p, "w").write("# account_id: bbva_debito\n# currency: MXN\n# period_start: 2026-05-20\n"
                       "# period_end: 2026-06-19\n# opening_balance: 0.00\n# closing_balance: 0.00\n"
                       "# declared_credits: 0.00\n# declared_debits: 0.00\n# declared_count: 1\n"
                       "fecha,descripcion,monto,ref\n2026-06-25,COMPRA,ILEGIBLE,J1\n")
    S.ingest([p], dry_run=True, run_id="q"); S.commit("q")
    months = [r["month"] for r in q(root, "select month from quarantine")]
    r = S.close_month("2026-06")
    blocked = any(pp.get("status") == "cuarentena" for pp in r["pending"])
    check("R8-A-003 cuarentena por date_op -> close_month del mes real la ve",
          "2026-06" in months and blocked, f"months={months} pending_cuarentena={blocked}")


def case_quarantine_amount_estimate():
    # R8-A-004: importe dudoso -> monto estimado != 0.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "d.csv")
    open(p, "w").write("# account_id: bbva_debito\n# currency: MXN\n# period_start: 2026-06-01\n"
                       "# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: 0.00\n"
                       "# declared_credits: 0.00\n# declared_debits: 0.00\n# declared_count: 1\n"
                       "fecha,descripcion,monto,ref\n2026-06-07,COMPRA RARA 1234.56 basura,ILEGIBLE,A1\n")
    S.ingest([p], dry_run=True, run_id="d"); S.commit("d")
    amt = q(root, "select coalesce(sum(amount_minor),0) a from quarantine")[0]["a"]
    check("R8-A-004 monto de cuarentena estimado != 0 en dudoso", amt > 0,
          f"amount_minor={amt}")


def case_untrust_strips_controls():
    root = fresh(); load(root)
    from finance import services as S
    out = S._untrust("PAGO \x1b[2J\x1b[31m HACK \x00 \x07 fin")
    ok = "\x1b" not in out and "\x00" not in out and "\x07" not in out and "PAGO" in out
    check("R8-A-005 _untrust quita controles C0/ANSI", ok, f"out={out!r}")


def case_close_month_blocks_movement_cuarentena():
    # R8-A-001 (retest sol 2026-07-21): close_month debe bloquear también con cuarentena a nivel
    # MOVIMIENTO (review_status='cuarentena', supersession ambigua), no solo a nivel statement.
    root = fresh(); S = load(root)
    def w(name, rows, close, debits):
        p = os.path.join(root, "fixtures", name)
        open(p, "w").write(f"# account_id: bbva_debito\n# currency: MXN\n# period_start: 2026-06-01\n"
                           f"# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: {close}\n"
                           f"# declared_credits: 0.00\n# declared_debits: {debits}\n# declared_count: {len(rows)}\n"
                           "fecha,descripcion,monto,ref\n" + "".join(f"{f},{d},{m},{r}\n" for f, d, m, r in rows))
        return p
    s1 = w("s1.csv", [("2026-06-07", "STARBUCKS", "-100.00", ""), ("2026-06-07", "STARBUCKS", "-100.00", "")],
           "-200.00", "-200.00")
    S.ingest([s1], dry_run=True, run_id="s1"); S.commit("s1")
    s2 = w("s2.csv", [("2026-06-07", "STARBUCKS", "-100.00", ""), ("2026-06-07", "STARBUCKS", "-120.00", "")],
           "-220.00", "-220.00")
    S.ingest([s2], dry_run=True, run_id="s2"); S.commit("s2")
    n_cuar = q(root, "select count(*) c from transactions where review_status='cuarentena'")[0]["c"]
    r = S.close_month("2026-06")
    blocked = any(p.get("status") == "cuarentena" and p.get("account") == "bbva_debito" for p in r["pending"])
    status = q(root, "select status from month_close where account_id='bbva_debito' and month='2026-06'")
    not_closed = not status or status[0]["status"] != "closed"
    check("R8-A-001 close_month bloquea con cuarentena a nivel movimiento",
          n_cuar >= 1 and blocked and not_closed,
          f"n_cuarentena={n_cuar} blocked={blocked} pending={[p.get('status') for p in r['pending']]}")


def case_ambiguous_feeds_review_contract():
    # SOL-R2-006 (1524): la supersession ambigua (committeada, posible doble-conteo) DEBE alimentar
    # el contrato/dashboard: review_txns > 0.
    root = fresh(); S = load(root)
    def w(name, rows, **kw):
        p = os.path.join(root, "fixtures", name)
        d = dict(close="0.00", debits="0.00", n=len(rows)); d.update(kw)
        open(p, "w").write(f"# account_id: bbva_debito\n# currency: MXN\n# period_start: 2026-06-01\n"
                           f"# period_end: 2026-06-30\n# opening_balance: 0.00\n# closing_balance: {d['close']}\n"
                           f"# declared_credits: 0.00\n# declared_debits: {d['debits']}\n# declared_count: {d['n']}\n"
                           "fecha,descripcion,monto,ref\n" + "".join(f"{f},{de},{m},{r}\n" for f, de, m, r in rows))
        return p
    s1 = w("s1.csv", [("2026-06-07", "STARBUCKS", "-100.00", ""), ("2026-06-07", "STARBUCKS", "-100.00", "")],
           close="-200.00", debits="-200.00")
    S.ingest([s1], dry_run=True, run_id="s1"); S.commit("s1")
    s2 = w("s2.csv", [("2026-06-07", "STARBUCKS", "-100.00", ""), ("2026-06-07", "STARBUCKS", "-120.00", "")],
           close="-220.00", debits="-220.00")
    S.ingest([s2], dry_run=True, run_id="s2"); S.commit("s2")
    contract = json.load(open(os.path.join(root, "data", "exports", "_contract.json")))
    check("SOL-R2-006 supersession ambigua alimenta review_txns del contrato",
          contract.get("review_txns", 0) >= 1 and contract.get("review_amount_minor", 0) > 0,
          f"review_txns={contract.get('review_txns')} monto={contract.get('review_amount_minor')}")


if __name__ == "__main__":
    for fn in (case_saldo_not_tautological, case_saldo_missing_row_blocks, case_account_word_boundary,
               case_account_ambiguous_blocks, case_quarantine_by_dateop, case_quarantine_amount_estimate,
               case_untrust_strips_controls, case_ambiguous_feeds_review_contract,
               case_close_month_blocks_movement_cuarentena):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
