# -*- coding: utf-8 -*-
"""pdf_ext.py — PDF con texto nativo (pdfplumber). Si no hay texto, sugiere OCR.

F5-R3-02: cuando se detecta el banco, se usa una PLANTILLA (bank_templates) que maneja el layout
CARGO/ABONO/SALDO real (separa el saldo del importe y asigna el signo). Si no se detecta banco,
se cae al parser genérico de una columna (fail-safe)."""
from __future__ import annotations
import re
import calendar
import datetime as _dt
from . import EXTRACTOR_VERSION
from .textparse import parse_lines
from . import bank_templates as BT
from .. import normalize as _N


def _meta_from_text(text):
    _, meta, _ = parse_lines(text)   # reutiliza la extracción de saldos/periodo (solo meta)
    return meta


# Un PDF puede traer "texto" y aun así no decir nada: con fuentes Type 3 (glifos definidos como
# procedimientos de dibujo) o sin mapa ToUnicode, pdfplumber devuelve '(cid:NN)' o letras
# arbitrarias. El caso `sin texto` ya estaba cubierto; este es el traicionero, porque el extractor
# cree tener texto nativo y devuelve CERO movimientos en silencio. Se detecta por el contenido:
# un estado de cuenta SIEMPRE trae importes con dos decimales.
_IMPORTE_RX = re.compile(r"\d[\d,]*\.\d{2}")
_MIN_GLIFOS_PARA_SOSPECHAR = 500


def full_text_nativo(page_texts):
    return "\n".join(t for _, t in page_texts)


# Glifos sin mapa Unicode. No sirve para DECIDIR el OCR por sí solo (los estados de BBVA traen
# cientos y se leen bien), pero sí como corroboración cuando el parseo nativo no produjo NADA.
_INDICIO_TEXTO_ROTO = re.compile(r"\(cid:")


def _texto_inservible(text):
    """Se decide por CONTENIDO, no por la presencia de '(cid:'. Los estados de BBVA traen cientos
    de '(cid:NN)' —tipografías decorativas del encabezado— y aun así su tabla se lee perfecto;
    mandarlos a OCR extraía PEOR (cero filas). El criterio que sí discrimina: un estado de cuenta
    siempre trae importes con dos decimales, así que un documento largo sin uno solo no se puede
    aprovechar."""
    t = text.strip()
    if len(t) < _MIN_GLIFOS_PARA_SOSPECHAR:
        return False          # documento trivial: no vale la pena rasterizar
    return not _IMPORTE_RX.search(t)


# Cotas anti-DoS del OCR de PDF. Las de `image_ext` protegen la ruta de imágenes sueltas, pero no
# esta: aquí el bitmap lo produce `page.to_image()` (pdfium), no `Image.open()`, así que ni la
# guarda de bombas de PIL ni MAX_IMAGE_PIXELS intervienen. Y `max_rows_per_statement` se evalúa
# DESPUÉS de extraer, o sea después de haber rasterizado y OCReado el documento entero.
MAX_OCR_PAGES = 40
MAX_OCR_PIXELS = 60_000_000        # mismo techo que image_ext.MAX_IMAGE_PIXELS
MIN_OCR_DPI = 200

# Umbral para marcar una FILA como dudosa por confianza OCR. Es más bajo que el `ocr_min_confidence`
# del documento (60) a propósito: `_lines_with_conf` puntúa cada línea con el MÍNIMO de sus
# palabras, así que una sola palabra de ruido gráfico hunde la línea entera. Medido sobre un estado
# real: las cuatro líneas de movimiento puntuaban 51–69 con sus cifras verificadas correctas (la
# cadena de saldos cerraba al centavo), mientras que solo 6 de 82 líneas bajaban de 40. Con 60 se
# marcaba TODO y el banco quedaba imposible de incorporar. Calibrado sobre un documento: súbelo si
# aparece un caso donde un error real de OCR puntúe por encima.
OCR_CONF_FILA = 40
OCR_DPI_PRIMARIO = 500
OCR_DPIS_VERIFICACION = (400, 300)


_FECHA_INICIAL_RX = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2})(?=\s)")


