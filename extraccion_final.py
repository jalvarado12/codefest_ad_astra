"""
Pipeline de extracción, self-contained — fusión de extraccion/adaptadores.py,
extraccion/pipeline.py, extraccion/registro.py, extraccion/texto_utils.py y
json_extract.py en un solo archivo, sin imports de paquete ni dependencia de
otros archivos del repo (pensado para copiarse/subirse suelto, p. ej. a Colab).

Uso:
    from extraccion_final import generar_documentos
    for doc in generar_documentos("CORPUS", "doc_registry.json"):
        ...

Contrato de cada documento generado:
    {
        "doc_id": str,
        "fuente": str,
        "formato": str,
        "fenomeno": int | None,
        "texto_limpio": str,   # "" si el archivo no aporta señal semántica
    }
"""

import argparse
import csv
import difflib
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

UMBRAL_TEXTO_VACIO = 50
UMBRAL_IMAGEN_SIN_SENAL = 30

# ==============================================================================
# texto_utils — limpieza de texto compartida
# ==============================================================================
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WEIRD_SPACES_RE = re.compile(r"[ ​‌‍﻿]")
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


def fila_a_texto(fila: dict, separador: str = " | ") -> str:
    pares = [
        f"{columna}: {valor}"
        for columna, valor in fila.items()
        if valor is not None and str(valor).strip() != "" and str(valor).lower() != "nan"
    ]
    return separador.join(pares)


# ==============================================================================
# registro — asignación de doc_id e inferencia de fenómeno
# ==============================================================================
class RegistroDocumentos:
    """Asigna y persiste doc_id únicos, compartido entre todos los
    adaptadores para no repetir IDs entre formatos."""

    def __init__(self, registry_path: str = "doc_registry.json"):
        self.registry_path = Path(registry_path)
        if self.registry_path.exists():
            with open(self.registry_path, "r", encoding="utf-8") as f:
                self._mapa = json.load(f)
        else:
            self._mapa = {}
        self._siguiente_num = self._calcular_siguiente_num()

    def _calcular_siguiente_num(self) -> int:
        numeros = [int(v.split("-")[1]) for v in self._mapa.values()]
        return max(numeros, default=0) + 1

    def obtener_o_crear(self, ruta_relativa: str) -> str:
        clave = str(ruta_relativa)
        if clave in self._mapa:
            return self._mapa[clave]
        doc_id = f"DOC-{self._siguiente_num:04d}"
        self._mapa[clave] = doc_id
        self._siguiente_num += 1
        return doc_id

    def guardar(self):
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(self._mapa, f, ensure_ascii=False, indent=2)


FENOMENO_PATTERNS = [
    re.compile(r"fen[oó]meno[\s_-]*([123])", re.IGNORECASE),
    re.compile(r"^f([123])[_\s-]", re.IGNORECASE),
]


def inferir_fenomeno(ruta_relativa: Path) -> int | None:
    for segmento in ruta_relativa.parts:
        for patron in FENOMENO_PATTERNS:
            coincidencia = patron.search(segmento)
            if coincidencia:
                return int(coincidencia.group(1))
    return None


# ==============================================================================
# adaptadores — patrón Adapter: cada uno expone extraer(file_path) -> str con
# el TEXTO CRUDO (sin limpiar); "" si el archivo no aporta señal semántica.
# ==============================================================================

# EasyOCR — reader compartido y perezoso (se instancia en el primer uso real
# de OCR, no en __init__, para no penalizar corpus sin PDFs escaneados/imágenes).
_MAPA_IDIOMAS_TESSERACT_A_EASYOCR = {"spa": "es", "eng": "en"}
_lector_easyocr_cache: dict = {}


def _mapear_idiomas_easyocr(idiomas_tesseract: str) -> list[str]:
    langs = [
        _MAPA_IDIOMAS_TESSERACT_A_EASYOCR.get(codigo.strip(), codigo.strip())
        for codigo in idiomas_tesseract.split("+")
        if codigo.strip()
    ]
    return langs or ["es"]


def _obtener_lector_easyocr(idiomas: str, gpu: bool = False):
    langs = tuple(_mapear_idiomas_easyocr(idiomas))
    if langs not in _lector_easyocr_cache:
        import easyocr

        _lector_easyocr_cache[langs] = easyocr.Reader(list(langs), gpu=gpu, verbose=False)
    return _lector_easyocr_cache[langs]


