"""
Adaptadores de extracción — patrón Adapter.

Cada adaptador expone el mismo contrato: `extraer(file_path) -> str`, que
devuelve el TEXTO CRUDO (sin limpiar) leído directamente en memoria a partir
del archivo original, sin escribir nada a disco. La limpieza (clean_text) y
el empaquetado en el esquema final los aplica el orquestador (pipeline.py)
de forma uniforme sobre lo que cada adaptador devuelve.

Regla de "data basura": si un archivo no aporta señal semántica (una portada
sin texto, una foto decorativa), el adaptador debe devolver "" en lugar de
lanzar una excepción o inventar contenido. La cadena vacía muere sola en la
fase de limpieza/chunking.

PBFExtractor es la única excepción al contrato de un archivo = un documento:
un tileset PBF vive repartido en muchos .pbf (uno por nivel de zoom/tile), así
que su método es extraer_tileset(raiz, archivos) — ver pipeline.py para el
agrupamiento.
"""

import csv
import io
import sys
from html.parser import HTMLParser
from pathlib import Path

from .texto_utils import fila_a_texto

# json_extract.py vive en la raíz del repo (aporte del equipo), no dentro del
# paquete extraccion/; se asegura que la raíz esté en sys.path sin importar
# desde dónde se invoque el pipeline.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

UMBRAL_TEXTO_VACIO = 50   # por debajo de esto, un PDF se considera "sin capa de texto"
UMBRAL_IMAGEN_SIN_SENAL = 30  # por debajo de esto, una imagen se considera decorativa


