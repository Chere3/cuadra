# -*- coding: utf-8 -*-
"""
test_r21_bbva_tdc.py — BBVA tarjeta de crédito: plantilla propia, saldos del resumen e identificación.

Calibrado contra el estado real de jun–jul 2026 (el cuerpo de abajo es SINTÉTICO con la misma
maqueta). Lo que fija:
1. Mismo emisor, dos maquetas: con 'DESGLOSE DE MOVIMIENTOS' se usa la gramática de la tarjeta
   ('DD-mmm-AAAA DD-mmm-AAAA desc ; Tarjeta Digital ***NNNN ± $monto'); sin ella, la del débito.
2. Signo NATIVO de crédito: '+' cargo, '−' pago/devolución. Fecha de cargo en `date_post`. La tarjeta
   se aparta a `detail` y el renglón 'USD … TIPO DE CAMBIO …' se pega al movimiento anterior.
3. Los saldos del periodo salen del resumen ('Adeudo del periodo anterior' → 'Saldo deudor total:11'),
   y en un estado de DÉBITO de BBVA no se extrae ninguno (no hay rótulos).
4. Las líneas de puntos ('2026-07-05 POR COMPRAS …') y la distribución del último pago NO son
   movimientos.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.extractors import bank_templates as BT  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


TDC = """Página 1 de 6
TU PAGO REQUERIDO ESTE PERIODO
Periodo: 23-jun-2026 al 21-jul-2026
TARJETA CREA BBVA (CLASICA) Pago para no generar intereses:2 $2,010.73
RESUMEN DE CARGOS Y ABONOS DEL PERIODO INDICADORES DEL COSTO ANUAL TOTAL DE LA TARJETA
Adeudo del periodo anterior $0.00 Monto de intereses pagados en los últimos 12 $0.00
Cargos regulares (no a meses) + $2,015.73
Pagos y abonos - $5.00
Saldo cargos regulares: $2,010.73
Saldo deudor total:11 $2,010.73
DISTRIBUCIÓN DE TU ÚLTIMO PAGO
-$5.00 = +$5.00 +$0.00 +$0.00 +$0.00 +$0.00 +$0.00
DESGLOSE DE MOVIMIENTOS
CARGOS,COMPRAS Y ABONOS REGULARES(NO A MESES) Tarjeta titular: XXXXXXXXXXXX0000
23-jun-2026 24-jun-2026 PAY PAL*0000000000 ; Tarjeta Digital ***0000 + $5.00
23-jun-2026 24-jun-2026 PAY PAL*0000000000 ; Tarjeta Digital ***0000 - $5.00
25-jun-2026 26-jun-2026 COMERCIO EJEMPLO SUB ; Tarjeta Digital ***0000 + $1,770.94
USD $100.00 TIPO DE CAMBIO $17.71
10-jul-2026 13-jul-2026 OTRO COMERCIO ; Tarjeta Digital ***0000 + $239.79
TOTAL CARGOS $2,015.73
TOTAL ABONOS -$5.00
DETALLE DE TRANSACCIONES DE BENEFICIOS
2026-07-05 POR COMPRAS TARJETA DE CREDITO PUNTOS BBVA 179 0 0
"""

DEBITO = """Libretón Básico Cuenta Digital
Periodo DEL 19/06/2026 AL 18/07/2026
Saldo Anterior 100.00
OPER LIQ DESCRIPCION REFERENCIA CARGOS ABONOS OPERACION LIQUIDACION
20/JUN 20/JUN OXXO 50.00 50.00 50.00
Total de Movimientos
"""


def test_tarjeta_gramatica_y_signos():
    rows, _m, w = BT.parse_bank(TDC, "bbva", opening="0.00", closing="2,010.73",
                                period_start="2026-06-23", period_end="2026-07-21")
    imp = [float(r["amount"]) for r in rows]
    check("4 movimientos, signo nativo de crédito (+ cargo, − abono)",
          imp == [5.0, -5.0, 1770.94, 239.79], f"{imp} w={w}")
    check("puntos y distribución del último pago NO son movimientos", len(rows) == 4, str(len(rows)))
    check("fecha de operación y de cargo separadas",
          rows[2]["date_op"] == "25-jun-2026" and rows[2]["date_post"] == "26-jun-2026",
          f"{rows[2]['date_op']} / {rows[2]['date_post']}")
    check("descripción sin la tarjeta; tarjeta y tipo de cambio en detail",
          rows[2]["description"] == "COMERCIO EJEMPLO SUB"
          and rows[2]["detail"] == "Tarjeta Digital ***0000 | USD $100.00 TIPO DE CAMBIO $17.71",
          f"{rows[2]['description']!r} / {rows[2]['detail']!r}")
    check("sin dudosos ni avisos", not any(r["_flags"] for r in rows) and not w, str(w))
    check("Σ nativa == saldo deudor total", round(sum(imp), 2) == 2010.73, str(round(sum(imp), 2)))


def test_resumen_de_credito():
    cs = BT.credit_summary(TDC, "bbva")
    check("adeudo anterior y saldo deudor total (con nota al pie pegada)",
          cs == {"opening_raw": "0.00", "closing_raw": "2,010.73"}, str(cs))
    check("en el estado de DÉBITO no se extraen saldos de crédito", BT.credit_summary(DEBITO, "bbva") == {})


def test_debito_sigue_por_su_plantilla():
    rows, _m, _w = BT.parse_bank(DEBITO, "bbva", opening="100.00", closing="50.00",
                                 period_start="2026-06-19", period_end="2026-07-18")
    check("sin 'DESGLOSE DE MOVIMIENTOS' se usa la plantilla de débito (saldo por línea)",
          len(rows) == 1 and rows[0]["amount"] == "-50.00" and rows[0]["balance"] == "50.00",
          str(rows))


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
