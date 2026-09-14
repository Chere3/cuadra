# -*- coding: utf-8 -*-
"""
test_r7_pdf_bancos.py — F5-R3-02: plantillas de extracción PDF por banco.

Genera PDFs SINTÉTICOS (ficticios) con layout real CARGO/ABONO/SALDO y verifica que la plantilla:
- se detecta por marcador de banco,
- separa el saldo del importe (no lo contamina),
- asigna el SIGNO correcto (cargo negativo / abono positivo) vía delta de saldo,
- reconcilia (opening + suma de movimientos == closing).

Fixtures dorados: se construyen en un tempdir por corrida (no hay estados reales en el repo).
"""
import os, sys, tempfile, shutil

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from reportlab.pdfgen import canvas  # noqa: E402
from finance.extractors.pdf_ext import extract_pdf  # noqa: E402
from finance.extractors import bank_templates as BT  # noqa: E402

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def make_pdf(path, header, lines):
    c = canvas.Canvas(path)
    y = 780
    for ln in [header] + lines:
        c.drawString(40, y, ln); y -= 22
    c.showPage(); c.save()


# (banco, header, [(desc, saldo)], opening, closing, esperados {desc: importe_firmado})
# BBVA/Nu/Ualá tienen sus tests de layout REAL en test_r9_bancos_reales.py. Aquí solo la estrategia
# genérica 'saldo' por delta de saldo (GBM inversión) y 'cargo_abono' (vexi) + fallback.
GOLDEN = {
    "gbm": dict(
        header="GBM Grupo Bursátil  Estado  Saldo inicial 5,000.00",
        lines=["2026-06-03 Aportacion recibida 2,000.00 7,000.00",
               "2026-06-08 Retiro parcial 800.00 6,200.00",
               "Saldo final 6,200.00"],
        opening=5000.0, closing=6200.0,
        expected={"Aportacion": 2000.0, "Retiro": -800.0}),
}


def case_saldo_bank(bank):
    g = GOLDEN[bank]
    d = tempfile.mkdtemp(prefix=f"pdf-{bank}-")
    try:
        p = os.path.join(d, f"{bank}.pdf")
        make_pdf(p, g["header"], g["lines"])
        ext = extract_pdf(p, account_hint=f"{bank}_debito")
        method_ok = ext["extraction_method"] == f"pdf_template:{bank}"
        # mapear importes por substring de descripción
        got = {}
        for r in ext["rows"]:
            for key in g["expected"]:
                if key.lower() in (r.get("description", "") or "").lower():
                    got[key] = float(r["amount"]) if r.get("amount") else None
        signs_ok = all(abs(got.get(k, 1e9) - v) < 0.01 for k, v in g["expected"].items())
        # reconciliación: opening + suma == closing
        total = sum(float(r["amount"]) for r in ext["rows"] if r.get("amount"))
        recon_ok = abs((g["opening"] + total) - g["closing"]) < 0.01
        check(f"F5-R3-02 {bank}: plantilla+signo+concilia",
              method_ok and signs_ok and recon_ok,
              f"method={method_ok} signos={got} concilia={recon_ok}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_fallback_generic():
    # un PDF sin marcador de banco cae al parser genérico (no rompe)
    d = tempfile.mkdtemp(prefix="pdf-gen-")
    try:
        p = os.path.join(d, "otro.pdf")
        make_pdf(p, "Estado de cuenta genérico", ["2026-06-05 PAGO SERVICIO 100.00"])
        ext = extract_pdf(p, account_hint=None)
        check("F5-R3-02 sin banco -> parser genérico (fail-safe)",
              ext["extraction_method"] in ("pdf_text", "pdf_no_text"),
              f"method={ext['extraction_method']}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_vexi_cargo_abono():
    # tarjeta sin saldo corriente: signo por rótulo CARGO/ABONO
    rows, meta, warn = BT.parse_bank(
        "Vexi Tarjeta\n2026-06-05 RESTAURANTE CARGO 450.00\n2026-06-20 PAGO ABONO 1,000.00",
        "vexi")
    m = {r["description"][:10]: float(r["amount"]) for r in rows if r.get("amount")}
    ok = any(v < 0 for v in m.values()) and any(v > 0 for v in m.values())
    check("F5-R3-02 vexi: CARGO negativo / ABONO positivo", ok, f"importes={m}")


if __name__ == "__main__":
    for b in ("gbm",):
        try:
            case_saldo_bank(b)
        except Exception as e:
            check(f"F5-R3-02 {b}", False, "EXCEPTION " + repr(e))
    for fn in (case_fallback_generic, case_vexi_cargo_abono):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
