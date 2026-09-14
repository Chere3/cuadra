# -*- coding: utf-8 -*-
"""
test_r12_export_decimales.py — Escala de la moneda en el CSV-contrato de exportación.

`from_minor` dividía sin fijar la escala, así que 20000 daba Decimal('200') y 300050 daba
Decimal('3000.5'). Ese formato variable se filtraba tal cual a la columna `importe` del
CSV que consume el Excel: un contrato que dice ser estable no puede alternar entre 0, 1 y 2
decimales. El valor numérico nunca fue incorrecto — el round-trip a centavos ya cerraba — pero
el formato sí.

Se verifica: escala fija por moneda, round-trip exacto (no se redondea ningún importe) y el
formato REAL de la columna del CSV generado por el pipeline.
"""
import os, sys, csv, shutil, tempfile
from decimal import Decimal

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def fresh():
    d = tempfile.mkdtemp(prefix="r12dec-")
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


def case_escala_por_moneda():
    root = fresh(); load(root)
    from finance import money as M
    casos = [(110397, "1103.97"), (20000, "200.00"), (300050, "3000.50"),
             (5, "0.05"), (100, "1.00"), (0, "0.00")]
    malos = [(c, str(M.from_minor(c, "MXN")), esp) for c, esp in casos
             if str(M.from_minor(c, "MXN")) != esp]
    # una moneda sin decimales no debe recibir ".00"
    jpy = str(M.from_minor(1500, "JPY"))
    check("R12-06 from_minor fija la escala de la moneda",
          not malos and jpy == "1500", f"malos={malos} jpy={jpy}")


def case_round_trip_exacto():
    root = fresh(); load(root)
    from finance import money as M
    malos = []
    for c in (1, 5, 50, 99, 100, 1742, 20000, 110397, 300050, 999999, 3000500):
        if M.to_minor(str(M.from_minor(c, "MXN")), "MXN") != c:
            malos.append(c)
        if M.from_minor(c, "MXN") != Decimal(c) / 100:
            malos.append(("valor", c))
    check("R12-07 cuantizar no altera ningún importe (round-trip exacto)",
          not malos, f"malos={malos}")


def case_csv_exportado():
    """El formato que de verdad llega al Excel: toda la columna `importe` con 2 decimales."""
    root = fresh(); S = load(root)
    fx = os.path.join(root, "fixtures", "sanitized")
    paths = [os.path.join(fx, n) for n in sorted(os.listdir(fx))
             if n.lower().endswith((".csv", ".xlsx", ".pdf"))]
    r = S.ingest(paths, dry_run=False)
    if not r.get("auto_committed"):
        S.commit(r["run_id"])
    exp = S.export_excel()
    with open(exp["path"], newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    imps = [x["importe"].lstrip("'") for x in filas]
    malos = [v for v in imps if "." not in v or len(v.split(".")[1]) != 2]
    check("R12-08 la columna `importe` del CSV-contrato lleva 2 decimales",
          filas and not malos, f"n={len(filas)} malos={malos[:5]}")


if __name__ == "__main__":
    for fn in (case_escala_por_moneda, case_round_trip_exacto, case_csv_exportado):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