class ExtractorBase:
    """Interfaz común de los adaptadores de extracción."""

    extensiones: tuple[str, ...] = ()

    def soporta(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensiones

    def extraer(self, file_path: Path) -> str:
        raise NotImplementedError


class PDFExtractor(ExtractorBase):
    extensiones = (".pdf",)

    def __init__(self, idiomas_ocr: str = "spa+eng", dpi_ocr: int = 300, gpu_ocr: bool = False):
        self.idiomas_ocr = idiomas_ocr
        self.dpi_ocr = dpi_ocr
        self.gpu_ocr = gpu_ocr

    def extraer(self, file_path: Path) -> str:
        import fitz  # PyMuPDF

        doc = fitz.open(str(file_path))
        try:
            texto_por_pagina = []
            for page in doc:
                bloques = page.get_text("blocks")
                bloques.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
                texto_pagina = "\n".join(b[4].strip() for b in bloques if b[4].strip())
                texto_por_pagina.append(texto_pagina)
            texto_completo = "\n\n".join(texto_por_pagina)

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

            lector = _obtener_lector_easyocr(self.idiomas_ocr, gpu=self.gpu_ocr)
        except ImportError:
            return ""

        zoom = self.dpi_ocr / 72
        mat = fitz.Matrix(zoom, zoom)
        texto_por_pagina = []
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            try:
                resultados = lector.readtext(pix.tobytes("png"), detail=0, paragraph=True)
                texto_por_pagina.append("\n\n".join(resultados))
            except Exception:
                texto_por_pagina.append("")
        return "\n\n".join(texto_por_pagina)


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
            if None in fila or any(v is None for v in fila.values()):
                continue
            texto_fila = fila_a_texto(fila)
            if texto_fila:
                filas_texto.append(texto_fila)
        return "\n".join(filas_texto)


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


class TextoExtractor(ExtractorBase):
    extensiones = (".txt", ".md")

    def extraer(self, file_path: Path) -> str:
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1")


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


class JSONExtractor(ExtractorBase):
    """Delega en extract_json() (ver sección json_extract más abajo).

    Los catálogos/registros (CATALOG_FILES, ver sección catalog_metadata) no
    son prosa real -- título/url/status por PDF descargado -- así que su
    texto se vacía aquí mismo en vez de indexarse como documento falso. El
    documento se sigue registrando (doc_id, fuente) con texto_limpio="";
    su metadata se rescata aparte y se adjunta al documento real que
    describe (ver _indexar_metadata_catalogo / generar_documentos)."""

    extensiones = (".json",)

    def extraer(self, file_path: Path) -> str:
        if file_path.name in CATALOG_FILES:
            return ""
        try:
            texto, _meta, _traza = extract_json(file_path)
            return texto
        except JsonSinTexto:
            return ""


class ImagenExtractor(ExtractorBase):
    extensiones = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")

    def __init__(self, idiomas: str = "spa+eng", gpu_ocr: bool = False):
        self.idiomas = idiomas
        self.gpu_ocr = gpu_ocr

    def extraer(self, file_path: Path) -> str:
        try:
            lector = _obtener_lector_easyocr(self.idiomas, gpu=self.gpu_ocr)
        except ImportError:
            return ""

        try:
            resultados = lector.readtext(str(file_path), detail=0, paragraph=True)
        except Exception:
            return ""
        texto = "\n\n".join(resultados)

        if len(texto.strip()) < UMBRAL_IMAGEN_SIN_SENAL:
            return ""
        return texto


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


# ==============================================================================
# pipeline — orquestador: generar_documentos() recorre el corpus, despacha
# cada archivo al adaptador correspondiente y hace yield del esquema final.
# ==============================================================================
_ADAPTADORES: list[ExtractorBase] = [
    PDFExtractor(),
    CSVExtractor(),
    ExcelExtractor(),
    TextoExtractor(),
    HTMLExtractor(),
    JSONExtractor(),
    ImagenExtractor(),
]

_FORMATO_POR_EXT = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".txt": "txt",
    ".md": "md",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".jpg": "imagen",
    ".jpeg": "imagen",
    ".png": "imagen",
    ".tif": "imagen",
    ".tiff": "imagen",
    ".bmp": "imagen",
    ".webp": "imagen",
}

_DISPATCH: dict[str, ExtractorBase] = {
    ext: adaptador
    for adaptador in _ADAPTADORES
    for ext in adaptador.extensiones
}


def _localizar_raiz_tileset(pbf_path: Path, input_dir: Path) -> Path:
    for ancestro in pbf_path.parents:
        if ancestro == input_dir:
            break
        if ancestro.name.lower() == "tiles":
            return ancestro.parent
    return pbf_path.parent


def _agrupar_tilesets_pbf(input_dir: Path) -> dict[Path, list[Path]]:
    grupos: dict[Path, list[Path]] = defaultdict(list)
    for pbf_path in input_dir.rglob("*.pbf"):
        raiz = _localizar_raiz_tileset(pbf_path, input_dir)
        grupos[raiz].append(pbf_path)
    return grupos


