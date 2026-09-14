# -*- coding: utf-8 -*-
"""
test_regresion_auditoria.py — Reproduce y verifica los hallazgos de las auditorías
externas (audits/fable5 + audits/sol). Cada test AFIRMA el comportamiento CORREGIDO,
de modo que falla en el estado 'rojo' (pre-fix) y pasa cuando el hallazgo queda resuelto.

Cada corrida usa un FINANCE_ROOT temporal aislado (copia de config/schemas/fixtures)
para no tocar la base real ni acumular estado entre pruebas.
"""
from __future__ import annotations
import os
import sys
import csv
import json
import shutil
import sqlite3
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSYS = os.path.dirname(HERE)                       # raíz del subsistema (copia de trabajo)
sys.path.insert(0, os.path.join(SUBSYS, "src"))


def _fresh_root():
    """Crea un FINANCE_ROOT temporal con config/schemas y estructura mínima."""
    root = tempfile.mkdtemp(prefix="finreg_")
    for d in ("config", "schemas", "fixtures/sanitized"):
        shutil.copytree(os.path.join(SUBSYS, d), os.path.join(root, d))
    for d in ("data/exports", "staging", "statements/raw"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    # accounts.yml está fuera de Git; en un clone limpio los tests que lo mutan parten del ejemplo.
    accts = os.path.join(root, "config", "accounts.yml")
    if not os.path.exists(accts):
        shutil.copy(os.path.join(root, "config", "accounts.example.yml"), accts)
    return root


def _services(root):
    os.environ["FINANCE_ROOT"] = root
    # recargar módulos que cachean rutas/config
    for m in list(sys.modules):
        if m == "finance" or m.startswith("finance."):
            del sys.modules[m]
    from finance import services as S
    from finance import config as C
    C.reset_cache()
    return S


def _write_csv(root, name, header_comments, header, rows):
    p = os.path.join(root, "fixtures/sanitized", name)
    with open(p, "w", encoding="utf-8") as f:
        for c in header_comments:
            f.write(f"# {c}\n")
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return p


def _export_rows(S):
    p = S._p("data", "exports", "transacciones_excel.csv")
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------
# F-01 — Doble conteo de estados reemitidos/corregidos
# --------------------------------------------------------------------------
def test_f01_no_doble_conteo_estado_reemitido():
    root = _fresh_root()
    S = _services(root)
    bbva = os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")

    S.ingest([bbva], dry_run=True, run_id="run_a")
    S.commit("run_a")
    rows1 = _export_rows(S)
    total1 = sum(int(round(float(r["importe"]) * 100)) *
                 (1 if r["ingreso_gasto"] == "Ingreso" else -1) for r in rows1)

    # estado reemitido: mismos movimientos, archivo con bytes distintos
    reemitido = os.path.join(root, "fixtures/sanitized/bbva_2026-06_reemitido.csv")
    with open(bbva) as f:
        data = f.read()
    with open(reemitido, "w") as f:
        f.write(data + "\n# reemision (bytes distintos, mismos movimientos)\n")

    S.ingest([reemitido], dry_run=True, run_id="run_b")
    S.commit("run_b")
    rows2 = _export_rows(S)
    total2 = sum(int(round(float(r["importe"]) * 100)) *
                 (1 if r["ingreso_gasto"] == "Ingreso" else -1) for r in rows2)

    assert len(rows2) == len(rows1), (
        f"F-01: el estado reemitido DUPLICÓ filas ({len(rows1)} -> {len(rows2)})")
    assert total2 == total1, f"F-01: totales duplicados ({total1} -> {total2})"


# --------------------------------------------------------------------------
# F-06 — Falso positivo de transferencia por solo importe
# --------------------------------------------------------------------------
def test_f06_no_transferencia_por_solo_importe():
    root = _fresh_root()
    S = _services(root)
    # compra real en BBVA y reembolso NO relacionado del mismo monto en Nu
    bbva = _write_csv(root, "bbva_compra.csv",
        ["account_id: bbva_debito", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30",
         "opening_balance: 10000.00", "closing_balance: 9223.00",
         "declared_credits: 0.00", "declared_debits: -777.00", "declared_count: 1"],
        ["fecha", "fecha_cargo", "descripcion", "monto", "saldo", "ref"],
        [["2026-06-10", "2026-06-10", "AMAZON MX COMPRA", "-777.00", "9223.00", "AMZ1"]])
    nu = _write_csv(root, "nu_reembolso.csv",
        ["account_id: nu_debito", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30",
         "opening_balance: 2000.00", "closing_balance: 2777.00",
         "declared_credits: 777.00", "declared_debits: 0.00", "declared_count: 1"],
        ["fecha", "descripcion", "monto", "ref"],
        [["2026-06-11", "REEMBOLSO MERCADO LIBRE", "777.00", "ML9"]])

    S.ingest([bbva, nu], dry_run=True, run_id="run_t")
    S.commit("run_t")
    con = sqlite3.connect(S.db_path())
    n_links = con.execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0]
    con.close()
    assert n_links == 0, (
        f"F-06: se vincularon como transferencia {n_links} movimientos NO relacionados "
        f"(mismo importe, sin señal adicional)")


