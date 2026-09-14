# -*- coding: utf-8 -*-
"""
test_r15_periodo.py — detección del periodo del estado en sus distintos rotulados.

El periodo no es cosmético: asigna el estado a un mes (`audit-month`), da el `year_hint` para las
fechas DD/MMM sin año y, en la plantilla HSBC, es lo que permite fechar el día suelto de cada
renglón. Un estado sin periodo entra con movimientos correctos pero fuera de la cobertura del mes.

Ualá no escribe 'del X al Y': nombra el mes y cuántos días abarca. Datos ficticios.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.extractors.pdf_ext import _extract_period  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def test_rotulado_por_mes_y_dias():
    """Ualá: 'Periodo: Enero 2026 … Núm de días del periodo 31'."""
    got = _extract_period("Periodo: Enero 2026 Núm de días del periodo 31")
    check("mes + número de días -> mes natural completo",
          got == ("2026-01-01", "2026-01-31"), str(got))


def test_dias_que_no_cuadran_no_deducen_periodo():
    """Si el emisor pasa a un corte a mitad de mes, el número de días deja de coincidir con el mes
    nombrado: no se deduce nada en vez de fabricar un periodo (regla 00)."""
    got = _extract_period("Periodo: Enero 2026 Núm de días del periodo 30")
    check("días que no cuadran con el mes -> sin periodo", got == (None, None), str(got))


def test_mes_corto():
    got = _extract_period("Periodo: Febrero 2026 Núm de días del periodo 28")
    check("febrero se resuelve con su longitud real",
          got == ("2026-02-01", "2026-02-28"), str(got))


def test_rotulado_en_el_encabezado():
    """Vexi no escribe 'Periodo': lo declara en el encabezado del documento."""
    got = _extract_period("Estado de cuenta del 30/12/2025 al 03/01/2026")
    check("'Estado de cuenta del X al Y' también es periodo",
          got == ("2025-12-30", "2026-01-03"), str(got))


def test_no_confunde_la_letra_pequena():
    """El ancla evita tomar un 'del … al …' cualquiera del texto legal."""
    got = _extract_period("...pagos contados del 1 al 5 de cada mes naturales...")
    check("un 'del X al Y' sin ancla no es periodo", got == (None, None), str(got))


def test_formatos_previos_siguen_funcionando():
    a = _extract_period("Periodo: del 01 al 31 ene 2026")
    check("el rotulado 'del X al Y' sigue funcionando", a == ("2026-01-01", "2026-01-31"), str(a))
    b = _extract_period("Periodo: del 19 dic 2025 al 18 ene 2026")
    check("y el que lleva las dos fechas completas",
          b == ("2025-12-19", "2026-01-18"), str(b))
    c = _extract_period("Periodo: 29 MAY 2026 al 27 JUN 2026")
    check("y el de dos fechas con mes en texto", c == ("2026-05-29", "2026-06-27"), str(c))


def test_sin_periodo():
    check("un texto sin periodo no inventa uno",
          _extract_period("Estado de cuenta mensual") == (None, None))


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
