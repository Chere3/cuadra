# -*- coding: utf-8 -*-
"""
test_r22_uala_monto_previo.py — Ualá TDC: importe huérfano en la línea anterior y cierre del resumen.

Calibrado contra los estados reales de jun–jul 2026 (cuerpo SINTÉTICO con la misma maqueta):
1. Cuando el renglón no cabe, Ualá imprime el importe SOLO en la línea anterior a la de
   fecha+descripción. Se rescata con aviso (nunca en silencio) y raw_text conserva ambas líneas.
2. Una línea que no sea únicamente un importe firmado NO se usa como monto de otro movimiento.
3. El cierre del periodo es 'Pago para no generar intereses', no 'Saldo deudor total' (desde
   jun-2026 este último incluye el saldo a meses y no resta pagos).
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.extractors import bank_templates as BT  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


CUERPO = """Ualá Estado de cuenta
Adeudo del periodo anterior $1,000.00
Pago para no generar intereses² $1,265.00
Saldo deudor total: ¹¹ $2,436.99
Cargos, abonos y compras regulares (no a meses) Tarjeta titular 8167
02 Jul 2026 03 Jul 2026 STARBUCKS ANDARES CSI020226MV4 MEX + $97.00
+ $109.00
10 Jul 2026 11 Jul 2026 STARBUCKS ANDARES CSI020226MV4 MEX
15 Jul 2026 15 Jul 2026 PAGO SPEI - $100.00
BPAY GLOBAL D MANAMA CENTER 048
16 Jul 2026 17 Jul 2026 + $1,053.79
60.00USD (1USD= 17.56MXN)
17 Jul 2026 18 Jul 2026 SIN MONTO NI LINEA PREVIA VALIDA
Total cargos + $1,365.00
"""


def test_monto_en_linea_previa():
    rows, meta, w = BT.parse_bank(CUERPO, "uala")
    imp = [float(r["amount"]) for r in rows]
    check("4 movimientos; el de 109.00 se rescata, el sin monto se descarta",
          imp == [97.0, 109.0, -100.0, 1053.79], f"{imp} w={w}")
    r = rows[1]
    check("descripción y fecha del movimiento rescatado",
          r["date_op"] == "10 Jul 2026" and r["description"] == "STARBUCKS ANDARES CSI020226MV4 MEX",
          f"{r['date_op']} / {r['description']!r}")
    check("raw_text conserva ambas líneas", r["raw_text"] == "+ $109.00\n10 Jul 2026 11 Jul 2026 STARBUCKS ANDARES CSI020226MV4 MEX",
          repr(r["raw_text"]))
    check("el rescate deja aviso (nunca silencioso)",
          len(w) == 1 and "línea anterior" in w[0] and "109.00" in w[0], str(w))
    check("la descripción en línea previa (BPAY) sigue funcionando",
          rows[3]["description"] == "BPAY GLOBAL D MANAMA CENTER 048", rows[3]["description"])


def test_cierre_resumen():
    meta = BT.credit_summary(CUERPO, "uala")
    check("opening = adeudo anterior, closing = pago para no generar intereses",
          meta.get("opening_raw") == "1,000.00" and meta.get("closing_raw") == "1,265.00", str(meta))


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