# --------------------------------------------------------------------------
# SOL-010 — Mezcla de monedas
# --------------------------------------------------------------------------
def test_sol010_cuenta_usd_rechaza_estado_mxn():
    root = _fresh_root()
    # añadir cuenta USD desechable
    accts_p = os.path.join(root, "config/accounts.yml")
    import yaml
    accts = yaml.safe_load(open(accts_p))
    accts["accounts"].append({
        "id": "usd_test", "institution": "bbva", "name": "USD Test",
        "type": "debito", "currency": "USD", "mask": "0000",
        "active": True, "match_hints": ["USDTESTACCT"]})
    yaml.safe_dump(accts, open(accts_p, "w"), allow_unicode=True)

    S = _services(root)
    # estado que declara MXN pero identifica la cuenta USD
    usd = _write_csv(root, "usd_estado.csv",
        ["account_id: usd_test", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30",
         "opening_balance: 100.00", "closing_balance: 200.00",
         "declared_credits: 100.00", "declared_debits: 0.00", "declared_count: 1"],
        ["fecha", "descripcion", "monto", "ref"],
        [["2026-06-10", "USDTESTACCT DEPOSITO", "100.00", "X1"]])

    res = S.ingest([usd], dry_run=True, run_id="run_fx")
    estados = res.get("estados", {})
    assert all(v != "READY_TO_COMMIT" for v in estados.values()), (
        f"SOL-010: estado con moneda inconsistente cuenta(USD)/estado(MXN) quedó "
        f"committeable: {estados}")