def _empaquetar(registro: RegistroDocumentos, fuente: str, formato: str,
                 texto_crudo: str, ruta_relativa: Path,
                 metadata_catalogo: list | None = None) -> dict:
    return {
        "doc_id": registro.obtener_o_crear(fuente),
        "fuente": fuente,
        "formato": formato,
        "fenomeno": inferir_fenomeno(ruta_relativa),
        "texto_limpio": clean_text(texto_crudo),
        "metadata_catalogo": metadata_catalogo or [],
    }


def generar_documentos(input_dir: str, registry_path: str = "doc_registry.json",
                        on_error=None) -> Iterator[dict]:
    """Generador ciego al formato: recorre `input_dir` y hace yield de un
    documento estandarizado por cada archivo/tileset soportado.

    `on_error(fuente, excepcion)` es opcional; si se pasa, se invoca cuando
    un archivo falla en vez de propagar la excepción (el documento se sigue
    entregando, con texto_limpio="").

    Todo archivo soportado se registra siempre, incluso sin señal (tier-3,
    catálogo/registro, o error de extracción): texto_limpio="" no calza con
    ninguna búsqueda, pero el documento no desaparece del corpus.
    """
    input_dir = Path(input_dir)
    registro = RegistroDocumentos(registry_path)
    pbf_extractor = PBFExtractor()
    catalogo_por_fuente = _indexar_metadata_catalogo(input_dir)

    tilesets = _agrupar_tilesets_pbf(input_dir)
    for tileset_root, archivos_pbf in sorted(tilesets.items()):
        ruta_relativa = tileset_root.relative_to(input_dir)
        fuente = str(ruta_relativa)
        try:
            texto_crudo = pbf_extractor.extraer_tileset(tileset_root, archivos_pbf)
        except Exception as e:
            if on_error:
                on_error(fuente, e)
            texto_crudo = ""
        yield _empaquetar(registro, fuente, "pbf", texto_crudo, ruta_relativa)

    archivos_pbf_ya_procesados = {p for archivos in tilesets.values() for p in archivos}

    for file_path in sorted(input_dir.rglob("*")):
        if not file_path.is_file() or file_path in archivos_pbf_ya_procesados:
            continue

        ext = file_path.suffix.lower()
        adaptador = _DISPATCH.get(ext)
        if adaptador is None:
            continue

        ruta_relativa = file_path.relative_to(input_dir)
        fuente = str(ruta_relativa)
        try:
            texto_crudo = adaptador.extraer(file_path)
        except Exception as e:
            if on_error:
                on_error(fuente, e)
            texto_crudo = ""

        yield _empaquetar(registro, fuente, _FORMATO_POR_EXT[ext], texto_crudo, ruta_relativa,
                          catalogo_por_fuente.get(fuente.replace("\\", "/")))

    registro.guardar()


# ==============================================================================
# json_extract — extracción determinista de cuerpo/metadata en JSON (usada por
# JSONExtractor arriba). Fusión de json_extract.py, sin cambios de lógica.
# ==============================================================================
JSON_MIN_WORDS = 30

JSON_MIN_STR = 40
JSON_MIN_STR_TIER2 = 15  # tier 2 ya es un rescate; se dejan pasar títulos más cortos

JSON_TRUNC_META = 300  # los valores de metadata son para filtrar, no leer


class JsonIlegible(Exception):
    """El archivo no se pudo parsear como JSON ni como NDJSON."""

    def __init__(self, path, detalle):
        super().__init__(f"{path}: {detalle}")
        self.path, self.detalle = str(path), detalle


class JsonSinTexto(Exception):
    """El archivo parseó bien, pero no arrojó texto de cuerpo por encima del piso."""

    def __init__(self, path, traza):
        super().__init__(f"{path}: no usable text (tier 3)")
        self.path, self.traza = str(path), traza


# --- roles de campo -----------------------------------------------------------------
# Cada nombre fue observado en el censo real del corpus ADL (964 archivos, 15 formas) o
# lo nombra la sección 2.1 del spec. Nada aquí es especulativo.

JSON_TITLE = {"title", "titulo", "headline", "heading", "subject", "nombre", "encabezado",
              "subtitle", "subtitulo"}

# Blobs de cuerpo completo: un string que contiene todo el artículo.
JSON_BODY_BLOB = {"body_text", "bodytext", "body", "content", "contenido", "texto", "text",
                  "full_text", "fulltext", "article_body", "articlebody"}

# Listas de párrafos: el mismo cuerpo, ya separado en sus fronteras reales.
JSON_BODY_PARA = {"body_paragraphs", "paragraphs", "parrafos", "parragrafos"}

# Prosa independiente que no duplica el cuerpo.
JSON_BODY_OTHER = {"abstract", "resumen", "excerpt", "summary", "sumario", "description",
                   "descripcion", "lead", "lede", "extract", "tema_clave"}

JSON_BODY = JSON_BODY_BLOB | JSON_BODY_PARA | JSON_BODY_OTHER

