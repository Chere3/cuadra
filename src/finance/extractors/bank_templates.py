# -*- coding: utf-8 -*-
"""
bank_templates.py — Plantillas de extracción por banco (F5-R3-02).

El parser genérico de una sola columna (`textparse.parse_lines`) NO maneja los estados reales
con columnas separadas CARGO/ABONO/SALDO: capturaba el importe contaminado con el saldo y sin
signo. Estas plantillas resuelven ambos:

- Estrategia `saldo` (BBVA/Nu/Ualá/GBM y cualquier estado con SALDO corriente): el importe FIRMADO
  de cada movimiento se deriva del delta del saldo (`saldo_actual − saldo_previo`). Esto separa el
  saldo del importe Y asigna el signo (cargo negativo / abono positivo) sin depender de la posición
  de columnas en el texto aplanado del PDF. Si no hay saldo previo para el primer movimiento, se
  marca `importe_dudoso` (bloquea) — NUNCA se inventa el signo.
- Estrategia `cargo_abono` (tarjetas tipo Vexi sin saldo corriente): usa columnas/rotulado
  CARGO/ABONO explícito; si no se puede desambiguar, marca `importe_dudoso`.

Detección por marcadores de texto o por `account_hint`. Si ningún banco casa, el llamador cae al
parser genérico (fail-safe). Fixtures dorados por banco en `fixtures/bank_golden/` (regla 70).
"""
from __future__ import annotations
import re
import math
import datetime as _dt

from .. import normalize as _N

# importes con 2 decimales (es-MX): 1,234.56 / (250.00) / -$30.00
_AMOUNT_TOK = re.compile(r"\(?-?\$?\s?\d[\d,]*\.\d{2}\)?")
_DATE_TOK = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/\-][A-Za-z0-9]{1,4}[/\-]\d{2,4}|\d{1,2}\s+[A-Za-zÁÉÍÓÚáéíóú]{3,}\.?)")

BANKS = {
    "bbva": {"markers": ["bbva", "bancomer"], "strategy": "bbva"},
    "nu":   {"markers": ["nu ", "nubank", "cuenta nu", "nu méxico", "nu mexico"], "strategy": "signed"},
    "uala": {"markers": ["uala", "ualá"], "strategy": "signed"},
    "gbm":  {"markers": ["gbm", "grupo bursátil", "grupo bursatil"], "strategy": "saldo"},
    "vexi": {"markers": ["vexi"], "strategy": "cargo_abono"},
    "hsbc": {"markers": ["hsbc"], "strategy": "hsbc"},
}


# SOL-009: resumen del emisor en estados de CRÉDITO REVOLVENTE. El cuerpo lista los movimientos,
# pero no hay saldo corriente por línea: los saldos del periodo viven en el RESUMEN contable
# (deuda_anterior + cargos − abonos = deuda_al_corte). Cada banco lo rotula distinto; los campos
# están CALIBRADOS contra un estado real enmascarado de cada banco:
#   Nu:   'Adeudo del periodo anterior' → 'Saldo cargos regulares' (la identidad cuadra al centavo
#         con los movimientos listados, incluido el IVA con fecha de cargo posterior al corte)
#   Ualá: 'Adeudo del periodo anterior' → 'Saldo deudor total' (incluye la mensualidad a meses
#         que sí aparece como movimiento del periodo)
#   Vexi: 'Saldo revolvente anterior'   → 'Saldo revolvente al corte' (admite saldo a favor: −)
# Los montos extraídos son DEUDA declarada (positiva); services los NIEGA para cuentas
# sign_convention='inverted' (R10-A-002), con lo que la conciliación estándar
# opening + Σ == closing aplica sin cambios. Si el banco cambia el rótulo, simplemente no se
# extraen saldos y el estado queda REVIEW_REQUIRED (fail-safe; nunca se inventan).
_CS_AMT = r"\$?\s*(-?\s?[\d,]+\.\d{2})"
_CS_AMT_SGN = r"(-?\s?\$?\s?[\d,]+\.\d{2})"
CREDIT_SUMMARY = {
    # R12-C-002: Nu convive con DOS diseños de estado de tarjeta. El más reciente rotula
    # 'Adeudo del periodo anterior' → 'Saldo cargos regulares'; el anterior (estados de 2025 y
    # principios de 2026) usa 'Saldo inicial del periodo (MMM AAAA)' → 'Saldo final del periodo'.
    # Se prueban en orden y gana el primero que aparezca; si el banco cambia otra vez el rótulo,
    # simplemente no se extraen saldos y el estado queda en revisión (fail-safe).
    # Diseño anterior: un saldo A FAVOR se imprime como '- $188.50' (el signo va ANTES del '$',
    # separado por un espacio). Con _CS_AMT el '-' quedaba fuera del grupo y el saldo salía
    # positivo (jun-2025 descuadraba justo 2×188.50) o, con el rótulo 'Saldo final' ausente
    # (oct/nov-2025 solo traen 'Saldo total del periodo'), no se extraía nada. _CS_AMT_SGN captura
    # el signo delante del '$'; credit_summary() limpia espacios y '$'.
    "nu": {
        "opening": [re.compile(r"adeudo\s+del\s+periodo\s+anterior\s*=?\s*" + _CS_AMT, re.I),
                    re.compile(r"saldo\s+inicial\s+del\s+periodo\s*(?:\([^)\n]{0,20}\))?\s*:?\s*"
                               + _CS_AMT_SGN, re.I)],
        "closing": [re.compile(r"saldo\s+cargos\s+regulares\s*=?\s*" + _CS_AMT, re.I),
                    re.compile(r"saldo\s+final\s+del\s+periodo\s*:?\s*" + _CS_AMT_SGN, re.I),
                    re.compile(r"saldo\s+total\s+del\s+periodo\s*:?\s*" + _CS_AMT_SGN, re.I)],
    },
    "uala": {
        "opening": re.compile(r"adeudo\s+del\s+periodo\s+anterior\s*" + _CS_AMT, re.I),
        # El cierre que encadena periodos es 'Pago para no generar intereses² $…' (= adeudo anterior
        # + cargos − abonos, y coincide con el 'Adeudo del periodo anterior' del siguiente estado;
        # verificado ene–jul 2026). 'Saldo deudor total' NO sirve: desde jun-2026 Ualá lo imprime
        # como cargos regulares + saldo a meses sin restar pagos. El superíndice de la nota puede
        # aplanarse a ASCII ('intereses2'), así que se admite un número CORTO seguido de espacio —
        # nunca puede morder el monto (que lleva '.dd' pegado).
        "closing": re.compile(r"pago\s+para\s+no\s+generar\s+intereses[:\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\d{1,2}\s+)?" + _CS_AMT, re.I),
    },
    "vexi": {
        "opening": re.compile(r"saldo\s+revolvente\s+anterior\s*" + _CS_AMT, re.I),
        "closing": re.compile(r"saldo\s+revolvente\s+al\s+corte\s*" + _CS_AMT, re.I),
    },
    # BBVA tarjeta de crédito: 'Adeudo del periodo anterior $0.00' → 'Saldo deudor total:11 $3,584.22'
    # (la nota al pie va pegada a los dos puntos, como en Ualá). En un estado de DÉBITO de BBVA no
    # aparece ninguno de los dos rótulos, así que ahí no se extrae nada y el débito sigue igual.
    "bbva": {
        "opening": re.compile(r"adeudo\s+del\s+periodo\s+anterior\s*" + _CS_AMT, re.I),
        "closing": re.compile(r"saldo\s+deudor\s+total[:\s]*(?:\d{1,2}\s+)?" + _CS_AMT, re.I),
    },
}