# --------------------------------------------------------------------------
# F-07 / SOL-002 — Inyección de fórmula en el CSV de exportación
# --------------------------------------------------------------------------
def test_f07_csv_injection_sanitizada():
    root = _fresh_root()
    S = _services(root)
    mal = _write_csv(root, "bbva_inject.csv",
        ["account_id: bbva_debito", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30",
         "opening_balance: 10000.00", "closing_balance: 9750.00",
         "declared_credits: 0.00", "declared_debits: -250.00", "declared_count: 1"],
        ["fecha", "fecha_cargo", "descripcion", "monto", "saldo", "ref"],
        [["2026-06-05", "2026-06-05", "=cmd|'/c calc'!A1 PAGO", "-250.00", "9750.00", "Z1"]])

    S.ingest([mal], dry_run=True, run_id="run_inj")
    S.commit("run_inj")
    p = S._p("data", "exports", "transacciones_excel.csv")
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    # ninguna celda de datos debe comenzar con = + - @ sin neutralizar
    with open(p, encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0:
                continue
            for cell in row:
                assert not cell[:1] in ("=", "+", "@") or cell[:1] == "'", (
                    f"F-07: celda sin neutralizar en export: {cell!r}")


# --------------------------------------------------------------------------
# F-13 — Tolerancia de conciliación 0 para monedas de exponente 2
# --------------------------------------------------------------------------
def test_f13_tolerancia_cero():
    root = _fresh_root()
    S = _services(root)
    from finance import config as C
    tol = C.policy()["reconciliation"]["tolerance_by_currency"]["MXN"]
    assert tol == 0, f"F-13: tolerancia MXN debería ser 0, es {tol}"
    # un centavo de diferencia debe bloquear (no quedar OK)
    off = _write_csv(root, "bbva_off.csv",
        ["account_id: bbva_debito", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30",
         "opening_balance: 10000.00", "closing_balance: 9750.01",   # 1 centavo de más
         "declared_credits: 0.00", "declared_debits: -250.00", "declared_count: 1"],
        ["fecha", "fecha_cargo", "descripcion", "monto", "saldo", "ref"],
        [["2026-06-05", "2026-06-05", "OXXO", "-250.00", "9750.00", "Z1"]])
    res = S.ingest([off], dry_run=True, run_id="run_off")
    assert all(v != "READY_TO_COMMIT" for v in res["estados"].values()), (
        f"F-13: diferencia de 1 centavo quedó committeable: {res['estados']}")


# --------------------------------------------------------------------------
# F-14 — Ambigüedad miles/decimal se marca dudosa (no se asume ×1000)
# --------------------------------------------------------------------------
def test_f14_importe_ambiguo():
    _services(_fresh_root())
    from finance import money as M
    try:
        M.parse_amount("5.000", "MXN")
        assert False, "F-14: '5.000' se aceptó silenciosamente (debería ser ambiguo)"
    except M.AmountParseError:
        pass
    # formatos inequívocos siguen funcionando
    assert M.parse_amount("1.234,56", "MXN") == 123456
    assert M.parse_amount("30000.00", "MXN") == 3000000


# --------------------------------------------------------------------------
# SOL-003 — Validación de esquema + CHECKs de dominio en la BD
# --------------------------------------------------------------------------
def test_sol003_check_rechaza_dominio_invalido():
    root = _fresh_root()
    S = _services(root)
    S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")], dry_run=True, run_id="r")
    S.commit("r")
    con = sqlite3.connect(S.db_path())
    # moneda inválida rechazada por CHECK
    try:
        con.execute("INSERT INTO statements(statement_id,account_id,file_sha256,original_name,"
                    "currency,ingested_at,status) VALUES('stmt_x','bbva_debito','a','n','zz','t','COMMITTED')")
        con.commit()
        assert False, "SOL-003: CHECK de moneda no bloqueó 'zz'"
    except sqlite3.IntegrityError:
        pass
    con.close()


def test_sol003_jsonschema_activo():
    _services(_fresh_root())
    from finance import validate as V
    errs = V.validate_transaction({"transaction_id": "malformado", "account_id": "a",
        "statement_id": "stmt_x", "date_op": "2026-13-40", "description_raw": "x",
        "amount_minor": "no-int", "currency": "zz", "sign": 0, "fingerprint": "f"})
    assert errs, "SOL-003: jsonschema no detectó una transacción inválida"


# --------------------------------------------------------------------------
# SOL-011 — Migración fallida no deja esquema parcial
# --------------------------------------------------------------------------
def test_sol011_migracion_atomica():
    root = _fresh_root()
    S = _services(root)
    from finance import db
    S.status()  # crea la base y migra a la versión actual
    con = db.connect(S.db_path())
    base_v = db.current_version(con)
    assert base_v == db.SCHEMA_VERSION
    nextv = db.SCHEMA_VERSION + 1
    db.MIGRATIONS[nextv] = "CREATE TABLE migration_probe(x);\nINSERT INTO migration_probe(nope) VALUES(1);"
    try:
        try:
            db.migrate(con, "2026-07-18T00:00:00+00:00", db_file=S.db_path())
            assert False, "SOL-011: la migración inválida no lanzó"
        except Exception:
            pass
        assert db.current_version(con) == base_v, "SOL-011: la versión avanzó pese al fallo"
        probe = con.execute("SELECT name FROM sqlite_master WHERE name='migration_probe'").fetchone()
        assert probe is None, "SOL-011: quedó tabla parcial migration_probe"
    finally:
        db.MIGRATIONS.pop(nextv, None)
        con.close()


# --------------------------------------------------------------------------
# F-02 — dry-run por defecto en la CLI
# --------------------------------------------------------------------------
def test_f02_dry_run_por_defecto():
    _services(_fresh_root())
    from finance import cli
    a = cli.build_parser().parse_args(["ingest", "ruta"])
    assert a.commit is False, "F-02: --commit debería ser False por defecto (dry-run seguro)"


# --------------------------------------------------------------------------
# F-04 — Ruta de aprobación REVIEW_REQUIRED -> READY_TO_COMMIT
# --------------------------------------------------------------------------
def test_f04_approve_incorpora_tras_revision():
    root = _fresh_root()
    S = _services(root)
    # CSV sin saldos declarados => reconcile REVIEW_REQUIRED (bloqueo blando, aprobable)
    sinsaldo = _write_csv(root, "nu_sinsaldo.csv",
        ["account_id: nu_debito", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30"],
        ["fecha", "descripcion", "monto", "ref"],
        [["2026-06-15", "DEPOSITO", "500.00", "R1"]])
    res = S.ingest([sinsaldo], dry_run=True, run_id="run_rev")
    sid = list(res["estados"].keys())[0]
    assert res["estados"][sid] == "REVIEW_REQUIRED"
    # commit sin aprobar => no incorpora
    c = S.commit("run_rev")
    assert c.get("skipped"), "F-04: debió reportar statements omitidos"
    con = sqlite3.connect(S.db_path()); n0 = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]; con.close()
    assert n0 == 0
    # aprobar y volver a commitear => incorpora
    S.approve("run_rev", sid, reason="saldos no disponibles en el estado; verificado manualmente")
    S.commit("run_rev")
    con = sqlite3.connect(S.db_path()); n1 = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]; con.close()
    assert n1 == 1, f"F-04: la aprobación no incorporó el movimiento (n={n1})"


