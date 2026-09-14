# -*- coding: utf-8 -*-
"""image_ext.py — Imágenes/PDF escaneado vía OCR (tesseract). Solo si no hay texto nativo.

Endurecido (SOL-009):
- Detección de ORIENTACIÓN (image_to_osd) y rotación/deskew antes de OCR: un estado
  rotado ya no produce cero filas.
- CONFIANZA por línea (image_to_data): las líneas de baja confianza marcan el importe
  como dudoso (bloqueante) en vez de fabricar un número a partir de OCR ilegible.
"""
from __future__ import annotations
import os
from . import EXTRACTOR_VERSION
from .textparse import parse_lines

# Límites anti-DoS (SOL-014): archivo e imagen acotados; PIL además avisa de bombas.
MAX_IMAGE_BYTES = 40 * 1024 * 1024      # 40 MB
MAX_IMAGE_PIXELS = 60_000_000          # 60 MP


def _deskew(img, warnings):
    """Corrige la orientación usando la OSD de tesseract; si falla, deja la imagen igual."""
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(img)
        import re
        m = re.search(r"Rotate:\s*(\d+)", osd)
        angle = int(m.group(1)) if m else 0
        if angle % 360 != 0:
            warnings.append(f"OCR: imagen rotada {angle}° detectada; se corrigió orientación.")
            return img.rotate(-angle, expand=True)
    except Exception as e:
        warnings.append(f"OCR: no se pudo detectar orientación ({e}).")
    return img


def _lines_with_conf(img, lang):
    """Reconstruye líneas de texto con su confianza mínima usando image_to_data."""
    import pytesseract
    from pytesseract import Output
    data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT)
    groups = {}  # (block,par,line) -> {"words":[...], "confs":[...]}
    for i, word in enumerate(data["text"]):
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        g = groups.setdefault(key, {"words": [], "confs": []})
        if word.strip():
            g["words"].append(word)
            if conf >= 0:
                g["confs"].append(conf)
    lines, line_conf = [], []
    for key in sorted(groups):
        g = groups[key]
        if not g["words"]:
            continue
        lines.append(" ".join(g["words"]))
        line_conf.append(min(g["confs"]) if g["confs"] else 0.0)
    return lines, line_conf


def extract_image(path, account_hint=None, policy=None):
    pol = policy or {}
    lang = pol.get("extraction", {}).get("ocr_lang", "spa")
    min_conf = pol.get("extraction", {}).get("ocr_min_confidence", 60)
    warnings = []
    try:
        sz = os.path.getsize(path)
        if sz > MAX_IMAGE_BYTES:
            raise ValueError(f"imagen demasiado grande ({sz} bytes > {MAX_IMAGE_BYTES}); posible DoS")
        import pytesseract  # noqa: F401
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS   # guarda contra bombas de descompresión
        img = Image.open(path)
        img.load()   # fuerza la decodificación bajo el límite de píxeles
        img = _deskew(img, warnings)
        lines, line_conf = _lines_with_conf(img, lang)
        text = "\n".join(lines)
    except Exception as e:
        return {"extraction_method": "ocr", "extractor_version": EXTRACTOR_VERSION,
                "meta": {"declared": {}}, "rows": [],
                "warnings": [f"OCR no disponible o falló: {e}"]}

    if not lines:
        warnings.append("OCR: no se extrajo texto legible de la imagen (revisar calidad/escaneo).")
    else:
        avg = sum(line_conf) / len(line_conf)
        if avg < min_conf:
            warnings.append(f"OCR: confianza promedio baja ({avg:.0f}<{min_conf}); revisa el documento.")

    rows, meta, w = parse_lines(text, page=1, line_conf=line_conf, min_conf=min_conf)
    warnings.extend(w)
    warnings.append("Extracción por OCR: verifica manualmente importes y fechas dudosos.")
    if account_hint:
        meta.setdefault("account_id", account_hint)
    return {"extraction_method": "ocr", "extractor_version": EXTRACTOR_VERSION,
            "meta": meta, "rows": rows, "warnings": warnings}
