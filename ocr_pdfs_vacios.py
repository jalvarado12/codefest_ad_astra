"""
===============================================================================
 OCR_PDFS_VACIOS.PY
===============================================================================
Script AUTÓNOMO y PUNTUAL para aplicar OCR a un lote específico de PDFs que
procesar_pdf.py no pudo extraer (texto vacío o < 50 caracteres, típicamente
PDFs escaneados sin capa de texto seleccionable).

NO reprocesa todo el corpus de nuevo: SOLO toca las líneas de
documentos_pdf.jsonl cuyo 'fuente' esté en la lista de archivos afectados
(--lista), dejando el resto del archivo exactamente igual.

CÓMO FUNCIONA
-------------
    1. Lee documentos_pdf.jsonl línea por línea.
    2. Para cada línea cuyo 'fuente' esté en la lista de afectados:
         a. Abre el PDF original con PyMuPDF (fitz).
         b. Renderiza cada página como imagen (300 DPI, buen balance
            calidad/velocidad para OCR).
         c. Le pasa cada imagen a Tesseract (idiomas: spa+eng).
         d. Une el texto de todas las páginas, aplica la MISMA limpieza
            (clean_text) que usa procesar_pdf.py, para mantener
            consistencia con el resto del corpus.
         e. Reemplaza 'texto' y 'num_caracteres' en esa línea, y agrega
            'meta_extra.ocr_aplicado: true' y 'meta_extra.ocr_confianza_media'
            (para poder filtrar/priorizar después si hace falta).
    3. Para las líneas NO afectadas, las copia tal cual, sin tocarlas.
    4. Escribe todo a un archivo NUEVO (--output) para no arriesgar el
       original hasta confirmar que el resultado se ve bien. Cuando estés
       conforme, tú mismo reemplazas documentos_pdf.jsonl con el nuevo.

CÓMO SE USA
-----------
    python ocr_pdfs_vacios.py --pdf-root "C:\\ruta\\al\\corpus" --input documentos_pdf.jsonl --output documentos_pdf_ocr.jsonl --lista lista_afectados.txt --tesseract "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"

    --pdf-root    : carpeta raíz del corpus (donde vive el 'fuente' relativo)
    --input       : documentos_pdf.jsonl actual (NO se modifica)
    --output      : archivo .jsonl nuevo con los 51 corregidos
    --lista       : archivo de texto plano, un 'fuente' relativo por línea
                    (las rutas que salieron en el reporte de verificación)
    --tesseract   : ruta al ejecutable tesseract.exe
    --dpi         : resolución de renderizado (default 300; subir a 400
                    si el texto sigue saliendo muy corto)
    --idiomas     : idiomas para Tesseract (default "spa+eng")

DEPENDENCIAS
------------
    pip install pytesseract Pillow
    (PyMuPDF y langdetect ya deberían estar instalados)
    + Tesseract-OCR instalado en el sistema (no es un paquete de pip)
===============================================================================
"""

import re
import json
import argparse
import unicodedata
import traceback
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io as _io

from langdetect import detect, DetectorFactory, LangDetectException
DetectorFactory.seed = 0


# ==============================================================================
# LIMPIEZA DE TEXTO — idéntica a procesar_pdf.py, para mantener consistencia
# ==============================================================================
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WEIRD_SPACES_RE = re.compile(r"[\u00a0\u200b\u200c\u200d\ufeff]")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def remove_control_chars(text: str) -> str:
    text = _WEIRD_SPACES_RE.sub(" ", text)
    return _CONTROL_CHARS_RE.sub("", text)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def collapse_whitespace(text: str) -> str:
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def remove_repeated_lines(text: str, min_repeats: int = 3, max_len: int = 80) -> str:
    lines = text.split("\n")
    counts = Counter(line.strip() for line in lines if line.strip())
    repeated = {
        line for line, n in counts.items()
        if n >= min_repeats and len(line) <= max_len
    }
    cleaned = [line for line in lines if line.strip() not in repeated]
    return "\n".join(cleaned)


def detect_language(text: str, sample_chars: int = 2000) -> str:
    sample = text[:sample_chars].strip()
    if len(sample) < 20:
        return "und"
    try:
        return detect(sample)
    except LangDetectException:
        return "und"


def clean_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = remove_control_chars(raw_text)
    text = normalize_unicode(text)
    text = remove_repeated_lines(text)
    text = collapse_whitespace(text)
    return text


# ==============================================================================
# OCR
# ==============================================================================
def ocr_pdf(file_path: Path, idiomas: str, dpi: int) -> tuple[str, dict]:
    """
    Renderiza cada página del PDF como imagen y le aplica OCR.
    Devuelve (texto_crudo, metadata_extra_ocr).
    """
    doc = fitz.open(str(file_path))
    zoom = dpi / 72  # fitz trabaja en puntos (72 dpi base)
    mat = fitz.Matrix(zoom, zoom)

    texto_por_pagina = []
    confianzas = []

    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        img = Image.open(_io.BytesIO(img_bytes))

        # image_to_data nos da confianza por palabra; la usamos para
        # calcular un promedio y detectar páginas donde el OCR falló feo.
        data = pytesseract.image_to_data(
            img, lang=idiomas, output_type=pytesseract.Output.DICT
        )
        palabras = [w for w in data["text"] if w.strip()]
        confs = [int(c) for c, w in zip(data["conf"], data["text"])
                 if w.strip() and str(c).lstrip("-").isdigit() and int(c) >= 0]
        if confs:
            confianzas.extend(confs)

        texto_pagina = " ".join(palabras)
        texto_por_pagina.append(texto_pagina)

    num_paginas = doc.page_count
    titulo_pdf = (doc.metadata or {}).get("title") or None
    doc.close()

    texto_completo = "\n\n".join(texto_por_pagina)
    confianza_media = round(sum(confianzas) / len(confianzas), 1) if confianzas else 0.0

    metadata_extra = {
        "num_paginas": num_paginas,
        "titulo_pdf": titulo_pdf,
        "ocr_aplicado": True,
        "ocr_dpi": dpi,
        "ocr_idiomas": idiomas,
        "ocr_confianza_media": confianza_media,
    }
    return texto_completo, metadata_extra