def test_f04_approve_rechaza_bloqueo_duro():
    root = _fresh_root()
    S = _services(root)
    # importe ilegible => missing_critical_fields (bloqueo DURO, no aprobable)
    malo = _write_csv(root, "nu_malo.csv",
        ["account_id: nu_debito", "currency: MXN",
         "period_start: 2026-06-01", "period_end: 2026-06-30",
         "opening_balance: 0.00", "closing_balance: 0.00"],
        ["fecha", "descripcion", "monto", "ref"],
        [["2026-06-15", "DEPOSITO", "ilegible", "R1"]])
    res = S.ingest([malo], dry_run=True, run_id="run_dur")
    sid = list(res["estados"].keys())[0]
    try:
        S.approve("run_dur", sid, reason="intento")
        assert False, "F-04: aprobó un bloqueo contable duro"
    except PermissionError:
        pass


# --------------------------------------------------------------------------
# SOL-005 — Correcciones auditadas (tabla corrections + audit_events)
# --------------------------------------------------------------------------
def test_sol005_correcciones_auditadas():
    root = _fresh_root()
    S = _services(root)
    from finance import cli
    S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")], dry_run=True, run_id="run_c")
    full = S._read_json(S._p("staging", "run_c", "_full.json"))
    tid = full["statements"][0]["txns"][0]["transaction_id"]
    corr_file = os.path.join(root, "corr.json")
    json.dump({"corrections": [
        {"transaction_id": tid, "field": "category", "value": "Restaurantes/Delivery", "reason": "test"},
        {"transaction_id": tid, "field": "amount_minor", "value": 999, "reason": "prohibido"},
    ]}, open(corr_file, "w"))

    class A: pass
    a = A(); a.run_id = "run_c"; a.file = corr_file
    cli.cmd_correct(a)
    S.commit("run_c")
    con = sqlite3.connect(S.db_path())
    ncorr = con.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
    nev = con.execute("SELECT COUNT(*) FROM audit_events WHERE action='correct'").fetchone()[0]
    amt = con.execute("SELECT amount_minor FROM transactions WHERE transaction_id=?", (tid,)).fetchone()[0]
    con.close()
    assert ncorr == 1, f"SOL-005: se esperaba 1 corrección registrada, hay {ncorr}"
    assert nev == 1, f"SOL-005: sin evento de auditoría de corrección ({nev})"
    assert amt != 999, "SOL-005: una corrección alteró un campo contable (importe)"


