# -*- coding: utf-8 -*-
"""
test_r17_tolerancia_cuenta.py — tolerancia de conciliación POR CUENTA.

La tolerancia global es 0: un residuo se audita, no se ignora. Pero existen emisores cuyo propio
estado no cuadra consigo mismo —declara un saldo final y la suma de los componentes que él mismo
imprime da un centavo menos—, y bloquear ahí deja fuera datos correctos por un defecto ajeno.

La excepción es POR CUENTA y exige justificación documentada junto al valor (regla 25). Lo que
este test fija es que la excepción NO se derrama: cualquier otra cuenta con el mismo residuo sigue
bloqueando, y un residuo mayor bloquea también en la cuenta exceptuada.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.reconcile import reconcile  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


POLICY = {"reconciliation": {"default_tolerance_minor": 0,
                             "tolerance_by_currency": {"MXN": 0, "USD": 0},
                             "tolerance_by_account": {"cuenta_con_residuo": 1}}}

# opening 1000, un cargo de 500 y un abono de 200 -> calculado 700; declarado 699 => diff 1
TXNS = [{"amount_minor": -50000}, {"amount_minor": 20000}]
OPENING, CLOSING_1C = 100000, 69999


def rec(account_id, closing=CLOSING_1C, txns=None):
    return reconcile(OPENING, closing, txns if txns is not None else TXNS, {}, POLICY, "MXN",
                     account_id=account_id)


def test_la_cuenta_exceptuada_admite_el_residuo():
    r = rec("cuenta_con_residuo")
    check("la cuenta con excepción admite 1 centavo",
          r["result"] == "OK" and r["tolerance_minor"] == 1, f"{r['result']} tol={r['tolerance_minor']}")
    check("y el residuo queda REGISTRADO, no borrado", r["diff_minor"] == 1, str(r["diff_minor"]))


def test_no_se_derrama_a_otras_cuentas():
    for otra in ("otra_cuenta", None):
        r = rec(otra)
        check(f"la excepción no aplica a {otra!r}",
              r["result"] == "BLOCKED" and r["tolerance_minor"] == 0,
              f"{r['result']} tol={r['tolerance_minor']}")


def test_residuo_mayor_bloquea_igual():
    """La excepción es de UN centavo, no una barra libre."""
    r = rec("cuenta_con_residuo", closing=69995)     # diff = 5
    check("un residuo de 5 centavos bloquea en la cuenta exceptuada",
          r["result"] == "BLOCKED", f"{r['result']} diff={r['diff_minor']}")


def test_sin_excepciones_configuradas():
    pol = {"reconciliation": {"default_tolerance_minor": 0, "tolerance_by_currency": {"MXN": 0}}}
    r = reconcile(OPENING, CLOSING_1C, TXNS, {}, pol, "MXN", account_id="cuenta_con_residuo")
    check("sin `tolerance_by_account` se usa la tolerancia de la moneda",
          r["result"] == "BLOCKED" and r["tolerance_minor"] == 0, str(r["result"]))


def test_cuadre_exacto_sigue_siendo_ok():
    r = rec("otra_cuenta", closing=70000)
    check("un estado que cuadra exacto sigue en OK", r["result"] == "OK", str(r["result"]))


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    malos = [r for r in RESULTS if not r[1]]
    print()
    if malos:
        print(f"=== {len(malos)} FALLO(S) de {len(RESULTS)} ===")
        sys.exit(1)
    print(f"=== TODO VERDE === ({len(RESULTS)} checks)")
