"""
===============================================================================
 OCR_IMAGENES_A_TEXTO.PY
===============================================================================
Script AUTÓNOMO basado en ocr_pdfs_vacios.py que, en lugar de aplicar OCR a
PDFs escaneados dentro de un .jsonl, aplica el MISMO OCR (Tesseract vía
pytesseract, con la misma limpieza de texto que usa el resto del corpus)
directamente sobre imágenes sueltas (JPG, PNG, TIFF, BMP, WEBP, etc.) y
guarda el resultado como texto plano (.txt), un archivo por imagen.

CÓMO FUNCIONA
-------------
    1. Recorre --input-dir buscando imágenes (recursivo si se pasa
       --recursivo).
    2. Para cada imagen:
         a. La abre con Pillow.
         b. Le pasa la imagen a Tesseract (idiomas: spa+eng por defecto).
         c. Aplica la MISMA limpieza (clean_text) que usa
            ocr_pdfs_vacios.py / procesar_pdf.py, para mantener
            consistencia con el resto del corpus.
         d. Escribe el texto limpio en --output-dir/<nombre_imagen>.txt
            (conservando la estructura de subcarpetas si --recursivo).
    3. Imprime un resumen al final (OK / vacías / errores).

Por defecto recorre TODA la carpeta "CORPUS CODEFEST AD ASTRA 2026" (recursivamente,
buscando imágenes en cualquier subcarpeta) y guarda los .txt en una carpeta
paralela "_ocr_imagenes_txt", replicando la misma estructura de subcarpetas.

CÓMO SE USA
-----------
    python ocr_imagenes_a_texto.py

    (usa los valores por defecto: --input-dir "CORPUS CODEFEST AD ASTRA 2026",
    --output-dir "_ocr_imagenes_txt", recursivo activado)

    O bien, apuntando a otra carpeta:
    python ocr_imagenes_a_texto.py --input-dir "C:\\ruta\\a\\imagenes" \\
        --output-dir "C:\\ruta\\a\\salida_txt" \\
        --tesseract "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"

    --input-dir   : carpeta a recorrer en busca de imágenes
                    (default: "CORPUS CODEFEST AD ASTRA 2026")
    --output-dir  : carpeta donde se escriben los .txt de salida
                    (default: "_ocr_imagenes_txt")
    --tesseract   : ruta al ejecutable tesseract.exe
    --idiomas     : idiomas para Tesseract (default "spa+eng")
    --no-recursivo: si se pasa, NO busca en subcarpetas (por defecto SÍ busca)
    --extensiones : lista de extensiones a procesar (default: jpg, jpeg,
                    png, tif, tiff, bmp, webp)

DEPENDENCIAS
------------
    pip install pytesseract Pillow
    + Tesseract-OCR instalado en el sistema (no es un paquete de pip)
===============================================================================
"""

import re
import argparse
import unicodedata
import traceback
from collections import Counter
from pathlib import Path

import pytesseract
from PIL import Image


# ==============================================================================
# LIMPIEZA DE TEXTO — idéntica a ocr_pdfs_vacios.py / procesar_pdf.py
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
def ocr_imagen(file_path: Path, idiomas: str) -> tuple[str, float]:
    """
    Aplica OCR a una única imagen.
    Devuelve (texto_crudo, confianza_media).
    """
    img = Image.open(file_path)

    # image_to_data nos da confianza por palabra; la usamos para calcular
    # un promedio y detectar imágenes donde el OCR falló feo.
    data = pytesseract.image_to_data(
        img, lang=idiomas, output_type=pytesseract.Output.DICT
    )
    palabras = [w for w in data["text"] if w.strip()]
    confs = [int(c) for c, w in zip(data["conf"], data["text"])
             if w.strip() and str(c).lstrip("-").isdigit() and int(c) >= 0]

    confianza_media = round(sum(confs) / len(confs), 1) if confs else 0.0
    texto_crudo = " ".join(palabras)
    return texto_crudo, confianza_media


# ==============================================================================
# PIPELINE
# ==============================================================================
def listar_imagenes(input_dir: Path, extensiones: set, recursivo: bool):
    patron = "**/*" if recursivo else "*"
    for path in sorted(input_dir.glob(patron)):
        if path.is_file() and path.suffix.lower().lstrip(".") in extensiones:
            yield path


def procesar(input_dir: str, output_dir: str, tesseract_path: str,
             idiomas: str, recursivo: bool, extensiones: set):
    if tesseract_path:
        pytesseract.pytesseract.tesseract_cmd = tesseract_path

    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    imagenes = list(listar_imagenes(input_dir, extensiones, recursivo))
    print(f"Imágenes a procesar: {len(imagenes)}")

    n_ok, n_vacio, n_error = 0, 0, 0

    for img_path in imagenes:
        rel_path = img_path.relative_to(input_dir)
        out_path = (output_dir / rel_path).with_suffix(".txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"  OCR: {rel_path}")
        try:
            texto_crudo, confianza_media = ocr_imagen(img_path, idiomas)
            texto_limpio = clean_text(texto_crudo)

            if len(texto_limpio) < 50:
                n_vacio += 1
                print(f"    -> vacío/corto ({len(texto_limpio)} caracteres)")
            else:
                n_ok += 1
                print(f"    -> OK ({len(texto_limpio)} caracteres, "
                      f"confianza media {confianza_media})")

            out_path.write_text(texto_limpio, encoding="utf-8")

        except Exception as e:
            n_error += 1
            print(f"    -> ERROR: {e}")
            traceback.print_exc()

    print(f"\nOCR aplicado con éxito (>=50 car.): {n_ok}")
    print(f"OCR aplicado pero vacío/corto     : {n_vacio}")
    print(f"Errores                           : {n_error}")
    print(f"Salida                            : {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aplica OCR a imágenes sueltas y guarda el resultado "
                    "como texto plano (CODEFEST AD ASTRA 2026)."
    )
    parser.add_argument("--input-dir", default="CORPUS CODEFEST AD ASTRA 2026",
                         help="Carpeta a recorrer en busca de imágenes "
                              '(default: "CORPUS CODEFEST AD ASTRA 2026")')
    parser.add_argument("--output-dir", default="_ocr_imagenes_txt",
                         help="Carpeta donde se escriben los .txt de salida "
                              '(default: "_ocr_imagenes_txt")')
    parser.add_argument("--tesseract", default=None,
                         help="Ruta al ejecutable de Tesseract. Si no se pasa, "
                              "se usa el que esté en el PATH del sistema "
                              "(en Windows suele ser "
                              r'"C:\Program Files\Tesseract-OCR\tesseract.exe")')
    parser.add_argument("--idiomas", default="spa+eng",
                         help='Idiomas para Tesseract (default "spa+eng")')
    parser.add_argument("--no-recursivo", action="store_true",
                         help="No buscar imágenes en subcarpetas (por defecto SÍ se buscan)")
    parser.add_argument("--extensiones", default="jpg,jpeg,png,tif,tiff,bmp,webp",
                         help="Extensiones de imagen a procesar, separadas por coma")
    args = parser.parse_args()

    extensiones = {e.strip().lower().lstrip(".") for e in args.extensiones.split(",") if e.strip()}

    procesar(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        tesseract_path=args.tesseract,
        idiomas=args.idiomas,
        recursivo=not args.no_recursivo,
        extensiones=extensiones,
    )