# --------------------------------------------------------------------------
# F-10 — Cierre de mes persiste status='closed'
# --------------------------------------------------------------------------
def test_f10_cierre_mes_persiste():
    root = _fresh_root()
    S = _services(root)
    S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv"),
              os.path.join(root, "fixtures/sanitized/nu_2026-06.csv")], dry_run=True, run_id="run_m")
    S.commit("run_m")
    S.close_month("2026-06")
    con = sqlite3.connect(S.db_path())
    n_closed = con.execute("SELECT COUNT(*) FROM month_close WHERE status='closed' AND month='2026-06'").fetchone()[0]
    con.close()
    assert n_closed >= 1, f"F-10: ningún mes quedó 'closed' de forma persistente ({n_closed})"


# --------------------------------------------------------------------------
# SOL-R2-001 — Mutaciones al staging bloquean el commit; corrección legítima pasa
# --------------------------------------------------------------------------
def _ingest_one(S, root, run):
    S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")], dry_run=True, run_id=run)
    return S._p("staging", run, "_full.json")


def test_solr2001_mutaciones_staging_bloquean():
    import copy as _copy
    mutaciones = {
        "sign": lambda t, s: t.__setitem__("sign", -t["sign"]),
        "cent": lambda t, s: t.__setitem__("amount_minor", t["amount_minor"] + 1),
        "month": lambda t, s: t.__setitem__("date_op", "2026-07-05"),
        "account": lambda t, s: t.__setitem__("account_id", "nu_debito"),
        "currency": lambda t, s: t.__setitem__("currency", "USD"),
        "closing": lambda t, s: s.__setitem__("closing_minor", (s["closing_minor"] or 0) + 1),
        "omit": lambda t, s: s["txns"].pop(),
        "category": lambda t, s: t.__setitem__("category", "CATEGORIA_INEXISTENTE"),
    }
    for kind, mut in mutaciones.items():
        root = _fresh_root()
        S = _services(root)
        p = _ingest_one(S, root, "mut")
        d = json.load(open(p))
        st = d["statements"][0]
        mut(st["txns"][0], st)
        json.dump(d, open(p, "w"))
        blocked = False
        try:
            S.commit("mut")
        except Exception:
            blocked = True
        con = sqlite3.connect(S.db_path())
        n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        con.close()
        assert blocked and n == 0, f"SOL-R2-001: mutación '{kind}' NO bloqueó (commit ok, {n} txns)"


def test_solr2001_correccion_legitima_pasa():
    root = _fresh_root()
    S = _services(root)
    from finance import cli
    _ingest_one(S, root, "ok")
    full = S._read_json(S._p("staging", "ok", "_full.json"))
    tid = full["statements"][0]["txns"][0]["transaction_id"]
    cf = os.path.join(root, "c.json")
    json.dump({"corrections": [{"transaction_id": tid, "field": "category", "value": "Restaurantes/Delivery", "reason": "t"}]}, open(cf, "w"))
    class A: pass
    a = A(); a.run_id = "ok"; a.file = cf
    cli.cmd_correct(a)
    S.commit("ok")   # no debe lanzar
    con = sqlite3.connect(S.db_path())
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    con.close()
    assert n == 5, f"SOL-R2-001: la corrección legítima de categoría no committeó ({n})"