JSON_META = {"url", "link", "permalink", "canonical_url", "source_url", "pdf_url", "page_url",
             "date", "fecha", "fecha_emision", "published", "published_at", "pubdate",
             "datepublished", "year", "año", "scraped_at",
             "author", "authors", "autor", "autores", "byline", "editors",
             "tags", "keywords", "topics", "categories", "categoria", "category", "section",
             "source", "fuente", "language", "languages", "idioma", "lang",
             "id", "doc_id", "codigo", "doi", "issue", "tipo", "municipios", "country",
             "fenomeno", "edition", "filename", "status"}

# Contenedores de links y assets. Observados en el censo como portadores puros de
# URL/anchor/alt-text; recursar en ellos arrastra chrome de navegación y rutas de
# archivo al cuerpo. Solo se recorren como último recurso en tier 2.
JSON_NOISE = {"images", "links", "pdf_links", "doc_links", "external_links", "internal_links",
              "science_links", "all_links", "additional_links", "pdfs", "urls", "hashes",
              "articulos", "fields", "lists_links", "detail_url", "image_preview",
              "local_path", "dest", "path", "file", "json", "src", "href"}

# Valores que son identificadores o maquinaria, nunca prosa.
JSON_RE_JUNK = re.compile(
    r"^(?:https?://\S+"
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\S*)?"
    r"|[\d\s.,%+-]+"
    r"|[0-9a-f]{16,}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-\S+"
    r"|\w+/[\w.+-]+"
    r"|\S+\.(?:pdf|json|html?|jpe?g|png|pbf|csv|xlsx)"
    r")$", re.I)


def _json_norm(key):
    """Minúsculas y sin acentos, para que 'Título' y 'titulo' sean la misma clave."""
    k = unicodedata.normalize("NFKD", str(key)).encode("ascii", "ignore").decode()
    return k.strip().lower()


def _json_scalar(v):
    return isinstance(v, (str, int, float, bool))


# Los scripts CJK escriben sin espacios, así que str.split() los subcuenta. Contar
# ideogramas individualmente mantiene el piso significativo para chino/japonés en
# vez de rechazar una página completa como "8 palabras".
JSON_RE_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def _json_wc(text):
    """Conteo de palabras que no asume un script delimitado por espacios."""
    return len(text.split()) + len(JSON_RE_CJK.findall(text))


def _json_load(path):
    """Parsea como un único valor JSON, si no como NDJSON. Si ninguno, JsonIlegible."""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    try:
        return json.loads(raw), "json"
    except json.JSONDecodeError as first:
        lines = [l for l in raw.splitlines() if l.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(l) for l in lines], "ndjson"
            except json.JSONDecodeError:
                pass
        raise JsonIlegible(path, f"{first.msg} (line {first.lineno} col {first.colno})")


def _json_harvest(obj, out, meta, traza, prefix="", tier2=False, role=None):
    """Recorre en orden de inserción, emitiendo (role, key_path, text) en `out`.

    `role` propaga el contexto de cuerpo a través de contenedores sin nombre, para que
    una lista de párrafos de objetos -- body_paragraphs: [{"text": ...}] -- se siga
    reconociendo como cuerpo.
    """
    min_str = JSON_MIN_STR_TIER2 if tier2 else JSON_MIN_STR

    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = _json_norm(k)
            path = f"{prefix}.{k}" if prefix else str(k)

            if nk in JSON_META and (_json_scalar(v) or (isinstance(v, list) and all(_json_scalar(x) for x in v))):
                if nk not in meta:
                    val = ", ".join(str(x) for x in v) if isinstance(v, list) else v
                    meta[nk] = str(val)[:JSON_TRUNC_META] if isinstance(val, str) else val
                    traza["claves_meta"].append(path)
                continue

            if nk in JSON_NOISE and not tier2:
                continue

            if nk in JSON_BODY or nk in JSON_TITLE:
                sub = "para" if nk in JSON_BODY_PARA else "blob" if nk in JSON_BODY_BLOB else "other"
                _json_harvest(v, out, meta, traza, path, tier2, role=sub)
            else:
                _json_harvest(v, out, meta, traza, path, tier2, role=None)

    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            _json_harvest(x, out, meta, traza, f"{prefix}[{i}]", tier2, role=role)

    elif isinstance(obj, str):
        s = obj.strip()
        if not s or JSON_RE_JUNK.match(s):
            return
        if role is None and len(s) < min_str:
            return
        out.append((role or "libre", prefix, s))


