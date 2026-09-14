"""R23: Nu débito (ago/nov-2025) parte un movimiento en tres renglones con la descripción a ambos
lados del importe: 'DD MMM <desc1>' / '+$monto' / 'YYYY <desc2>'. Antes el renglón con fecha no
tenía importe y el movimiento se perdía (conciliación bloqueada justo por ese monto)."""
from finance.extractors import bank_templates as BT

CUERPO = """08 AGO
Pago a tu tarjeta de crédito Nu -$1,516.63
2025
08 AGO JUAN PEREZ LOPEZ Transferencia de
+$4,909.00
2025 Nomina
Depósito SPEI, Hora: 20:20:56, Recibido de HSBC. Del cliente JUAN
09 AGO 2025 Supermercado Oxxo -$45.00
12 ABONOS
+$1.00
2025
"""


def test_tres_renglones_con_descripcion_partida():
    rows, _, warnings = BT._parse_signed(CUERPO, 1, bank="nu")
    amts = [float(r["amount"]) for r in rows]
    assert amts == [-1516.63, 4909.0, -45.0], amts
    assert rows[1]["date_op"] == "08 AGO 2025"
    assert rows[1]["description"] == "JUAN PEREZ LOPEZ Transferencia de Nomina"
    # la cabecera '12 ABONOS' no es un mes: no se recompone un falso movimiento
    assert not any("ABONOS" in r["description"] for r in rows)


def test_sin_anio_no_recompone():
    rows, _, _ = BT._parse_signed("08 AGO Algo sin importe\n+$10.00\nOtra cosa\n", 1, bank="nu")
    assert rows == []
