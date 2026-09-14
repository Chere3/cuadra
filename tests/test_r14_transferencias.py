# -*- coding: utf-8 -*-
"""
test_r14_transferencias.py — señales de transferencia entre cuentas propias (regla 26).

Vincular es destructivo para el P&L: las dos patas se excluyen del cálculo de ingreso/gasto. Por
eso importe igual + signo opuesto + ventana NO basta, y hace falta una señal. Aquí se cubren las
dos señales nuevas y, sobre todo, los casos en que NO deben dispararse:

- `titular`: varios bancos rotulan el traspaso propio con el nombre del dueño en vez del banco
  destino. Se exige el nombre COMPLETO como secuencia — en las mismas cuentas hay transferencias
  de FAMILIARES que comparten apellido, y vincularlas las borraría del P&L.
- `pago_tarjeta`: 'pago a tu tarjeta…' + cuenta destino de tipo crédito.
- tokens de contraparte: ya NO se derivan de `match_hints`. Esas frases describen el DOCUMENTO
  ('Comisiones cobradas por Nu'), y producían tokens como 'por' o 'saldo' que aparecen en
  cualquier descripción: bastaba uno para vincular dos importes iguales sin relación.

Datos ficticios.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance.dedup import _transfer_signal, find_transfers  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


TITULAR = "Ana María Pérez López"


def mov(tid, acc, monto, desc, fecha="2026-03-10", tipo="debito", ref=None):
    return {"transaction_id": tid, "account_id": acc, "amount_minor": monto, "date_op": fecha,
            "description_norm": desc, "bank_ref": ref, "currency": "MXN", "account_type": tipo}


def test_titular_vincula():
    frm = mov("a", "banco_a", -50000, "CGO Transferencia SPEI 998877")
    to = mov("b", "banco_b", 50000, "ANA MARIA PEREZ LOPEZ Transferencia SPEI")
    sig, conf = _transfer_signal(frm, to, {}, TITULAR)
    check("nombre completo del titular es señal", sig == "titular" and conf > 0.8, str(sig))


def test_titular_solo_cuenta_en_la_pata_que_recibe():
    """Aceptarlo en cualquiera de las dos degradaba la señal a 'importe igual + ventana': los
    bancos escriben al ORDENANTE (el titular) en toda transferencia enviada, también a terceros.
    Un regalo a un tercero y un reembolso ajeno del mismo importe se vinculaban y desaparecían
    los dos del P&L."""
    frm = mov("a", "banco_a", -100000, "spei enviado ana maria perez lopez a juan n ref 9")
    to = mov("b", "banco_b", 100000, "deposito recibido")
    sig, _ = _transfer_signal(frm, to, {}, TITULAR)
    check("el titular como ORDENANTE en la salida no es señal", sig is None, str(sig))


def test_titular_exige_limites_de_palabra():
    """`objetivo in desc` casaba como subcadena: un nombre pegado a otro texto contaba."""
    frm = mov("a", "banco_a", -50000, "traspaso")
    to = mov("b", "banco_b", 50000, "XANA MARIA PEREZ LOPEZ Transferencia")
    sig, _ = _transfer_signal(frm, to, {}, TITULAR)
    check("nombre pegado a otro texto NO es mención", sig is None, str(sig))


def test_titular_admite_puntuacion_entre_nombres():
    frm = mov("a", "banco_a", -50000, "traspaso")
    to = mov("b", "banco_b", 50000, "ANA  MARIA, PEREZ-LOPEZ transferencia")
    sig, _ = _transfer_signal(frm, to, {}, TITULAR)
    check("separadores flexibles entre los nombres sí casan", sig == "titular", str(sig))


def test_titular_ignora_acentos_y_mayusculas():
    frm = mov("a", "banco_a", -50000, "traspaso")
    to = mov("b", "banco_b", 50000, "ana maria perez lopez transferencia")
    sig, _ = _transfer_signal(frm, to, {}, TITULAR)
    check("acentos y mayúsculas son indiferentes", sig == "titular")


def test_familiar_con_mismos_apellidos_NO_vincula():
    """El caso que justifica exigir el nombre completo."""
    frm = mov("a", "banco_a", -50000, "CGO Transferencia SPEI 998877")
    to = mov("b", "banco_b", 50000, "JOSE EMILIANO PEREZ LOPEZ Transferencia")
    sig, _ = _transfer_signal(frm, to, {}, TITULAR)
    check("familiar con los mismos apellidos NO es señal", sig is None, str(sig))


def test_nombre_parcial_NO_vincula():
    frm = mov("a", "banco_a", -50000, "traspaso")
    to = mov("b", "banco_b", 50000, "ANA PEREZ Transferencia")
    sig, _ = _transfer_signal(frm, to, {}, TITULAR)
    check("nombre incompleto NO es señal", sig is None, str(sig))


def test_titular_de_una_palabra_no_es_senal():
    frm = mov("a", "banco_a", -50000, "traspaso ana")
    to = mov("b", "banco_b", 50000, "deposito ana")
    sig, _ = _transfer_signal(frm, to, {}, "Ana")
    check("un titular de una sola palabra no identifica a nadie", sig is None, str(sig))


def test_pago_tarjeta_vincula():
    frm = mov("a", "banco_a", -30000, "Pago a tu tarjeta de crédito Nu")
    to = mov("b", "banco_tdc", 30000, "¡Muchas gracias! Pago a tu tarjeta de crédito",
             tipo="credito")
    sig, conf = _transfer_signal(frm, to, {}, None)
    check("pago a tarjeta propia es señal", sig == "pago_tarjeta" and conf > 0.8, str(sig))


def test_devolucion_de_compra_no_es_pago_de_tarjeta():
    """Sin acuse del lado de la tarjeta, un pago real casaba con cualquier abono del mismo importe
    en otra tarjeta propia. Una devolución de compra es un abono legítimo que NO es un pago, y
    vincularla la sacaba del P&L."""
    frm = mov("a", "banco_a", -30000, "Pago a tu tarjeta de crédito Nu")
    to = mov("b", "otra_tdc", 30000, "Devolución Oxxo Andares", tipo="credito")
    sig, _ = _transfer_signal(frm, to, {}, None)
    check("una devolución de compra NO es pago de tarjeta", sig is None, str(sig))


def test_pago_tarjeta_exige_destino_de_credito():
    frm = mov("a", "banco_a", -30000, "Pago a tu tarjeta de crédito Nu")
    to = mov("b", "banco_b", 30000, "deposito", tipo="debito")
    sig, _ = _transfer_signal(frm, to, {}, None)
    check("sin destino de crédito NO hay señal de pago de tarjeta", sig is None, str(sig))


def test_tokens_genericos_no_vinculan():
    """'por'/'saldo'/'estado' salían de match_hints y bastaban como señal: ya no se usan."""
    tokens = {"banco_b": {"por", "saldo", "estado"}}
    frm = mov("a", "banco_a", -12345, "compra por internet")
    to = mov("b", "banco_b", 12345, "abono saldo a favor")
    sig, _ = _transfer_signal(frm, to, tokens, None)
    check("palabras genéricas no vinculan aunque estén en los tokens", sig is None, str(sig))


def test_token_corto_explicito_si_vincula():
    tokens = {"banco_b": {"nu"}}
    frm = mov("a", "banco_a", -12345, "transferencia a nu mexico")
    to = mov("b", "banco_b", 12345, "deposito recibido")
    sig, _ = _transfer_signal(frm, to, tokens, None)
    check("una marca corta declarada explícitamente sí es señal", sig == "counterparty_token")


def test_sin_senal_no_vincula_y_marca_revision():
    a = mov("a", "banco_a", -70000, "cargo")
    b = mov("b", "banco_b", 70000, "abono")
    links = find_transfers([a, b], [], ["banco_a", "banco_b"], titular=TITULAR)
    marcado = "posible_transferencia_sin_confirmar" in a.get("_flags", [])
    check("sin señal: no vincula y deja los extremos para revisión",
          links == [] and marcado, f"links={len(links)}")


def test_no_hay_vinculos_cruzados():
    """Dos pagos del mismo importe el mismo día producían los CUATRO cruces posibles: `seen` solo
    evitaba repetir el mismo par, no reutilizar un extremo. Como el export marca
    `es_transferencia` para cualquier id que aparezca en cualquier vínculo, un cruce con un
    movimiento no relacionado lo saca del P&L."""
    movs = [
        mov("p1", "banco_a", -200000, "Pago a tu tarjeta de crédito", fecha="2026-03-10"),
        mov("p2", "banco_a", -200000, "Pago a tu tarjeta de crédito", fecha="2026-03-10"),
        mov("t1", "tdc_uno", 200000, "Gracias por tu pago", fecha="2026-03-10", tipo="credito"),
        mov("t2", "tdc_dos", 200000, "Gracias por tu pago", fecha="2026-03-10", tipo="credito"),
    ]
    links = find_transfers(movs, [], ["banco_a", "tdc_uno", "tdc_dos"])
    extremos = [x for l in links for x in (l["from"], l["to"])]
    check("importes iguales ambiguos no generan vínculos cruzados",
          len(links) == 0 and len(extremos) == len(set(extremos)), f"links={len(links)}")
    check("y los extremos ambiguos quedan marcados para revisión",
          all("posible_transferencia_sin_confirmar" in m.get("_flags", []) for m in movs))


def test_par_inequivoco_si_se_vincula():
    """El 1-a-1 no debe romper el caso normal: importes distintos, sin ambigüedad."""
    movs = [
        mov("p1", "banco_a", -200000, "Pago a tu tarjeta de crédito", fecha="2026-03-10"),
        mov("t1", "tdc_uno", 200000, "Gracias por tu pago", fecha="2026-03-10", tipo="credito"),
        mov("p2", "banco_a", -310000, "Pago a tu tarjeta de crédito", fecha="2026-03-11"),
        mov("t2", "tdc_dos", 310000, "Gracias por tu pago", fecha="2026-03-11", tipo="credito"),
    ]
    links = find_transfers(movs, [], ["banco_a", "tdc_uno", "tdc_dos"])
    check("dos pares inequívocos sí se vinculan", len(links) == 2, f"links={len(links)}")


def test_find_transfers_end_to_end():
    a = mov("a", "banco_a", -50000, "CGO Transferencia SPEI 998877")
    b = mov("b", "banco_b", 50000, "ANA MARIA PEREZ LOPEZ Transferencia SPEI", fecha="2026-03-11")
    links = find_transfers([a, b], [], ["banco_a", "banco_b"], titular=TITULAR)
    ok = len(links) == 1 and links[0]["from"] == "a" and links[0]["to"] == "b"
    check("find_transfers vincula el par con señal de titular", ok, str(links))


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    malos = [r for r in RESULTS if not r[1]]
    print()
    if malos:
        print(f"=== {len(malos)} FALLO(S) de {len(RESULTS)} ===")
        sys.exit(1)
    print(f"=== TODO VERDE === ({len(RESULTS)} checks)")
