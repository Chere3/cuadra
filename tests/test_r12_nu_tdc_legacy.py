# -*- coding: utf-8 -*-
"""
test_r12_nu_tdc_legacy.py — Tarjeta Nu, diseño ANTERIOR + texto superpuesto + año fuera de periodo.

Nu convive con dos diseños de estado de tarjeta. El anterior (estados de 2025 / principios de
2026) rompía la extracción por tres motivos independientes:

- R12-C-001  El nombre del titular va impreso como MARCA DE AGUA sobre la tabla. `extract_text()`
             entrelaza ambas capas carácter a carácter y la línea deja de casar: el movimiento se
             perdía en silencio y la conciliación no cuadraba.
- R12-C-002  Otros rótulos de saldo ('Saldo inicial/final del periodo'), periodo con guion en vez
             de 'al', y movimientos con fecha SIN año e importe SIN signo. Además el estado repite
             las compras a meses en un desglose con tasa y parcialidad, que no debe contarse.
- R12-C-003  Los ajustes de IVA se cargan con la FECHA DE CORTE, un día después del último día del
             periodo. Al no caber en el rango, el resolvedor de año se quedaba con la fecha
             original y el movimiento aterrizaba doce meses lejos (2025-01-29 por 2026-01-29).

Cifras y datos FICTICIOS.
"""
import os, sys, json, shutil, tempfile
from reportlab.pdfgen import canvas

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def fresh():
    d = tempfile.mkdtemp(prefix="r12tdc-")
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


def _ingest(root, S, path, run):
    S.ingest([path], dry_run=True, run_id=run)
    return json.load(open(os.path.join(root, "staging", run, "_full.json")))["statements"][0]


# 1,000.00 + cargos(118.00+50.00+10.27) - abonos(500.00) = 678.27
CUERPO = [
    "Tarjeta de credito Nu 9571",
    "Periodo: 29 DIC 2025 - 28 ENE 2026 (31 días)",
    "Saldo inicial del periodo (DIC 2025) $1,000.00",
    "05 ENE Supermercado Tienda Ejemplo $50.00",
    "10 ENE ¡Muchas gracias! Pago a tu tarjeta de crédito - $500.00",
    "12 ENE Ajuste Aumentaste tu límite de crédito con garantía $300.00",
    "29 ENE Ajuste IVA de disposición de saldo $10.27",
    "Saldo final del periodo $678.27",
]


def _pdf_simple(path, extra=()):
    c = canvas.Canvas(path); y = 780
    for ln in list(CUERPO) + list(extra):
        c.setFont("Helvetica", 10); c.drawString(40, y, ln); y -= 20
    c.showPage(); c.save()


def _pdf_marca_a_la_izquierda(path):
    """La marca arranca MÁS A LA IZQUIERDA que el movimiento. Con la heurística vieja (conservar
    la fuente de la primera palabra) se conservaba la marca y el movimiento desaparecía."""
    c = canvas.Canvas(path); y = 780
    for ln in CUERPO:
        c.setFont("Helvetica", 10); c.drawString(40, y, ln); y -= 20
    c.setFont("Helvetica-Bold", 10)
    for i, palabra in enumerate(["NOMBRE", "APELLIDO", "SEGUNDO"]):
        c.drawString(20 + i * 70, y, palabra)          # empieza en x=20, ANTES del movimiento
    c.setFont("Helvetica", 10); c.drawString(40, y, "17 ENE Supermercado Otra Tienda $118.00")
    c.showPage(); c.save()


def _pdf_tabla_multifuente(path):
    """Tabla legítima que ALTERNA fuentes sin superponerlas (fecha y importe en negrita,
    descripción y saldo regulares). No es una marca de agua: no debe perderse ni una palabra."""
    c = canvas.Canvas(path); y = 780
    for ln in CUERPO[:3]:
        c.setFont("Helvetica", 10); c.drawString(40, y, ln); y -= 20
    x = 40
    for texto, negrita in (("05 ENE", True), ("Tienda Ejemplo", False),
                           ("$50.00", True), ("$950.00", False)):
        c.setFont("Helvetica-Bold" if negrita else "Helvetica", 10)
        c.drawString(x, y, texto); x += 120           # sin solaparse
    y -= 20
    c.setFont("Helvetica", 10); c.drawString(40, y, "Saldo final del periodo $950.00")
    c.showPage(); c.save()