class ExtractorBase:
    """Interfaz común de los adaptadores de extracción."""

    extensiones: tuple[str, ...] = ()

    def soporta(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensiones

    def extraer(self, file_path: Path) -> str:
        raise NotImplementedError


# ==============================================================================
# PDF (con fallback a OCR en memoria si la capa de texto viene vacía)
# ==============================================================================
class PDFExtractor(ExtractorBase):
    extensiones = (".pdf",)

    def __init__(self, idiomas_ocr: str = "spa+eng", dpi_ocr: int = 300):
        self.idiomas_ocr = idiomas_ocr
        self.dpi_ocr = dpi_ocr

    def extraer(self, file_path: Path) -> str:
        import fitz  # PyMuPDF

        doc = fitz.open(str(file_path))
        try:
            texto_por_pagina = []
            for page in doc:
                # "blocks" preserva el orden de lectura y separa de forma
                # natural cabeceras/pies de página como bloques propios,
                # que remove_repeated_lines() elimina más adelante por
                # repetirse idénticos en varias páginas.
                bloques = page.get_text("blocks")
                bloques.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
                texto_pagina = "\n".join(b[4].strip() for b in bloques if b[4].strip())
                texto_por_pagina.append(texto_pagina)
            texto_completo = "\n\n".join(texto_por_pagina)

            # PDF escaneado sin capa de texto: se aplica OCR en memoria,
            # renderizando cada página como imagen (sin guardar nada a disco).
            if len(texto_completo.strip()) < UMBRAL_TEXTO_VACIO:
                texto_ocr = self._ocr_fallback(doc)
                if len(texto_ocr.strip()) > len(texto_completo.strip()):
                    texto_completo = texto_ocr

            return texto_completo
        finally:
            doc.close()

    def _ocr_fallback(self, doc) -> str:
        try:
            import fitz
            import pytesseract
            from PIL import Image
            import io as _io
        except ImportError:
            return ""

        zoom = self.dpi_ocr / 72
        mat = fitz.Matrix(zoom, zoom)
        texto_por_pagina = []
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(_io.BytesIO(pix.tobytes("png")))
            try:
                texto_por_pagina.append(pytesseract.image_to_string(img, lang=self.idiomas_ocr))
            except Exception:
                texto_por_pagina.append("")
        return "\n\n".join(texto_por_pagina)


# ==============================================================================
# CSV
# ==============================================================================
class CSVExtractor(ExtractorBase):
    extensiones = (".csv",)

    def __init__(self, delimitador: str = ","):
        self.delimitador = delimitador

    def extraer(self, file_path: Path) -> str:
        try:
            contenido = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contenido = file_path.read_text(encoding="latin-1")

        lector = csv.DictReader(io.StringIO(contenido), delimiter=self.delimitador)
        filas_texto = []
        for fila in lector:
            # None como clave/valor => la fila no calzó con el encabezado.
            if None in fila or any(v is None for v in fila.values()):
                continue
            texto_fila = fila_a_texto(fila)
            if texto_fila:
                filas_texto.append(texto_fila)
        return "\n".join(filas_texto)


# ==============================================================================
# Excel (XLSX/XLS)
# ==============================================================================
class ExcelExtractor(ExtractorBase):
    extensiones = (".xlsx", ".xls")

    def __init__(self, sheet_name=0):
        self.sheet_name = sheet_name

    def extraer(self, file_path: Path) -> str:
        import pandas as pd

        df = pd.read_excel(file_path, sheet_name=self.sheet_name, dtype=str)
        df = df.fillna("")
        filas_texto = [fila_a_texto(fila) for fila in df.to_dict(orient="records")]
        return "\n".join(f for f in filas_texto if f)


# ==============================================================================
# Texto plano / Markdown
# ==============================================================================
class TextoExtractor(ExtractorBase):
    """.txt y .md: se devuelve el contenido tal cual (clean_text no toca la
    sintaxis Markdown de encabezados/listas, solo colapsa espacios y quita
    boilerplate repetido), preservando las señales estructurales para el
    chunking posterior."""

    extensiones = (".txt", ".md")

    def extraer(self, file_path: Path) -> str:
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1")


# ==============================================================================
# HTML — conserva encabezados/párrafos/listas como marcadores estructurales
# (estilo Markdown) incrustados en el texto, para que el chunking jerárquico
# los pueda usar como guía.
# ==============================================================================
_TAGS_ENCABEZADO = {f"h{i}": "#" * i for i in range(1, 7)}
_TAGS_BLOQUE = {"p", "div", "tr", "section", "article", "br"}
_TAGS_SIN_CONTENIDO = {"script", "style", "head", "noscript"}


class _ParserEstructuralHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._omitir_contenido = 0

    def handle_starttag(self, tag, attrs):
        if tag in _TAGS_SIN_CONTENIDO:
            self._omitir_contenido += 1
        elif tag in _TAGS_ENCABEZADO:
            self._partes.append("\n\n" + _TAGS_ENCABEZADO[tag] + " ")
        elif tag == "li":
            self._partes.append("\n- ")
        elif tag in _TAGS_BLOQUE:
            self._partes.append("\n\n")

    def handle_endtag(self, tag):
        if tag in _TAGS_SIN_CONTENIDO:
            self._omitir_contenido = max(0, self._omitir_contenido - 1)

    def handle_data(self, data):
        if self._omitir_contenido:
            return
        texto = data.strip()
        if texto:
            self._partes.append(texto + " ")

    def obtener_texto(self) -> str:
        return "".join(self._partes)


class HTMLExtractor(ExtractorBase):
    extensiones = (".html", ".htm")

    def extraer(self, file_path: Path) -> str:
        try:
            contenido = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contenido = file_path.read_text(encoding="latin-1")

        parser = _ParserEstructuralHTML()
        parser.feed(contenido)
        return parser.obtener_texto()


# ==============================================================================
# JSON — delega en json_extract.py (aporte del equipo, raíz del repo): separa
# cuerpo de metadata siguiendo la Sección 2.1 del spec (title/body_text/
# body_paragraphs se tratan como texto; url/date/authors/tags quedan fuera del
# cuerpo, nunca mezclados), con NDJSON/BOM, deduplicación de body_text contra
# body_paragraphs, y un fallback en dos niveles antes de rendirse.
# ==============================================================================
class JSONExtractor(ExtractorBase):
    extensiones = (".json",)

    def extraer(self, file_path: Path) -> str:
        from json_extract import JsonSinTexto, extract_json

        try:
            texto, _meta, _traza = extract_json(file_path)
            return texto
        except JsonSinTexto:
            # Parseó bien pero no hay prosa aprovechable: no es un error de
            # extracción, es el caso "data basura" -> "" (muere naturalmente).
            return ""


# ==============================================================================
# Imágenes (OCR en memoria)
# ==============================================================================
class ImagenExtractor(ExtractorBase):
    extensiones = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")

    def __init__(self, idiomas: str = "spa+eng"):
        self.idiomas = idiomas

    def extraer(self, file_path: Path) -> str:
        import pytesseract
        from PIL import Image

        img = Image.open(file_path)
        texto = pytesseract.image_to_string(img, lang=self.idiomas)

        # Imagen decorativa/sin texto (ej. una foto de portada): se
        # devuelve "" en vez de ruido de OCR sin valor semántico.
        if len(texto.strip()) < UMBRAL_IMAGEN_SIN_SENAL:
            return ""
        return texto


# ==============================================================================
# PBF (Mapbox Vector Tile) — un tileset = muchos .pbf agrupados
# ==============================================================================
class PBFExtractor:
    def extraer_tileset(self, tileset_root: Path, archivos_pbf: list[Path]) -> str:
        import mapbox_vector_tile

        elementos_unicos: dict[tuple, str] = {}
        for pbf_path in sorted(archivos_pbf):
            try:
                tile = mapbox_vector_tile.decode(pbf_path.read_bytes())
            except Exception:
                continue

            for nombre_capa, capa in tile.items():
                for elemento in capa.get("features", []):
                    atributos = elemento.get("properties", {})
                    # id estable del elemento (igual en todos los niveles de
                    # zoom): se usa para deduplicar y quedarse con una sola
                    # versión de cada municipio/zona.
                    id_elemento = (
                        atributos.get("fid")
                        or atributos.get("id")
                        or atributos.get("FID")
                        or tuple(sorted(atributos.items(), key=lambda kv: kv[0]))
                    )
                    clave = (nombre_capa, id_elemento)
                    if clave in elementos_unicos:
                        continue

                    pares = [f"capa: {nombre_capa}"] + [
                        f"{atributo}: {dato}"
                        for atributo, dato in atributos.items()
                        if dato is not None and str(dato).strip() != ""
                    ]
                    elementos_unicos[clave] = " | ".join(pares)

        return "\n".join(elementos_unicos.values())