def _firma_critica_ocr(linea):
    """Firma solo los campos contables que el OCR no puede adivinar: fecha e importes.

    Descripciones, referencias y ruido gráfico no forman parte de la firma. Se usan como máximo
    los dos últimos importes porque las tablas con saldo traen ``(movimiento, saldo)`` y antes de
    ellos puede aparecer una tasa o importe informativo en la descripción.
    """
    fecha = _FECHA_INICIAL_RX.match(linea or "")
    importes = _IMPORTE_RX.findall(linea or "")
    if not fecha or not importes:
        return None
    return fecha.group(1), tuple(importes[-2:])


def _lineas_dudosas_cruzadas(lineas, confs, lineas_verificacion, umbral):
    """Bloquea si una línea baja no se confirma, o si dos resoluciones discrepan en campos críticos.

    El mínimo de confianza de toda la línea sigue siendo una señal útil, pero una palabra comercial
    borrosa no debe convertir en dudosos fecha e importes que dos OCR independientes leen igual.
    A la inversa, una lectura primaria de alta confianza no basta si la segunda resolución discrepa:
    Tesseract puede estar seguro y equivocado en un glifo pequeño.
    """
    verificadas = {_firma_critica_ocr(ln) for ln in lineas_verificacion}
    verificadas.discard(None)
    dudosas = []
    for linea, conf in zip(lineas, confs):
        firma = _firma_critica_ocr(linea)
        if firma is None:
            if conf < umbral:
                dudosas.append(linea)
            continue
        if firma not in verificadas:
            dudosas.append(linea)
    return dudosas


class DocumentoExcedeLimite(ValueError):
    """El documento supera las cotas de OCR; se bloquea en vez de intentarlo (regla 00/anti-DoS)."""


def _ocr_paginas(pdf, policy, warnings):
    """OCR de TODAS las páginas del mismo documento (un PDF = un estado). Devuelve [(pno, texto)].

    La lectura primaria usa 500 dpi y se cruza con 400/300. Una sola resolución no basta: a 300
    puede perderse una columna, mientras que a 600 Tesseract llegó a cambiar glifos con confianza
    alta. El acuerdo de dos de las tres lecturas conserva la fila y detecta la discrepancia.

    Devuelve además las líneas cuya confianza queda por debajo del mínimo, para que el llamador
    marque como dudosas las filas que salgan de ellas. NO se descartan: `_lines_with_conf` puntúa
    cada línea con el MÍNIMO de sus palabras, así que el ruido gráfico de un estado hunde líneas
    perfectamente legibles —descartarlas costaba 3 de los 4 movimientos de un estado real— y tirar
    un movimiento para "estar seguros" es justo lo que prohíbe la regla 00. Marcar sí es correcto:
    un importe dudoso bloquea el commit y el movimiento sigue ahí.

    Hace falta porque el promedio del documento no controla nada (una línea al 30 entre cientos al
    95 lo deja en ~94) y porque la cruza-validación importe-vs-saldo, que sí atrapa un dígito mal
    leído en una cifra, NO cubre la FECHA: un '18' leído como '13' cuadra igual.
    """
    pol = (policy or {}).get("extraction", {})
    lang = pol.get("ocr_lang", "spa")
    dpi_base = int(pol.get("ocr_dpi", OCR_DPI_PRIMARIO))
    dpis_verificacion = pol.get("ocr_verify_dpis", OCR_DPIS_VERIFICACION)
    if isinstance(dpis_verificacion, (int, float, str)):
        dpis_verificacion = (int(dpis_verificacion),)
    else:
        dpis_verificacion = tuple(int(x) for x in dpis_verificacion)
    min_conf = pol.get("ocr_min_confidence", 60)
    conf_fila = pol.get("ocr_min_confidence_fila", OCR_CONF_FILA)
    max_pages = int(pol.get("ocr_max_pages", MAX_OCR_PAGES))
    max_pixels = int(pol.get("ocr_max_pixels", MAX_OCR_PIXELS))
    if len(pdf.pages) > max_pages:
        raise DocumentoExcedeLimite(
            f"PDF de {len(pdf.pages)} páginas supera el límite de {max_pages} para OCR; "
            f"divide el documento")
    from .image_ext import _lines_with_conf, _deskew
    out, confs, dudosas = [], [], []
    deskew_ok = True
    for pno, page in enumerate(pdf.pages, start=1):
        dpi = dpi_base
        px = (page.width / 72.0 * dpi) * (page.height / 72.0 * dpi)
        if px > max_pixels:
            dpi = max(MIN_OCR_DPI, int(dpi * (max_pixels / px) ** 0.5))
            warnings.append(f"p{pno}: página muy grande; OCR reducido a {dpi} dpi para no "
                            f"exceder {max_pixels} píxeles.")
            if (page.width / 72.0 * dpi) * (page.height / 72.0 * dpi) > max_pixels:
                raise DocumentoExcedeLimite(
                    f"p{pno}: la página excede {max_pixels} píxeles incluso a {MIN_OCR_DPI} dpi")
        img = page.to_image(resolution=dpi).original
        if deskew_ok:
            # La detección de orientación necesita `osd.traineddata`; si no está, avisa UNA vez
            # en lugar de una por página.
            antes = len(warnings)
            img = _deskew(img, warnings)
            deskew_ok = len(warnings) == antes
        lineas, conf = _lines_with_conf(img, lang)
        # Una segunda rasterización evita confiar en un glifo que Tesseract leyó con confianza alta
        # pero mal. 500/400 dpi resultó más estable que 600 en tablas Type 3: a 600 se alteraba un
        # día, un saldo y desaparecía una fila aunque la confianza reportada fuera alta.
        dpis_ver = [x for x in dpis_verificacion if x > 0 and x != dpi]
        if dpis_ver:
            lineas_ver = []
            for dpi_verificacion in dpis_ver:
                img_ver = page.to_image(resolution=dpi_verificacion).original
                lineas_dpi, _ = _lines_with_conf(img_ver, lang)
                lineas_ver.extend(lineas_dpi)
            dudosas.extend(_lineas_dudosas_cruzadas(
                lineas, conf, lineas_ver, conf_fila))
        else:
            dudosas.extend(ln for ln, c in zip(lineas, conf) if c < conf_fila)
        out.append((pno, "\n".join(lineas)))
        confs.extend(conf)
    if dudosas:
        warnings.append(f"OCR: {len(dudosas)} línea(s) por debajo de la confianza mínima "
                        f"({conf_fila}); las filas que salgan de ellas quedan marcadas dudosas.")
    if confs:
        avg = sum(confs) / len(confs)
        if avg < min_conf:
            warnings.append(f"OCR: confianza promedio baja ({avg:.0f}<{min_conf}); "
                            f"revisa importes y fechas del estado.")
    warnings.append("Extracción por OCR (el PDF no trae texto legible): los importes se "
                    "cruza-validan contra la cadena de saldos, pero verifica el resultado.")
    return out, dudosas


