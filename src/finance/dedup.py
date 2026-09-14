# -*- coding: utf-8 -*-
"""
dedup.py — Duplicados y transferencias (regla 26).

NO se declara duplicado solo por fecha+descripción+importe. Se usa:
- fingerprint (cuenta+fecha+importe+desc_norm+ref)
- bank_ref cuando existe
- solape de periodos entre estados
Un cargo genuino repetido NO se fusiona: se marca para revisión, no se borra.
"""
from __future__ import annotations
import datetime as _dt
import re
import unicodedata


def find_duplicates(new_txns, existing_by_fp):
    """
    existing_by_fp: {fingerprint: [ {transaction_id, statement_id, bank_ref, date_op, period_start, period_end} ]}
    Devuelve dict {transaction_id_nuevo: {"dup_of":..., "reason":...}} SOLO para duplicados de alta confianza.
    Los sospechosos (misma huella sin ref ni solape) se marcan review, no dup.
    """
    result = {}
    for t in new_txns:
        fp = t["fingerprint"]
        cands = existing_by_fp.get(fp, [])
        for c in cands:
            if c["statement_id"] == t["statement_id"]:
                continue  # mismo estado => idempotencia por transaction_id, no es "otro" duplicado
            same_ref = t.get("bank_ref") and c.get("bank_ref") and t["bank_ref"] == c["bank_ref"]
            overlap = _periods_overlap(t.get("period_start"), t.get("period_end"),
                                       c.get("period_start"), c.get("period_end"))
            if same_ref or overlap:
                result[t["transaction_id"]] = {
                    "dup_of": c["transaction_id"],
                    "reason": "mismo bank_ref" if same_ref else "misma huella en periodos solapados",
                }
                break
            else:
                # sospechoso: no fusionar; marcar review
                t.setdefault("_flags", []).append("posible_duplicado_sin_confirmar")
    return result


def find_transfers(new_txns, candidate_pool, own_group, window_days=3, account_tokens=None,
                   bucket_cap=300, titular=None):
    """
    Empareja salida (amount<0) en cuenta A con entrada (amount>0) en cuenta B,
    mismo importe absoluto, dentro de window_days, ambas en own_group.
    candidate_pool: transacciones ya existentes/known (dicts con account_id, amount_minor,
    date_op, transaction_id, description_norm, bank_ref).

    Regla 26 / F-06: el importe absoluto igual + signo opuesto + ventana NO basta para
    vincular (dos movimientos no relacionados del mismo monto se confundirían y podrían
    desaparecer del P&L). Se EXIGE una señal adicional: bank_ref cruzado o un token de la
    contraparte en la descripción. Sin señal se marca 'posible_transferencia_sin_confirmar'
    para revisión manual y NO se vincula automáticamente.
    """
    account_tokens = account_tokens or {}
    new_ids = {t["transaction_id"] for t in new_txns}
    candidatos = []
    pool = list(candidate_pool) + list(new_txns)
    by_absamount = {}
    for t in pool:
        if t["account_id"] in own_group:
            by_absamount.setdefault(abs(t["amount_minor"]), []).append(t)
    seen = set()
    for t in new_txns:
        if t["account_id"] not in own_group:
            continue
        bucket = by_absamount.get(abs(t["amount_minor"]), [])
        # R5-A-004: cota anti-DoS. Un grupo enorme del mismo importe absoluto vuelve la
        # comparación O(k²). Por encima del tope NO se auto-vincula (se marca revisión); es más
        # seguro pedir revisión manual que colgar el pipeline con miles de líneas iguales.
        if len(bucket) > bucket_cap:
            if t["transaction_id"] in new_ids:
                t.setdefault("_flags", []).append("posible_transferencia_sin_confirmar")
            continue
        for other in bucket:
            if other["transaction_id"] == t["transaction_id"]:
                continue
            if other["account_id"] == t["account_id"]:
                continue
            if (t.get("currency") or "MXN") != (other.get("currency") or "MXN"):
                continue  # SOL-R2-003: no emparejar entre monedas distintas (exige FX auditado)
            if (t["amount_minor"] > 0) == (other["amount_minor"] > 0):
                continue  # deben tener signos opuestos
            if not _within(t["date_op"], other["date_op"], window_days):
                continue
            key = tuple(sorted([t["transaction_id"], other["transaction_id"]]))
            if key in seen:
                continue
            seen.add(key)
            frm = t if t["amount_minor"] < 0 else other
            to = other if t["amount_minor"] < 0 else t
            signal, conf = _transfer_signal(frm, to, account_tokens, titular)
            if not signal:
                # sin señal adicional: NO vincular; marcar los extremos nuevos para revisión
                for x in (t, other):
                    if x["transaction_id"] in new_ids:
                        x.setdefault("_flags", []).append("posible_transferencia_sin_confirmar")
                continue
            kind = "card_payment" if _is_credit(to) else "transfer"
            candidatos.append({"from": frm["transaction_id"], "to": to["transaction_id"],
                               "kind": kind, "confidence": conf, "signal": signal,
                               "_frm": frm, "_to": to})

    # Un movimiento pertenece a UN solo par. Sin esto, dos pagos del mismo importe el mismo día
    # producían los cuatro cruces posibles; y como el export marca `es_transferencia` para
    # cualquier id que aparezca en cualquier vínculo, un cruce con un movimiento NO relacionado del
    # mismo importe (p. ej. una devolución de compra) lo sacaba del P&L. `seen` solo evitaba repetir
    # el MISMO par, no reutilizar un extremo. La ambigüedad no se resuelve por orden de iteración:
    # si un extremo admite más de un candidato, no se vincula ninguno y se manda a revisión.
    grado = {}
    for c in candidatos:
        grado[c["from"]] = grado.get(c["from"], 0) + 1
        grado[c["to"]] = grado.get(c["to"], 0) + 1
    links = []
    for c in candidatos:
        if grado[c["from"]] == 1 and grado[c["to"]] == 1:
            links.append({k: v for k, v in c.items() if not k.startswith("_")})
            continue
        for x in (c["_frm"], c["_to"]):
            if x["transaction_id"] in new_ids:
                flags = x.setdefault("_flags", [])
                if "posible_transferencia_sin_confirmar" not in flags:
                    flags.append("posible_transferencia_sin_confirmar")
    return links