def _pdf_con_marca_de_agua(path):
    """Dibuja un movimiento y, ENCIMA, el nombre del titular en otra fuente e intercalado en x —
    igual que el estado real, donde ambas capas comparten banda vertical."""
    c = canvas.Canvas(path); y = 780
    for ln in CUERPO:
        c.setFont("Helvetica", 10); c.drawString(40, y, ln); y -= 20
    # la línea que solo se recupera separando capas
    c.setFont("Helvetica", 10); c.drawString(40, y, "17 ENE Supermercado Otra Tienda $118.00")
    c.setFont("Helvetica-Bold", 10)
    for i, palabra in enumerate(["NOMBRE", "APELLIDO", "SEGUNDO"]):
        c.drawString(70 + i * 60, y, palabra)      # superpuesto sobre la misma línea
    c.showPage(); c.save()


def case_formato_anterior_concilia():
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_tdc_legacy.pdf")
    _pdf_simple(p, extra=["17 ENE Supermercado Otra Tienda $118.00"])
    d = _ingest(root, S, p, "tdc1")
    signos = {(t["description_norm"] or "")[:18]: t["amount_minor"] for t in d["txns"]}
    pago = [v for k, v in signos.items() if "gracias" in k.lower()]
    check("R12-13 diseño anterior: cargos y abonos con el signo correcto, concilia al centavo",
          d["account_id"] == "nu_tdc" and d["reconciliation"]["result"] == "OK" and
          d["reconciliation"]["diff_minor"] == 0 and pago and pago[0] > 0,
          f"cuenta={d['account_id']} n={len(d['txns'])} recon={d['reconciliation']['result']} "
          f"diff={d['reconciliation']['diff_minor']}")


def case_aumento_de_limite_no_es_cargo():
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_tdc_limite.pdf")
    _pdf_simple(p, extra=["17 ENE Supermercado Otra Tienda $118.00"])
    d = _ingest(root, S, p, "tdc2")
    lim = [t for t in d["txns"] if "aumentaste" in (t["description_norm"] or "").lower()]
    check("R12-14 aumentar el límite de crédito no se ingiere como cargo",
          not lim and d["reconciliation"]["diff_minor"] == 0, f"encontrados={len(lim)}")


def case_desglose_de_meses_no_duplica():
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_tdc_meses.pdf")
    _pdf_simple(p, extra=["17 ENE Supermercado Otra Tienda $118.00",
                          "05 ENE Compra diferida - Tienda $150.00 59.90% 3/3 $50.00"])
    d = _ingest(root, S, p, "tdc3")
    check("R12-15 el desglose de compras a meses no se cuenta como movimiento",
          len(d["txns"]) == 4 and d["reconciliation"]["diff_minor"] == 0,
          f"n={len(d['txns'])} diff={d['reconciliation']['diff_minor']}")


def case_marca_de_agua():
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_tdc_marca.pdf")
    _pdf_con_marca_de_agua(p)
    d = _ingest(root, S, p, "tdc4")
    rec = [t for t in d["txns"] if "otra tienda" in (t["description_norm"] or "").lower()]
    check("R12-16 el movimiento tapado por la marca de agua se recupera y el estado cuadra",
          len(rec) == 1 and rec[0]["amount_minor"] == -11800 and
          d["reconciliation"]["diff_minor"] == 0,
          f"recuperados={len(rec)} diff={d['reconciliation']['diff_minor']}")


def case_marca_a_la_izquierda():
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_tdc_izq.pdf")
    _pdf_marca_a_la_izquierda(p)
    d = _ingest(root, S, p, "tdc5")
    rec = [t for t in d["txns"] if "otra tienda" in (t["description_norm"] or "").lower()]
    check("R12-20 la marca de agua a la IZQUIERDA no borra el movimiento",
          len(rec) == 1 and rec[0]["amount_minor"] == -11800,
          f"recuperados={len(rec)} n={len(d['txns'])}")


