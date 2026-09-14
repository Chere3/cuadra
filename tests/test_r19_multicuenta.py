# -*- coding: utf-8 -*-
"""
test_r19_multicuenta.py — fallos de extracción que salieron al traer HSBC, Nu y Ualá de 2026.

Los cuatro son de la misma familia: el parser daba por buena una lectura PARCIAL del documento y
seguía adelante. Ninguno levantaba error; el único síntoma era una conciliación que no cuadraba —o,
peor, que sí cuadraba con datos incompletos.

1. Nu parte la FECHA en dos renglones cuando la descripción es larga ('04 MAR' / '<movimiento>' /
   '2026'). El renglón de en medio no empieza por fecha, así que se descartaba: 4 movimientos
   perdidos en un solo estado.
2. Dentro de una Cajita, Nu lista congelar y descongelar saldo con fecha e importe, pero ahí no
   entra ni sale dinero de la cuenta. Contarlos descuadraba el estado por su importe exacto.
3. HSBC separa los miles con un ESPACIO ('$ 4 431.00'). El patrón de importes solo conoce la coma,
   así que leía '431.00' y perdía el millar sin avisar: la cifra truncada sigue siendo un importe
   válido.
4. La tabla de HSBC no siempre empieza por el código de operación en letras: en las compras con
   tarjeta antepone el número de autorización. Exigir [A-Z]{2,7} tiraba esos renglones.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.extractors import bank_templates as BT     # noqa: E402
from finance.extractors.pdf_ext import _extract_period  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def _sum(rows):
    return round(sum(float(r["amount"]) for r in rows), 2)


# --------------------------------------------------------------------------
# 1 · Nu: fecha partida en dos renglones
# --------------------------------------------------------------------------
# Fixture FICTICIO (regla 70): comercios inventados, personas inventadas, cifras inventadas. Lo que
# se reproduce del documento real es solo la GRAMATICA del renglón partido, que es lo que se prueba.
NU_PARTIDA = """31 MAR 2026 TIENDA FICTICIA UNO Compra -$111.11
09 MAR
Persona Ficticia Uno Transferencia -$222.22
2026
06 MAR
PERSONA FICTICIA DOS Deposito pago servicio +$333.33
2026
04 MAR
PERSONA FICTICIA TRES Sin concepto +$4,444.44
2026
01 MAR 2026 Pago a tu tarjeta de credito Nu -$55.55
"""


def test_nu_fecha_partida():
    rows, _, _ = BT.parse_bank(NU_PARTIDA, "nu")
    check("Nu: los 3 movimientos con fecha partida se recuperan", len(rows) == 5, f"n={len(rows)}")
    fechas = [r["date_op"] for r in rows]
    check("Nu: la fecha recompuesta lleva el año del renglón de abajo",
          "09 MAR 2026" in fechas and "04 MAR 2026" in fechas, str(fechas))
    check("Nu: el importe firmado sobrevive a la recomposición",
          _sum(rows) == round(-111.11 - 222.22 + 333.33 + 4444.44 - 55.55, 2), str(_sum(rows)))


def test_nu_fecha_partida_no_inventa():
    # Las tres condiciones son necesarias: sin el año debajo, con fecha completa en medio, o sin
    # importe firmado, NO se une nada. Si el patrón fuera laxo, uniría renglones sin relación.
    casos = [
        ("sin año debajo", "04 MAR\nPERSONA FICTICIA Sin concepto +$100.00\nno es un año\n"),
        ("el de en medio ya trae fecha", "02 MAR\n05 ABR 2026 otro +$50.00\n2026\n"),
        ("sin importe firmado", "04 MAR\nsolo texto\n2026\n"),
    ]
    for nombre, txt in casos:
        rows, _, _ = BT.parse_bank(txt + "10 ENE 2026 control +$1.00\n", "nu")
        descs = [r["description"] for r in rows]
        check(f"Nu: no une cuando falta una condición ({nombre})",
              all("Sin concepto" not in d and "solo texto" not in d for d in descs), str(descs))


# --------------------------------------------------------------------------
# 2 · Nu: congelar / descongelar saldo dentro de una Cajita
# --------------------------------------------------------------------------
NU_CAJITA = """12 ABR 2026 Deposito en Cajita: Fondo de emergencia -$400.00
19 ABR 2026 Descongelamos saldo de tu Cajita: Computadora +$700.91
12 ABR 2026 Congelaste saldo hasta el 10 MAY 2026 en tu Cajita: Fondo -$400.00
12 ABR 2026 Deposito en Cajita: Fondo de emergencia +$400.00
"""


def test_nu_congelar_no_es_movimiento():
    rows, _, w = BT.parse_bank(NU_CAJITA, "nu")
    descs = [r["description"] for r in rows]
    check("Nu: congelar/descongelar saldo NO entra como movimiento",
          len(rows) == 2 and not any("ongela" in d for d in descs), str(descs))
    check("Nu: 'Depósito en Cajita' (movimiento real) SÍ se conserva",
          _sum(rows) == 0.00 and len(rows) == 2, str(_sum(rows)))
    check("Nu: la exclusión deja constancia (nunca en silencio)",
          sum(1 for x in w if "excluida por rótulo" in x) == 2, str(len(w)))


def test_nu_congelar_con_descripcion_en_linea_previa():
    # Cuando el texto no cabe, Nu deja la descripción ARRIBA y el renglón con fecha e importe se
    # queda sin rótulo. Buscar el rótulo en el renglón (y no en la descripción ya resuelta) dejaba
    # pasar justo estas: fue el caso real de abril de 2026.
    txt = ("Congelaste saldo hasta el 10 MAY 2026 en tu Cajita: Fondo de emergencia\n"
           "12 ABR 2026 -$400.00\n"
           "Deposito en Cajita: Computadora\n"
           "12 ABR 2026 -$700.00\n")
    rows, _, _ = BT.parse_bank(txt, "nu")
    check("Nu: se excluye también con la descripción en la línea anterior",
          len(rows) == 1 and "Congelaste" not in rows[0]["description"],
          str([r["description"] for r in rows]))


# --------------------------------------------------------------------------
# 3 y 4 · HSBC: separador de miles con espacio, y renglón que empieza por número
# --------------------------------------------------------------------------
# Fixture FICTICIO (regla 70): seriales, autorizaciones y cifras inventados. Lo reproducido del
# documento real es la FORMA: miles separados con espacio, y un renglón que empieza por el número
# de autorización en vez del código de operación en letras.
HSBC = """Dia Descripcion Serial Retiro/Cargo Deposito/Abono Saldo
10 NETNM NOMINA FICTICIA CON 11110000 $ 4 931.00 $ 4 931.00
20 CGO Transferencia entre cuentas 22220000 $ 500.00 $ 4 431.00
27  999999999999999999999999COMERCIO FICTICIO 33330000 $ 1,740.52 $ 2,690.48
Telefono: 55 5721 3390 a nivel nacional
"""


def test_hsbc_miles_con_espacio():
    rows, _, _ = BT._parse_hsbc(HSBC, None, 0.0, "2026-02-01", "2026-02-28")
    montos = [float(r["amount"]) for r in rows]
    check("HSBC: '4 931.00' se lee completo, no '931.00'", 4931.00 in montos, str(montos))
    check("HSBC: el saldo con espacio también (si no, el delta no cuadra y todo queda dudoso)",
          not any("importe_dudoso" in r["_flags"] for r in rows),
          str([r["_flags"] for r in rows]))


def test_hsbc_renglon_que_empieza_por_numero():
    rows, _, _ = BT._parse_hsbc(HSBC, None, 0.0, "2026-02-01", "2026-02-28")
    check("HSBC: la compra con número de autorización delante se extrae", len(rows) == 3,
          f"n={len(rows)} descs={[r['description'][:28] for r in rows]}")
    check("HSBC: la cadena de saldos cierra donde debe",
          rows[-1]["balance"] == "2690.48", str(rows[-1]["balance"]))


def test_hsbc_no_ingiere_lineas_sueltas():
    # El teléfono lleva dos grupos de dígitos y empieza por letra: ni se une ni se ingiere. Es la
    # contraparte del patrón estrecho de miles.
    rows, _, _ = BT._parse_hsbc(HSBC, None, 0.0, "2026-02-01", "2026-02-28")
    check("HSBC: una línea de contacto no se vuelve movimiento",
          not any("5721" in (r["description"] or "") for r in rows),
          str([r["description"][:24] for r in rows]))


# --------------------------------------------------------------------------
# 5 · Periodo derivado de la fecha de corte (Ualá y el layout a dos columnas de Nu)
# --------------------------------------------------------------------------
def test_periodo_por_fecha_de_corte():
    casos = [
        # Ualá cuenta los días INCLUSIVE: 29 para un febrero de 28. Por eso el mes natural no sirve.
        ("Fecha de corte 28 Feb 2026\nPeriodo: Febrero 2026 Num de dias del periodo 29",
         ("2026-01-31", "2026-02-28")),
        ("Fecha de corte 30 Abr 2026\nPeriodo: Abril 2026 Num de dias del periodo 31",
         ("2026-03-31", "2026-04-30")),
        # Nu, layout a dos columnas: 'Periodo:' y el rango quedan en renglones distintos.
        # El domicilio del titular es lo que en el documento real queda pegado al rango; aquí va
        # una calle y un código postal FICTICIOS (regla 70) porque lo que se prueba es el estorbo,
        # no el dato.
        ("CALLE FICTICIA 000 29 MAR 2026 al 27 ABR\nPeriodo:\n00000 Fecha de corte: 27 ABR 2026\n"
         "Numero de dias en el periodo: 30 dias", ("2026-03-29", "2026-04-27")),
    ]
    for txt, exp in casos:
        got = _extract_period(txt)
        check(f"periodo por corte -> {exp[0]}..{exp[1]}", got == exp, f"got={got}")


def test_periodo_por_corte_no_inventa():
    check("sin número de días no se deriva periodo",
          _extract_period("Fecha de corte 30 Mayo 2026") == (None, None))
    check("un número de días fuera de un ciclo plausible no deriva nada",
          _extract_period("Fecha de corte 30 Mayo 2026\nNumero de dias en el periodo: 400") ==
          (None, None))
    check("el rango explícito sigue teniendo prioridad sobre el corte",
          _extract_period("Periodo DEL 19/06/2026 AL 18/07/2026\nFecha de corte: 18 JUL 2026\n"
                          "Numero de dias en el periodo: 30 dias") == ("2026-06-19", "2026-07-18"))


# --------------------------------------------------------------------------
# 6 · Regresiones que salieron de la auditoría de estos mismos cambios
# --------------------------------------------------------------------------
def test_miles_solo_dentro_del_importe():
    """La restitución del millar va anclada al '$'. Sin ese ancla rompía por los dos lados: fundía
    un número suelto con el importe siguiente, y convertía el arranque del renglón en '05,123',
    con lo que la fila desaparecía sin ningún aviso."""
    # un número suelto delante de un importe NO se funde con él
    rows, _, _ = BT._parse_hsbc("10 123456 FOLIO 12 345.67 $ 20,000.00\n", None, 7654.33,
                                "2026-02-01", "2026-02-28")
    montos = [float(r["amount"]) for r in rows]
    check("no se fabrica un importe fundiendo un número suelto con el de al lado",
          12345.67 not in montos, str(montos))
    # y el renglón cuyo segundo token son 3 dígitos sigue siendo un movimiento
    rows, _, _ = BT._parse_hsbc("05 123 DEPOSITO $ 1,000.00 $ 5,000.00\n"
                                "06 OTRO CARGO $ 500.00 $ 4,500.00\n", None, 4000.0,
                                "2026-02-01", "2026-02-28")
    check("un renglón que empieza por 'DD 123' no desaparece", len(rows) == 2, f"n={len(rows)}")
    # y lo que motivó el cambio sigue leyéndose completo
    rows, _, _ = BT._parse_hsbc("10 NOMINA FICTICIA $ 4 931.00 $ 4 931.00\n", None, 0.0,
                                "2026-02-01", "2026-02-28")
    check("el millar separado por espacio se sigue restituyendo",
          [float(r["amount"]) for r in rows] == [4931.00], str([r["amount"] for r in rows]))


def test_hsbc_avisa_de_los_renglones_sueltos():
    """Un candidato a movimiento sin su saldo no se ingiere (no se inventa el par) pero tampoco se
    descarta callando."""
    _rows, _, w = BT._parse_hsbc("10 NOMINA FICTICIA $ 1,000.00\n", None, 0.0,
                                 "2026-02-01", "2026-02-28")
    check("un renglón con día y un solo importe deja aviso",
          any("un solo importe" in x for x in w), str(w))


def test_fecha_partida_exige_un_mes_de_verdad():
    """Una cabecera de columna como '12 ABONOS' entre un importe y un año suelto se recomponía en
    un movimiento inexistente con fecha '12 ABONOS 2026'."""
    rows, _, _ = BT.parse_bank("12 ABONOS\nlinea con importe +$10.00\n2026\n"
                               "05 ENE 2026 movimiento de control -$1.00\n", "nu")
    check("una cabecera con palabra que no es mes no fabrica movimiento",
          len(rows) == 1 and rows[0]["date_op"] == "05 ENE 2026",
          str([r["date_op"] for r in rows]))


def test_exclusion_por_rotulo_no_pierde_su_aviso():
    """Si la exclusión se lleva TODAS las filas, `_parse_signed` reintentaba con la gramática del
    diseño anterior y descartaba los warnings acumulados: 0 filas y 0 avisos, que es exactamente la
    exclusión silenciosa que prohíbe la regla 21."""
    txt = ("12 ABR 2026 Congelaste saldo en tu Cajita: Ficticia -$400.00\n"
           "13 ABR 2026 Descongelamos saldo de tu Cajita: Ficticia +$400.00\n")
    rows, _, w = BT.parse_bank(txt, "nu")
    check("excluirlo todo no deja el estado sin avisos",
          len(rows) == 0 and any("excluida por rótulo" in x for x in w),
          f"filas={len(rows)} warnings={len(w)}")


def test_mes_contable_lo_fija_el_cierre_del_periodo():
    """`month_close` se indexaba por el INICIO del periodo. Con un ciclo de tarjeta que arranca el
    último día del mes anterior, el estado de febrero sobrescribía el registro de enero y febrero
    se quedaba sin registro."""
    import sqlite3
    from finance import services as _S
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE month_close(account_id TEXT, month TEXT, received INT, "
                "period_covered TEXT, imported_at TEXT, reconciliation TEXT, status TEXT, "
                "PRIMARY KEY(account_id,month))")
    for ini, fin in (("2025-12-31", "2026-01-30"), ("2026-01-31", "2026-02-28")):
        _S._update_month_close(con, {"account_id": "tarjeta_ficticia", "period_start": ini,
                                     "period_end": fin, "reconciliation": {"result": "OK"}})
    meses = sorted(m for (m,) in con.execute("SELECT month FROM month_close"))
    check("dos ciclos que arrancan a fin de mes producen DOS registros mensuales distintos",
          meses == ["2026-01", "2026-02"], str(meses))


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