def credit_summary(text, bank):
    """Extrae {'opening_raw','closing_raw'} (deuda declarada) del resumen de un estado de crédito
    revolvente, según la plantilla del banco. Devuelve {} si el banco no tiene plantilla de
    crédito o los rótulos no aparecen (p. ej. un estado de débito del mismo banco)."""
    out = {}
    for key, rxs in (CREDIT_SUMMARY.get(bank) or {}).items():
        for rx in (rxs if isinstance(rxs, (list, tuple)) else [rxs]):
            m = rx.search(text or "")
            if m:
                out["opening_raw" if key == "opening" else "closing_raw"] = (
                    m.group(1).replace(" ", "").replace("$", ""))
                break
    return out


# Conceptos que el emisor declara SOLO en el RESUMEN y que sí mueven el saldo, pero que no
# aparecen como línea de movimiento en el cuerpo del estado. Nu abona los rendimientos de la
# cuenta ('Dinero generado este mes') sin listarlos: sin ellos la suma de movimientos se queda
# corta por ese importe y la conciliación bloquea para siempre.
# El importe es el DECLARADO por el documento — no se calcula, no se infiere y no se prorratea.
# Si el rótulo no aparece, no se añade nada (fail-safe). La conciliación sigue siendo el control:
# si el banco un día empezara a listar el concepto además de resumirlo, el saldo dejaría de
# cuadrar y el estado se bloquearía en vez de doble-contarlo en silencio.
SUMMARY_EXTRAS = {
    "nu": [
        {"label": "Dinero generado este mes",
         # el rótulo aparece también dentro de 'Dinero generado antes de impuestos (...)': se ancla
         # a 'este mes' para tomar el neto abonado y se usa UNA sola coincidencia.
         "rx": re.compile(r"dinero\s+generado\s+este\s+mes\s*" + _CS_AMT, re.I),
         "sign": 1},
    ],
}


def summary_extras(text, bank):
    """Movimientos declarados solo en el resumen del estado (regla 21: se extraen del documento,
    nunca se inventan). Devuelve [{'label','amount','raw_text'}] SIN fecha: el llamador la fija al
    cierre del periodo, y si no hay periodo no los incorpora."""
    out = []
    for spec in (SUMMARY_EXTRAS.get(bank) or []):
        m = spec["rx"].search(text or "")
        if not m:
            continue
        val = _to_float(m.group(1).replace(" ", ""))
        if not val:
            continue
        out.append({"label": spec["label"],
                    "amount": spec["sign"] * abs(val),
                    "raw_text": re.sub(r"\s+", " ", m.group(0)).strip()})
    return out


# Totales que el emisor DECLARA en su resumen y que la conciliación usa como control CRUZADO
# (regla 25): número de movimientos y suma de abonos/cargos. Sin ellos `reconcile()` deja
# `declared_count` y `coverage_ok` en None, y el único candado que corre es la identidad de saldo
# —que NO detecta una página que no se parseó si su neto es cero, ni un movimiento omitido junto a
# otro de más por el mismo importe—. La regla 21 los exige "cuando existan", y BBVA los imprime.
# Se extraen del documento: no se calculan, no se infieren. Si el rótulo no aparece no se declara
# nada (fail-safe) y la conciliación se comporta igual que antes.
DECLARED_TOTALS = {
    "bbva": {
        # 'Depósitos / Abonos (+) 3 14,163.00' — el resumen da conteo Y monto en la misma línea.
        "credits": re.compile(r"dep[oó]sitos\s*/\s*abonos\s*\(\+\)\s*(\d+)\s+" + _CS_AMT, re.I),
        "debits": re.compile(r"retiros\s*/\s*cargos\s*\(-\)\s*(\d+)\s+" + _CS_AMT, re.I),
    },
}


def declared_totals(text, bank):
    """Totales y conteo declarados en el resumen del estado, para el control cruzado de la
    conciliación. Devuelve {'credits','debits','n'} con los importes en TEXTO crudo (el llamador
    los pasa por `parse_amount`) y los débitos con signo negativo, que es la convención canónica
    contra la que compara `reconcile._check_declared_totals`. {} si el banco no tiene plantilla o
    los rótulos no aparecen."""
    spec = DECLARED_TOTALS.get(bank) or {}
    hallados = {}
    for key, rx in spec.items():
        m = rx.search(text or "")
        if m:
            hallados[key] = (int(m.group(1)), m.group(2).replace(" ", ""))
    out = {}
    for key, (_n, amt) in hallados.items():
        out[key] = ("-" + amt) if key == "debits" and not amt.startswith("-") else amt
    # El conteo solo se declara si aparecieron AMBAS mitades del resumen: con una sola, el total
    # sería parcial y `coverage_ok` marcaría un descuadre falso en un estado que sí está completo.
    if spec and len(hallados) == len(spec):
        out["n"] = sum(n for n, _ in hallados.values())
    return out


def detect_bank(text, account_hint=None):
    """Banco EMISOR por FRECUENCIA de sus marcadores (no 'primero gana'): un estado de Nu que
    menciona 'BBVA' en una transferencia no debe detectarse como BBVA — la marca del emisor domina."""
    t = (text or "").lower()
    best, best_cnt = None, 0
    for bank, cfg in BANKS.items():
        cnt = sum(t.count(m) for m in cfg["markers"])
        if cnt > best_cnt:
            best, best_cnt = bank, cnt
    if best:
        return best
    if account_hint:
        h = account_hint.lower()
        for bank in BANKS:
            if h.startswith(bank):
                return bank
    return None


def _to_float(tok):
    if tok is None:
        return None
    tok = str(tok).strip()
    neg = tok.startswith("(") or tok.startswith("-") or tok.startswith("$-")
    n = re.sub(r"[^\d.]", "", tok)
    if not n or n == ".":
        return None
    try:
        v = float(n)
    except ValueError:
        return None
    return -v if neg else v


def _description(line, date_match, first_amount):
    start = date_match.end()
    pos = line.find(first_amount, start)
    seg = line[start:pos] if pos > start else line[start:]
    return re.sub(r"\s+", " ", seg).strip(" -·|")


def parse_bank(text, bank, page=None, opening=None, period_start=None, period_end=None,
               closing=None):
    """Devuelve (rows, meta, warnings) para el banco dado. `opening` es el saldo inicial (float o
    str) para poder firmar el primer movimiento con la estrategia de saldo. `period_start` (ISO)
    solo lo necesitan los bancos cuya tabla trae el DÍA suelto en vez de la fecha completa.
    `closing` (saldo final declarado) permite firmar el último tramo de BBVA cuando el estado no
    imprime saldo en sus movimientos finales."""
    strat = BANKS.get(bank, {}).get("strategy")
    op = _to_float(opening) if isinstance(opening, str) else opening
    if strat == "bbva":
        # Mismo emisor, dos maquetas: la TARJETA lista 'fecha fecha descripción ; tarjeta ±$monto'
        # bajo 'DESGLOSE DE MOVIMIENTOS'; la cuenta de débito trae saldo corriente por línea.
        if _BBVA_TDC_MARK.search(text or ""):
            return _parse_bbva_tdc(text, page)
        return _parse_bbva(text, page, op, period_start, period_end,
                           _to_float(closing) if isinstance(closing, str) else closing)
    if strat == "signed":
        return _parse_signed(text, page, bank=bank)
    if strat == "saldo":
        return _parse_saldo(text, page, op)
    if strat == "cargo_abono":
        return _parse_cargo_abono(text, page)
    if strat == "hsbc":
        return _parse_hsbc(text, page, op, period_start, period_end)
    return [], {}, [f"plantilla '{bank}' sin estrategia"]


