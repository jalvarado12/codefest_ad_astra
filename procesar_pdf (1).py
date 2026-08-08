"""
===============================================================================
 PROCESAR_PDF.PY
===============================================================================
Script AUTÓNOMO que hace TODO el proceso para archivos PDF, de principio a fin:

    1. Busca todos los .pdf dentro de una carpeta (y sus subcarpetas,
       sin importar cuántos niveles tenga).
    2. Extrae el texto de cada PDF.
    3. Lo limpia (quita basura, normaliza, detecta boilerplate repetido).
    4. Detecta el idioma predominante (es/en/pt).
    5. Le asigna un doc_id único e inmutable.
    6. Escribe todo a un archivo .jsonl (un documento por línea).

Es "autónomo" a propósito: no importa nada de otros scripts. Todo lo que
necesita está en este único archivo, para que lo puedas correr o pasarle
a un compañero sin depender de más piezas. Por eso vas a ver funciones
muy parecidas repetidas en procesar_excel.py, procesar_csv.py y
procesar_texto.py: es duplicación intencional, no un error.

CÓMO SE USA
-----------
    python3 procesar_pdf.py --input data_raw --output documentos_pdf.jsonl

    --input     : carpeta raíz donde están tus PDFs (busca en subcarpetas
                  también, sin importar cuántos niveles de anidamiento).
    --output    : archivo .jsonl de salida (default: documentos_pdf.jsonl)
    --errors    : archivo .jsonl donde quedan los PDFs que fallaron
                  (default: errores_pdf.jsonl)
    --registry  : archivo donde se guardan los doc_id ya asignados
                  (default: doc_registry.json). IMPORTANTE: si vas a
                  correr también procesar_excel.py / procesar_csv.py /
                  procesar_texto.py sobre el MISMO corpus, déjalos todos
                  apuntando al mismo --registry (el nombre por defecto ya
                  es el mismo en los 4 scripts) para que ningún doc_id se
                  repita entre formatos.
    --resume    : si la corrida se corta a mitad de camino, corre de
                  nuevo con esta bandera y no repite el trabajo ya hecho.

DEPENDENCIAS (instalar con pip)
--------------------------------
    pip install PyMuPDF langdetect --break-system-packages
===============================================================================
"""

# ==============================================================================
# SECCIÓN 1: IMPORTS
# ==============================================================================
# fitz es el nombre interno de la librería PyMuPDF. Se usa para abrir y leer
# el contenido de los PDF.
import fitz

# re: expresiones regulares, las usamos para limpiar texto y para detectar
# el número de "fenómeno" en el nombre de las carpetas.
import re

# unicodedata: para normalizar acentos y caracteres especiales a una forma
# estándar (ver la función normalize_unicode más abajo).
import unicodedata

# Counter: para contar cuántas veces se repite cada línea de texto (así
# detectamos encabezados/pies de página que se repiten en cada hoja).
from collections import Counter

# Path: manejo de rutas de archivos de forma segura, sin pelear con
# separadores de carpeta distintos entre sistemas operativos.
from pathlib import Path

# json: para escribir cada documento como una línea JSON válida (.jsonl),
# y para leer/escribir el registro de doc_id.
import json

# argparse: para poder correr el script desde la terminal con opciones
# como --input, --output, etc.
import argparse

# time: solo para mostrar cuánto tiempo lleva corriendo el proceso.
import time

# traceback: para guardar el detalle completo de un error cuando un PDF
# falla, y así poder diagnosticarlo sin tener que repetir la corrida.
import traceback

# langdetect: detecta el idioma predominante de un texto (es/en/pt/...).
from langdetect import detect, DetectorFactory, LangDetectException

# Fijamos una semilla fija para que la detección de idioma sea siempre
# igual entre corridas (por defecto langdetect usa un muestreo interno
# que puede variar ligeramente cada vez que se llama).
DetectorFactory.seed = 0


# ==============================================================================
# SECCIÓN 2: LIMPIEZA Y NORMALIZACIÓN DE TEXTO
# ==============================================================================
# Estas funciones reciben texto CRUDO (recién extraído del PDF) y devuelven
# texto limpio, listo para guardarse. Ninguna de ellas fragmenta el texto en
# pedazos (chunking): eso es trabajo de otra etapa del proyecto, no de este
# script.

# Caracteres de control invisibles que a veces quedan al extraer texto de un
# PDF (saltos de página \x0c, caracteres nulos, etc.). No aportan significado
# y pueden confundir a los pasos siguientes del pipeline.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Espacios "raros" que no son el espacio normal (nbsp, espacios de ancho cero,
# etc.), comunes en PDFs generados desde Word o InDesign.
_WEIRD_SPACES_RE = re.compile(r"[\u00a0\u200b\u200c\u200d\ufeff]")