def case_tabla_multifuente_intacta():
    """Regresión de R12-E-001: alternar fuentes SIN solaparse no debe descartar nada."""
    root = fresh(); load(root)
    import pdfplumber
    from finance.extractors.pdf_ext import _page_text
    p = os.path.join(root, "fixtures", "nu_multifuente.pdf")
    _pdf_tabla_multifuente(p)
    with pdfplumber.open(p) as pdf:
        pg = pdf.pages[0]
        crudo, (limpio, warns) = (pg.extract_text() or ""), _page_text(pg)
    faltan = [w for w in crudo.split() if crudo.split().count(w) > limpio.split().count(w)]
    check("R12-21 una tabla que alterna fuentes sin superponerse queda intacta",
          crudo.split() == limpio.split() and not warns,
          f"palabras {len(crudo.split())}->{len(limpio.split())} perdidas={faltan[:4]} warns={len(warns)}")


def case_descarte_avisado():
    """La pérdida no puede ser silenciosa: descartar capa ajena emite warning."""
    root = fresh(); load(root)
    import pdfplumber
    from finance.extractors.pdf_ext import _page_text
    p = os.path.join(root, "fixtures", "nu_aviso.pdf")
    _pdf_con_marca_de_agua(p)
    with pdfplumber.open(p) as pdf:
        _, warns = _page_text(pdf.pages[0])
    check("R12-22 descartar una capa superpuesta deja aviso",
          len(warns) == 1 and "superpuesto" in warns[0],
          f"warns={warns}")


def case_signo_ambiguo_bloquea():
    """R12-E-003: dos signos cruzados del mismo importe se compensan y la identidad de saldo da OK.
    Los conceptos cuyo sentido no se deduce del rótulo se marcan dudosos y bloquean."""
    root = fresh(); load(root)
    from finance.extractors.bank_templates import _parse_nu_legacy
    adversarial = ("Tarjeta de credito Nu 9571\n"
                   "Saldo inicial del periodo (DIC 2025) $1,000.00\n"
                   "05 ENE Otros Devolucion por compra $100.00\n"
                   "10 ENE Otros Reverso Pago a tu tarjeta de credito $100.00\n"
                   "Saldo final del periodo $1,000.00")
    normal = ("Tarjeta de credito Nu 9571\n"
              "Saldo inicial del periodo (DIC 2025) $1,000.00\n"
              "05 ENE Supermercado Tienda Ejemplo $50.00\n"
              "10 ENE ¡Muchas gracias! Pago a tu tarjeta de crédito - $500.00\n"
              "Saldo final del periodo $550.00")
    ra, _, _ = _parse_nu_legacy(adversarial, None)
    rn, _, _ = _parse_nu_legacy(normal, None)
    da = sum(1 for r in ra if "importe_dudoso" in r["_flags"])
    dn = sum(1 for r in rn if "importe_dudoso" in r["_flags"])
    check("R12-25 un concepto de signo ambiguo bloquea en vez de adivinar",
          da >= 1 and dn == 0, f"dudosos adversarial={da} normal={dn}")


def case_exclusiones_dejan_rastro():
    """Excluir una línea no puede ser silencioso: podría tragarse un cargo legítimo."""
    root = fresh(); load(root)
    from finance.extractors.bank_templates import _parse_nu_legacy
    txt = ("Tarjeta de credito Nu 9571\n"
           "Saldo inicial del periodo (DIC 2025) $1,000.00\n"
           "05 ENE Comision por gestion 59.90% 3/3 $25.00\n"
           "Saldo final del periodo $1,000.00")
    rows, _, warns = _parse_nu_legacy(txt, None)
    check("R12-26 toda línea excluida por rótulo deja warning",
          len(rows) == 0 and any("excluida" in w for w in warns),
          f"filas={len(rows)} warns={len(warns)}")


def case_fecha_de_corte_elige_el_anio_cercano():
    root = fresh(); S = load(root)
    r = S._resolve_year_in_period("2025-01-29", "29 ENE", ("2025-12-29", "2026-01-28"))
    dentro = S._resolve_year_in_period("2025-01-05", "05 ENE", ("2025-12-29", "2026-01-28"))
    check("R12-17 fuera del periodo se toma el año MÁS CERCANO, no el original",
          r == "2026-01-29" and dentro == "2026-01-05", f"corte={r} dentro={dentro}")


if __name__ == "__main__":
    for fn in (case_formato_anterior_concilia, case_aumento_de_limite_no_es_cargo,
               case_desglose_de_meses_no_duplica, case_marca_de_agua,
               case_marca_a_la_izquierda, case_tabla_multifuente_intacta,
               case_descarte_avisado, case_signo_ambiguo_bloquea,
               case_exclusiones_dejan_rastro, case_fecha_de_corte_elige_el_anio_cercano):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
