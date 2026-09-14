# -*- coding: utf-8 -*-
"""
test_r12_high_auditoria.py — HIGH de la auditoría externa de la ronda 12.

- R12-E-004  Los vínculos de transferencia se descartaban si los dos extremos no venían en el
             MISMO commit. Importar cuenta por cuenta —el flujo normal— deja cada lado en un run
             distinto: ningún vínculo se persistía y el libro contaba el mismo dinero como gasto
             real en una cuenta y como ingreso real en la otra.
- R12-E-005  `import_id` se derivaba de (run, cuenta). Dos estados de la MISMA cuenta en un run
             colisionaban en la PK y abortaban el commit ENTERO, incluidas las demás cuentas.
- R12-E-006  `validate_statement()` no se invocaba en ninguna parte, y un periodo invertido
             (inicio > fin) llegaba a READY_TO_COMMIT.

Cifras y datos FICTICIOS.
"""
import os, sys, csv, json, shutil, sqlite3, tempfile

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def fresh():
    d = tempfile.mkdtemp(prefix="r12high-")
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


def case_vinculo_entre_runs():
    """Cada estado en su propio run y commit: el vínculo debe persistirse igual."""
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    for i, a in enumerate(sorted(os.listdir(fx))):
        S.ingest([os.path.join(fx, a)], dry_run=True, run_id=f"sep{i}")
        S.commit(f"sep{i}")
    exp = S.export_excel()
    con = sqlite3.connect(S.db_path()); con.row_factory = sqlite3.Row
    links = con.execute("SELECT COUNT(*) c FROM transfer_links").fetchone()["c"]
    con.close()
    rows = list(csv.DictReader(open(exp["path"], encoding="utf-8")))
    marcadas = sum(1 for r in rows if str(r["es_transferencia"]).strip() == "1")
    check("R12-27 el vínculo de transferencia sobrevive aunque los lados vayan en runs distintos",
          links >= 1 and marcadas >= 2, f"links={links} filas_marcadas={marcadas}/{len(rows)}")


def case_dos_estados_misma_cuenta():
    """Dos archivos de la misma cuenta en un run no deben abortar el commit entero."""
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    uno = os.path.join(fx, sorted(os.listdir(fx))[0])
    copia = os.path.join(root, "statements", "inbox", "copia_mismo_mes.csv")
    txt = open(uno, encoding="utf-8").read()
    # mismo formato y cuenta, movimientos distintos -> otro sha256, misma cuenta
    open(copia, "w", encoding="utf-8").write(txt.replace("2026-06-0", "2026-06-1"))
    r = S.ingest([uno, copia], dry_run=True, run_id="dos")
    ids_imp = set()
    try:
        S.commit("dos")
        err = ""
    except Exception as e:
        err = repr(e)
    con = sqlite3.connect(S.db_path()); con.row_factory = sqlite3.Row
    ids_imp = {x["import_id"] for x in con.execute("SELECT import_id FROM imports")}
    n_tx = con.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    con.close()
    doc = S.doctor()
    check("R12-28 dos estados de la misma cuenta en un run no colisionan en la PK de imports",
          not err and len(ids_imp) >= 2 and n_tx > 0 and doc["ok"],
          f"error={err[:80]} imports={len(ids_imp)} txns={n_tx} doctor={doc['ok']}")


def case_periodo_invertido_bloquea():
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    S.ingest([os.path.join(fx, sorted(os.listdir(fx))[0])], dry_run=True, run_id="inv")
    run_dir = S._p("staging", "inv")
    full = S._read_sealed_full(run_dir)
    st = full["statements"][0]
    st["period_start"], st["period_end"] = "2026-01-29", "2026-01-28"   # invertido
    full["_seal"] = S._seal_staging(full)
    S._write_json(os.path.join(run_dir, "_full.json"), full)
    problemas = S._revalidate_statement(st, S.config.policy())
    try:
        S.commit("inv")
        bloqueo = False
    except Exception:
        bloqueo = True
    check("R12-29 un periodo invertido se detecta y bloquea el commit",
          bloqueo and any("invertido" in p for p in problemas),
          f"bloqueo={bloqueo} problemas={problemas[:2]}")


def case_schema_del_statement_se_aplica():
    """El validador del estado debe estar realmente enchufado, y admitir las plantillas por banco."""
    root = fresh(); S = load(root)
    from finance import validate as V
    base = {"statement_id": "stmt_0123456789abcdef", "account_id": "nu_debito", "file_sha256": "a" * 64,
            "original_name": "x.pdf", "currency": "MXN"}
    ok_tpl = V.validate_statement(dict(base, extraction_method="pdf_template:nu"))
    ok_csv = V.validate_statement(dict(base, extraction_method="csv"))
    mal = V.validate_statement(dict(base, extraction_method="inventado"))
    # y comprobar que _revalidate_statement lo consume
    problemas = S._revalidate_statement(dict(base, extraction_method="inventado", txns=[]),
                                        S.config.policy())
    check("R12-30 el esquema del estado se aplica y admite las plantillas por banco",
          not ok_tpl and not ok_csv and mal and any("invalid_schema" in p for p in problemas),
          f"tpl={ok_tpl} csv={ok_csv} invalido={bool(mal)} enchufado={any('invalid_schema' in p for p in problemas)}")


if __name__ == "__main__":
    for fn in (case_vinculo_entre_runs, case_dos_estados_misma_cuenta,
               case_periodo_invertido_bloquea, case_schema_del_statement_se_aplica):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