# 3 o más saltos de línea seguidos se colapsan a 2 (deja como máximo un
# párrafo en blanco entre bloques de texto).
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

# 2 o más espacios/tabs seguidos se colapsan a 1 solo espacio.
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def remove_control_chars(text: str) -> str:
    """Quita caracteres de control y espacios raros del texto."""
    text = _WEIRD_SPACES_RE.sub(" ", text)
    return _CONTROL_CHARS_RE.sub("", text)


def normalize_unicode(text: str) -> str:
    """
    Normaliza el texto a la forma Unicode NFC.

    Por qué importa: algunos PDFs codifican una letra acentuada como DOS
    caracteres (la letra + un acento "combinante" aparte), en vez de UNO
    solo. Visualmente se ven idénticos ("é" y "é"), pero para el
    computador son cadenas de texto DIFERENTES. NFC las une en un solo
    caracter, para que comparaciones y búsquedas de texto funcionen bien
    más adelante.
    """
    return unicodedata.normalize("NFC", text)


def collapse_whitespace(text: str) -> str:
    """Colapsa espacios y saltos de línea redundantes, y recorta bordes."""
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    # Quita espacios sueltos al inicio/fin de cada línea individual
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def remove_repeated_lines(text: str, min_repeats: int = 3, max_len: int = 80) -> str:
    """
    Elimina líneas CORTAS que se repiten muchas veces en el documento.

    Esto sirve para quitar encabezados y pies de página que un PDF repite
    en cada hoja (por ejemplo, un título de reporte que aparece arriba de
    cada página, o la numeración "Página 3 de 40"). Solo se descartan
    líneas de máximo `max_len` caracteres que aparecen `min_repeats` veces
    o más, así que el riesgo de borrar contenido real (que normalmente es
    largo y no se repite igual) es bajo.
    """
    lines = text.split("\n")
    counts = Counter(line.strip() for line in lines if line.strip())
    repeated = {
        line for line, n in counts.items()
        if n >= min_repeats and len(line) <= max_len
    }
    cleaned = [line for line in lines if line.strip() not in repeated]
    return "\n".join(cleaned)


def detect_language(text: str, sample_chars: int = 2000) -> str:
    """
    Detecta el idioma predominante del texto (es, en, pt, ...).
    Solo usa los primeros `sample_chars` caracteres para no gastar tiempo
    de más en documentos largos. Si el texto es muy corto o ambiguo,
    devuelve 'und' (undetermined) en vez de adivinar.
    """
    sample = text[:sample_chars].strip()
    if len(sample) < 20:
        return "und"
    try:
        return detect(sample)
    except LangDetectException:
        return "und"


def clean_text(raw_text: str) -> str:
    """
    Pipeline completo de limpieza: aplica, EN ESTE ORDEN:
      1) quitar caracteres de control
      2) normalizar unicode
      3) quitar líneas repetidas (boilerplate)
      4) colapsar espacios/saltos de línea
    El orden importa: por ejemplo, hay que quitar el boilerplate ANTES de
    colapsar espacios, porque el boilerplate se detecta comparando líneas
    completas.
    """
    if not raw_text:
        return ""
    text = remove_control_chars(raw_text)
    text = normalize_unicode(text)
    text = remove_repeated_lines(text)
    text = collapse_whitespace(text)
    return text


# ==============================================================================
# SECCIÓN 3: EXTRACCIÓN DE TEXTO DEL PDF
# ==============================================================================
def extraer_texto_pdf(file_path: Path) -> tuple[str, dict]:
    """
    Abre un PDF y devuelve (texto_crudo, metadata_extra).

    Cómo funciona:
      - fitz.open() abre el archivo.
      - Recorremos cada página con `for page in doc`.
      - page.get_text("blocks") devuelve el texto dividido en "bloques"
        (párrafos, celdas de tabla, etc.), cada uno con su posición
        (x0, y0, x1, y1) en la hoja.
      - Ordenamos esos bloques por posición vertical y luego horizontal
        (round(y0), round(x0)) para reconstruir el orden de lectura
        normal (de arriba hacia abajo, de izquierda a derecha). Esto es
        importante en PDFs con columnas o tablas, donde el orden interno
        del archivo no siempre coincide con el orden visual.
      - Unimos el texto de todas las páginas con una línea en blanco
        entre cada una, para que quede claro dónde termina una página y
        empieza la siguiente (luego collapse_whitespace lo deja limpio).

    Nota: las imágenes dentro del PDF se ignoran (no hacemos OCR aquí);
    si tu corpus tiene infografías con texto relevante dentro de
    imágenes, eso se procesaría con otro script (fuera del alcance
    actual: PDF, Excel, CSV, texto).
    """
    doc = fitz.open(str(file_path))
    texto_por_pagina = []

    for page in doc:
        bloques = page.get_text("blocks")
        # Cada bloque es una tupla: (x0, y0, x1, y1, texto, num_bloque, tipo)
        bloques.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
        texto_pagina = "\n".join(b[4].strip() for b in bloques if b[4].strip())
        texto_por_pagina.append(texto_pagina)

    metadata_extra = {
        "num_paginas": doc.page_count,
        "titulo_pdf": (doc.metadata or {}).get("title") or None,
    }
    doc.close()

    texto_completo = "\n\n".join(texto_por_pagina)
    return texto_completo, metadata_extra


