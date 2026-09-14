# -*- coding: utf-8 -*-
"""
test_r18_declarados_y_tokens.py — totales declarados por plantilla + señal de contraparte.

Dos huecos que la revisión de la ingesta BBVA feb–jul 2026 dejó al descubierto:

1. La plantilla de BBVA no extraía los totales que el propio estado imprime, así que
   `declared_count`/`coverage_ok` llegaban en None y el control cruzado de la regla 25 se saltaba
   EN SILENCIO: la conciliación daba OK verificando solo la identidad de saldo, que por sí sola no
   detecta una página no parseada cuyo neto sea cero.

2. Ninguna cuenta declaraba `counterparty_tokens`, de modo que las cuentas cuyo nombre no produce
   un token utilizable (Nu: 2 letras; Ualá: el token derivado lleva acento y el banco rotula sin
   él; HSBC: BBVA imprime el rótulo pegado, "SPEI RECIBIDOHSBC") NUNCA podían cumplir la señal
   adicional que exige la regla 26 — ni con ambos extremos cargados.

Al cerrar (2) apareció el riesgo inverso, que este test fija como regresión: un token de 2 letras
casa con la marca aislada de un comercio ("COMPRA NU SKIN"), y el candado de grado único NO lo
cubre —con UN cargo y UN abono del mismo importe el grado es 1 en ambos—, así que se vinculaba con
la misma confianza que un traspaso real y ambos movimientos salían del P&L.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance import dedup  # noqa: E402
from finance.extractors import bank_templates as BT  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


# --------------------------------------------------------------------------
# 1 · Totales declarados (plantilla BBVA)
# --------------------------------------------------------------------------
RESUMEN_BBVA = ("Periodo DEL 19/06/2026 AL 18/07/2026\n"
                "Saldo Promedio 1,234.00 Saldo Anterior 21.75\n"
                "Días del Periodo 30 Depósitos / Abonos (+) 2 14,729.00\n"
                "Tasa Bruta Anual % 0.000 Retiros / Cargos (-) 12 13,990.27\n")


def test_bbva_extrae_conteo_y_totales():
    d = BT.declared_totals(RESUMEN_BBVA, "bbva")
    check("BBVA declara n = abonos + cargos", d.get("n") == 14, f"n={d.get('n')}")
    check("BBVA declara créditos en positivo", d.get("credits") == "14,729.00", str(d.get("credits")))
    check("BBVA declara débitos en NEGATIVO (convención de reconcile)",
          d.get("debits") == "-13,990.27", str(d.get("debits")))


def test_cero_no_se_confunde_con_ausencia():
    txt = ("Depósitos / Abonos (+) 0 0.00\n"
           "Retiros / Cargos (-) 1 32.00\n")
    d = BT.declared_totals(txt, "bbva")
    check("un resumen en cero SÍ se declara (0 != ausente)",
          d.get("credits") == "0.00" and d.get("n") == 1, str(d))


def test_media_mitad_no_declara_conteo():
    # Si solo aparece una de las dos mitades, el conteo sería PARCIAL y `coverage_ok` marcaría un
    # descuadre falso en un estado que sí está completo. Se prefiere no declararlo.
    d = BT.declared_totals("Depósitos / Abonos (+) 3 1,000.00\n", "bbva")
    check("con media mitad NO se declara n", "n" not in d, str(d))
    check("con media mitad sí se conserva el importe visto", d.get("credits") == "1,000.00", str(d))


def test_banco_sin_plantilla_no_cambia_nada():
    for banco in ("nu", "uala", "vexi", "hsbc", None):
        d = BT.declared_totals(RESUMEN_BBVA, banco)
        check(f"banco {banco!r} sin plantilla -> {{}} (comportamiento previo intacto)", d == {}, str(d))


# --------------------------------------------------------------------------
# 2 · Señal de contraparte
# --------------------------------------------------------------------------
GRUPO = ["bbva_debito", "nu_debito", "nu_tdc", "uala_tdc", "vexi_tdc", "gbm_inversion", "hsbc_debito"]

# Tokens FIJOS: el test verifica el motor, no la config del usuario (que puede cambiar).
TOKENS = {
    "nu_debito": {"enviado nu", "recibido nu", "cuenta"},
    "nu_tdc": {"enviado nu", "recibido nu"},
    "uala_tdc": {"uala", "ualá"},
    "hsbc_debito": {"hsbc", "recibidohsbc"},
    "bbva_debito": {"bbva"},
}


def mv(tid, acc, amt, desc, fecha, tipo="debito"):
    return {"transaction_id": tid, "account_id": acc, "amount_minor": amt, "currency": "MXN",
            "description_norm": desc, "date_op": fecha, "account_type": tipo, "bank_ref": ""}


def enlaza(a, b):
    return len(dedup.find_transfers([a, b], [a, b], GRUPO, window_days=3, account_tokens=TOKENS))


def test_marca_comercial_no_vincula():
    # El fallo caro: un comercio que lleva el nombre del banco como palabra aislada, y un abono
    # ajeno del mismo importe dentro de la ventana. Antes se vinculaban con confianza 0.8.
    n = enlaza(mv("a", "bbva_debito", -12345, "compra nu skin", "2026-06-10"),
               mv("b", "nu_tdc", 12345, "devolucion de compra", "2026-06-10", "credito"))
    check("comercio 'NU SKIN' + devolución ajena NO se vinculan", n == 0, f"links={n}")

    n = enlaza(mv("c", "bbva_debito", -8000, "cafe nu centro", "2026-06-10"),
               mv("d", "nu_debito", 8000, "compra en tienda", "2026-06-11"))
    check("comercio 'CAFE NU' + cargo ajeno NO se vinculan", n == 0, f"links={n}")


def test_traspaso_real_sigue_vinculando():
    n = enlaza(mv("e", "bbva_debito", -102000, "spei enviado nu mexico", "2026-05-10"),
               mv("f", "nu_debito", 102000, "deposito recibido", "2026-05-10"))
    check("SPEI ENVIADO NU -> depósito en Nu sí vincula", n == 1, f"links={n}")


def test_rotulo_pegado_hsbc():
    # BBVA imprime la entrada sin separador; el token derivado del nombre no casa porque `\b` exige
    # límite de palabra entre "recibido" y "hsbc".
    n = enlaza(mv("g", "hsbc_debito", -50000, "spei enviado", "2026-05-08"),
               mv("h", "bbva_debito", 50000, "spei recibidohsbc", "2026-05-08"))
    check("rótulo pegado 'SPEI RECIBIDOHSBC' sí vincula", n == 1, f"links={n}")


def test_grafia_sin_acento_uala():
    n = enlaza(mv("i", "bbva_debito", -370620, "spei enviado uala", "2026-05-10"),
               mv("j", "uala_tdc", 370620, "pago recibido", "2026-05-10", "credito"))
    check("'UALA' sin acento sí vincula (el nombre lleva 'Ualá')", n == 1, f"links={n}")


def test_lexico_es_condicion_adicional_no_sustituto():
    # El léxico por sí solo no basta: sin token de contraparte no hay señal y no se vincula.
    n = enlaza(mv("k", "bbva_debito", -5000, "spei enviado a tercero", "2026-06-01"),
               mv("l", "nu_debito", 5000, "spei recibido de tercero", "2026-06-01"))
    check("léxico de traspaso SIN token de contraparte NO vincula", n == 0, f"links={n}")


def test_token_largo_no_exige_lexico():
    # La condición extra es solo para tokens de <=3 caracteres; los largos siguen bastando solos.
    check("token largo sin léxico sigue casando",
          dedup._mentions("pago a cuenta bbva del titular", {"bbva"}) is True)
    check("token corto sin léxico ya no casa",
          dedup._mentions("compra nu skin", {"nu"}) is False)
    check("token corto CON léxico sí casa",
          dedup._mentions("spei enviado nu mexico", {"nu"}) is True)


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    malos = [r for r in RESULTS if not r[1]]
    print()
    if malos:
        print(f"=== {len(malos)} FALLO(S) de {len(RESULTS)} ===")
        for n, _, d in malos:
            print(f"  - {n} :: {d}")
        sys.exit(1)
    print(f"=== TODO VERDE === ({len(RESULTS)} checks)")