def _json_assemble(out):
    """Descarta blobs duplicados por listas de párrafos, luego dedupe exacto, en orden.

    En el corpus real, 485 de 848 artículos JSON traen tanto `body_text` como
    `body_paragraphs`, donde el primero es el segundo unido -- emitir ambos duplicaría
    cada documento. Gana la lista de párrafos: la sección 2.1 pide preservar el orden
    de párrafos, y la lista es donde están las fronteras reales.
    """
    has_para = any(r == "para" for r, _, _ in out)
    seen, kept, rutas = set(), [], []
    for role, path, s in out:
        if has_para and role == "blob":
            continue
        if s in seen:
            continue
        seen.add(s)
        kept.append(s)
        rutas.append(path)
    return "\n\n".join(kept), rutas


def extract_json(path):
    """Devuelve (texto, meta, traza). Lanza JsonIlegible o JsonSinTexto -- nunca en silencio."""
    data, carga = _json_load(path)
    traza = {"archivo": Path(path).name, "tier": 1, "carga": carga,
             "claves_texto": [], "claves_meta": [], "claves_raiz": [], "advertencias": []}
    traza["claves_raiz"] = (sorted(data.keys()) if isinstance(data, dict)
                            else ["<lista-nivel-raiz>"])

    out, meta = [], {}
    _json_harvest(data, out, meta, traza)
    texto, traza["claves_texto"] = _json_assemble(out)

    if _json_wc(texto) < JSON_MIN_WORDS:
        traza["tier"] = 2
        out, meta2 = [], {}
        _json_harvest(data, out, meta2, traza, tier2=True)
        for k, v in meta2.items():
            meta.setdefault(k, v)
        texto, traza["claves_texto"] = _json_assemble(out)
        traza["advertencias"].append("tier1 bajo el piso; recuperado por barrido filtrado")

    if _json_wc(texto) < JSON_MIN_WORDS:
        traza["tier"] = 3
        raise JsonSinTexto(path, traza)

    traza["n_palabras"] = _json_wc(texto)
    return texto, meta, traza