# Tokens genéricos que NO identifican una contraparte (evitan falsos positivos).
# Defensa en profundidad: la fuente ya llega depurada (services._account_tokens), pero un token
# genérico mal declarado en `counterparty_tokens` bastaría para vincular dos importes iguales sin
# relación y sacarlos del P&L. Las de la segunda mitad son las que producía `match_hints` cuando
# se usaba como fuente, más las palabras de formulario que salen en cualquier estado de cuenta.
_TOKEN_STOP = {"cuenta", "tarjeta", "credito", "crédito", "debito", "débito", "banco",
               "personal", "nomina", "nómina", "pago", "compra", "spei", "transferencia",
               "mexico", "méxico", "amex",
               "por", "saldo", "estado", "este", "esta", "generar", "comisiones", "cobradas",
               "abono", "cargo", "deposito", "depósito", "retiro", "movimiento", "movimientos",
               "fecha", "total", "importe", "monto", "referencia", "clave", "folio", "concepto",
               # funcionales de 2–3 letras: desde que `_mentions` acepta tokens cortos (para marcas
               # como 'Nu'), cualquiera de estas declarada por error en `counterparty_tokens`
               # bastaría para vincular dos importes iguales sin relación.
               "de", "del", "la", "el", "los", "las", "un", "una", "al", "en", "con", "sin",
               "mi", "tu", "su", "lo", "se", "no", "es", "ya", "que", "para", "mx", "sa", "cv"}


def _transfer_signal(frm, to, account_tokens, titular=None):
    """Devuelve (nombre_de_señal, confianza) o (None, 0) si no hay señal adicional."""
    rf, rt = (frm.get("bank_ref") or "").strip(), (to.get("bank_ref") or "").strip()
    if rf and rt and _ref_related(rf, rt):
        return "bank_ref", 0.95
    df = (frm.get("description_norm") or frm.get("description_raw") or "").lower()
    dt = (to.get("description_norm") or to.get("description_raw") or "").lower()
    if _mentions(df, account_tokens.get(to["account_id"], set())) or \
       _mentions(dt, account_tokens.get(frm["account_id"], set())):
        return "counterparty_token", 0.8
    # El banco de origen no siempre nombra al banco destino: muchos traspasos propios se rotulan
    # con el NOMBRE DEL TITULAR ("JUAN PEREZ … Transferencia SPEI"). Se exige el nombre COMPLETO como
    # secuencia, nunca apellidos sueltos: en las mismas cuentas hay transferencias de familiares
    # que comparten apellido, y emparejarlas las borraría del P&L.
    #
    # Solo cuenta en la pata que RECIBE. Aceptarlo en cualquiera de las dos degradaba la señal a
    # "importe igual + ventana": los bancos escriben al ORDENANTE (el titular) en TODA transferencia
    # enviada, también a terceros, así que un regalo a un tercero y un reembolso ajeno del mismo
    # importe se vinculaban y desaparecían los dos del P&L. En el lado que recibe, en cambio, estos
    # bancos rotulan a la CONTRAPARTE —se comprueba en los estados: las entradas de terceros llevan
    # el nombre del tercero—, así que ver ahí el nombre del dueño sí implica cuenta propia.
    if titular and _menciona_titular(dt, titular):
        return "titular", 0.85
    # Pago de tarjeta propia: el cargo dice "pago a tu tarjeta…", el destino ES una cuenta de
    # crédito Y su propio renglón acusa un pago recibido. Las palabras sueltas ('pago', 'tarjeta',
    # 'crédito') son demasiado genéricas para servir de token, pero las dos frases + el tipo de
    # cuenta destino + importe exacto sí distinguen. Se exige la confirmación del lado de la
    # tarjeta porque, sin ella, un pago real casaba con cualquier abono del mismo importe en otra
    # tarjeta propia —una DEVOLUCIÓN de compra, por ejemplo— y la devolución salía del P&L.
    if _is_credit(to) and _PAGO_TARJETA.search(df) and _PAGO_RECIBIDO.search(dt):
        return "pago_tarjeta", 0.85
    return None, 0.0