def _marcar_filas_dudosas(rows, lineas_dudosas):
    """Marca `importe_dudoso` (bloqueante) en las filas que salieron de una línea de baja confianza.

    Se emparejan por los IMPORTES, no por el texto: las plantillas reescriben el renglón (HSBC le
    antepone la fecha ISO), así que el `raw_text` de la fila ya no es idéntico a la línea OCR.
    """
    sospechosos = {tuple(_IMPORTE_RX.findall(ln)) for ln in lineas_dudosas}
    sospechosos.discard(())
    if not sospechosos:
        return 0
    tocadas = 0
    for r in rows:
        # Igualdad de la secuencia completa: un comprobante informativo con un solo importe no debe
        # contaminar un movimiento que casualmente comparte esa cifra y además trae saldo.
        if tuple(_IMPORTE_RX.findall(r.get("raw_text") or "")) in sospechosos:
            flags = r.setdefault("_flags", [])
            if "importe_dudoso" not in flags:
                flags.append("importe_dudoso")
            tocadas += 1
    return tocadas


def _fila_solapada(fila):
    """True si en la fila hay palabras de FUENTES DISTINTAS cuyas cajas se pisan en horizontal.

    Es la firma inequívoca de dos capas impresas una sobre otra: en texto normal las palabras
    nunca se solapan, por muchas fuentes que mezcle la línea. El margen de 0.5 pt absorbe el
    redondeo del extractor.
    """
    for i, a in enumerate(fila):
        for b in fila[i + 1:]:
            if b["x0"] >= a["x1"] - 0.5:
                break                      # ordenadas por x0: las siguientes tampoco solapan
            if a["fontname"] != b["fontname"]:
                return True
    return False