# fecha 'DD MMM YYYY' (20 JUN 2026) o 'DD/MMM/YYYY'; importe con signo explícito +$/-$.
_SIGNED_DATE = re.compile(r"^(\d{1,2}[\s/][A-Za-zÁÉÍÓÚáéíóú]{3,}\.?[\s/]\d{4}|\d{4}-\d{2}-\d{2})")
_SIGNED_AMT = re.compile(r"([-+])\s?\$?\s?(\d[\d,]*\.\d{2})")
# Ualá (jul-2026): cuando el renglón no cabe, el importe se imprime SOLO en la línea ANTERIOR a la
# de fecha+descripción ('+ $109.00' / '10 Jul 2026 11 Jul 2026 STARBUCKS …'). Solo se acepta una
# línea que sea ÚNICAMENTE un importe firmado, para no robarle el monto a otro movimiento.
_SIGNED_AMT_SOLO = re.compile(r"^\s*([-+])\s?\$?\s?(\d[\d,]*\.\d{2})\s*$")


# encabezados de tabla de Nu/Ualá: nunca son la descripción de un movimiento.
_SIGNED_HDR = re.compile(
    r"(descripci[oó]n del movimiento|monto en pesos|fecha de la operaci[oó]n|fecha de cargo"
    r"|saldo anterior|saldo al corte|detalle de (?:tus )?movimientos)", re.I)


# Cuando la descripción es larga, Nu deja la FECHA en dos renglones y el movimiento en medio:
#   '04 MAR' / 'JUAN PEREZ LOPEZ Sin concepto +$7,726.18' / '2026'
# El renglón del importe no empieza por fecha, así que el bucle lo saltaba y el movimiento se
# perdía en silencio. Se exigen las TRES partes a la vez —día+mes solos, año solo dos renglones
# más abajo, importe firmado en medio— para que el patrón no pueda casar con otra cosa.
_SIGNED_DIA_MES = re.compile(r"^(\d{1,2}\s+([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?)$")
_SIGNED_ANIO = re.compile(r"^(\d{4})$")
# Variante (Nu débito, ago/nov-2025): la descripción larga arranca en el renglón del día+mes, el
# importe queda SOLO en el renglón de en medio y el año encabeza el renglón del resto de la
# descripción:
#   '08 AGO JUAN PEREZ LOPEZ Transferencia de' / '+$4,909.00' / '2025 Nomina'
# Se exigen las tres partes a la vez: día+mes real seguido de texto SIN importe firmado, un
# renglón que sea ÚNICAMENTE un importe firmado y un renglón que empiece por el año.
_SIGNED_DIA_MES_DESC = re.compile(r"^(\d{1,2}\s+([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?)\s+(\S.*)$")
_SIGNED_ANIO_DESC = re.compile(r"^(\d{4})(?:\s+(\S.*))?$")


# Dentro de una Cajita, Nu lista con importe y fecha dos operaciones que NO mueven dinero: congelar
# saldo (queda reservado hasta una fecha) y descongelarlo. No tienen contrapartida en la cuenta, así
# que contarlas descuadra la conciliación justo por su importe —verificado: son la diferencia exacta
# de abril y mayo de 2026—. Salir o entrar de verdad de una Cajita se rotula 'Retiro de Cajita' /
# 'Depósito en Cajita', y esos sí se conservan.
_SIGNED_NO_MOV = re.compile(r"(congelaste\s+saldo|descongelamos\s+saldo)", re.I)


def _unir_fecha_partida(lines):
    """Recompone en un solo renglón los movimientos cuya fecha el emisor partió en dos. No añade
    ni quita información: reordena tres fragmentos contiguos del propio documento."""
    out, i = [], 0
    while i < len(lines):
        dm = _SIGNED_DIA_MES.match(lines[i].strip())
        # El mes tiene que ser un mes de verdad. Sin esta comprobación, una cabecera de columna como
        # '12 ABONOS' seguida de un renglón con importe y de un '2026' suelto se recomponía en un
        # falso movimiento con fecha '12 ABONOS 2026'; la normalización acababa marcándolo
        # `fecha_dudosa` y bloqueando, pero el estado quedaba tumbado por una fila que nunca existió.
        if dm and _N.MESES.get(dm.group(2).rstrip(".").lower()[:3]) and i + 2 < len(lines):
            medio = lines[i + 1].strip()
            anio = _SIGNED_ANIO.match(lines[i + 2].strip())
            if anio and _SIGNED_AMT.search(medio) and not _SIGNED_DATE.match(medio):
                out.append(f"{dm.group(1)} {anio.group(1)} {medio}")
                i += 3
                continue
        dmd = _SIGNED_DIA_MES_DESC.match(lines[i].strip())
        if (dmd and _N.MESES.get(dmd.group(2).rstrip(".").lower()[:3]) and i + 2 < len(lines)
                and not _SIGNED_DATE.match(lines[i].strip())
                and not _SIGNED_AMT.search(dmd.group(3))):
            monto = _SIGNED_AMT_SOLO.match(lines[i + 1].strip())
            anio = _SIGNED_ANIO_DESC.match(lines[i + 2].strip())
            if monto and anio:
                resto = f" {anio.group(2)}" if anio.group(2) else ""
                out.append(f"{dmd.group(1)} {anio.group(1)} {dmd.group(3)}{resto} "
                           f"{lines[i + 1].strip()}")
                i += 3
                continue
        out.append(lines[i])
        i += 1
    return out


def _desc_linea_previa(lines, i):
    """R12-A-002: Nu/Ualá parten el movimiento en varias líneas cuando el texto no cabe, dejando la
    DESCRIPCIÓN en la línea anterior a la del importe. Solo se acepta esa línea si es texto suelto:
    sin fecha de movimiento, sin importe firmado y sin ser encabezado de la tabla. No se inventa
    nada (regla 00): si no hay texto válido, la descripción queda vacía."""
    j = i - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j < 0:
        return ""
    prev = lines[j].strip()
    if _SIGNED_DATE.match(prev) or _SIGNED_AMT.search(prev) or _SIGNED_HDR.search(prev):
        return ""
    return re.sub(r"\s+", " ", prev).strip(" -$·|")