_PAGO_TARJETA = re.compile(r"pago\s+(?:a\s+)?(?:tu\s+|mi\s+|la\s+)?tarjeta", re.I)
# Acuse del lado de la tarjeta. Deliberadamente NO incluye 'bonificación'/'devolución': son abonos
# legítimos que no son pagos y no deben emparejarse con una salida de la cuenta.
_PAGO_RECIBIDO = re.compile(r"\bpago\b|gracias", re.I)


def _sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


def _menciona_titular(desc, titular):
    # Con límites de palabra y separadores flexibles: `objetivo in desc` casaba como SUBCADENA, así
    # que un nombre pegado a otro texto ('...XANA MARIA PEREZ LOPEZ') contaba como mención, y la
    # puntuación entre apellidos ('PEREZ  LOPEZ', 'PEREZ, LOPEZ') impedía casar el caso legítimo.
    tokens = re.findall(r"\w+", _sin_acentos(titular).lower())
    if len(tokens) < 2:                # un solo nombre no identifica a nadie: no es señal
        return False
    patron = r"(?<!\w)" + r"\W+".join(re.escape(t) for t in tokens) + r"(?!\w)"
    return bool(re.search(patron, _sin_acentos(desc).lower()))


def _ref_related(a, b):
    # SOL-R5-003: el match por prefijo sobre folios CORTOS produce falsos positivos
    # ("700".startswith("7")) que vinculan movimientos no relacionados como transferencia y
    # los borran del P&L. Exigir igualdad; el prefijo solo cuenta si el más corto ya es
    # razonablemente específico (>=6) — así SPEI/claves largas siguen emparejando.
    a, b = a.upper().strip(), b.upper().strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 6 and (a.startswith(b) or b.startswith(a))


# Verbo/sustantivo de la operación. Un traspaso entre cuentas propias SIEMPRE lo lleva; el nombre
# de un comercio, no. Es el desempate para tokens tan cortos que su sola aparición no distingue una
# contraparte de una marca comercial.
_LEXICO_TRASPASO = re.compile(
    r"\b(spei|transferencia|traspaso|enviado|env[ií]o|recibido|dep[oó]sito|abono)\b")


def _mentions(desc, tokens):
    # >=2 y no genérico: los tokens ya llegan depurados desde services._account_tokens (nombre de
    # cuenta/emisor o `counterparty_tokens` explícitos), así que una marca corta como 'Nu' es
    # legítima. El filtro de longitud existía para contener la basura de `match_hints`, que ya no
    # se usa como fuente.
    #
    # Pero un token de 2-3 letras casa con la marca aislada de un comercio ("COMPRA NU SKIN"), y el
    # candado de grado único NO cubre ese caso: con UN cargo y UN abono del mismo importe el grado
    # es 1 en ambos y se vinculaba con la misma confianza que un traspaso real, sacando los dos del
    # P&L. Para esos tokens se exige además el léxico de la operación. La condición solo RESTA
    # vínculos —nunca crea uno nuevo—, así que su peor caso es mandar a revisión manual algo
    # legítimo, no borrar dinero real.
    for tok in tokens:
        if len(tok) >= 2 and tok not in _TOKEN_STOP and re.search(rf"\b{re.escape(tok)}\b", desc):
            if len(tok) <= 3 and not _LEXICO_TRASPASO.search(desc):
                continue
            return True
    return False


def _is_credit(t):
    return (t.get("account_type") or "").startswith("credito")


def _within(d1, d2, days):
    try:
        a = _dt.date.fromisoformat(d1)
        b = _dt.date.fromisoformat(d2)
        return abs((a - b).days) <= days
    except Exception:
        return False


def _periods_overlap(s1, e1, s2, e2):
    try:
        if not all([s1, e1, s2, e2]):
            return False
        s1, e1, s2, e2 = map(_dt.date.fromisoformat, [s1, e1, s2, e2])
        return s1 <= e2 and s2 <= e1
    except Exception:
        return False