def _page_text(page):
    """Texto de la página, deshaciendo el TEXTO SUPERPUESTO si lo hay.

    R12-C-001: algunos estados llevan el nombre del titular impreso como marca de agua sobre la
    tabla de movimientos. `extract_text()` aplana ambas capas entrelazándolas carácter a carácter
    y la línea deja de casar con cualquier patrón: el movimiento se pierde EN SILENCIO y la
    conciliación no cuadra. Ejemplo FICTICIO de la misma forma — movimiento '05 FEB Tienda Ejemplo'
    bajo la marca 'NOMBRE APELLIDO' produce '0N5O MFBERBE  TAiPeEnLdLaI DEOjemplo'.

    R12-E-001: la primera versión decidía por NÚMERO DE CAMBIOS DE FUENTE y conservaba la capa de
    la primera palabra. Ambas cosas estaban mal y destruían datos:
      - una tabla legítima que alterna fuentes (fecha en negrita, descripción regular, importe en
        negrita, saldo regular) supera el umbral y perdía descripción Y saldo corrido, sin que la
        conciliación lo notara porque los importes sobrevivían;
      - si la marca de agua empezaba más a la IZQUIERDA que el movimiento, se conservaba la marca
        y se borraba el movimiento entero — justo el fallo que pretendía arreglar.
    Ahora la superposición se detecta por SOLAPAMIENTO GEOMÉTRICO (imposible en texto normal) y se
    conserva la fuente DOMINANTE DE LA PÁGINA —el cuerpo del documento— en vez de la que abra la
    línea. Lo descartado se informa: la pérdida deja de ser silenciosa.

    Devuelve (texto, warnings). Si la página no trae superposición, el texto es el de
    `extract_text()` sin tocar, para no alterar lo que ya consumen los demás bancos.
    """
    try:
        words = page.extract_words(extra_attrs=["fontname"])
    except Exception:
        return (page.extract_text() or ""), []
    if not all("fontname" in w for w in words):
        return (page.extract_text() or ""), []
    filas = {}
    for w in words:
        filas.setdefault(round(w["top"] / 3), []).append(w)
    ordenadas = [sorted(filas[k], key=lambda x: x["x0"]) for k in sorted(filas)]
    afectadas = [f for f in ordenadas if _fila_solapada(f)]
    if not afectadas:
        return (page.extract_text() or ""), []
    # capa del cuerpo = fuente con más palabras en la página; la marca de agua es siempre
    # minoritaria frente al texto real del estado.
    conteo = {}
    for w in words:
        conteo[w["fontname"]] = conteo.get(w["fontname"], 0) + 1
    dominante = max(conteo, key=lambda k: conteo[k])
    out, descartadas = [], 0
    for fila in ordenadas:
        if _fila_solapada(fila):
            limpia = [w for w in fila if w["fontname"] == dominante]
            descartadas += len(fila) - len(limpia)
            fila = limpia
        out.append(" ".join(w["text"] for w in fila))
    warns = []
    if descartadas:
        warns.append(f"texto superpuesto en la página: se descartaron {descartadas} palabra(s) "
                     f"de una capa ajena al cuerpo del documento (marca de agua) en "
                     f"{len(afectadas)} línea(s)")
    return "\n".join(out), warns


# admite fechas 'DD/MMM/YYYY' y también 'DD MMM YYYY' con espacios (Nu: 'Periodo: 29 MAY 2026
# al 27 JUN 2026'); el ':' opcional cubre el rotulado de Nu.
_PER_DATE = r"(\d{1,2}[/\-.]\w{1,4}[/\-.]\d{2,4}|\d{1,2}\s+[A-Za-zÁÉÍÓÚáéíóú]{3,}\.?\s+\d{4})"
# El ancla puede ser 'Periodo' o el propio encabezado del documento: Vexi rotula
# 'Estado de cuenta del 30/12/2025 al 03/01/2026', sin la palabra 'periodo'. Se exige una de las
# dos anclas y DOS fechas completas, para no confundir un 'del … al …' de la letra pequeña.
_PERIODO = re.compile(
    rf"(?:per[ií]odo|estado\s+de\s+cuenta):?\s+(?:del\s+)?{_PER_DATE}\s+(?:al|[-–—])\s+"
    rf"{_PER_DATE}",
    re.I)