# --- Formato ANTERIOR de tarjeta Nu (estados de 2025 / principios de 2026) ---------------------
# La línea es 'DD MMM <categoría Nu> <descripción> $importe': la fecha NO trae año (lo aporta el
# periodo, aguas arriba) y el importe NO trae signo. En una tarjeta el signo no se adivina: se
# deriva del ROTULADO —solo los pagos al emisor abonan— y la identidad
# deuda_anterior + cargos − abonos = deuda_al_corte lo verifica al centavo. Si un solo signo
# estuviera mal, la conciliación no cuadra y el estado se bloquea (nunca pasa en silencio).
# El importe SÍ lleva signo cuando es un abono: Nu lo imprime como '- $403.29' (guion, espacio,
# '$'). Pagos al emisor, devoluciones, reversiones parciales y 'Abono por plan de pagos fijos'
# van todos así; los cargos van sin signo. Antes el guion se tragaba en la descripción y todos
# los abonos que no eran 'Pago a tu tarjeta' se contaban como cargos: cada uno descuadraba la
# conciliación por 2× su importe (jul-2025: reversión de 70.00 → −140.00; nov–dic-2025: abono de
# plan de pagos de 324.64 → −649.28; verificado contra los estados reales).
_NUL_LINEA = re.compile(
    r"^(\d{1,2}\s+[A-Za-zÁÉÍÓÚáéíóú]{3,4})\.?\s+(.*?)\s+(-\s?)?\$?([\d,]+\.\d{2})\s*$")
# Desglose de compras a meses: repite las MISMAS compras con su tasa y parcialidad
# ('… $XXX.XX NN.NN% 3/3 $XXX.XX'). Contarlo duplicaría cada compra diferida.
# La tasa puede venir SIN decimales ('0% 1/3' en compras a meses sin intereses, jul–ago 2025): con
# decimales obligatorios esas líneas pasaban por movimiento y su última columna (saldo pendiente)
# se sumaba como cargo: el estado descuadraba justo por el 'Saldo total pendiente a meses'.
_NUL_DESGLOSE = re.compile(r"\d+(?:[.,]\d+)?\s*%\s*\d+\s*/\s*\d+")
# Los pagos al emisor son los ÚNICOS abonos del estado (coinciden al centavo con el total
# 'Pagos a tu tarjeta en el periodo' declarado en el resumen).
_NUL_ABONO = re.compile(r"pago\s+a\s+tu\s+tarjeta", re.I)
# Aumentar la línea de crédito con garantía se lista con importe pero NO es un cargo: no genera
# deuda, solo amplía el límite disponible.
_NUL_NO_CARGO = re.compile(r"aumentaste\s+tu\s+l[íi]mite", re.I)
# Total de abonos que DECLARA el emisor en su resumen. Es la contraprueba del signo: la identidad
# de saldo solo controla el NETO, así que dos signos invertidos que se compensan la satisfacen
# (R12-E-003). Comparar la suma de abonos contra este total rompe esa compensación.
# Conceptos cuyo SENTIDO no se deduce del rótulo: una devolución abona, el reverso de una
# devolución carga, y una bonificación puede ser cualquiera de las dos según el emisor. No se
# adivinan: se marcan dudosos y bloquean hasta que el usuario los resuelva.
_NUL_AMBIGUO = re.compile(
    r"(devoluci|revers[ao]|bonificaci|nota\s+de\s+cr[ée]dito|a\s+favor|cancelaci|ajuste\s+a\s+favor)",
    re.I)
_NUL_TOTAL_PAGOS = re.compile(
    r"pagos\s+a\s+tu\s+tarjeta\s+en\s+el\s+periodo[^\d\n]*([\d,]+\.\d{2})", re.I)


def _parse_nu_legacy(text, page):
    """Tarjeta Nu, diseño anterior: 'DD MMM <categoría> <descripción> $importe' (sin año ni signo)."""
    rows, warnings = [], []
    ridx = 0
    for raw in (text or "").splitlines():
        s = raw.strip()
        m = _NUL_LINEA.match(s)
        if not m:
            continue
        desc = re.sub(r"\s+", " ", m.group(2)).strip(" -·|")
        if _NUL_DESGLOSE.search(desc) or _NUL_NO_CARGO.search(desc):
            # se excluye a propósito (desglose de meses / ampliación de línea), pero se DEJA
            # constancia: una exclusión silenciosa puede tragarse un cargo legítimo cuyo texto
            # case por accidente con el patrón.
            warnings.append(f"línea excluida por rótulo (no es movimiento del periodo): {desc[:60]}")
            continue
        amount = float(m.group(4).replace(",", ""))
        flags = []
        if m.group(3) or _NUL_ABONO.search(desc):
            amount = -amount          # abono explícito ('- $') o pago al emisor: reduce la deuda
        elif _NUL_AMBIGUO.search(desc):
            # R12-E-003: hay conceptos cuyo sentido NO se deduce del rótulo —una devolución abona,
            # el reverso de una devolución carga, y ambos se escriben casi igual—. La identidad de
            # saldo no los atrapa: dos de esos con el mismo importe y el signo cruzado se compensan
            # y la conciliación sigue dando OK. Regla 00/21: lo que no se puede leer con certeza se
            # marca dudoso (bloquea el commit); no se adivina.
            flags.append("importe_dudoso")
        ridx += 1
        rows.append({
            "locator": (f"page:{page}|" if page else "") + f"line:{ridx}",
            "raw_text": s, "page": page, "row_idx": ridx,
            "date_op": m.group(1), "description": desc,
            "amount": f"{amount:.2f}", "_flags": flags})

    # R12-E-003: contraprueba del signo contra el total de abonos declarado. Si no cuadra, los
    # signos no son de fiar: se marcan TODOS los importes como dudosos (bloquea el commit) en vez
    # de dejar pasar una conciliación que solo verifica el neto.
    m = _NUL_TOTAL_PAGOS.search(text or "")
    if m and rows:
        declarado = round(float(m.group(1).replace(",", "")) * 100)
        # Solo los PAGOS al emisor entran en ese total: devoluciones, reversiones y abonos de plan
        # de pagos también son negativos pero el emisor los declara aparte ('Abonos y devoluciones').
        abonos = round(sum(-float(r["amount"]) for r in rows
                           if float(r["amount"]) < 0 and _NUL_ABONO.search(r["description"])) * 100)
        if abonos != declarado:
            warnings.append(
                f"los abonos extraídos ({abonos} centavos) no coinciden con el total de pagos "
                f"declarado por el emisor ({declarado}): el signo de algún movimiento no es fiable")
            for r in rows:
                r["_flags"].append("importe_dudoso")
    return rows, {}, warnings