# ==============================================================================
# PIPELINE
# ==============================================================================
def cargar_lista_afectados(lista_path: str) -> set:
    afectados = set()
    with open(lista_path, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip().strip('"').replace("\\\\", "\\")
            if linea:
                afectados.add(linea)
    return afectados


def procesar(pdf_root: str, input_path: str, output_path: str, lista_path: str,
             tesseract_path: str, idiomas: str, dpi: int, log_path: str):
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    pdf_root = Path(pdf_root)
    afectados = cargar_lista_afectados(lista_path)
    print(f"Archivos a reprocesar con OCR: {len(afectados)}")

    encontrados = set()
    n_ocr_ok, n_ocr_sigue_vacio, n_no_afectados = 0, 0, 0

    with open(input_path, "r", encoding="utf-8") as in_f, \
         open(output_path, "w", encoding="utf-8") as out_f, \
         open(log_path, "w", encoding="utf-8") as log_f:

        for linea in in_f:
            linea_strip = linea.strip()
            if not linea_strip:
                continue
            doc = json.loads(linea_strip)
            fuente = doc.get("fuente", "")

            if fuente not in afectados:
                out_f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                n_no_afectados += 1
                continue

            encontrados.add(fuente)
            file_path = pdf_root / fuente
            print(f"  OCR: {fuente}")

            try:
                texto_crudo, meta_ocr = ocr_pdf(file_path, idiomas, dpi)
                texto_limpio = clean_text(texto_crudo)

                if len(texto_limpio) < 50:
                    n_ocr_sigue_vacio += 1
                    log_f.write(f"SIGUE VACÍO tras OCR ({len(texto_limpio)} "
                                f"caracteres): {fuente}\n")
                else:
                    n_ocr_ok += 1
                    log_f.write(f"OK ({len(texto_limpio)} caracteres, "
                                f"confianza media {meta_ocr['ocr_confianza_media']}): "
                                f"{fuente}\n")

                doc["texto"] = texto_limpio
                doc["num_caracteres"] = len(texto_limpio)
                doc["idioma"] = detect_language(texto_limpio)
                # Conservamos meta_extra original (num_paginas, titulo_pdf)
                # y le agregamos los campos de OCR.
                doc["meta_extra"] = {**doc.get("meta_extra", {}), **meta_ocr}

            except Exception as e:
                log_f.write(f"ERROR procesando {fuente}: {e}\n")
                log_f.write(traceback.format_exc() + "\n")
                # Dejamos el documento como estaba (con su texto vacío
                # original) en vez de perderlo del .jsonl.

            out_f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    faltantes = afectados - encontrados
    if faltantes:
        log_f_msg = (f"\nAVISO: {len(faltantes)} 'fuente' de --lista no se "
                     f"encontraron en {input_path} (revisa la lista):")
        print(log_f_msg)
        for f in faltantes:
            print(f"    - {f}")

    print(f"\nNo afectados (copiados tal cual) : {n_no_afectados}")
    print(f"OCR aplicado con éxito (>=50 car.): {n_ocr_ok}")
    print(f"OCR aplicado pero sigue vacío     : {n_ocr_sigue_vacio}")
    print(f"Salida                            : {output_path}")
    print(f"Log detallado                     : {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aplica OCR puntual a los PDFs con texto vacío/corto "
                    "(CODEFEST AD ASTRA 2026)."
    )
    parser.add_argument("--pdf-root", required=True,
                         help="Carpeta raíz del corpus (donde viven los PDF originales)")
    parser.add_argument("--input", default="documentos_pdf.jsonl",
                         help="documentos_pdf.jsonl actual (no se modifica)")
    parser.add_argument("--output", default="documentos_pdf_ocr.jsonl",
                         help="Archivo .jsonl de salida con los 51 corregidos")
    parser.add_argument("--lista", required=True,
                         help="Archivo .txt con un 'fuente' relativo por línea")
    parser.add_argument("--tesseract", default=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                         help="Ruta al ejecutable tesseract.exe")
    parser.add_argument("--idiomas", default="spa+eng",
                         help='Idiomas para Tesseract (default "spa+eng")')
    parser.add_argument("--dpi", type=int, default=300,
                         help="Resolución de renderizado (default 300)")
    parser.add_argument("--log", default="log_ocr.txt",
                         help="Archivo de log detallado por documento")
    args = parser.parse_args()

    procesar(
        pdf_root=args.pdf_root,
        input_path=args.input,
        output_path=args.output,
        lista_path=args.lista,
        tesseract_path=args.tesseract,
        idiomas=args.idiomas,
        dpi=args.dpi,
        log_path=args.log,
    )
