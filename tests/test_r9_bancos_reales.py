# -*- coding: utf-8 -*-
"""
test_r9_bancos_reales.py — Fixtures DORADOS por banco (F5-R3-02), derivados del LAYOUT de estados
reales (calibrados con estados enmascarados). Cifras/datos FICTICIOS; el estado real no se versiona.

Cada caso construye un PDF sintético con el layout del banco y corre el PIPELINE completo
(extracción → normalización → conciliación), verificando: cuenta identificada, fechas OK, signos
correctos (cargo negativo / abono positivo) y CONCILIACIÓN OK (opening + Σ == closing).
"""
import os, sys, json, shutil, tempfile
from reportlab.pdfgen import canvas

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fresh():
    d = tempfile.mkdtemp(prefix="r9-")
    for sub in ("src", "config", "fixtures", "schemas"):
        shutil.copytree(os.path.join(SRC_ROOT, sub), os.path.join(d, sub))
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    return d


def load(root):
    os.environ["FINANCE_ROOT"] = root
    for n in list(sys.modules):
        if n == "finance" or n.startswith("finance."):
            del sys.modules[n]
    sys.path.insert(0, os.path.join(root, "src"))
    from finance import services as S, config as C
    C.reset_cache()
    return S


def make_pdf(path, lines):
    c = canvas.Canvas(path); y = 790
    for ln in lines:
        c.drawString(36, y, ln); y -= 20
        if y < 40:
            c.showPage(); y = 790
    c.showPage(); c.save()


RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def _ingest(root, S, path, run):
    S.ingest([path], dry_run=True, run_id=run)
    return json.load(open(os.path.join(root, "staging", run, "_full.json")))["statements"][0]


def case_bbva():
    # Layout BBVA débito: 'OPER LIQ DESC ... CARGOS ABONOS OPERACION LIQUIDACION' (importe=1er nº,
    # saldos al final; líneas sin saldo se firman por delta del tramo). Header con periodo y saldos.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "bbva_gold.pdf")
    make_pdf(p, [
        # R21: la cuenta de débito ya no se identifica por la marca 'BBVA' (la comparte con la
        # tarjeta de crédito del mismo emisor) sino por el nombre del PRODUCTO, que el estado real
        # imprime en todas sus páginas.
        "BBVA Bancomer  Estado de Cuenta  Libretón Básico Cuenta Digital  Cuenta de Nomina",
        "Periodo DEL 01/06/2026 AL 30/06/2026",
        "Saldo Anterior 1,000.00",
        "OPER LIQ DESCRIPCION REFERENCIA CARGOS ABONOS OPERACION LIQUIDACION",
        "05/JUN 05/JUN DEPOSITO NOMINA ACME 3,000.00 4,000.00 4,000.00",
        "06/JUN 06/JUN OXXO TIENDA 1234 200.00",                 # sin saldo (mismo dia)
        "07/JUN 07/JUN UBER TRIP 150.00 3,650.00 3,650.00",       # cierra el tramo (OXXO+UBER cargos)
        "08/JUN 08/JUN SPEI RECIBIDO HSBC 500.00 4,150.00 4,150.00",
        "Saldo Final 4,150.00",
    ])
    d = _ingest(root, S, p, "b")
    signos = {t["description_norm"]: t["amount_minor"] for t in d["txns"]}
    ok_signos = (any("NOMINA" in k and v == 300000 for k, v in signos.items()) and
                 any("OXXO" in k and v == -20000 for k, v in signos.items()) and
                 any("UBER" in k and v == -15000 for k, v in signos.items()) and
                 any("RECIBIDO" in k and v == 50000 for k, v in signos.items()))
    check("F5-R3-02 BBVA: cuenta+fechas+signos+concilia",
          d["account_id"] == "bbva_debito" and len(d["txns"]) == 4 and
          all(t["date_op"] for t in d["txns"]) and ok_signos and
          d["reconciliation"]["result"] == "OK" and d["state"] == "READY_TO_COMMIT",
          f"cuenta={d['account_id']} n={len(d['txns'])} recon={d['reconciliation']['result']} "
          f"estado={d['state']} signos={signos}")


def case_nu_debito():
    # Layout Nu débito: importe con SIGNO explícito (+$/−$), fecha 'DD MMM YYYY'. Saldos con la
    # terminología de Nu ('Saldo inicial', 'Saldo al generar este estado de cuenta').
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_deb_gold.pdf")
    make_pdf(p, [
        "Nu México  Cuenta Nu  CLABE ##########",
        "Periodo: del 01 al 30 jun 2026",
        "Saldo inicial $0.00",
        "05 JUN 2026 Deposito recibido +$6,000.00",
        "07 JUN 2026 eBay Compra -$2,328.88",
        "10 JUN 2026 Pago de Prestamo Personal Nu -$1,920.00",
        "15 JUN 2026 STARBUCKS Devolucion +$1.00",
        "Saldo al generar este estado de cuenta $1,752.12",
    ])
    d = _ingest(root, S, p, "n")
    signos = {t["description_norm"]: t["amount_minor"] for t in d["txns"]}
    ok_signos = (any("Deposito" in k and v == 600000 for k, v in signos.items()) and
                 any("eBay" in k and v == -232888 for k, v in signos.items()) and
                 any("Prestamo" in k and v == -192000 for k, v in signos.items()) and
                 any("Devolucion" in k and v == 100 for k, v in signos.items()))
    check("F5-R3-02 Nu débito: cuenta+signo explícito+concilia",
          d["account_id"] == "nu_debito" and len(d["txns"]) == 4 and
          all(t["date_op"] for t in d["txns"]) and ok_signos and
          d["reconciliation"]["result"] == "OK" and d["state"] == "READY_TO_COMMIT",
          f"cuenta={d['account_id']} n={len(d['txns'])} recon={d['reconciliation']['result']} signos={signos}")