# ==============================================================================
# SECCIÓN 4: ASIGNACIÓN DE doc_id (registro persistente)
# ==============================================================================
class RegistroDocumentos:
    """
    Asigna un doc_id único (DOC-0001, DOC-0002, ...) a cada archivo, y lo
    recuerda entre corridas guardándolo en un archivo JSON en disco.

    Por qué es importante que sea PERSISTENTE y no un simple contador en
    memoria: vas a correr este script muchas veces mientras ajustas cosas.
    Si el doc_id se recalculara desde cero cada vez, el mismo PDF podría
    terminar con un doc_id distinto en cada corrida, y se perdería la
    trazabilidad que exige el reto (cada doc_id debe ser único E
    INMUTABLE).

    Además, si vas a correr procesar_excel.py / procesar_csv.py /
    procesar_texto.py sobre el mismo corpus, todos deben usar el MISMO
    archivo de registro (--registry) para que un DOC-0007 de Excel no
    choque con un DOC-0007 de PDF.
    """

    def __init__(self, registry_path: str = "doc_registry.json"):
        self.registry_path = Path(registry_path)
        if self.registry_path.exists():
            with open(self.registry_path, "r", encoding="utf-8") as f:
                self._mapa = json.load(f)
        else:
            self._mapa = {}
        self._siguiente_num = self._calcular_siguiente_num()

    def _calcular_siguiente_num(self) -> int:
        """Mira los doc_id ya asignados (de CUALQUIER formato, si el
        registro es compartido) y calcula cuál es el próximo número libre,
        para no repetir ninguno."""
        numeros = [int(v.split("-")[1]) for v in self._mapa.values()]
        return max(numeros, default=0) + 1

    def obtener_o_crear(self, ruta_relativa: str) -> str:
        """
        Si `ruta_relativa` ya tiene un doc_id asignado de una corrida
        anterior, lo devuelve tal cual. Si es nueva, le asigna el
        siguiente número disponible.
        """
        clave = str(ruta_relativa)
        if clave in self._mapa:
            return self._mapa[clave]
        doc_id = f"DOC-{self._siguiente_num:04d}"
        self._mapa[clave] = doc_id
        self._siguiente_num += 1
        return doc_id

    def guardar(self):
        """Escribe el registro actualizado a disco."""
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(self._mapa, f, ensure_ascii=False, indent=2)


# ==============================================================================
# SECCIÓN 5: INFERENCIA DEL FENÓMENO (1, 2 o 3) A PARTIR DE LA RUTA
# ==============================================================================
# Busca un segmento como "fenomeno_1", "Fenómeno 2", "fenomeno-3" en
# CUALQUIER parte de la ruta del archivo, sin importar en qué nivel de
# carpetas esté ni cómo esté escrito en mayúsculas/minúsculas.
#
# AJUSTA ESTE PATRÓN si tu corpus real usa otra convención de nombres de
# carpeta (por ejemplo "F1", "tema_2", "reto-3"). Es el único lugar del
# script que depende de cómo ADL organizó las carpetas.
FENOMENO_PATTERN = re.compile(r"fen[oó]meno[\s_-]*([123])", re.IGNORECASE)


def inferir_fenomeno(ruta_relativa: Path) -> int | None:
    """
    Recorre cada segmento de la ruta (cada nombre de carpeta) buscando el
    patrón de fenómeno. Devuelve 1, 2 o 3 si lo encuentra, o None si
    ningún segmento coincide (para que quede como advertencia explícita
    en vez de asumir un valor incorrecto).
    """
    for segmento in ruta_relativa.parts:
        coincidencia = FENOMENO_PATTERN.search(segmento)
        if coincidencia:
            return int(coincidencia.group(1))
    return None


