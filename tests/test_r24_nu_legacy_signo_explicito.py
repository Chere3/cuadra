"""R24: diseño anterior de la tarjeta Nu (2025). (a) Los abonos llevan '- $' delante del importe
y NO todos son pagos al emisor; (b) el resumen imprime saldos a favor como '- $188.50' y a veces
solo trae 'Saldo total del periodo'. Calibrado contra los estados reales de jun-2025, jul-2025,
oct-2025 y nov–dic-2025 (cuerpo sintético con la misma maqueta)."""
from finance.extractors import bank_templates as BT

CUERPO = """Periodo: 29 MAY 2025 - 27 JUN 2025 (30 días)
RESUMEN DE TRANSACCIONES MONTOS EN PESOS MEXICANOS
Saldo inicial del periodo (MAY 2025) - $188.50
Pagos a tu tarjeta en el periodo - $403.29
Compras $4,302.28
Abonos y devoluciones - $70.00
Saldo total del periodo $785.83
29 MAY ¡Muchas gracias! Pago a tu tarjeta de crédito - $403.29
29 MAY Otros Paypal*Patreonirel $70.00
08 DIC Ajuste Abono por plan de pagos fijos - $324.64
08 DIC Ajuste Abono por plan de pagos fijos $324.64
16 JUN Ajuste Ajuste de reversión parcial - $70.00
17 JUN Otros Devolución $12.00
SALDO A MESES CON O SIN INTERESES MONTOS EN PESOS MEXICANOS
08 JUN Disposición de saldo en $180.00 87.96% 1/1 $180.00 $0.00
17 JUL Mercadopago *Mercadol $795.81 0% 1/3 $265.27 $530.54
"""


def test_signo_explicito_y_total_de_pagos():
    rows, _, warnings = BT._parse_nu_legacy(CUERPO, 1)
    amts = [float(r["amount"]) for r in rows]
    assert amts == [-403.29, 70.0, -324.64, 324.64, -70.0, 12.0], amts
    # el pago cuadra con el total declarado: nada se marca dudoso por el cruce de abonos
    assert not any("importe_dudoso" in r["_flags"] for r in rows[:5])
    # una 'Devolución' SIN signo sigue siendo dudosa (no se adivina)
    assert "importe_dudoso" in rows[5]["_flags"]
    assert sum("excluida por rótulo" in w for w in warnings) == 2   # incluye la tasa '0%' sin decimales
    assert not any("no coinciden con el total de pagos" in w for w in warnings)


def test_resumen_con_saldo_a_favor_y_rotulo_alterno():
    cs = BT.credit_summary(CUERPO, "nu")
    assert cs == {"opening_raw": "-188.50", "closing_raw": "785.83"}, cs
    cs2 = BT.credit_summary("Saldo inicial del periodo (ABR 2025) $447.82\nSaldo final del periodo - $188.50\n", "nu")
    assert cs2 == {"opening_raw": "447.82", "closing_raw": "-188.50"}, cs2