def _parse_signed(text, page, bank=None):
    """Nu/Ualá: el importe trae SIGNO explícito (+$/−$) en la línea; el signo NO se infiere. Cada
    movimiento empieza con fecha 'DD MMM YYYY'. Se toma el ÚLTIMO importe firmado de la línea (evita
    montos de contexto tipo saldo/USD)."""
    rows, warnings = [], []
    lines = _unir_fecha_partida(text.splitlines())
    ridx = 0
    for i, raw in enumerate(lines):
        s = raw.strip()
        m = _SIGNED_DATE.match(s)
        if not m:
            continue
        signs = _SIGNED_AMT.findall(s)
        monto_previo = None
        if not signs:
            # importe huérfano en la línea inmediatamente anterior (ver _SIGNED_AMT_SOLO). Nunca en
            # silencio: el aviso deja rastro de que el monto vino de otro renglón (regla 21).
            mp = _SIGNED_AMT_SOLO.match(lines[i - 1]) if i > 0 else None
            if not mp:
                continue
            signs = [mp.groups()]
            monto_previo = lines[i - 1].strip()
            warnings.append(f"importe tomado de la línea anterior ('{monto_previo}') para el "
                            f"movimiento sin monto: {s[:70]}")
        sign_ch, num = signs[-1]                # último importe firmado
        amount = (-1 if sign_ch == "-" else 1) * float(num.replace(",", ""))
        # descripción = entre la fecha y el primer importe firmado; se limpia la 2ª fecha (crédito
        # lleva fecha de operación + de cargo) y el sufijo '| RFC: ...' de Nu.
        cut = _SIGNED_AMT.search(s)
        desc = re.sub(r"\s+", " ", s[m.end():cut.start()] if cut else s[m.end():])
        desc = re.sub(r"^\s*\d{1,2}[\s/][A-Za-zÁÉÍÓÚáéíóú]{3,}\.?[\s/]\d{4}\s+", "", desc)
        desc = re.sub(r"\s*\|\s*RFC:.*$", "", desc).strip(" -$·|")
        raw_text = s if monto_previo is None else f"{monto_previo}\n{s}"
        if not desc:
            desc = _desc_linea_previa(lines, i)
            if desc:
                # raw_text conserva AMBAS líneas: el original sigue siendo recuperable (regla 21/23).
                raw_text = f"{desc}\n{s}"
        # El rótulo se busca sobre la descripción YA resuelta, no sobre el renglón: cuando el texto
        # no cabe, Nu lo deja en la línea anterior y el renglón con fecha e importe se queda sin él.
        if _SIGNED_NO_MOV.search(desc):
            # se excluye a propósito, pero NUNCA en silencio (mismo criterio que el desglose a
            # meses de la tarjeta): si el rótulo casara por accidente con un movimiento real,
            # el aviso lo delata.
            warnings.append(f"línea excluida por rótulo (cambio de estado dentro de una Cajita, "
                            f"no entra ni sale dinero de la cuenta): {desc[:70]}")
            continue
        ridx += 1
        rows.append({
            "locator": (f"page:{page}|" if page else "") + f"line:{ridx}",
            "raw_text": raw_text, "page": page, "row_idx": ridx,
            "date_op": m.group(1), "description": desc,
            "amount": f"{amount:.2f}", "_flags": []})
    # R12-C-002: si el estado no trae NINGÚN importe firmado, es el diseño anterior de la tarjeta
    # Nu (fecha sin año, importe sin signo). Se intenta esa gramática antes de rendirse; así el
    # camino que ya funcionaba para el diseño actual queda intacto.
    # R12-E-007: la estrategia `signed` la comparten Nu y Ualá. El fallback estaba abierto a ambos,
    # así que un estado de Ualá que no casara caía en la gramática de Nu y salía con TODO positivo
    # y sin un solo warning: indistinguible de un parseo correcto. Se restringe al emisor para el
    # que está calibrado, y si otro emisor llega aquí se avisa en vez de inventar una lectura.
    if not rows:
        if bank in (None, "nu"):
            # Los warnings acumulados aquí (exclusiones por rótulo, sobre todo) se PERDÍAN al
            # devolver el fallback tal cual: un estado del que se excluyó todo salía con 0 filas y
            # 0 avisos, es decir, una exclusión silenciosa —justo lo que prohíbe la regla 21—. Se
            # arrastran, y se deja constancia de que el camino cambió de gramática.
            r_leg, meta_leg, w_leg = _parse_nu_legacy(text, page)
            if warnings:
                warnings.append("ninguna línea con importe firmado sobrevivió; se reintenta con la "
                                "gramática del diseño anterior (los avisos de arriba siguen "
                                "aplicando a las líneas descartadas)")
            return r_leg, meta_leg, warnings + w_leg
        warnings.append(f"ningún importe firmado reconocido para '{bank}': el estado queda sin "
                        f"movimientos (no se aplica la gramática de otro emisor)")
    return rows, {}, warnings


# es-MX: fecha DD/MMM (19/JUN) o DD/MMM/AA; el año viene del periodo (year_hint aguas arriba).
_BBVA_DATE = re.compile(r"^(\d{1,2}/[A-Za-zÁÉÍÓÚáéíóú]{3,4}(?:/\d{2,4})?)\b")
_ABONO_KW = re.compile(r"RECIBID|DEP[OÓ]SIT|ABONO|REEMBOLS|DEVOLU|INTERES(?:ES)? A FAVOR", re.I)


# BBVA imprime, DEBAJO de cada movimiento, uno o más renglones de detalle sin fecha ni importe:
# en compras con tarjeta 'RFC: … HH:MM AUT: … Referencia ******NNNN'; en SPEI/transferencias el
# concepto que escribió el ordenante ('Pago TDC', 'Transf a …'), la referencia, el banco, la CLABE,
# la clave de rastreo y el nombre de la contraparte. El bucle los saltaba y la salida se quedaba
# con 'SPEI ENVIADO NU MEXICO' a secas. Se conservan en `detail`, tal cual y en orden (regla 21:
# texto original junto al valor). El detalle puede continuar en la página siguiente, así que se
# filtra SOLO la maqueta del documento (cabecera/pie de página), nunca su contenido, y se corta al
# llegar al total de movimientos. `raw_text` sigue siendo el renglón del movimiento: no cambia la
# identidad de nada ya incorporado.
_BBVA_LIQ = re.compile(r"^\s*(\d{1,2}/[A-Za-zÁÉÍÓÚáéíóú]{3,4}(?:/\d{2,4})?)\s+")
_BBVA_FIN_TABLA = re.compile(
    r"^\s*(total\s+de\s+movimientos|total\s+importe\s+(cargos|abonos)|saldo\s+final\b)", re.I)
_BBVA_MAQUETA = re.compile(
    r"^\s*(estado\s+de\s+cuenta\s*$|libret[oó]n\b|pagina\s+\d+\s*/|no\.\s*de\s+(cuenta|cliente)\b"
    r"|bbva\s+mexico,\s*s\.a\.|av\.\s*paseo\s+de\s+la\s+reforma|la\s+gat\s+real\b"
    r"|fecha\s+saldo\s*$|oper\s+liq\s+descripcion|detalle\s+de\s+movimientos)", re.I)


def _bbva_iso(tok, ini, fin):
    """'DD/MMM' -> ISO resuelto contra el PERIODO del estado (la tabla de BBVA no imprime el año).
    Se prueban los años del inicio y del fin del periodo: gana el que cae dentro; si ninguno, el
    más cercano a menos de 45 días (lag operación/liquidación); si tampoco, None y el llamador
    conserva el crudo, que aguas abajo se resuelve o se marca dudoso. Nunca se adivina un año que
    deje la fecha a meses del periodo."""
    if not (tok and ini and fin):
        return None
    m = re.match(r"^(\d{1,2})/([A-Za-zÁÉÍÓÚáéíóú]{3,4})(?:/(\d{2,4}))?$", tok.strip())
    if not m:
        return None
    dia, mes = int(m.group(1)), _N.MESES.get(m.group(2).lower()[:3])
    if not mes:
        return None
    if m.group(3):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return _dt.date(y, mes, dia).isoformat()
        except ValueError:
            return None
    mejor = None
    for y in sorted({ini.year, fin.year}):
        try:
            c = _dt.date(y, mes, dia)
        except ValueError:
            continue
        if ini <= c <= fin:
            return c.isoformat()
        dist = (ini - c).days if c < ini else (c - fin).days
        if dist <= 45 and (mejor is None or dist < mejor[0]):
            mejor = (dist, c)
    return mejor[1].isoformat() if mejor else None


