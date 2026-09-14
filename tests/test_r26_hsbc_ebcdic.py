"""R26: PDFs de mainframe (HSBC) con texto EBCDIC en fuentes Type 3. pdfplumber entrega
'(cid:NN)' o el carácter de StandardEncoding; se invierte la tabla y se decodifica con cp037."""
from pdfminer.encodingdb import EncodingDB
from finance.extractors.pdf_ext import _decodificar_ebcdic, _paginas_ebcdic


def _rev():
    return {u: c for c, u in EncodingDB.get_encoding("StandardEncoding", None).items()}


def _ch(text, x, top=10.0, w=5.0):
    return {"text": text, "x0": x, "x1": x + w, "top": top}


def test_decodifica_cuenta_y_importe():
    # 'CUENTA' = C3 E4 C5 D5 E3 C1: C y E llegan como glifos de StandardEncoding ('ˆ', '¯'),
    # el resto como (cid:NN). El importe '$ 4,931.00' = 5B 40 F4 6B F9 F3 F1 4B F0 F0.
    chars = [_ch("ˆ", 0), _ch("(cid:228)", 5), _ch("¯", 10), _ch("(cid:213)", 15),
             _ch("(cid:227)", 20), _ch("(cid:193)", 25),
             # columna separada por hueco grande -> espacios
             _ch("(cid:91)", 80), _ch("(cid:64)", 85), _ch("(cid:244)", 90), _ch("(cid:107)", 95),
             _ch("(cid:249)", 100), _ch("(cid:243)", 105), _ch("(cid:241)", 110),
             _ch("(cid:75)", 115), _ch("(cid:240)", 120), _ch("(cid:240)", 125),
             # segunda línea
             _ch("(cid:133)", 0, top=30.0)]
    txt, desconocidos, total = _decodificar_ebcdic(chars, _rev())
    assert desconocidos == 0 and total == len(chars)
    l1, l2 = txt.split("\n")
    assert l1 == "CUENTA   $ 4,931.00"
    assert l2 == "e"


def test_glifos_no_mapeados_cuentan():
    txt, desconocidos, total = _decodificar_ebcdic([_ch("Ω", 0), _ch("(cid:193)", 5)], _rev())
    assert (desconocidos, total) == (1, 2) and txt == "?A"


def test_paginas_ebcdic_rechaza_documento_sin_importes():
    class _Page:
        chars = [_ch("(cid:193)", 0)]
        class page_obj:
            resources = {}
    class _Pdf:
        pages = [_Page()]
    assert _paginas_ebcdic(_Pdf()) is None