def case_credito_inverted():
    # Tarjeta de crédito (Vexi, cargo_abono): la COMPRA aumenta la deuda (nativo +) pero la cuenta
    # es sign_convention='inverted' -> se guarda como GASTO negativo; el PAGO como positivo.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "vexi_gold.pdf")
    make_pdf(p, [
        "Vexi Amex  Estado de cuenta  Tarjeta de credito",
        "Periodo del 01/07/2026 al 31/07/2026",
        "02/07/2026 PAYPAL *DISCORD $ 177.14",
        "15/07/2026 AMAZON MX $ 500.00",
        "20/07/2026 Pago recibido $ 300.00",
    ])
    d = _ingest(root, S, p, "v")
    sig = {t["description_norm"]: t["amount_minor"] for t in d["txns"]}
    compras_gasto = any("PAYPAL" in k and v == -17714 for k, v in sig.items()) and \
                    any("AMAZON" in k and v == -50000 for k, v in sig.items())
    pago_positivo = any("Pago" in k and v == 30000 for k, v in sig.items())
    check("F5-R3-02 crédito: compra=gasto(−), pago=(+) por sign_convention inverted",
          d["account_id"] == "vexi_tdc" and len(d["txns"]) == 3 and compras_gasto and pago_positivo,
          f"cuenta={d['account_id']} n={len(d['txns'])} signos={sig}")


def case_cross_year():
    # R9-A-004: estado que cruza dic→ene con fechas DD/MMM (sin año). La de DICIEMBRE debe llevar el
    # AÑO ANTERIOR (2025), no el del fin de periodo (2026); resuelto por el periodo.
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "xyear.pdf")
    make_pdf(p, [
        "BBVA Bancomer  Cuenta de Nomina",
        "Periodo DEL 20/12/2025 AL 19/01/2026",
        "Saldo Anterior 1,000.00",
        "OPER LIQ DESCRIPCION CARGOS ABONOS OPERACION LIQUIDACION",
        "28/DIC 28/DIC COMPRA DICIEMBRE 100.00 900.00 900.00",
        "05/ENE 05/ENE COMPRA ENERO 200.00 700.00 700.00",
        "Saldo Final 700.00",
    ])
    d = _ingest(root, S, p, "xy")
    fechas = sorted(t["date_op"] for t in d["txns"])
    check("R9-A-004 cross-year: diciembre lleva año anterior",
          fechas == ["2025-12-28", "2026-01-05"] and
          not any(t.get("_flags") for t in d["txns"]) and
          d["reconciliation"]["result"] == "OK",
          f"fechas={fechas} recon={d['reconciliation']['result']}")


def case_no_phantom_saldo():
    # R9-A-002: una línea de resumen con fecha EMBEBIDA (no al inicio) no debe volverse movimiento.
    root = fresh(); load(root)
    from finance.extractors.bank_templates import parse_bank
    text = ("GBM\n2026-06-05 Compra fondo GBMF 1,000.00 9,000.00\n"
            "Rendimiento acumulado del 01/06/2026 al 30/06/2026 500.00 9,500.00\n"
            "2026-06-15 Deposito 5,000.00 14,500.00")
    rows, _, _ = parse_bank(text, "gbm", opening=10000.0)
    descs = [r["description"] for r in rows]
    check("R9-A-002 línea de resumen no se ingiere como movimiento fantasma",
          len(rows) == 2 and not any("30/06/2026" in d for d in descs),
          f"n={len(rows)} descs={descs}")


def case_nu_tdc_identifies():
    # R9-A-003: acento plegado + peso por especificidad -> Nu TDC vs Nu débito se distinguen.
    root = fresh(); S = load(root)
    tdc = S.identify_account({}, "", header="Estado de tu Tarjeta de credito Nu  Plan de pagos fijos  Cuenta Nu")[0]
    # El estado de la cuenta de débito se reconoce por frases que SOLO ella imprime. Antes bastaba
    # 'Cuenta Nu', y por eso se rompía: ver el caso de abajo.
    deb = S.identify_account({}, "", header="Comisiones cobradas por Nu  Rendimientos  CLABE 12345")[0]
    check("R9-A-003 Nu TDC y Nu débito se identifican (acento + especificidad)",
          tdc == "nu_tdc" and deb == "nu_debito", f"tdc={tdc} deb={deb}")


def case_nu_tdc_no_la_roba_la_descripcion():
    """R18: en el diseño viejo de la tarjeta, la cabecera del PDF es ya la tabla de movimientos, y
    una disposición de efectivo se rotula 'Disposición de saldo en Cuenta Nu'. Con 'Cuenta Nu' como
    hint de la cuenta de débito, ese estado de TARJETA se ruteaba a la cuenta de DÉBITO: como esa
    cuenta no lleva `sign_convention: inverted`, la deuda entraba al libro como saldo a favor."""
    root = fresh(); S = load(root)
    header = ("11 feb Otros Disposicion de saldo en Cuenta Nu - 1/3 $604.97 "
              "12 feb Otros Disposicion de saldo en Cuenta Nu - 2/3 $115.26 "
              "Saldo a meses con o sin intereses de este periodo  Pagos a tu tarjeta en el periodo")
    acc = S.identify_account({}, "", header=header)[0]
    check("R18 estado de TARJETA con 'Cuenta Nu' en la descripción no cae en la cuenta de débito",
          acc == "nu_tdc", f"cuenta={acc}")


if __name__ == "__main__":
    for fn in (case_bbva, case_nu_debito, case_credito_inverted, case_cross_year,
               case_no_phantom_saldo, case_nu_tdc_identifies,
               case_nu_tdc_no_la_roba_la_descripcion):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
