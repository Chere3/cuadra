# -*- coding: utf-8 -*-
"""
test_r12_nu_debito.py — Cuenta Nu (débito): conceptos declarados SOLO en el resumen y periodo
con la fecha inicial abreviada.

Motivación (estado real de enero 2026, enmascarado): Nu abona los rendimientos de la cuenta
('Dinero generado este mes') pero NO los lista como movimiento; sin ellos la suma de movimientos
se queda corta por ese importe y la conciliación bloquea para siempre. Además el estado rotula el
periodo como 'del 01 al 31 ene 2026' —la fecha inicial es solo el día—, formato que el parser no
reconocía y dejaba el estado sin periodo.

Cifras y datos FICTICIOS; el estado real no se versiona (regla 50/70).
"""
import os, sys, json, shutil, tempfile
from reportlab.pdfgen import canvas

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fresh():
    d = tempfile.mkdtemp(prefix="r12-")
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


# Layout del estado de Cuenta Nu: cabecera con el periodo abreviado, bloque de resumen (donde
# vive el rendimiento) y luego los movimientos con importe firmado.
def _lineas(periodo, saldo_ini, saldo_fin, rendimiento="17.42", movs=None):
    out = [
        "Cuenta Nu: 00000000000",
        "CLABE: 000000000000000000",
        periodo,
        "Saldo al generar este estado de cuenta $%s" % saldo_fin,
        "Comisiones cobradas por Nu $0.00",
    ]
    if rendimiento is not None:
        out.append("Dinero generado este mes $%s" % rendimiento)
        out.append("Dinero generado antes de impuestos (Interes Bruto Anual de 10%%) $%s" % rendimiento)
    out += [
        "Saldo inicial $%s" % saldo_ini,
        "Movimientos",
    ]
    out += (movs if movs is not None else [
        "05 ENE 2026 Deposito de nomina ACME +$3,000.00",
        "07 ENE 2026 Tienda de conveniencia -$200.00",
    ])
    out.append("Saldo al generar este estado de cuenta $%s" % saldo_fin)
    return out


def case_rendimiento_concilia():
    """El rendimiento del resumen entra como abono y el estado cuadra al centavo."""
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_deb_rend.pdf")
    # 1,000.00 + 3,000.00 - 200.00 + 17.42 = 3,817.42
    make_pdf(p, _lineas("Periodo: del 01 al 31 ene 2026", "1,000.00", "3,817.42"))
    d = _ingest(root, S, p, "r12a")
    rend = [t for t in d["txns"] if "generado" in (t["description_raw"] or "").lower()]
    check("R12-01 rendimiento del resumen se incorpora y el estado concilia al centavo",
          d["account_id"] == "nu_debito" and len(d["txns"]) == 3 and
          len(rend) == 1 and rend[0]["amount_minor"] == 1742 and
          rend[0]["date_op"] == "2026-01-31" and
          d["reconciliation"]["result"] == "OK" and d["reconciliation"]["diff_minor"] == 0 and
          d["state"] == "READY_TO_COMMIT",
          f"cuenta={d['account_id']} n={len(d['txns'])} rend={[(t['amount_minor'], t['date_op']) for t in rend]} "
          f"recon={d['reconciliation']['result']} diff={d['reconciliation']['diff_minor']}")


def case_periodo_dia_abreviado():
    """'del 01 al 31 ene 2026': la fecha inicial es solo el día; mes/año salen de la final."""
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_deb_per.pdf")
    make_pdf(p, _lineas("Periodo: del 01 al 31 ene 2026", "1,000.00", "3,817.42"))
    d = _ingest(root, S, p, "r12b")
    check("R12-02 periodo 'del D al D mmm AAAA' se interpreta completo",
          d["period_start"] == "2026-01-01" and d["period_end"] == "2026-01-31",
          f"periodo={d['period_start']}..{d['period_end']}")


def case_sin_periodo_no_inventa_fecha():
    """Fail-safe: sin periodo NO se fecha el rendimiento — se avisa y el estado no cuadra,
    en vez de inventarle una fecha y dar una conciliación falsamente correcta."""
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_deb_sinper.pdf")
    make_pdf(p, _lineas("Estado de cuenta mensual", "1,000.00", "3,817.42"))
    d = _ingest(root, S, p, "r12c")
    rend = [t for t in d["txns"] if "generado" in (t["description_raw"] or "").lower()]
    aviso = any("generado" in w.lower() for w in (d.get("warnings") or []))
    check("R12-03 sin periodo no se inventa la fecha del rendimiento (falla seguro)",
          not rend and aviso and d["reconciliation"]["result"] != "OK",
          f"rend={len(rend)} aviso={aviso} recon={d['reconciliation']['result']}")


def case_sin_rendimiento_no_agrega_nada():
    """Un estado sin el rótulo no recibe ningún movimiento extra (no se fabrica el concepto)."""
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_deb_norend.pdf")
    # 1,000.00 + 3,000.00 - 200.00 = 3,800.00 (sin rendimiento)
    make_pdf(p, _lineas("Periodo: del 01 al 31 ene 2026", "1,000.00", "3,800.00", rendimiento=None))
    d = _ingest(root, S, p, "r12d")
    check("R12-04 sin el rótulo no se agrega movimiento alguno",
          len(d["txns"]) == 2 and d["reconciliation"]["result"] == "OK" and
          d["reconciliation"]["diff_minor"] == 0,
          f"n={len(d['txns'])} recon={d['reconciliation']['result']} diff={d['reconciliation']['diff_minor']}")


def case_tdc_no_recibe_extra():
    """El concepto es de la cuenta de débito: un estado de crédito Nu no debe recibirlo."""
    root = fresh(); S = load(root)
    p = os.path.join(root, "fixtures", "nu_tdc_extra.pdf")
    # R12-E-008: el fixture DEBE contener la frase del resumen de la cuenta; si no, el test pasa
    # sin ejercitar la garantía (el código antes no la implementaba y el test verde lo ocultaba).
    make_pdf(p, [
        "Tarjeta de credito Nu 9571",
        "Periodo: 01 ENE 2026 al 31 ENE 2026",
        "Adeudo del periodo anterior = $1,000.00",
        "Dinero generado este mes $17.42",
        "05 ENE 2026 Compra en linea +$500.00",
        "Saldo cargos regulares = $1,500.00",
    ])
    d = _ingest(root, S, p, "r12e")
    extra = [t for t in d["txns"] if "generado" in (t["description_raw"] or "").lower()]
    check("R12-05 el estado de crédito Nu no recibe el concepto de la cuenta",
          d["account_id"] == "nu_tdc" and not extra,
          f"cuenta={d['account_id']} extras={len(extra)}")


if __name__ == "__main__":
    for fn in (case_rendimiento_concilia, case_periodo_dia_abreviado,
               case_sin_periodo_no_inventa_fecha, case_sin_rendimiento_no_agrega_nada,
               case_tdc_no_recibe_extra):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, "EXCEPTION " + repr(e))
    ok = all(r[1] for r in RESULTS)
    print("\n=== " + ("TODO VERDE" if ok else "HAY FALLAS") + " ===")
    sys.exit(0 if ok else 1)