# Tramo de BBVA (varios movimientos del mismo día y un solo saldo al final): el signo se resuelve
# en tres pasos, del más al menos directo, y si ninguno cierra el tramo queda DUDOSO (bloquea).
#   1. Todos del mismo signo: |delta| == Σ importes.
#   2. Signo por rótulo (RECIBIDO/DEPÓSITO/ABONO… = abono, resto = cargo), VERIFICADO contra el delta.
#   3. El rótulo no basta —'PAGO CUENTA DE TERCERO' es cargo cuando el titular envía y abono cuando un
#      tercero le transfiere ('Transf a JUAN PEREZ'), y la línea es idéntica en ambos casos— pero el
#      saldo impreso sí determina el signo: se busca la asignación de signos cuya suma da el delta y
#      se acepta SOLO si es única. Dos asignaciones que cuadren (p. ej. dos importes iguales con
#      signo cruzado) no se desempatan adivinando: dudoso.
# Nada de esto inventa un importe: los importes son los impresos y el saldo también.
_BBVA_MAX_TRAMO = 12          # 2^12 combinaciones; un tramo real trae 2–6 movimientos


def _bbva_firmar_tramo(grp, net, warnings):
    mags = round(sum(x["mov"] or 0 for x in grp), 2)
    if abs(abs(net) - mags) < 0.01:                                   # 1) todos iguales
        for x in grp:
            x["sign"] = 1 if net >= 0 else -1
        return
    for x in grp:                                                     # 2) rótulo, verificado
        x["sign"] = 1 if _ABONO_KW.search(x["desc"]) else -1
    if abs(round(sum((x["mov"] or 0) * x["sign"] for x in grp) - net, 2)) < 0.01:
        return
    if len(grp) <= _BBVA_MAX_TRAMO and all(x["mov"] is not None for x in grp):   # 3) por saldo
        cent = [round(x["mov"] * 100) for x in grp]
        objetivo = round(net * 100)
        soluciones = []
        for mask in range(1 << len(grp)):
            if sum(c if mask >> i & 1 else -c for i, c in enumerate(cent)) == objetivo:
                soluciones.append(mask)
                if len(soluciones) > 1:
                    break
        if len(soluciones) == 1:
            for i, x in enumerate(grp):
                x["sign"] = 1 if soluciones[0] >> i & 1 else -1
            warnings.append("tramo de signo mixto resuelto por el saldo impreso (única asignación "
                            "que cuadra): " + "; ".join(x["desc"][:30] for x in grp))
            return
        if len(soluciones) > 1:
            warnings.append("tramo de signo mixto con VARIAS asignaciones que cuadran con el saldo; "
                            "no se desempata adivinando: " + "; ".join(x["desc"][:30] for x in grp))
    for x in grp:
        x["sign"] = 0   # tramo irresoluble -> dudoso (bloquea)


def _parse_bbva(text, page, opening, period_start=None, period_end=None, closing=None):
    """BBVA débito: columnas [OPER LIQ DESC REF CARGOS ABONOS OPERACION LIQUIDACION]. En el texto
    aplanado el importe es el PRIMER número y los saldos van al final (OPERACION=penúltimo de 3).
    No hay signo por línea; se deriva del DELTA del saldo corriente. Las líneas sin saldo (mismo día)
    se agrupan con la siguiente que sí lo trae y se firman por el delta del tramo; si el tramo es de
    signo mixto se usa la heurística RECIBIDO/DEPÓSITO (abono) vs resto (cargo) y se VERIFICA que
    sume el delta; si no, el movimiento se marca dudoso. La conciliación (opening+Σ vs closing) es el
    candado final (R8-A-001).
    Además de la fila, se conservan la fecha de LIQUIDACIÓN (`date_post`), el saldo impreso
    (`balance`, solo en las líneas que lo traen) y los renglones de detalle (`detail`)."""
    try:
        ini = _dt.date.fromisoformat(str(period_start))
        fin = _dt.date.fromisoformat(str(period_end))
    except (TypeError, ValueError):
        ini = fin = None
    recs, cur = [], None
    for raw in text.splitlines():
        s = raw.strip()
        m = _BBVA_DATE.match(s)
        a = _AMOUNT_TOK.findall(s) if m else []
        if m and a:
            mov = _to_float(a[0])
            bal = _to_float(a[-2]) if len(a) >= 3 else None   # OPERACION (saldo corriente)
            ml = _BBVA_LIQ.match(s[m.end():])
            # BBVA lleva DOS fechas (operación, liquidación); quitar la 2ª del inicio de la descripción.
            desc = re.sub(r"^\s*\d{1,2}/[A-Za-zÁÉÍÓÚáéíóú]{3,4}\s+", "", _description(s, m, a[0]))
            cur = {"date": m.group(1), "liq": ml.group(1) if ml else None, "desc": desc,
                   "mov": mov, "bal": bal, "sign": None, "raw": s, "detail": []}
            recs.append(cur)
            continue
        if cur is None or not s:
            continue
        if _BBVA_FIN_TABLA.match(s):
            cur = None                      # se acabó la tabla: lo que sigue no es detalle
            continue
        if _BBVA_MAQUETA.match(s):
            continue                        # cabecera/pie de página entre dos renglones de detalle
        cur["detail"].append(s)
    warnings = []
    run, pend = opening, []
    for r in recs:
        if r["bal"] is None:
            pend.append(r)
            continue
        grp = pend + [r]
        if run is not None and r["bal"] is not None:
            _bbva_firmar_tramo(grp, round(r["bal"] - run, 2), warnings)
        else:
            for x in grp:
                x["sign"] = 1 if _ABONO_KW.search(x["desc"]) else -1
        run = r["bal"]
        pend = []
    if pend and run is not None and closing is not None:
        # Movimientos finales sin saldo impreso: el estado sí declara el SALDO FINAL, y ese cierra
        # el tramo con la misma regla que los demás (delta contra el último saldo corriente).
        _bbva_firmar_tramo(pend, round(closing - run, 2), warnings)
    else:
        for x in pend:   # sin saldo final declarado: signo por rótulo, avisado
            x["sign"] = 1 if _ABONO_KW.search(x["desc"]) else -1
            warnings.append(f"movimiento sin saldo de cierre; signo por descripción: {x['desc'][:40]}")
    rows = []
    for i, r in enumerate(recs, 1):
        flags = []
        if r["mov"] is None or r["sign"] == 0:
            flags.append("importe_dudoso")
            warnings.append(f"signo/importe indeterminado: {r['raw'][:60]}")
            amount = r["mov"]
        else:
            amount = round(r["mov"] * r["sign"], 2)
        rows.append({
            "locator": (f"page:{page}|" if page else "") + f"line:{i}",
            "raw_text": r["raw"], "page": page, "row_idx": i,
            "date_op": _bbva_iso(r["date"], ini, fin) or r["date"],
            "date_post": (_bbva_iso(r["liq"], ini, fin) or r["liq"]) if r["liq"] else None,
            "description": r["desc"],
            "amount": f"{amount:.2f}" if amount is not None else "",
            "balance": f"{r['bal']:.2f}" if r["bal"] is not None else None,
            "detail": " | ".join(r["detail"]),
            "_flags": flags})
    return rows, {}, warnings


