"""R25: Vexi rotula la penalización 'Comisión por pago tardío' / 'IVA pago tardío'. La palabra
'pago' la hacía pasar por abono y el estado descuadraba 2×(300+48). Solo 'Su pago... Gracias.'
es abono."""
from finance.extractors.bank_templates import _parse_cargo_abono


def test_pago_tardio_es_cargo():
    txt = ("MOVIMIENTOS DE LA CUENTA\n"
           "Fecha   Descripción   Monto\n"
           "20/02/2026   Su pago... Gracias.                              $   175.00\n"
           "23/02/2026   Comisión por pago tardío                         $   300.00\n"
           "23/02/2026   IVA pago tardío                                  $    48.00\n"
           "03/03/2026   Intereses saldo compras y comisiones             $    29.85\n")
    rows, _, _ = _parse_cargo_abono(txt, page=2)
    assert [float(r["amount"]) for r in rows] == [-175.0, 300.0, 48.0, 29.85]
