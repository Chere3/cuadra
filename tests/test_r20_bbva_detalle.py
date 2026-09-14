# -*- coding: utf-8 -*-
"""
test_r20_bbva_detalle.py — plantilla BBVA: detalle, fecha de liquidación, saldo y año por periodo.

Calibrado contra los siete estados reales de nov 2025–jul 2026 (el cuerpo de abajo es SINTÉTICO
con la misma maqueta; no se copia ningún estado). Lo que fija:

1. Los renglones de detalle que BBVA imprime bajo cada movimiento (RFC/hora/AUT/tarjeta en compras;
   concepto, referencia, banco, CLABE, clave de rastreo y contraparte en SPEI) se conservan en
   `detail`, también cuando el bloque cruza un salto de página: se filtra la MAQUETA (cabecera y
   pie), nunca el contenido. Y el detalle se corta en el total de movimientos.
2. La fecha de LIQUIDACIÓN se separa en `date_post` (regla 21) y el saldo impreso en `balance`.
3. Con periodo dic→ene, '28/DIC' lleva el año anterior y '05/ENE' el siguiente, en operación Y en
   liquidación. Sin periodo la fecha queda CRUDA: no se inventa un año.
4. `raw_text` sigue siendo solo el renglón del movimiento (la identidad de lo ya incorporado no
   cambia) y lo calibrado antes —signo por delta de saldo, tramos sin saldo, conciliación— no se mueve.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.extractors import bank_templates as BT  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


CUERPO = """Estado de Cuenta
Libretón Básico Cuenta Digital
PAGINA 1 / 2
Periodo DEL 19/12/2025 AL 18/01/2026
Saldo Anterior 1,000.00
Detalle de Movimientos Realizados
FECHA SALDO
OPER LIQ DESCRIPCION REFERENCIA CARGOS ABONOS OPERACION LIQUIDACION
28/DIC 27/DIC STARBUCKS ANDARES 97.00 903.00 903.00
RFC: CSI 020226MV4 08:12 AUT: 743744 Referencia ******7902
30/DIC 30/DIC SPEI RECIBIDOHSBC 5,000.00 5,903.00 5,903.00
1000000PORTABILIDAD DE NOMINA Referencia 0125923526 021
00012345678901234567
BBVA MEXICO, S.A., INSTITUCION DE BANCA MULTIPLE, GRUPO FINANCIERO BBVA MEXICO
Av. Paseo de la Reforma 510, Col. Juárez, Alcaldía Cuauhtémoc; C.P. 06600, Ciudad de México, México R.F.C. BBA830831LJ2
Estado de Cuenta
Libretón Básico Cuenta Digital
PAGINA 2 / 2
No. de Cuenta 0000000000
No. de Cliente D0000000
2025123040021NNNN0000000000001
NOMBRE APELLIDO APELLIDO
05/ENE 05/ENE OXXO TORREMOLINOS 41.50
RFC: CCO 8605231N4 16:21 AUT: 024963 Referencia ******7902
05/ENE 07/ENE SPEI ENVIADO NU MEXICO 1,850.00 4,011.50 4,011.50
0506260Pago TDC Referencia 0072745727 638
00098765432109876543
MBAN01002601050072745727
NOMBRE APELLIDO APELLIDO
Total de Movimientos
TOTAL IMPORTE CARGOS 1,988.50 TOTAL MOVIMIENTOS CARGOS 3
TOTAL IMPORTE ABONOS 5,000.00 TOTAL MOVIMIENTOS ABONOS 1
Saldo Final 4,011.50
"""


def _parse(**kw):
    rows, _m, w = BT.parse_bank(CUERPO, "bbva", opening="1,000.00", **kw)
    return rows, w


def test_signos_y_conciliacion_intactos():
    rows, w = _parse(period_start="2025-12-19", period_end="2026-01-18")
    importes = [float(r["amount"]) for r in rows]
    check("4 movimientos con el signo por delta de saldo",
          importes == [-97.0, 5000.0, -41.5, -1850.0], str(importes))
    check("saldo_anterior + Σ == saldo_final", round(1000.0 + sum(importes), 2) == 4011.50)
    check("sin dudosos ni avisos", not any(r["_flags"] for r in rows) and not w, str(w))
    check("raw_text es SOLO el renglón del movimiento",
          rows[1]["raw_text"] == "30/DIC 30/DIC SPEI RECIBIDOHSBC 5,000.00 5,903.00 5,903.00",
          rows[1]["raw_text"])


def test_fechas_por_periodo_y_liquidacion():
    rows, _ = _parse(period_start="2025-12-19", period_end="2026-01-18")
    check("operación: diciembre lleva 2025 y enero 2026",
          [r["date_op"] for r in rows] == ["2025-12-28", "2025-12-30", "2026-01-05", "2026-01-05"],
          str([r["date_op"] for r in rows]))
    check("liquidación separada en date_post, con el mismo criterio de año",
          [r["date_post"] for r in rows] == ["2025-12-27", "2025-12-30", "2026-01-05", "2026-01-07"],
          str([r["date_post"] for r in rows]))
    rows, _ = _parse()
    check("sin periodo la fecha queda cruda (no se inventa año)",
          rows[0]["date_op"] == "28/DIC" and rows[0]["date_post"] == "27/DIC",
          f"{rows[0]['date_op']} / {rows[0]['date_post']}")


def test_saldo_impreso():
    rows, _ = _parse(period_start="2025-12-19", period_end="2026-01-18")
    check("balance solo en las líneas que lo imprimen",
          [r["balance"] for r in rows] == ["903.00", "5903.00", None, "4011.50"],
          str([r["balance"] for r in rows]))


def test_detalle():
    rows, _ = _parse(period_start="2025-12-19", period_end="2026-01-18")
    d = [r["detail"] for r in rows]
    check("compra: RFC/AUT/tarjeta en detail",
          d[0] == "RFC: CSI 020226MV4 08:12 AUT: 743744 Referencia ******7902", d[0])
    check("SPEI que cruza página: concepto, CLABE, rastreo y contraparte se conservan",
          all(x in d[1] for x in ("PORTABILIDAD DE NOMINA", "Referencia 0125923526",
                                   "00012345678901234567", "2025123040021NNNN0000000000001",
                                   "NOMBRE APELLIDO APELLIDO")), d[1])
    check("SPEI que cruza página: la maqueta (cabecera/pie) NO entra al detalle",
          not any(x in d[1] for x in ("PAGINA", "No. de Cuenta", "No. de Cliente", "BBVA MEXICO",
                                      "Paseo de la Reforma", "Libretón", "Estado de Cuenta")), d[1])
    check("orden del detalle = orden del documento",
          d[1].split(" | ")[0].startswith("1000000PORTABILIDAD") and d[1].endswith("NOMBRE APELLIDO APELLIDO"),
          d[1])
    check("último movimiento: el detalle se corta en 'Total de Movimientos'",
          "Pago TDC" in d[3] and not any(x in d[3] for x in ("Total", "TOTAL", "Saldo Final")), d[3])
    check("movimiento sin saldo (tramo) también conserva su detalle",
          d[2].startswith("RFC: CCO 8605231N4"), d[2])


def test_bbva_iso_no_adivina_lejos():
    import datetime as dt
    ini, fin = dt.date(2025, 12, 19), dt.date(2026, 1, 18)
    check("fecha dentro del periodo", BT._bbva_iso("05/ENE", ini, fin) == "2026-01-05")
    check("lag de días fuera del periodo: el año más cercano", BT._bbva_iso("20/ENE", ini, fin) == "2026-01-20")
    check("a meses del periodo: None (no se inventa)", BT._bbva_iso("01/AGO", ini, fin) is None)
    check("año explícito se respeta", BT._bbva_iso("05/ENE/24", ini, fin) == "2024-01-05")
    check("token que no es fecha: None", BT._bbva_iso("hola", ini, fin) is None)


# Calibrado contra el estado real jul–ago 2026: 'PAGO CUENTA DE TERCERO' es abono cuando un tercero
# transfiere al titular ('Transf a JUAN PEREZ') y cargo cuando él envía; la línea es idéntica. Solo el
# saldo impreso decide. Y los movimientos finales del estado no traen saldo: los cierra el SALDO
# FINAL declarado.
TRAMOS = """Periodo DEL 19/07/2026 AL 18/08/2026
Saldo Anterior 9,066.38
OPER LIQ DESCRIPCION REFERENCIA CARGOS ABONOS OPERACION LIQUIDACION
25/JUL 27/JUL CARGO META 4,000.00
25/JUL 27/JUL SPEI ENVIADO STP 350.00
25/JUL 27/JUL KUESKI 1,304.38
25/JUL 27/JUL PAGO TARJETA DE CREDITO 2,000.00
25/JUL 27/JUL PAGO CUENTA DE TERCERO 300.00 1,712.00 9,066.38
BNET 0000000000 Transf a NOMBRE APE Referencia 0040265203
17/AGO 17/AGO SPEI ENVIADO STP 370.30
17/AGO 17/AGO SPEI ENVIADO STP 347.17
Total de Movimientos
"""


def test_tramo_mixto_por_saldo_y_cierre_por_saldo_final():
    rows, _m, w = BT.parse_bank(TRAMOS, "bbva", opening="9,066.38", closing="994.53",
                            period_start="2026-07-19", period_end="2026-08-18")
    imp = [float(r["amount"]) for r in rows]
    check("tramo mixto: la única asignación que cuadra con el saldo (300 abono, resto cargo)",
          imp[:5] == [-4000.0, -350.0, -1304.38, -2000.0, 300.0] and not any(r["_flags"] for r in rows[:5]),
          f"{imp[:5]} flags={[r['_flags'] for r in rows[:5]]}")
    check("tramo final sin saldo impreso: lo cierra el saldo final declarado (ambos cargos)",
          imp[5:] == [-370.30, -347.17] and not any(r["_flags"] for r in rows[5:]), str(imp[5:]))
    check("Σ cuadra con saldo final", round(9066.38 + sum(imp), 2) == 994.53)
    check("se avisa que el tramo se resolvió por saldo", any("resuelto por el saldo" in x for x in w), str(w))
    rows, _m, w = BT.parse_bank(TRAMOS, "bbva", opening="9,066.38",
                            period_start="2026-07-19", period_end="2026-08-18")
    check("sin saldo final: el tramo final cae al rótulo y AVISA (comportamiento previo)",
          [float(r["amount"]) for r in rows[5:]] == [-370.30, -347.17]
          and sum("sin saldo de cierre" in x for x in w) == 2, str(w))


def test_tramo_ambiguo_queda_dudoso():
    # dos importes iguales con signo cruzado: 100 − 100 = 0 casa con dos asignaciones → no se adivina
    cuerpo = """Periodo DEL 19/07/2026 AL 18/08/2026
Saldo Anterior 500.00
OPER LIQ DESCRIPCION REFERENCIA CARGOS ABONOS OPERACION LIQUIDACION
25/JUL 25/JUL KUESKI 100.00
25/JUL 25/JUL PAGO CUENTA DE TERCERO 100.00
25/JUL 25/JUL OXXO 50.00 450.00 450.00
Total de Movimientos
"""
    rows, _m, w = BT.parse_bank(cuerpo, "bbva", opening="500.00", closing="450.00",
                            period_start="2026-07-19", period_end="2026-08-18")
    check("tramo ambiguo: los tres quedan importe_dudoso (bloquea), no se adivina",
          all("importe_dudoso" in r["_flags"] for r in rows) and any("VARIAS" in x for x in w),
          f"flags={[r['_flags'] for r in rows]} w={w}")


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