# Variante en la que la fecha INICIAL es solo el día y el mes/año viven en la final
# ('Periodo: del 01 al 31 ene 2026', cuenta Nu). El mes/año se toman de la fecha final —
# no se infieren de otra parte del documento.
_PERIODO_DIA = re.compile(
    r"per[ií]odo:?\s+del\s+(\d{1,2})\s+al\s+"
    r"(\d{1,2})\s+([A-Za-zÁÉÍÓÚáéíóú]{3,}\.?)\s+(\d{4})",
    re.I)
# Variante sin fechas: el estado nombra el MES y cuántos días abarca ('Periodo: Enero 2026 …
# Núm de días del periodo 31', Ualá). Se exige el número de días Y que coincida con los del mes
# nombrado: es la prueba de que el periodo ES el mes natural. Si el emisor pasara a un corte a
# mitad de mes, el número dejaría de cuadrar y no se deriva nada, en vez de inventar un periodo.
_PERIODO_MES = re.compile(
    r"per[ií]odo:?\s+([A-Za-zÁÉÍÓÚáéíóú]{3,}\.?)\s+(\d{4})[^\n]{0,40}?"
    r"d[ií]as\s+del\s+per[ií]odo\s+(\d{1,2})",
    re.I)
# Última variante: el estado no imprime el rango pero SÍ la fecha de corte y cuántos días abarca,
# que juntos lo determinan. Cubre dos casos reales que las anteriores no atrapan:
#   · Ualá cuenta los días de forma INCLUSIVA (dice 29 para un febrero de 28), así que
#     `_PERIODO_MES` descarta el mes natural por descuadre — y hace bien, porque el corte de Ualá
#     no es fin de mes: cae el 28, el 30, el 30…
#   · Nu, en el layout a dos columnas de may–jul, imprime "Periodo:" y el rango en líneas
#     distintas (el rango queda pegado al domicilio), de modo que `_PERIODO` no lo ve.
# Ambos datos son IMPRESOS por el emisor; el inicio se calcula, no se adivina.
_CORTE = re.compile(
    r"fecha\s+de\s+corte:?\s+(\d{1,2}\s+[A-Za-zÁÉÍÓÚáéíóú]{3,}\.?\s+\d{4})", re.I)
_NDIAS = re.compile(
    r"n[uú]m(?:ero)?\.?\s+de\s+d[ií]as\s+(?:del|en\s+el)\s+per[ií]odo:?\s*(\d{1,3})", re.I)


def _periodo_por_corte(text):
    """(corte, nº de días) -> (inicio, fin). El corte ES el fin; el inicio se cuenta hacia atrás
    de forma inclusiva, que es como lo rotulan los dos emisores donde esto aplica (comprobado
    contra los cuatro estados que sí traen el rango explícito: cuadra en los cuatro).

    Se acota el número de días a un ciclo de tarjeta plausible: un valor fuera de rango significa
    que el rótulo casó con otra cosa, y entonces NO se deriva periodo en vez de inventar uno."""
    mc, mn = _CORTE.search(text or ""), _NDIAS.search(text or "")
    if not (mc and mn):
        return None, None
    ndias = int(mn.group(1))
    if not 20 <= ndias <= 40:
        return None, None
    try:
        fin = _dt.date.fromisoformat(_N.normalize_date(mc.group(1)))
    except (_N.DateParseError, ValueError):
        return None, None
    return (fin - _dt.timedelta(days=ndias - 1)).isoformat(), fin.isoformat()


def _extract_period(text):
    """Periodo 'del X al Y' -> (period_start_iso, period_end_iso). El año da el year_hint para
    fechas DD/MMM sin año en las líneas de movimiento."""
    m = _PERIODO.search(text or "")
    if m:
        y = re.search(r"\d{4}", m.group(2))
        yh = int(y.group()) if y else None
        try:
            return (_N.normalize_date(m.group(1), year_hint=yh),
                    _N.normalize_date(m.group(2), year_hint=yh))
        except _N.DateParseError:
            return None, None
    m = _PERIODO_DIA.search(text or "")
    if m:
        d_ini, d_fin, mes, anio = m.groups()
        try:
            return (_N.normalize_date(f"{d_ini} {mes} {anio}", year_hint=int(anio)),
                    _N.normalize_date(f"{d_fin} {mes} {anio}", year_hint=int(anio)))
        except _N.DateParseError:
            return None, None
    ps, pe = _periodo_por_corte(text)
    if ps:
        return ps, pe
    m = _PERIODO_MES.search(text or "")
    if not m:
        return None, None
    mes, anio, ndias = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        ini = _dt.date.fromisoformat(_N.normalize_date(f"01 {mes} {anio}", year_hint=anio))
    except (_N.DateParseError, ValueError):
        return None, None
    ultimo = calendar.monthrange(ini.year, ini.month)[1]
    if ndias != ultimo:
        return None, None      # no abarca el mes natural: no se deduce un periodo
    return ini.isoformat(), ini.replace(day=ultimo).isoformat()