# --------------------------------------------------------------------------
# F-01 completo — Corrección que cambia importe no duplica (supersession por periodo)
# --------------------------------------------------------------------------
def test_f01_correccion_cambia_importe_no_duplica():
    root = _fresh_root()
    S = _services(root)
    bbva = os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")
    S.ingest([bbva], dry_run=True, run_id="orig"); S.commit("orig")
    # estado corregido: OXXO -250.00 -> -350.00 y cierre ajustado (-100)
    corr = os.path.join(root, "fixtures/sanitized/bbva_corr.csv")
    txt = open(bbva).read().replace("-250.00,39750.00,A1", "-350.00,39650.00,A1").replace(
        "closing_balance: 31600.00", "closing_balance: 31500.00").replace(
        "declared_debits: -8400.00", "declared_debits: -8500.00")
    open(corr, "w").write(txt)
    S.ingest([corr], dry_run=True, run_id="corr"); S.commit("corr")
    rows = _export_rows(S)
    total = sum(int(round(float(r["importe"]) * 100)) * (1 if r["ingreso_gasto"] == "Ingreso" else -1) for r in rows)
    assert len(rows) == 5, f"F-01: la corrección duplicó filas ({len(rows)} != 5)"
    # neto esperado: 30000 - 350 - 150 - 5000 - 3000 = 21500 (00s) = 2150000 centavos
    assert total == 2150000, f"F-01: total incorrecto {total} (esp 2150000)"


# --------------------------------------------------------------------------
# SOL-R2-002 — Migración v1 -> v2 dota de CHECK a una base existente
# --------------------------------------------------------------------------
def test_solr2002_upgrade_v1_a_v2():
    root = _fresh_root()
    S = _services(root)
    from finance import db
    # simular una base v1 ANTERIOR (sin CHECK): se derivan quitando los CHECK del DDL v1.
    sql_v1 = db.MIGRATION_1
    for chk in ("CHECK(currency GLOB '[A-Z][A-Z][A-Z]')",
                "CHECK(date_op GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')",
                "CHECK(sign IN (-1, 1))"):
        sql_v1 = sql_v1.replace(chk, "")
    assert "CHECK(" not in sql_v1, "el DDL v1 simulado no debería tener CHECK"
    os.makedirs(os.path.dirname(S.db_path()), exist_ok=True)
    c = sqlite3.connect(S.db_path())
    c.executescript(sql_v1)
    c.execute("INSERT INTO schema_migrations VALUES(1,'old','old')"); c.commit(); c.close()
    # migrar con el código nuevo
    con = db.connect(S.db_path())
    v = db.migrate(con, "now", db_file=S.db_path())
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='transactions'").fetchone()[0]
    con.close()
    assert v == db.SCHEMA_VERSION and v >= 2, f"SOL-R2-002: no migró al esquema vigente ({v})"
    assert "CHECK(currency" in sql and "CHECK(sign" in sql, "SOL-R2-002: v2 no añadió los CHECK"


# --------------------------------------------------------------------------
# SOL-R2-003 — Transferencias no emparejan entre monedas distintas
# --------------------------------------------------------------------------
def test_solr2003_transfer_cross_currency():
    _services(_fresh_root())
    from finance import dedup as D
    a = {"transaction_id": "a", "account_id": "bbva_debito", "account_type": "debito",
         "amount_minor": -10000, "currency": "MXN", "date_op": "2026-06-01",
         "description_norm": "nu", "bank_ref": "SAME", "_flags": []}
    b = {"transaction_id": "b", "account_id": "usd_test", "account_type": "debito",
         "amount_minor": 10000, "currency": "USD", "date_op": "2026-06-01",
         "description_norm": "bbva", "bank_ref": "SAME", "_flags": []}
    links = D.find_transfers([a, b], [], ["bbva_debito", "usd_test"], 3, {})
    assert links == [], f"SOL-R2-003: se vinculó una transferencia MXN↔USD ({links})"


# --------------------------------------------------------------------------
# SOL-R2-004 — Cuenta inactiva bloquea
# --------------------------------------------------------------------------
def test_solr2004_cuenta_inactiva_bloquea():
    root = _fresh_root()
    import yaml
    p = os.path.join(root, "config/accounts.yml")
    data = yaml.safe_load(open(p))
    for a in data["accounts"]:
        if a["id"] == "bbva_debito":
            a["active"] = False
    yaml.safe_dump(data, open(p, "w"), allow_unicode=True)
    S = _services(root)
    res = S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")], dry_run=True, run_id="inact")
    assert all(v != "READY_TO_COMMIT" for v in res["estados"].values()), (
        f"SOL-R2-004: cuenta inactiva quedó committeable: {res['estados']}")