# ==============================================================================
# SECCIÓN 6: PIPELINE PRINCIPAL (orquesta todo lo anterior)
# ==============================================================================
def _leer_fuentes_ya_procesadas(output_path: str) -> set:
    """Para el modo --resume: lee qué archivos ('fuente') ya quedaron
    escritos en una corrida anterior, para no repetir ese trabajo."""
    ya_procesadas = set()
    p = Path(output_path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    ya_procesadas.add(json.loads(linea)["fuente"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return ya_procesadas


def procesar_corpus_pdf(input_dir: str, output_path: str, errors_path: str,
                         registry_path: str, resume: bool, progress_every: int):
    input_dir = Path(input_dir)
    registro = RegistroDocumentos(registry_path)

    ya_procesadas = _leer_fuentes_ya_procesadas(output_path) if resume else set()
    modo_salida = "a" if resume else "w"
    modo_errores = "a" if resume else "w"

    n_ok, n_err, n_saltados = 0, 0, 0
    t0 = time.time()

    # rglob("*.pdf") busca, de forma recursiva, TODOS los archivos que
    # terminen en .pdf, sin importar en qué subcarpeta estén ni cuántos
    # niveles de anidamiento tenga la carpeta de entrada.
    archivos_pdf = sorted(input_dir.rglob("*.pdf"))
    total = len(archivos_pdf)
    print(f"PDFs encontrados bajo '{input_dir}': {total}")

    with open(output_path, modo_salida, encoding="utf-8") as out_f, \
         open(errors_path, modo_errores, encoding="utf-8") as err_f:

        for i, file_path in enumerate(archivos_pdf, start=1):
            ruta_relativa = file_path.relative_to(input_dir)
            ruta_relativa_str = str(ruta_relativa)

            # --resume: si este archivo ya está en el output de una
            # corrida anterior, lo saltamos sin volver a extraer nada.
            if resume and ruta_relativa_str in ya_procesadas:
                n_saltados += 1
                continue

            # Cada `progress_every` archivos: mostramos avance Y guardamos
            # un "checkpoint" en disco (flush + guardar registro). Así, si
            # el proceso se interrumpe a mitad de camino (por ejemplo en
            # el archivo 900 de 1800), lo ya escrito queda persistido y
            # consistente, y con --resume no hay que repetirlo.
            if progress_every and i % progress_every == 0:
                elapsed = time.time() - t0
                print(f"  [{i}/{total}] ok={n_ok} errores={n_err} "
                      f"saltados={n_saltados}  ({elapsed:.0f}s)")
                out_f.flush()
                err_f.flush()
                registro.guardar()

            try:
                texto_crudo, meta_extra = extraer_texto_pdf(file_path)
                texto_limpio = clean_text(texto_crudo)
                idioma = detect_language(texto_limpio)
                doc_id = registro.obtener_o_crear(ruta_relativa_str)
                fenomeno = inferir_fenomeno(ruta_relativa)

                registro_json = {
                    "doc_id": doc_id,
                    "fuente": ruta_relativa_str,
                    "formato": "pdf",
                    "fenomeno": fenomeno,
                    "idioma": idioma,
                    "num_caracteres": len(texto_limpio),
                    "texto": texto_limpio,
                    "meta_extra": meta_extra,
                }
                out_f.write(json.dumps(registro_json, ensure_ascii=False) + "\n")
                n_ok += 1

            except Exception as e:
                # Un PDF corrupto o protegido con contraseña no debe
                # tumbar toda la corrida: se registra el error y se sigue
                # con el siguiente archivo.
                err_f.write(json.dumps({
                    "fuente": ruta_relativa_str,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }, ensure_ascii=False) + "\n")
                n_err += 1

    registro.guardar()
    print(f"\nProcesados correctamente : {n_ok}")
    print(f"Con error                : {n_err}")
    if resume:
        print(f"Saltados (ya existían)   : {n_saltados}")
    print(f"Salida                   : {output_path}")
    print(f"Errores                  : {errors_path}")
    print(f"Registro de doc_id       : {registry_path}")


# ==============================================================================
# SECCIÓN 7: LÍNEA DE COMANDOS
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extracción y limpieza de archivos PDF (CODEFEST AD ASTRA 2026)."
    )
    parser.add_argument("--input", default="data_raw",
                         help="Carpeta raíz donde buscar archivos .pdf (recursivo)")
    parser.add_argument("--output", default="documentos_pdf.jsonl",
                         help="Archivo .jsonl de salida")
    parser.add_argument("--errors", default="errores_pdf.jsonl",
                         help="Archivo .jsonl con los PDFs que fallaron")
    parser.add_argument("--registry", default="doc_registry.json",
                         help="Archivo de registro de doc_id (compártelo entre "
                              "los 4 scripts para evitar IDs duplicados)")
    parser.add_argument("--resume", action="store_true",
                         help="No reprocesar archivos que ya estén en --output")
    parser.add_argument("--progress-every", type=int, default=50,
                         help="Cada cuántos archivos mostrar avance y guardar checkpoint")
    args = parser.parse_args()

    procesar_corpus_pdf(
        input_dir=args.input,
        output_path=args.output,
        errors_path=args.errors,
        registry_path=args.registry,
        resume=args.resume,
        progress_every=args.progress_every,
    )