# ---------------------------------------------------------------------------------------------
# R26: estados generados en mainframe (HSBC, productor "Xenos D2eVision") llevan el texto en
# EBCDIC dentro de fuentes Type 3 sin mapa Unicode. pdfplumber devuelve cada glifo como
# '(cid:NN)' —NN es el byte EBCDIC— o, para los códigos que StandardEncoding sí nombra, como el
# carácter de esa tabla ('ˆ' por 0xC3, que en EBCDIC es 'C'). Se invierte la tabla de la fuente
# para recuperar el byte y se decodifica con cp037. Sin esto el documento se declaraba inservible
# y se mandaba a OCR (que además exige tesseract). pdftotext NO sirve de alternativa: descarta
# los bytes 0x80–0x9F (controles C1 en Latin-1) y con ellos las minúsculas a–i y j–r.
# Se aplica SOLO cuando el texto nativo es inservible y el decodificado sí trae importes; si más
# del 5 % de los glifos no se pueden mapear, no se usa (fail-safe: mejor OCR o revisión que texto
# a medias).
_CID_RX = re.compile(r"^\(cid:(\d+)\)$")
_EBCDIC_CODEC = "cp037"
_EBCDIC_MAX_DESCONOCIDOS = 0.05


def _mapa_inverso_fuentes(page):
    """{carácter Unicode -> código de fuente} unificado para todas las fuentes de la página.
    pdfplumber rotula la fuente de cada glifo como 'unknown' en estos PDFs, así que no se puede
    mapear por fuente; todas comparten StandardEncoding y el mapa unificado no colisiona."""
    from pdfminer.pdftypes import resolve1
    from pdfminer.encodingdb import EncodingDB
    from pdfminer.psparser import literal_name
    rev = {}
    try:
        res = resolve1(page.page_obj.resources) or {}
        fonts = resolve1(res.get("Font")) or {}
    except Exception:
        return rev
    for ref in fonts.values():
        try:
            fd = resolve1(ref)
            enc = resolve1(fd.get("Encoding"))
            base, diffs = "StandardEncoding", None
            if isinstance(enc, dict):
                b = enc.get("BaseEncoding")
                base = literal_name(b) if b else base
                diffs = resolve1(enc.get("Differences"))
            elif enc is not None:
                base = literal_name(enc)
            for code, u in EncodingDB.get_encoding(base, diffs).items():
                rev.setdefault(u, code)
        except Exception:
            continue
    return rev


def _decodificar_ebcdic(chars, rev):
    """(texto con maqueta por posición, glifos no mapeados, glifos totales)."""
    filas, desconocidos, total = {}, 0, 0
    for c in chars:
        t = c["text"]
        total += 1
        m = _CID_RX.match(t)
        b = int(m.group(1)) if m else rev.get(t)
        if b is None or b > 255:
            desconocidos += 1
            ch = "?"
        else:
            ch = bytes([b]).decode(_EBCDIC_CODEC, errors="replace")
        filas.setdefault(round(c["top"] / 2), []).append((c["x0"], c["x1"], ch))
    lineas = []
    for k in sorted(filas):
        s, prev = "", None
        for x0, x1, ch in sorted(filas[k]):
            if prev is not None:
                gap, w = x0 - prev, max(x1 - x0, 1.0)
                # Hueco de columna -> espacios, con tope de 3: las expresiones genéricas de
                # saldo inicial/final admiten pocos caracteres entre rótulo e importe
                # ('Saldo Inicial del' … '$ 0.00' van en el mismo renglón separados por medio
                # ancho de página) y el parser de HSBC trabaja por tokens, no por posición.
                if gap > w * 0.5:
                    s += " " * min(3, max(1, int(gap / w)))
            s += ch
            prev = x1
        lineas.append(s.rstrip())
    return "\n".join(lineas), desconocidos, total