# --- BBVA tarjeta de crédito (calibrada contra el estado real de jun–jul 2026) ---------------
# Renglón: 'DD-mmm-AAAA DD-mmm-AAAA DESCRIPCIÓN ; Tarjeta Digital ***NNNN + $1,234.56'. Las dos fechas
# son operación y cargo; el signo es NATIVO de crédito ('+' cargo/compra, '−' pago o devolución), la
# misma convención que `_parse_cargo_abono`, y la cuenta con sign_convention='inverted' lo lleva a
# flujo de caja. Una compra en dólares añade un renglón 'USD $100.00 TIPO DE CAMBIO $17.71' sin fecha:
# se conserva en `detail` del movimiento anterior. La sección de puntos ('2026-07-05 POR COMPRAS …')
# empieza por fecha ISO y no trae importe firmado con '$', así que no casa. Saldos del periodo en
# CREDIT_SUMMARY['bbva']; el estado imprime TOTAL CARGOS / TOTAL ABONOS sin conteo, así que no
# alimentan `declared_totals` (la identidad de saldo sigue siendo el candado).
_BBVA_TDC_MARK = re.compile(r"desglose\s+de\s+movimientos|cargos,\s*compras\s+y\s+abonos\s+regulares", re.I)
_BBVA_TDC_LINEA = re.compile(
    r"^(\d{1,2}-[A-Za-zÁÉÍÓÚáéíóú]{3}-\d{4})\s+(\d{1,2}-[A-Za-zÁÉÍÓÚáéíóú]{3}-\d{4})\s+(.+?)\s*"
    r"([+-])\s*\$\s*([\d,]+\.\d{2})\s*$")
_BBVA_TDC_TARJETA = re.compile(r"\s*;\s*(tarjeta\s+\w+\s+\*+\d{4})\s*$", re.I)
_BBVA_TDC_USD = re.compile(r"^(USD\s*\$\s*[\d,]+\.\d{2}\s+TIPO\s+DE\s+CAMBIO\s+\$?\s*[\d,]+\.\d{2})", re.I)


def _parse_bbva_tdc(text, page):
    rows, warnings = [], []
    for raw in (text or "").splitlines():
        s = raw.strip()
        m = _BBVA_TDC_LINEA.match(s)
        if not m:
            mu = _BBVA_TDC_USD.match(s)
            if mu and rows:
                rows[-1]["detail"] = (rows[-1]["detail"] + " | " if rows[-1]["detail"] else "") + mu.group(1)
            continue
        desc = m.group(3).strip()
        detail = ""
        mt = _BBVA_TDC_TARJETA.search(desc)
        if mt:
            detail = mt.group(1)
            desc = desc[:mt.start()].strip(" ;")
        amount = float(m.group(5).replace(",", "")) * (1 if m.group(4) == "+" else -1)
        rows.append({
            "locator": (f"page:{page}|" if page else "") + f"line:{len(rows) + 1}",
            "raw_text": s, "page": page, "row_idx": len(rows) + 1,
            "date_op": m.group(1), "date_post": m.group(2),
            "description": desc, "amount": f"{amount:.2f}",
            "detail": detail, "_flags": []})
    if not rows:
        warnings.append("BBVA tarjeta: se reconoció la maqueta del desglose pero ningún renglón de "
                        "movimiento casó; revisa si la tabla se partió en la extracción")
    return rows, {}, warnings


def _parse_saldo(text, page, opening):
    """R8-A-001: el importe del movimiento es el DECLARADO en la línea (columna CARGO/ABONO); el
    saldo corriente solo aporta el SIGNO y CRUZA-VALIDA la magnitud. Antes el importe ERA el delta
    del saldo, lo que hacía la conciliación tautológica (Σdeltas telescopia a saldo_final−inicial,
    diff=0 siempre) y ocultaba saldos corruptos o filas perdidas. Ahora: importe declarado ≠ delta
    de saldo ⇒ `importe_dudoso` (bloquea); y una fila perdida ya NO se absorbe en el siguiente delta,
    así que la conciliación (que suma importes declarados) deja de cuadrar y bloquea."""
    rows, warnings = [], []
    prev = opening
    ridx = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        # R9-A-002: fecha ANCLADA al inicio (no `.search`): así una línea de resumen/subtotal con una
        # fecha embebida ('Rendimiento acumulado del 01/06/2026...') no se ingiere como movimiento
        # fantasma, aunque su saldo continúe la cadena.
        dm = _DATE_TOK.match(s)
        amts = _AMOUNT_TOK.findall(s)
        if not dm or len(amts) < 2:
            if dm and _AMOUNT_TOK.search(s):
                warnings.append(f"línea con fecha sin par (importe, saldo) — posible fila perdida: {s[:80]}")
            continue
        saldo = _to_float(amts[-1])
        stated = _to_float(amts[-2])                   # importe DECLARADO del movimiento
        ridx += 1
        flags = []
        if prev is not None and saldo is not None:
            delta = round(saldo - prev, 2)
            if stated is not None:
                # importe = magnitud DECLARADA, signo del delta; se cruza-valida contra el delta.
                amount = round(math.copysign(abs(stated), delta if delta != 0 else 1.0), 2)
                if abs(abs(stated) - abs(delta)) > 0.01:
                    flags.append("importe_dudoso")
                    warnings.append(f"importe declarado {stated:.2f} no cuadra con el delta de saldo "
                                    f"{delta:.2f} (saldo o importe corrupto): {s[:60]}")
            else:
                amount = delta                          # sin importe declarado: cae al delta (menos verificable)
                flags.append("importe_dudoso")
                warnings.append(f"sin importe declarado, solo saldo (marcado dudoso): {s[:60]}")
        else:
            amount = stated                             # sin saldo previo: no se puede firmar
            flags.append("importe_dudoso")
            warnings.append(f"sin saldo previo para inferir el signo (marcado dudoso): {s[:60]}")
        rows.append({
            "locator": (f"page:{page}|" if page else "") + f"line:{ridx}",
            "raw_text": s, "page": page, "row_idx": ridx,
            "date_op": dm.group(1).strip(),
            "description": _description(s, dm, amts[0]),
            "amount": f"{amount:.2f}" if amount is not None else "",
            "balance": f"{saldo:.2f}" if saldo is not None else None,
            "_flags": flags,
        })
        if saldo is not None:
            prev = saldo
    return rows, {}, warnings


# HSBC lista el DÍA suelto (columna 'Día') seguido del código de operación; la fecha completa vive
# en el periodo del estado. El encabezado real de la tabla es:
#   Día | Descripción | Referencia | Retiro/Cargo | Depósito/Abono | Saldo
# Cada renglón llena SOLO una de las dos columnas de importe, así que quedan dos cifras por línea:
# (importe, saldo). En texto plano las columnas se colapsan, de modo que NO se puede saber por
# posición si el importe fue cargo o abono: el signo se toma del delta del saldo corriente y se
# cruza-valida contra el importe declarado. Esa es exactamente la garantía de `_parse_saldo`, que
# se reutiliza tal cual en vez de reimplementar la lógica contable (un dígito mal leído por OCR
# rompe la igualdad |delta| == importe y queda `importe_dudoso`, que bloquea el commit).
# El renglón NO siempre empieza por el código de operación en letras: en las compras con tarjeta
# HSBC antepone el número de autorización ('27 240113461151000282905925CLAUDE.AI SUBSC ...'), y
# exigir [A-Z]{2,7} descartaba esos movimientos en silencio —dos de los cinco de abril de 2026—.
# Se pide solo el día seguido de algo; lo que impide que una línea cualquiera pase es el requisito
# de DOS importes en el renglón (importe y saldo), que se comprueba en `_parse_hsbc` antes de
# fechar y que `_parse_saldo` vuelve a exigir aguas abajo.
_HSBC_MOV = re.compile(r"^\s*(\d{1,2})\s+(?=\S{2,})")


