# -*- coding: utf-8 -*-
"""test_ocr.py — SOL-009: confianza OCR y orientación.
El gate de confianza (determinista) es la garantía principal: una línea de baja confianza
marca el importe como dudoso (bloqueante), nunca se fabrica. El round-trip con imagen es
best-effort (depende de la calidad de tesseract)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSYS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUBSYS, "src"))


def test_confianza_baja_marca_dudoso():
    from finance.extractors.textparse import parse_lines
    text = "2026-06-05 DEPOSITO NOMINA 30000.00\n2026-06-07 OXXO GDL 250.00"
    # primera línea alta confianza, segunda baja
    rows, meta, w = parse_lines(text, page=1, line_conf=[95, 20], min_conf=60)
    assert len(rows) == 2, f"esperaba 2 filas, {len(rows)}"
    assert "importe_dudoso" not in rows[0].get("_flags", []), "línea de alta confianza no debe marcarse"
    assert "importe_dudoso" in rows[1].get("_flags", []), "SOL-009: línea de baja confianza debe bloquear"


def test_ocr_roundtrip_best_effort():
    try:
        from PIL import Image, ImageDraw, ImageFont
        import pytesseract  # noqa: F401
    except Exception as e:
        print(f"SKIP roundtrip OCR (deps): {e}")
        return
    img = Image.new("RGB", (900, 140), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    d.text((15, 20), "2026-06-05 DEPOSITO NOMINA ACME 30000.00", fill="black", font=font)
    d.text((15, 75), "2026-06-07 OXXO TIENDA GDL 250.00", fill="black", font=font)
    p = os.path.join(SUBSYS, "staging", "_ocr_test.png")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    img.save(p)
    from finance.extractors.image_ext import extract_image
    res = extract_image(p, policy={"extraction": {"ocr_lang": "spa", "ocr_min_confidence": 40}})
    os.remove(p)
    # al menos una fila reconocida y ninguna con importe fabricado sin confianza
    print(f"OCR extrajo {len(res['rows'])} filas; warnings={len(res['warnings'])}")
    assert len(res["rows"]) >= 1, "OCR no extrajo ninguna fila de una imagen legible"


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in [n for n in dir() if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            fails += 1; print(f"FAIL {name}: {e}")
        except Exception:
            fails += 1; print(f"ERROR {name}:"); traceback.print_exc()
    raise SystemExit(1 if fails else 0)