# --------------------------------------------------------------------------
# SOL-R3-001 — El sello cubre transfer_links y corrections (forja bloquea)
# --------------------------------------------------------------------------
def test_solr3001_sello_cubre_links_corrections():
    root = _fresh_root()
    S = _services(root)
    fx = [os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv"),
          os.path.join(root, "fixtures/sanitized/nu_2026-06.csv")]
    S.ingest(fx, dry_run=True, run_id="x")
    p = S._p("staging", "x", "_full.json")
    d = json.load(open(p))
    ids_ = [s["txns"][0]["transaction_id"] for s in d["statements"]]
    d["transfer_links"] = [{"from": ids_[0], "to": ids_[1], "confidence": 1.0, "kind": "transfer"}]
    d["corrections"] = [{"transaction_id": ids_[0], "field": "category", "old_value": "A",
                         "new_value": "B", "reason": "FORGED"}]
    json.dump(d, open(p, "w"))
    blocked = False
    try:
        S.commit("x")
    except Exception:
        blocked = True
    con = sqlite3.connect(S.db_path())
    nl = con.execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0]
    nc = con.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
    con.close()
    assert blocked, "SOL-R3-001: forjar transfer_links/corrections no bloqueó"
    assert nl == 0 and nc == 0, f"SOL-R3-001: se persistieron links/corrections falsos ({nl}/{nc})"


# --------------------------------------------------------------------------
# SOL-R3-002 / F5-R2-08 — Estado distinto mismo periodo conserva AMBOS
# --------------------------------------------------------------------------
def test_solr3002_distinto_mismo_periodo_conserva_ambos():
    root = _fresh_root()
    S = _services(root)
    bbva = os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")
    S.ingest([bbva], dry_run=True, run_id="a"); S.commit("a")
    # estado B: mismo account/periodo, movimiento DISJUNTO (otro ref)
    g = _write_csv(root, "distinct.csv",
        ["account_id: bbva_debito", "currency: MXN", "period_start: 2026-06-01",
         "period_end: 2026-06-30", "opening_balance: 0.00", "closing_balance: 7.00",
         "declared_credits: 7.00", "declared_debits: 0.00", "declared_count: 1"],
        ["fecha", "descripcion", "monto", "ref"],
        [["2026-06-28", "BONO DISTINTO", "7.00", "NEWREF"]])
    z = S.ingest([g], dry_run=True, run_id="b")
    sid = next(iter(z["estados"]))
    if z["estados"][sid] != "READY_TO_COMMIT":
        S.approve("b", sid, "estado complementario del mismo periodo")
    S.commit("b")
    rows = _export_rows(S)
    con = sqlite3.connect(S.db_path())
    n_sup = con.execute("SELECT COUNT(*) FROM statements WHERE status='SUPERSEDED'").fetchone()[0]
    con.close()
    assert len(rows) == 6, f"SOL-R3-002: pérdida de datos — export {len(rows)} filas (esp 6: 5+1)"
    assert n_sup == 0, "SOL-R3-002: un estado quedó SUPERSEDED (no debería con movimientos disjuntos)"


# --------------------------------------------------------------------------
# SOL-R3-003 — Cuenta desactivada entre preview y commit (TOCTOU)
# --------------------------------------------------------------------------
def test_solr3003_toctou_cuenta_desactivada():
    root = _fresh_root()
    S = _services(root)
    bbva = os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")
    S.ingest([bbva], dry_run=True, run_id="x")
    # desactivar la cuenta DESPUÉS del preview
    import yaml
    from finance import config as C
    p = os.path.join(root, "config/accounts.yml")
    data = yaml.safe_load(open(p))
    for a in data["accounts"]:
        if a["id"] == "bbva_debito":
            a["active"] = False
    yaml.safe_dump(data, open(p, "w"), allow_unicode=True)
    C.reset_cache()
    blocked = False
    try:
        S.commit("x")
    except Exception:
        blocked = True
    con = sqlite3.connect(S.db_path())
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    con.close()
    assert blocked and n == 0, f"SOL-R3-003: commit aceptó cuenta desactivada tras preview ({n} txns)"