# HSBC separa los miles con un ESPACIO FINO, no con coma: la tabla imprime '$ X XXX.XX'. El patrón
# de importes solo reconoce la coma, así que se quedaba con la parte de la derecha ('XXX.XX') y
# perdía el dígito de millar —en silencio, porque la cifra truncada sigue siendo un importe
# válido—. El resultado era un delta de saldo que no cuadraba con el importe declarado y toda la
# cadena marcada dudosa.
#
# La restitución va ANCLADA AL '$'. Una primera versión operaba sobre el renglón entero, con el
# patrón "espacio entre un dígito y exactamente tres dígitos", y eso rompía por los dos lados:
#   · '10 123456 FOLIO 12 345.67 $ 20,000.00' fundía el '12' suelto con '345.67' y fabricaba un
#     importe de cinco cifras, sin marcarlo;
#   · '05 123 DEPOSITO … ' convertía el arranque en '05,123', con lo que el renglón dejaba de casar
#     como movimiento y la fila DESAPARECÍA sin un solo aviso.
# En la tabla de HSBC todo importe lleva '$' delante, así que exigirlo acota la reescritura a la
# columna de dinero y deja intactos folios, referencias, teléfonos y el número de autorización.
_HSBC_IMPORTE = re.compile(r"\$\s*\d{1,3}(?:[ ,]\d{3})*\.\d{2}")


def _restituir_miles(linea):
    """Devuelve el renglón con los espacios de millar convertidos en coma, solo dentro de importes."""
    return _HSBC_IMPORTE.sub(lambda m: m.group(0).replace(" ", ",").replace("$,", "$ "), linea)


def _fecha_de_dia(dia, ini, fin):
    """Día suelto -> fecha ISO, resuelta contra el periodo COMPLETO.

    Tomar el mes de `period_start` para todos los renglones fecha mal en cuanto el corte no es
    mes calendario: con 19/dic–18/ene, el renglón del día 05 es de ENERO y quedaba en diciembre
    —mes contable y ejercicio equivocados— sin warning y conciliando OK, porque la conciliación
    solo suma importes. Se recorre el periodo y se exige que UNA SOLA fecha tenga ese día; si hay
    cero o varias, no se adivina (regla 00).
    """
    dentro = [ini + _dt.timedelta(days=i)
              for i in range((fin - ini).days + 1)
              if (ini + _dt.timedelta(days=i)).day == dia]
    return dentro[0].isoformat() if len(dentro) == 1 else None


def _parse_hsbc(text, page, opening, period_start, period_end):
    try:
        ini = _dt.date.fromisoformat(str(period_start))
        fin = _dt.date.fromisoformat(str(period_end))
    except (TypeError, ValueError):
        return [], {}, ["HSBC: el estado no trae un periodo utilizable; sin él el DÍA suelto de "
                        "cada renglón no se puede fechar y no se inventa (regla 00)."]
    if fin < ini:
        return [], {}, ["HSBC: periodo invertido (fin < inicio); no se fecha nada."]
    lineas, warnings = [], []
    sueltas = 0
    for raw in text.splitlines():
        raw = _restituir_miles(raw)
        m = _HSBC_MOV.match(raw)
        if not m or len(_AMOUNT_TOK.findall(raw)) < 2:
            # Un renglón que empieza por día y trae UN importe es un candidato a movimiento que no
            # se pudo emparejar con su saldo. No se ingiere (no se inventa el par) pero tampoco se
            # tira callando: la regla 21 prohíbe el descarte silencioso.
            if m and _AMOUNT_TOK.search(raw):
                sueltas += 1
            lineas.append(raw)
            continue
        iso = _fecha_de_dia(int(m.group(1)), ini, fin)
        if iso is None:
            # El renglón se CONSERVA con el día crudo (no se omite: eliminar un movimiento para
            # cuadrar viola la regla 00). Sin fecha ISO delante, la normalización lo marca
            # `fecha_dudosa`, que bloquea el commit, y su saldo sigue en la cadena.
            warnings.append(f"HSBC: el día {m.group(1)} no se puede fechar sin ambigüedad dentro "
                            f"del periodo; renglón marcado dudoso: {raw.strip()[:60]}")
            lineas.append(raw)
            continue
        lineas.append(iso + raw[m.end(1):])
    rows, meta, w = _parse_saldo("\n".join(lineas), page, opening)
    if sueltas:
        warnings.append(f"HSBC: {sueltas} renglón(es) empiezan por día y traen un solo importe (sin "
                        f"su saldo): no se ingieren, revisa si la tabla se partió en la extracción")
    return rows, meta, warnings + w


# fecha al INICIO del renglón (evita agarrar líneas de resumen/saldos): DD/MM/YYYY o DD MMM YYYY.
_CC_DATE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}[\s/-][A-Za-zÁÉÍÓÚáéíóú]{3,}\.?[\s/-]\d{2,4})")
_PAGO_KW = re.compile(r"\b(pago|abono|dep[oó]sito|cr[eé]dito|bonific|reembols|devoluc)\w*", re.I)
# Vexi cobra la penalización como 'Comisión por pago tardío' / 'IVA pago tardío': lleva la palabra
# 'pago' pero es un CARGO. Sin esta excepción cada una entraba como abono y el estado descuadraba
# 2×(300+48) (feb y mar 2026). Se acota a la frase literal, no a 'comisión' en general, para no
# convertir en cargo una bonificación de comisión.
_PAGO_TARDIO = re.compile(r"pago\s+tard[ií]o", re.I)


def _parse_cargo_abono(text, page):
    """Tarjeta de crédito por RENGLÓN (fecha real al INICIO + importe). Signo NATIVO de crédito: la
    compra/cargo AUMENTA la deuda (+) y el pago/abono la reduce (−); la cuenta con
    sign_convention='inverted' lo lleva a flujo de caja canónico (compra = gasto). Exigir la fecha al
    inicio evita parsear líneas de resumen (pago mínimo, CAT, saldos) como movimientos."""
    rows, warnings = [], []
    ridx = 0
    for raw in text.splitlines():
        s = raw.strip()
        m = _CC_DATE.match(s)
        if not m:
            continue
        amts = _AMOUNT_TOK.findall(s)
        if not amts:
            continue
        tok = amts[0]
        val = _to_float(tok)
        if val is None:
            continue
        neg = tok.strip().startswith("(") or tok.strip().startswith("-")
        es_abono = neg or (_PAGO_KW.search(s) and not _PAGO_TARDIO.search(s))
        amount = -abs(val) if es_abono else abs(val)   # nativo: cargo +, pago −
        ridx += 1
        flags = []
        rows.append({
            "locator": (f"page:{page}|" if page else "") + f"line:{ridx}",
            "raw_text": s, "page": page, "row_idx": ridx,
            "date_op": m.group(1).strip(),
            "description": _description(s, m, tok),
            "amount": f"{amount:.2f}" if amount is not None else "",
            "_flags": flags,
        })
    return rows, {}, warnings