def _paginas_ebcdic(pdf):
    """page_texts decodificados, o None si el documento no es EBCDIC aprovechable."""
    out, desconocidos, total = [], 0, 0
    for pno, page in enumerate(pdf.pages, start=1):
        try:
            txt, d, n = _decodificar_ebcdic(page.chars, _mapa_inverso_fuentes(page))
        except Exception:
            return None
        out.append((pno, txt))
        desconocidos += d
        total += n
    full = full_text_nativo(out)
    if not total or desconocidos > total * _EBCDIC_MAX_DESCONOCIDOS or not _IMPORTE_RX.search(full):
        return None
    return out


def extract_pdf(path, account_hint=None, policy=None, _forzar_ocr=False):
    import pdfplumber
    rows, warnings = [], []
    meta = {"declared": {}}
    native_text_found = False
    ocr_usado = False
    ebcdic_usado = False
    ocr_dudosas = []
    page_texts = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            txt, _w = _page_text(page)
            warnings.extend(f"p{pno}: {x}" for x in _w)
            if txt.strip():
                native_text_found = True
            page_texts.append((pno, txt))
        # Sobre el documento COMPLETO, no página a página: los estados traen páginas enteras de
        # avisos legales y publicidad sin una sola cifra, y evaluarlas por separado mandaba a OCR
        # documentos que se leían perfectamente (costó 42 de 52 movimientos en una prueba real).
        # El caso mixto —resumen legible, tabla ilegible— lo cubre el reintento del final.
        if _forzar_ocr or _texto_inservible(full_text_nativo(page_texts)):
            decodificado = None if _forzar_ocr else _paginas_ebcdic(pdf)
            if decodificado is not None:
                page_texts = decodificado
                native_text_found, ebcdic_usado = True, True
                warnings.append("texto nativo en EBCDIC (fuentes Type 3 sin mapa Unicode): "
                                "decodificado con cp037, sin OCR")
        if (_forzar_ocr or _texto_inservible(full_text_nativo(page_texts))) and not ebcdic_usado:
            try:
                page_texts, ocr_dudosas = _ocr_paginas(pdf, policy, warnings)
                native_text_found, ocr_usado = True, True
            except DocumentoExcedeLimite as e:
                # No se degrada a "cero filas en silencio": se marca para que services bloquee.
                warnings.append(f"documento_excede_limite: {e}")
                raise
            except Exception as e:
                warnings.append(f"PDF sin texto legible (fuentes sin mapa Unicode) y el OCR de "
                                f"respaldo falló: {e}")
    full_text = "\n".join(t for _, t in page_texts)
    meta["_source_text"] = full_text[:2000]   # F5-R3-02: cabecera para identificar la cuenta/banco
    for k, v in _meta_from_text(full_text).items():
        if k != "declared":
            meta.setdefault(k, v)
    ps, pe = _extract_period(full_text)        # periodo -> year_hint para fechas DD/MMM sin año
    if ps:
        meta.setdefault("period_start", ps)
    if pe:
        meta.setdefault("period_end", pe)

    bank = BT.detect_bank(full_text, account_hint) if native_text_found else None
    if bank:
        # PLANTILLA por banco: se parsea el texto completo para hilar el saldo corriente continuo.
        # SOL-009: crédito revolvente — los saldos del periodo viven en el resumen del emisor,
        # no en las líneas; sin ellos la conciliación quedaba en REVIEW_REQUIRED para siempre.
        # La plantilla CALIBRADA del banco tiene prioridad sobre la heurística genérica.
        _cs = BT.credit_summary(full_text, bank)
        for k, v in _cs.items():
            meta[k] = v
        # Totales y conteo declarados por el emisor: alimentan el control cruzado de la regla 25.
        # `_meta_from_text` descarta la clave 'declared' de la heurística genérica, así que para un
        # PDF esto quedaba SIEMPRE vacío y la comprobación de cobertura se saltaba en silencio.
        meta["declared"].update(BT.declared_totals(full_text, bank))
        r, _m, w = BT.parse_bank(full_text, bank, page=None, opening=meta.get("opening_raw"),
                                 period_start=meta.get("period_start"),
                                 period_end=meta.get("period_end"),
                                 closing=meta.get("closing_raw"))
        rows.extend(r)
        warnings.extend(w)
        # Conceptos del resumen que mueven el saldo sin figurar como movimiento (rendimientos de
        # la cuenta Nu). Se fechan al CIERRE del periodo; SIN periodo no se incorporan y se avisa
        # —antes que inventarles una fecha— para que el estado quede en revisión, no descuadrado
        # en silencio.
        # R12-E-008: `SUMMARY_EXTRAS` está indexado por BANCO y ancla solo por rótulo, sin mirar
        # de qué producto es el estado. Los rendimientos son de la CUENTA; si la frase apareciera
        # en un estado de tarjeta del mismo emisor se fabricaba un movimiento —y, por la convención
        # de signo invertido del crédito, con el signo al revés—. Si el estado trae el resumen de
        # crédito revolvente, no es una cuenta: no se le añaden extras de cuenta.
        extras = [] if _cs else BT.summary_extras(full_text, bank)
        pe_iso = meta.get("period_end")
        for k, ex in enumerate(extras, start=1):
            # R12-E-009: si el concepto YA figura como movimiento del cuerpo, añadirlo lo
            # duplicaría. Se omite y se avisa, en vez de fiarlo todo a que la conciliación falle.
            _et = ex["label"].lower()
            if any(_et in (r.get("raw_text") or "").lower() for r in rows):
                warnings.append(f"'{ex['label']}' aparece como movimiento Y en el resumen: "
                                f"no se incorpora del resumen para no duplicarlo")
                continue
            if not pe_iso:
                warnings.append(
                    f"'{ex['label']}' viene declarado en el resumen pero el estado no trae periodo "
                    f"para fecharlo: NO se incorpora (la conciliación quedará corta por ese importe)")
                continue
            rows.append({
                "locator": f"resumen:{k}", "raw_text": ex["raw_text"],
                "page": None, "row_idx": len(rows) + 1,
                "date_op": pe_iso, "description": ex["label"],
                "amount": f"{ex['amount']:.2f}", "_flags": []})
        method = (f"pdf_ocr_template:{bank}" if ocr_usado
                  else f"pdf_ebcdic_template:{bank}" if ebcdic_usado
                  else f"pdf_template:{bank}")
    else:
        for pno, txt in page_texts:
            r, m, w = parse_lines(txt, page=pno)
            rows.extend(r)
            warnings.extend(w)
            for k, v in m.items():
                if k != "declared":
                    meta.setdefault(k, v)
        method = "pdf_text" if native_text_found else "pdf_no_text"

    if ocr_usado and ocr_dudosas:
        n = _marcar_filas_dudosas(rows, ocr_dudosas)
        if n:
            warnings.append(f"OCR: {n} fila(s) provienen de líneas de baja confianza y quedan "
                            f"marcadas como importe dudoso (bloquean el commit).")
    # Caso mixto: encabezado y resumen en tipografía normal (así que el documento SÍ trae importes
    # y no se declaró inservible) pero la TABLA en fuentes sin mapa Unicode. Se detecta por el
    # síntoma, no por adivinar a priori: el parseo nativo no produjo un solo movimiento y el texto
    # trae glifos rotos. Solo entonces se paga el OCR, así que no hay falsos positivos.
    if (not rows and not ocr_usado and not _forzar_ocr
            and _INDICIO_TEXTO_ROTO.search(full_text)):
        reintento = extract_pdf(path, account_hint, policy, _forzar_ocr=True)
        if reintento["rows"]:
            reintento["warnings"].insert(
                0, "el texto nativo no produjo ningún movimiento y trae glifos sin mapa Unicode: "
                   "se reintentó por OCR")
            return reintento
    if not native_text_found:
        warnings.append("PDF sin texto nativo: requiere OCR (usar ruta de imagen/escaneo).")
    if account_hint:
        meta.setdefault("account_id", account_hint)
    return {"extraction_method": method,
            "extractor_version": EXTRACTOR_VERSION, "meta": meta, "rows": rows,
            # Texto completo SOLO para identificar la cuenta (no se persiste). `_source_text` son
            # los primeros 2000 caracteres y las líneas de movimiento no bastan: lo que distingue
            # una tarjeta de la cuenta del mismo emisor —su máscara— vive en el cuerpo del
            # documento, fuera de ambos.
            "_full_text": full_text,
            "warnings": warnings}