# --------------------------------------------------------------------------
# SOL-R2-003 — Categoría fuera de catálogo por vía SANCIONADA (correct) bloquea el commit
# --------------------------------------------------------------------------
def test_solr2003_categoria_fuera_catalogo_bloquea():
    root = _fresh_root()
    S = _services(root)
    from finance import cli
    S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")], dry_run=True, run_id="cat")
    full = S._read_json(S._p("staging", "cat", "_full.json"))
    tid = full["statements"][0]["txns"][0]["transaction_id"]
    cf = os.path.join(root, "cat.json")
    # categoría inexistente aplicada por la vía sancionada (re-sella el staging; el sello HMAC NO
    # la detecta -> la barrera es la validación de catálogo en el commit).
    json.dump({"corrections": [{"transaction_id": tid, "field": "category",
                                "value": "Cripto-NFT-Meme", "reason": "typo"}]}, open(cf, "w"))
    class A: pass
    a = A(); a.run_id = "cat"; a.file = cf
    cli.cmd_correct(a)
    blocked = False
    try:
        S.commit("cat")
    except Exception:
        blocked = True
    con = sqlite3.connect(S.db_path())
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    con.close()
    assert blocked and n == 0, f"SOL-R2-003: se committeó una categoría fuera de catálogo ({n} txns)"


# --------------------------------------------------------------------------
# SOL-R2-001 — Un mes CERRADO rechaza commits nuevos (no los acepta en silencio)
# --------------------------------------------------------------------------
def test_solr2001_mes_cerrado_rechaza_commit_nuevo():
    root = _fresh_root()
    S = _services(root)
    bbva = os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")
    S.ingest([bbva], dry_run=True, run_id="m1"); S.commit("m1")
    n0 = _count_txn(S)
    S.close_month("2026-06")
    # estado NUEVO (bytes distintos -> statement_id distinto) para el MISMO mes/cuenta ya cerrado
    bbva2 = os.path.join(root, "fixtures/sanitized/bbva_2026-06_reemitido.csv")
    with open(bbva2, "w", encoding="utf-8") as f:
        f.write("# reemisión con distinto sha\n" + open(bbva, encoding="utf-8").read())
    S.ingest([bbva2], dry_run=True, run_id="m2")
    res = S.commit("m2")
    n1 = _count_txn(S)
    skipped_closed = any(sk.get("state") == "MONTH_CLOSED" for sk in (res.get("skipped") or []))
    assert n1 == n0 and skipped_closed, \
        f"SOL-R2-001: mes cerrado no rechazó el commit nuevo (n0={n0} n1={n1} skipped={res.get('skipped')})"


# --------------------------------------------------------------------------
# SOL-R2-002 — El motor rechaza fechas de calendario imposibles (backstop de trigger)
# --------------------------------------------------------------------------
def test_solr2002_fecha_calendario_invalida_bloquea_en_db():
    root = _fresh_root()
    S = _services(root)
    S.ingest([os.path.join(root, "fixtures/sanitized/bbva_2026-06.csv")], dry_run=True, run_id="d1")
    S.commit("d1")
    con = sqlite3.connect(S.db_path())
    tid = con.execute("SELECT transaction_id FROM transactions LIMIT 1").fetchone()[0]
    # '2026-99-99' (date() -> NULL) y '2026-02-30' (date() NORMALIZA a 2026-03-02): ambas deben
    # ser rechazadas por el trigger; el CHECK GLOB solo verifica el formato.
    for bad in ("2026-99-99", "2026-02-30"):
        raised = False
        try:
            con.execute("UPDATE transactions SET date_op=? WHERE transaction_id=?", (bad, tid))
        except sqlite3.Error:
            raised = True
        assert raised, f"SOL-R2-002: la BD aceptó una fecha de calendario imposible {bad!r}"
    con.close()


def _count_txn(S):
    con = sqlite3.connect(S.db_path())
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    con.close()
    return n


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception:
            fails += 1
            print(f"ERROR {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests)-fails}/{len(tests)} en verde")
    raise SystemExit(1 if fails else 0)
