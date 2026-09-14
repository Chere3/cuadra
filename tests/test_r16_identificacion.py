# -*- coding: utf-8 -*-
"""
test_r16_identificacion.py — a qué cuenta pertenece un estado.

Asignar un estado a la cuenta equivocada es de los errores más caros del pipeline y NO lo atrapa
la conciliación: los saldos salen del mismo documento, así que cuadran igual. Si además las dos
cuentas tienen distinta `sign_convention` (una tarjeta es `inverted`, una cuenta no), todos los
movimientos entran encima con el signo invertido.

Caso real que motiva el test: un estado de TARJETA se asignó a la CUENTA del mismo emisor. La
cabecera empata —ambas dicen la marca— y el desempate solo miraba las líneas de movimiento, donde
la máscara de la tarjeta no aparece. Lo que discrimina vive en el CUERPO del documento.

Cuentas ficticias: el test parchea la configuración, no depende de las cuentas reales.
"""
import os, sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC_ROOT, "src"))

from finance import config as C  # noqa: E402
import finance.services as S  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


CUENTAS = [
    {"id": "banco_cuenta", "institution": "banco", "name": "Banco Cuenta", "type": "debito",
     "currency": "MXN", "mask": "1111", "match_hints": ["Cuenta Banco"]},
    {"id": "banco_tdc", "institution": "banco", "name": "Banco Tarjeta", "type": "credito",
     "currency": "MXN", "mask": "2222", "sign_convention": "inverted",
     "match_hints": ["Tarjeta de crédito Banco", "2222"]},
]


def con_cuentas_ficticias(fn):
    orig = C.accounts
    C.accounts = lambda: CUENTAS
    try:
        return fn()
    finally:
        C.accounts = orig


def test_cuerpo_del_documento_desempata():
    """La cabecera empata; la máscara de la tarjeta solo aparece en el cuerpo."""
    # Empate deliberado, como en el estado real: la marca aparece por ambos lados y ningún hint
    # discriminante domina (los hits se ponderan por nº de palabras del hint).
    cabecera = "Banco. Estado de cuenta. Cuenta Banco. Referencia 2222 folio 2222."
    cuerpo = ("Resumen de la tarjeta 2222\nLímite de la 2222\nPago a tu tarjeta 2222\n"
              "Movimientos de la tarjeta 2222\n")
    # Las descripciones de movimientos no nombran la tarjeta, y alguna sí menciona la CUENTA
    # ("Disposición de saldo en Cuenta Banco"), que es lo que inclinaba la balanza al lado malo.
    lineas_de_movimiento = ("Restaurante la Crepe\nSupermercado\n"
                            "Disposición de saldo en Cuenta Banco\n")

    def escenario():
        empate = S._top_account(S._account_hits(cabecera))
        solo_movs = S.identify_account({}, lineas_de_movimiento, header=cabecera)
        completo = S.identify_account({}, cabecera + "\n" + cuerpo + lineas_de_movimiento,
                                      header=cabecera)
        return empate, solo_movs, completo

    empate, solo_movs, completo = con_cuentas_ficticias(escenario)
    check("la cabecera sola es ambigua (no se identifica)", empate is None, str(empate))
    check("con solo las líneas de movimiento NO se acierta la tarjeta",
          solo_movs[0] != "banco_tdc", str(solo_movs))
    check("con el documento completo sí se identifica la tarjeta",
          completo[0] == "banco_tdc", str(completo))


def test_extract_pdf_expone_el_texto_completo():
    """`_process_file` necesita ese campo para desempatar; `_source_text` va truncado a 2000."""
    import inspect
    from finance.extractors import pdf_ext
    src = inspect.getsource(pdf_ext.extract_pdf)
    check("extract_pdf devuelve `_full_text`", '"_full_text": full_text' in src)
    src_serv = inspect.getsource(S._process_file)
    check("_process_file lo usa para identificar la cuenta",
          'ext.get("_full_text")' in src_serv)


def test_ambiguedad_real_no_se_resuelve_a_ciegas():
    """Si ni el documento completo desempata, NO se elige 'el primero': se bloquea."""
    texto = "Cuenta Banco. Referencia 2222 folio 2222."

    def escenario():
        return S.identify_account({}, texto, header=texto)

    acc, how = con_cuentas_ficticias(escenario)
    check("empate total -> no se identifica cuenta (bloquea)",
          acc is None and how == "ambiguous_or_none", f"{acc} / {how}")


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
