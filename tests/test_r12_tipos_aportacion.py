# -*- coding: utf-8 -*-
"""
test_r12_tipos_aportacion.py — R12-B-001: tipos `aportacion_ahorro` / `aportacion_inversion`.

Apartar dinero (una Cajita de Nu, una aportación a la casa de bolsa) no es gasto: el dinero sigue
siendo tuyo. Sin un tipo propio caía en `gasto`, y el libro lo contaba como gasto real del mes
además de reemplazar su categoría por "Otros gastos" (el puente solo admite categorías de gasto
cuando el tipo es Gasto).

Se verifica el ciclo completo: `correct` fija el tipo -> el commit lo REVALIDA contra el catálogo
(no lo rechaza) -> el CSV-contrato lo exporta -> el mapa del puente lo traduce a un tipo que el
libro conoce y excluye del gasto real.

Cifras y datos FICTICIOS.
"""
import os, sys, csv, json, shutil, tempfile

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(SRC_ROOT)          # .../cuadra

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def fresh():
    d = tempfile.mkdtemp(prefix="r12tipos-")
    for sub in ("src", "config", "schemas"):
        shutil.copytree(os.path.join(SRC_ROOT, sub), os.path.join(d, sub))
    shutil.copytree(os.path.join(SRC_ROOT, "fixtures", "sanitized"),
                    os.path.join(d, "fixtures", "sanitized"))
    for sub in ("data/exports", "staging", "statements/inbox", "statements/raw"):
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


NUEVOS = ("aportacion_ahorro", "aportacion_inversion")


def case_catalogo_y_esquema():
    root = fresh(); S = load(root)
    import jsonschema
    esquema = json.load(open(os.path.join(root, "schemas", "transaction.schema.json")))
    enum = esquema["properties"]["type"]["enum"]
    check("R12-09 los tipos de aportación están en el catálogo y en el esquema",
          all(t in S.VALID_TYPES for t in NUEVOS) and all(t in enum for t in NUEVOS),
          f"valid={[t in S.VALID_TYPES for t in NUEVOS]} enum={[t in enum for t in NUEVOS]}")


def _corregir_tipo(S, run_id, tid, nuevo):
    """Réplica de la vía sancionada (cli.cmd_correct): re-sella tras verificar el sello previo."""
    run_dir = S._p("staging", run_id)
    full = S._read_sealed_full(run_dir)
    for st in full["statements"]:
        for t in st["txns"]:
            if t["transaction_id"] == tid:
                old = t.get("type")
                t["type"] = nuevo
                full.setdefault("corrections", []).append(
                    {"transaction_id": tid, "field": "type", "old_value": old,
                     "new_value": nuevo, "reason": "aportación, no gasto", "ts": S.now_iso()})
    full["_seal"] = S._seal_staging(full)
    S._write_json(os.path.join(run_dir, "_full.json"), full)


def case_ciclo_completo():
    """correct -> commit (revalida) -> export: el tipo sobrevive todo el camino."""
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    paths = [os.path.join(fx, n) for n in sorted(os.listdir(fx))]
    r = S.ingest(paths, dry_run=True, run_id="tipos1")
    full = json.load(open(os.path.join(root, "staging", "tipos1", "_full.json")))
    # se elige un GASTO cualquiera y se reclasifica como aportación a inversión
    objetivo = next(t for st in full["statements"] for t in st["txns"]
                    if t["amount_minor"] and t["amount_minor"] < 0)
    _corregir_tipo(S, "tipos1", objetivo["transaction_id"], "aportacion_inversion")
    try:
        S.commit("tipos1")
        commit_ok, err = True, ""
    except Exception as e:
        commit_ok, err = False, repr(e)
    exp = S.export_excel()
    with open(exp["path"], newline="", encoding="utf-8") as f:
        filas = {x["transaction_id"]: x for x in csv.DictReader(f)}
    fila = filas.get(objetivo["transaction_id"], {})
    check("R12-10 correct→commit→export conserva el tipo de aportación",
          commit_ok and fila.get("tipo") == "aportacion_inversion",
          f"commit_ok={commit_ok} err={err} tipo_exportado={fila.get('tipo')!r}")


def case_tipo_invalido_sigue_bloqueando():
    """La salvaguarda no se aflojó: un tipo fuera del catálogo sigue rechazándose en commit."""
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    paths = [os.path.join(fx, n) for n in sorted(os.listdir(fx))]
    S.ingest(paths, dry_run=True, run_id="tipos2")
    full = json.load(open(os.path.join(root, "staging", "tipos2", "_full.json")))
    objetivo = next(t for st in full["statements"] for t in st["txns"] if t["amount_minor"])
    _corregir_tipo(S, "tipos2", objetivo["transaction_id"], "aportacion_lo_que_sea")
    try:
        S.commit("tipos2")
        bloqueo = False
    except Exception:
        bloqueo = True
    # R12-E-011: comprobar el efecto (commit bloqueado) no basta: el enum del JSON-Schema bloquea
    # antes y el test seguía verde con la guarda R6-A-002 borrada. Se verifica ADEMÁS que la guarda
    # de VALID_TYPES reporta el problema por sí misma.
    st = json.load(open(os.path.join(root, "staging", "tipos2", "_full.json")))["statements"][0]
    problemas = S._revalidate_statement(st, S.config.policy())
    guarda = any("type fuera de catálogo" in p for p in problemas)
    check("R12-11 un tipo fuera del catálogo bloquea, y la guarda de VALID_TYPES lo señala",
          bloqueo and guarda, f"bloqueo={bloqueo} guarda_VALID_TYPES={guarda}")


def case_mapeo_del_puente():
    """El puente traduce los tipos nuevos a valores que el libro conoce y excluye del gasto."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "Herramientas"))
    for n in list(sys.modules):
        if n in ("puente_datos", "hojas_base", "estilos"):
            del sys.modules[n]
    from puente_datos import TIPO_MAP
    from hojas_base import TIPOS_MOV, GASTO_NO_AHORRO
    destinos = {t: TIPO_MAP.get(t) for t in NUEVOS}
    esperados = {"aportacion_ahorro": "Aportación a ahorro",
                 "aportacion_inversion": "Aportación a inversión"}
    check("R12-12 el puente mapea las aportaciones a tipos del libro excluidos del gasto real",
          destinos == esperados and
          all(v in TIPOS_MOV for v in destinos.values()) and
          all(v in GASTO_NO_AHORRO for v in destinos.values()),
          f"destinos={destinos}")


if __name__ == "__main__":
    for fn in (case_catalogo_y_esquema, case_ciclo_completo,
               case_tipo_invalido_sigue_bloqueando, case_mapeo_del_puente):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