def resumen_json(log_path):
    """Imprime el censo de esquemas a partir de un log de procedencia."""
    rows = [json.loads(l) for l in Path(log_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    tiers = Counter(r["tier"] for r in rows)
    print(f"documentos: {len(rows)}")
    for t in sorted(tiers):
        print(f"  tier {t}: {tiers[t]}")
    print(f"  carga ndjson: {sum(1 for r in rows if r.get('carga') == 'ndjson')}")

    paths = Counter()
    for r in rows:
        for p in r["claves_texto"]:
            paths[re.sub(r"\[\d+\]", "[]", p)] += 1
    print("\n== rutas de texto mas comunes ==")
    for p, c in paths.most_common(25):
        print(f"{c:5d}  {p}")

    metas = Counter(m.split(".")[-1] for r in rows for m in r["claves_meta"])
    print("\n== claves de metadata vistas ==")
    for m, c in metas.most_common(30):
        print(f"{c:5d}  {m}")

    print("\n== tier 2 (tabla de alias insuficiente) ==")
    for r in rows:
        if r["tier"] == 2:
            print(f"  {r['archivo']:60s} {r['claves_raiz']}")

    print("\n== tier 3 (sin texto -- decidir uno por uno) ==")
    for r in rows:
        if r["tier"] == 3:
            print(f"  {r['archivo']:60s} {r['claves_raiz']}")


def _json_selfcheck(tmp):
    def run(name, obj, raw=None):
        p = Path(tmp) / name
        if raw is not None:
            p.write_bytes(raw)
        else:
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return extract_json(p)

    P = "Esta es una oracion de relleno con suficientes palabras para superar el piso de treinta palabras que exige el extractor y asi poder comprobar el comportamiento real del arbol de decisiones sin recurrir al nivel dos."
    Q = "Segundo parrafo distinto del primero que tambien aporta bastantes palabras al conteo total del documento para que el resultado quede comodamente por encima del umbral fijado."

    t, m, z = run("a.json", {"title": "Titulo A", "body_text": P})
    assert z["tier"] == 1 and "Titulo A" in t and P in t, t

    t, m, z = run("b.json", {"headline": "Titulo B", "content": P, "author": "Ada Lovelace"})
    assert "Ada Lovelace" not in t and m["author"] == "Ada Lovelace", (t, m)

    t, m, z = run("c.json", {"article": {"title": "Titulo C", "paragraphs": [P, Q]}})
    assert t.index(P) < t.index(Q) and "Titulo C" in t, t

    t, m, z = run("d.json", {"content": {"blocks": [P, Q]}})
    assert P in t and Q in t, t

    t, m, z = run("e.json", {"body_paragraphs": [{"text": P}, {"text": Q}]})
    assert P in t and Q in t, t

    t, m, z = run("f.json", None, raw=b"\xef\xbb\xbf" + json.dumps({"body_text": P}).encode())
    assert P in t, t

    raw = (json.dumps({"body_text": P}) + "\n" + json.dumps({"body_text": Q})).encode()
    t, m, z = run("g.json", None, raw=raw)
    assert z["carga"] == "ndjson" and P in t and Q in t, (z, t)

    t, m, z = run("h.json", [{"title": "T1", "body_text": P}, {"title": "T2", "body_text": Q}])
    assert t.index(P) < t.index(Q), t

    t, m, z = run("i.json", {"title": "Solo titulo", "notas": {"raro": P}})
    assert z["tier"] == 1 and P in t, (z, t)
    t, m, z = run("i2.json", {"title": "Solo titulo corto", "images": [{"alt": P}]})
    assert z["tier"] == 2 and P in t, (z, t)

    try:
        run("j.json", {"url": "https://x.test/a", "date": "2026-01-01", "tags": ["a", "b"]})
        raise AssertionError("expected JsonSinTexto")
    except JsonSinTexto as e:
        assert e.traza["tier"] == 3

    try:
        run("k.json", None, raw=b'{"title": "trunc')
        raise AssertionError("expected JsonIlegible")
    except JsonIlegible:
        pass

    t, m, z = run("l.json", {"body_paragraphs": [P, Q], "body_text": P + "\n" + Q})
    assert t.count(P) == 1 and t.count(Q) == 1, t

    t, m, z = run("m.json", {"body_paragraphs": [P, Q], "alerta_meta": {"tema_clave": P}})
    assert t.count(P) == 1, t

    t, m, z = run("n.json", {"url": "https://x.test/very/long/path/that/exceeds/forty/chars",
                             "date": "2026-01-01", "body_text": P,
                             "pdf_links": ["https://x.test/another/long/url/file.pdf"]})
    assert "https://" not in t and "2026-01-01" not in t, t

    a = run("o.json", {"title": "R", "body_paragraphs": [P, Q]})
    b = run("o.json", {"title": "R", "body_paragraphs": [P, Q]})
    assert a[0] == b[0] and a[1] == b[1], "not reproducible"

    zh = "外空正在迅速变化。每年，越来越多、更加多元化的参与者在外空开展新颖、创新性和颠覆性的活动。他们加入了目前已在地球轨道上运行着超过1,500颗卫星的70多个国家、商业公司和国际组织。"
    t, m, z = run("p.json", {"title": "手册", "body_paragraphs": [zh]})
    assert z["tier"] == 1 and zh in t, (z, t)

    t, m, z = run("q.json", {"body_paragraphs": [P, Q], "body_text": P + "\n" + Q})
    assert "body_text" not in z["claves_texto"], z["claves_texto"]

    print("self-check OK (17 fixtures)")


# ==============================================================================
# catalog_metadata — rescata metadata por entrada de los .json de catálogo/
# registro (CATALOG_FILES) en vez de indexarlos como pseudo-documentos.
# Fusión de catalog_metadata.py (rama embedding), sin cambios de lógica.
#
# json_extract no distingue "una lista JSON de artículos reales cortos" de
# "una lista JSON de bookkeeping del scraper" (catálogo/registro): 14 fallan
# tier-3 limpiamente, otros 6 pasan tier-1 y se vuelven documentos falsos
# (JSONExtractor arriba los vacía a propósito). Cada entrada de catálogo se
# empareja con el archivo real que describe (ya presente en el corpus) y su
# metadata se adjunta al documento real vía generar_documentos, en vez de
# indexar el catálogo como su propio documento o descartarlo en silencio.
# ==============================================================================

# Los 20 catálogos/registros identificados por el audit corpus-wide: 14 que
# ya fallan tier-3 de json_extract (sin prosa de cuerpo) y 6 que se cuelan
# como pseudo-documentos en tier-1. Nada especulativo, solo nombres observados.
CATALOG_FILES = {
    "AMAZONUW_tiles-index.json", "ceeep_registro.json", "CEOBS_catalog-2.json",
    "ceobs_full_registro.json", "CSIS_catalog-2.json", "DEFENSA21_articulos-2.json",
    "DEFENSA21_catalog-2.json", "mapp_catalogo.json", "mapp_registro.json",
    "resdal_catalogo.json", "resdal_registro.json", "RUTAN_catalog-2.json",
    "SIPRI_catalog-2.json", "sipri_full_registro.json",
    "DAIO_catalog-2.json", "MAPPOEA_mapp-catalog.json", "RESDAL_catalog-2.json",
    "ceeep_catalogo.json", "ceobs_full_catalogo.json", "sipri_full_catalogo.json",
}

# Prioridad: se prefiere un campo de filename explícito, si no se deriva de
# cualquier campo con forma de URL que tenga la entrada.
CATALOG_FILENAME_FIELDS = ["filename", "url_pdf", "pdf_url", "pdf", "json", "url"]

# Último recurso cuando la entrada no nombra ningún archivo (ej. CEEEP: solo
# "url"/"pdf" con ids numéricos de CMS sin relación al archivo real). El
# nombre real es el título del artículo, slugificado y truncado -- substring
# literal del título normalizado, solo le falta el prefijo fuente/issue --
# así que longest-common-substring contra el título gana en vez de adivinar
# ese prefijo por familia de fuente.
CATALOG_TITLE_FIELDS = ["titulo", "title"]
CATALOG_MIN_TITLE_MATCH = 20  # chars normalizados; overlaps cortos son coincidencia, no hit real


def _catalog_norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _catalog_basename(value):
    """Extrae un nombre de archivo desnudo de un filename plano o una URL/ruta."""
    if not isinstance(value, str) or not value.strip():
        return None
    tail = unquote(urlparse(value).path or value).rsplit("/", 1)[-1]
    return tail if "." in tail else None


def _catalog_candidates(entry):
    """Pares (basename, extension) a probar, en orden de prioridad."""
    out = []
    for field in CATALOG_FILENAME_FIELDS:
        v = entry.get(field)
        values = v if isinstance(v, list) else [v]
        for item in values:
            b = _catalog_basename(item)
            if b:
                out.append((b, Path(b).suffix.lower()))
    return out


def build_catalog_file_index(root):
    """stem-normalizado -> [Path, ...], agrupado por extensión para matching seguro."""
    index = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            index.setdefault(p.suffix.lower(), {}).setdefault(_catalog_norm(p.stem), []).append(p)
    return index


def _catalog_title_match(entry, root, index):
    title = next((entry[f] for f in CATALOG_TITLE_FIELDS if entry.get(f)), None)
    if not isinstance(title, str):
        return None
    slug = _catalog_norm(title)
    best, best_len = None, CATALOG_MIN_TITLE_MATCH - 1
    for stem, paths in index.get(".json", {}).items():
        m = difflib.SequenceMatcher(None, stem, slug).find_longest_match(0, len(stem), 0, len(slug))
        if m.size > best_len:
            best, best_len = paths[0], m.size
    return str(best.relative_to(root)).replace("\\", "/") if best else None


def match_catalog_entry(entry, root, index):
    for basename, ext in _catalog_candidates(entry):
        by_ext = index.get(ext)
        if not by_ext:
            continue
        key = _catalog_norm(Path(basename).stem)
        if len(key) < 6:  # muy corto para confiar en un substring match
            continue
        if key in by_ext:
            hit = by_ext[key][0]
        else:
            hit = next((paths[0] for stem, paths in by_ext.items()
                        if key in stem or stem in key), None)
        if hit:
            return str(hit.relative_to(root)).replace("\\", "/")
    return _catalog_title_match(entry, root, index)


def _procesar_catalogo(root, catalog_path, index):
    entries = json.loads(Path(catalog_path).read_text(encoding="utf-8-sig"))
    if isinstance(entries, dict):
        entries = next((v for v in entries.values() if isinstance(v, list)), [])
    matched, unmatched = [], []
    for e in entries:
        if not isinstance(e, dict):
            unmatched.append(e)
            continue
        fuente = match_catalog_entry(e, root, index)
        row = {"catalogo": Path(catalog_path).name, "fuente": fuente, "metadata_catalogo": e}
        (matched if fuente else unmatched).append(row)
    return matched, unmatched


def _indexar_metadata_catalogo(input_dir: Path) -> dict:
    """fuente (relativa, '/') -> lista de entradas de catálogo que la describen.

    Entradas sin match (URL muerta, nunca descargada) no se pierden en
    silencio: quedan fuera del mapa, pero el catálogo que las contiene se
    sigue registrando como documento propio (texto_limpio="") en
    generar_documentos, así que no desaparecen del corpus."""
    encontrados = sorted(p for p in input_dir.rglob("*.json") if p.name in CATALOG_FILES)
    if not encontrados:
        return {}

    index = build_catalog_file_index(input_dir)
    mapa: dict = defaultdict(list)
    for p in encontrados:
        matched, _unmatched = _procesar_catalogo(input_dir, p, index)
        for row in matched:
            mapa[row["fuente"]].append(row["metadata_catalogo"])
    return dict(mapa)


def _catalog_selfcheck(tmp):
    tmp = Path(tmp)
    (tmp / "RESDAL" / "pdfs").mkdir(parents=True)
    (tmp / "RESDAL" / "pdfs" / "RESDAL_01-esp-el-marco-legal.pdf").write_bytes(b"%PDF-1.4 stub")
    (tmp / "RESDAL" / "catalog").mkdir()
    (tmp / "CEEEP" / "articulos").mkdir(parents=True)
    (tmp / "CEEEP" / "articulos" / "CEEEP_issue10-55-las-consecuencias-que-se-derivan.json").write_text(
        json.dumps({"body_text": "x"}), encoding="utf-8")
    (tmp / "CEEEP" / "catalog").mkdir()
    ceeep_catalog = [{"titulo": "Las consecuencias que se derivan de un evento cualquiera",
                       "url": "https://x.test/article/view/95", "pdf": "https://x.test/article/download/95/338"}]
    (tmp / "CEEEP" / "catalog" / "ceeep_catalogo.json").write_text(json.dumps(ceeep_catalog), encoding="utf-8")

    catalog = [
        {"title": "Atlas 2024 ESP - Cap. 1: El Marco Legal", "year": "2024",
         "filename": "01_ESP_El_Marco_Legal.pdf", "url": "https://x.test/01_ESP_El_Marco_Legal.pdf",
         "status": 200},
        {"title": "Dead link entry", "filename": "never-downloaded.pdf", "status": 404},
    ]
    catalog_path = tmp / "RESDAL" / "catalog" / "RESDAL_catalog-2.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    mapa = _indexar_metadata_catalogo(tmp)
    assert mapa["RESDAL/pdfs/RESDAL_01-esp-el-marco-legal.pdf"][0]["year"] == "2024", mapa
    assert mapa["CEEEP/articulos/CEEEP_issue10-55-las-consecuencias-que-se-derivan.json"][0]["titulo"], mapa
    assert not any("never-downloaded" in k for k in mapa), mapa  # dead link: sin match, no en el mapa

    # el catálogo mismo se registra como documento con texto vacío, no se pierde
    doc_catalogo = next(d for d in generar_documentos(tmp, str(tmp / "reg.json")) if d["fuente"].endswith("catalog-2.json"))
    assert doc_catalogo["texto_limpio"] == "", doc_catalogo
    doc_real = next(d for d in generar_documentos(tmp, str(tmp / "reg.json"))
                     if d["fuente"].replace("\\", "/") == "CEEEP/articulos/CEEEP_issue10-55-las-consecuencias-que-se-derivan.json")
    assert doc_real["metadata_catalogo"][0]["titulo"], doc_real

    print("self-check OK (catalog: filename match, title-fallback match, dead-link non-match, doc attach)")


def main():
    """CLI: --run DIR extrae todo el corpus; --resumen LOG imprime el censo;
    sin flags corre el self-check."""
    ap = argparse.ArgumentParser(description="Pipeline de extracción self-contained (extraccion_final.py)")
    ap.add_argument("--input", help="Carpeta raíz del corpus (modo pipeline completo -> .jsonl)")
    ap.add_argument("--output", default="documentos.jsonl", help="Archivo .jsonl de salida (con --input)")
    ap.add_argument("--registry", default="doc_registry.json", help="Archivo de registro de doc_id")
    ap.add_argument("--resumen", metavar="LOG", help="Imprime el censo de esquemas JSON de un log de procedencia")
    ap.add_argument("--run-json", metavar="DIR", help="Extrae solo los .json bajo DIR, escribiendo un log de procedencia")
    ap.add_argument("--log", default="arch_test/data/extraccion_json.jsonl")
    args = ap.parse_args()

    if args.resumen:
        resumen_json(args.resumen)
        return

    if args.run_json:
        files = sorted(Path(args.run_json).rglob("*.json"))
        log = Path(args.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        n_ok = 0
        with log.open("w", encoding="utf-8") as fh:
            for p in files:
                try:
                    texto, meta, traza = extract_json(p)
                    traza["n_meta"] = len(meta)
                    n_ok += 1
                except JsonSinTexto as e:
                    traza = e.traza
                except JsonIlegible as e:
                    traza = {"archivo": p.name, "tier": 0, "carga": "error",
                             "claves_texto": [], "claves_meta": [],
                             "claves_raiz": [], "advertencias": [e.detalle]}
                fh.write(json.dumps(traza, ensure_ascii=False) + "\n")
        print(f"archivos: {len(files)}  con texto: {n_ok}  log: {log}")
        return

    if args.input:
        import time

        errores = []
        n_ok, n_vacios = 0, 0
        t0 = time.time()
        with open(args.output, "w", encoding="utf-8") as out_f:
            for doc in generar_documentos(
                args.input, args.registry,
                on_error=lambda fuente, e: errores.append((fuente, str(e))),
            ):
                out_f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                n_ok += 1
                if not doc["texto_limpio"]:
                    n_vacios += 1
        print(f"Documentos generados         : {n_ok}")
        print(f"  ...sin señal (texto vacío) : {n_vacios}")
        print(f"Errores de extracción        : {len(errores)}")
        for fuente, error in errores:
            print(f"  - {fuente}: {error}")
        print(f"Tiempo total                 : {time.time() - t0:.0f}s")
        print(f"Salida                       : {args.output}")
        return

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        _json_selfcheck(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        _catalog_selfcheck(tmp)


if __name__ == "__main__":
    main()
