# -*- coding: utf-8 -*-
"""
test_r13_hsbc.py — plantilla HSBC + respaldo OCR para PDF sin texto legible.

HSBC tiene dos particularidades que ninguna otra plantilla cubría:

1. La tabla lista el **día suelto** (columna 'Día'), no la fecha completa: el mes y el año salen
   del periodo del estado. Sin periodo no se puede fechar y NO se inventa (regla 00).
2. Sus PDF usan fuentes **Type 3**, así que pdfplumber devuelve glifos sin mapa Unicode. Hay
   'texto', pero ni una fecha ni un importe: el extractor creía tener texto nativo y devolvía
   cero movimientos en silencio. Ahora se detecta y se cae a OCR.

El signo no se puede leer del texto plano (las columnas Retiro/Cargo y Depósito/Abono se colapsan
al extraer), así que se toma del delta del saldo corriente y se cruza-valida contra el importe
declarado. Ese cruce es la red de seguridad del OCR: un dígito mal leído rompe la igualdad y queda
`importe_dudoso`, que bloquea el commit.

Datos totalmente ficticios.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.extractors import bank_templates as BT  # noqa: E402
from finance.extractors.pdf_ext import (  # noqa: E402
    _lineas_dudosas_cruzadas, _marcar_filas_dudosas, _texto_inservible)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


# Layout real de HSBC (cifras inventadas). Arranca en 1,000.00:
#   +2,500.00 -> 3,500.00 | -1,200.00 -> 2,300.00 | +800.00 -> 3,100.00
CUERPO = """HSBC MEXICO ESTADO DE CUENTA
Día Descripción Referencia Retiro/Cargo Depósito/Abono Saldo
05 NETNM NOMINA 1Q ENE CON 111111 $2,500.00 $3,500.00
12 CGO Transferencia SPEI 222222 $1,200.00 $2,300.00
27 NETNM NOMINA 2Q ENE CON 333333 $800.00 $3,100.00
Saldo Inicial $1,000.00
Saldo Final $3,100.00
"""


def test_detecta_banco():
    check("detect_bank reconoce HSBC", BT.detect_bank(CUERPO) == "hsbc")


def test_parsea_con_dia_suelto():
    rows, _m, w = BT.parse_bank(CUERPO, "hsbc", opening="1000.00", period_start="2026-01-01",
                                period_end="2026-01-31")
    check("extrae los 3 movimientos", len(rows) == 3, f"obtuvo {len(rows)}")
    if len(rows) != 3:
        return
    fechas = [r["date_op"] for r in rows]
    check("compone la fecha con el mes/año del periodo",
          fechas == ["2026-01-05", "2026-01-12", "2026-01-27"], str(fechas))
    montos = [float(r["amount"]) for r in rows]
    check("el signo sale del delta de saldo (abono +, cargo -)",
          montos == [2500.00, -1200.00, 800.00], str(montos))
    check("ningún movimiento queda dudoso", all(not r["_flags"] for r in rows),
          str([r["_flags"] for r in rows]))
    check("concilia: inicial + Σ movimientos == final",
          abs(1000.00 + sum(montos) - 3100.00) < 0.005)


def test_importe_que_no_cuadra_queda_dudoso():
    """Un dígito mal leído por OCR: el importe declarado deja de cuadrar con el delta del saldo."""
    corrupto = CUERPO.replace("$2,500.00 $3,500.00", "$2,600.00 $3,500.00")
    rows, _m, _w = BT.parse_bank(corrupto, "hsbc", opening="1000.00", period_start="2026-01-01",
                                 period_end="2026-01-31")
    dudoso = rows and "importe_dudoso" in rows[0]["_flags"]
    check("importe que no cuadra con el delta se marca dudoso (bloquea)", bool(dudoso))


def test_sin_periodo_no_inventa_fecha():
    rows, _m, w = BT.parse_bank(CUERPO, "hsbc", opening="1000.00", period_start=None,
                                period_end=None)
    check("sin periodo no fecha ni inventa (regla 00)", rows == [] and bool(w),
          f"filas={len(rows)}")


def test_corte_que_cruza_dos_meses():
    """Regresión: usar el mes de `period_start` para todos los renglones fechaba el día 05 de un
    corte 19/dic–18/ene en DICIEMBRE — mes contable y ejercicio equivocados, sin warning y
    conciliando OK, porque la conciliación solo suma importes."""
    cuerpo = ("HSBC MEXICO\n"
              "Día Descripción Referencia Retiro/Cargo Depósito/Abono Saldo\n"
              "20 CGO Pago servicio 111111 $500.00 $9,500.00\n"
              "05 NETNM Nomina 222222 $1,500.00 $11,000.00\n")
    rows, _m, _w = BT.parse_bank(cuerpo, "hsbc", opening="10000.00",
                                 period_start="2025-12-19", period_end="2026-01-18")
    fechas = [r["date_op"] for r in rows]
    check("el día 20 queda en diciembre y el 05 en enero",
          fechas == ["2025-12-20", "2026-01-05"], str(fechas))


def test_dia_imposible_no_se_descarta():
    """Un renglón que no se puede fechar se CONSERVA (regla 00: no eliminar movimientos para
    cuadrar). Sin fecha ISO delante, la normalización lo deja `fecha_dudosa`, que bloquea."""
    raro = CUERPO.replace("27 NETNM NOMINA 2Q", "31 NETNM NOMINA 2Q")
    rows, _m, w = BT.parse_bank(raro, "hsbc", opening="1000.00", period_start="2026-02-01",
                                period_end="2026-02-28")
    # febrero 2026 tiene 28 días: el 31 no existe, pero su importe y su saldo no se tiran.
    fechado = [r for r in rows if r["date_op"].startswith("2026-")]
    check("el renglón no fechable se conserva, no se omite",
          len(rows) == 3 and len(fechado) == 2, f"filas={len(rows)} fechadas={len(fechado)}")
    check("y se avisa que quedó dudoso", any("no se puede fechar" in x for x in w))


def test_dia_ambiguo_en_periodo_largo():
    """Si el periodo abarca el mismo día dos veces, no se adivina cuál es."""
    cuerpo = ("HSBC MEXICO\n"
              "10 CGO Pago 111111 $500.00 $9,500.00\n")
    rows, _m, w = BT.parse_bank(cuerpo, "hsbc", opening="10000.00",
                                period_start="2026-01-01", period_end="2026-02-28")
    check("día que cae dos veces en el periodo no se fecha",
          bool(rows) and not rows[0]["date_op"].startswith("2026-"),
          str([r["date_op"] for r in rows]))


def test_detecta_pdf_con_texto_basura():
    cid = "(cid:12)(cid:34)(cid:56)" * 40
    check("texto con (cid:NN) se declara inservible", _texto_inservible(cid) is True)
    check("texto largo sin un solo importe se declara inservible",
          _texto_inservible("SIN CIFRAS " * 80) is True)
    check("un estado normal NO se declara inservible",
          _texto_inservible(CUERPO * 10) is False)
    # Regresión: los estados de BBVA traen cientos de '(cid:NN)' de las tipografías del
    # encabezado y su tabla se lee perfecto. Decidir por ese marcador los mandaba a OCR, que
    # extraía CERO filas y tumbaba una conciliación que antes cerraba en 0.
    check("texto con muchos (cid:) pero CON importes es utilizable",
          _texto_inservible("(cid:9)" * 400 + CUERPO) is False)


def test_plantilla_ocr_cuenta_como_fuente_nativa():
    """Regresión CRÍTICA: `startswith('pdf_template')` daba FALSO para `pdf_ocr_template:*`, así
    que una tarjeta (`sign_convention: inverted`) leída por OCR no invertía importes NI saldos.
    Y no se notaba: al no invertirse ninguno de los dos, opening+Σ==closing seguía cuadrando, la
    conciliación daba OK y cada cargo se exportaba como INGRESO."""
    from finance.services import _RX_PLANTILLA_NATIVA
    casos = {"pdf_template:nu": True, "pdf_ocr_template:nu": True, "pdf_ocr_template:hsbc": True,
             "csv": False, "pdf_text": False, "ocr": False, "image": False}
    malos = [m for m, esp in casos.items() if bool(_RX_PLANTILLA_NATIVA.match(m)) != esp]
    check("las plantillas OCR cuentan como fuente de signos nativos", not malos, str(malos))


def test_paginas_sin_cifras_no_disparan_ocr():
    """Regresión: evaluar `_texto_inservible` PÁGINA A PÁGINA mandaba a OCR documentos que se
    leían perfectamente, porque los estados traen páginas enteras de avisos legales y publicidad
    sin una sola cifra. Costó 42 de 52 movimientos en una prueba con documentos reales.

    El caso mixto (resumen legible + tabla ilegible) NO se resuelve aquí sino con el reintento de
    `extract_pdf`, que actúa por el síntoma: cero filas extraídas + glifos rotos en el texto.
    """
    legales = "AVISO DE PRIVACIDAD Y TERMINOS DEL CONTRATO. " * 30      # larga y sin cifras
    tabla = "05 NETNM Nomina 111111 $2,500.00 $3,500.00\n" * 5
    check("una página de avisos legales por sí sola parece inservible",
          _texto_inservible(legales) is True)
    check("pero el documento completo NO se manda a OCR",
          _texto_inservible(legales + tabla) is False)


def test_indicio_de_texto_roto():
    from finance.extractors.pdf_ext import _INDICIO_TEXTO_ROTO
    check("los glifos sin mapa se reconocen como indicio",
          bool(_INDICIO_TEXTO_ROTO.search("(cid:44)(cid:12)")) is True)
    check("y un estado normal no lo dispara",
          bool(_INDICIO_TEXTO_ROTO.search(CUERPO)) is False)


def test_ocr_cruza_campos_criticos_entre_resoluciones():
    primaria = ["30 COMPRA EJEMPLO $ 246.61 $ 641.53"]
    check("una palabra borrosa no bloquea si fecha e importes coinciden en dos resoluciones",
          _lineas_dudosas_cruzadas(primaria, [17.0], primaria, 40) == [])
    secundaria_fecha_distinta = ["20 COMPRA EJEMPLO $ 246.61 $ 641.53"]
    check("una discrepancia de fecha bloquea aunque la lectura primaria tenga confianza alta",
          _lineas_dudosas_cruzadas(primaria, [95.0], secundaria_fecha_distinta, 40) == primaria)


def test_comprobante_con_importe_repetido_no_contamina_movimiento():
    rows = [{"raw_text": "2026-05-05 MOVIMIENTO $ 100.00 $ 900.00", "_flags": []}]
    tocadas = _marcar_filas_dudosas(rows, ["05/05/2026 COMPROBANTE $100.00"])
    check("un comprobante de un importe no marca una fila con importe y saldo",
          tocadas == 0 and rows[0]["_flags"] == [])
    tocadas = _marcar_filas_dudosas(rows, ["05 MOVIMIENTO $ 100.00 $ 900.00"])
    check("la misma pareja importe/saldo sí marca la fila correspondiente",
          tocadas == 1 and "importe_dudoso" in rows[0]["_flags"])


if __name__ == "__main__":
    test_detecta_banco()
    test_parsea_con_dia_suelto()
    test_importe_que_no_cuadra_queda_dudoso()
    test_sin_periodo_no_inventa_fecha()
    test_corte_que_cruza_dos_meses()
    test_dia_imposible_no_se_descarta()
    test_dia_ambiguo_en_periodo_largo()
    test_detecta_pdf_con_texto_basura()
    test_plantilla_ocr_cuenta_como_fuente_nativa()
    test_paginas_sin_cifras_no_disparan_ocr()
    test_indicio_de_texto_roto()
    test_ocr_cruza_campos_criticos_entre_resoluciones()
    test_comprobante_con_importe_repetido_no_contamina_movimiento()
    malos = [r for r in RESULTS if not r[1]]
    print()
    if malos:
        print(f"=== {len(malos)} FALLO(S) de {len(RESULTS)} ===")
        sys.exit(1)
    print(f"=== TODO VERDE === ({len(RESULTS)} checks)")
