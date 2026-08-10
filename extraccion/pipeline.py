"""
Orquestador del pipeline de extracción.

`generar_documentos()` es un GENERADOR: recorre el corpus, despacha cada
archivo al adaptador correspondiente (patrón Adapter, ver adaptadores.py),
limpia el texto y hace `yield` de un diccionario con el esquema ESTRICTO que
consumirá la capa de chunking — sin escribir nada a disco ni depender del
formato original:

    {
        "doc_id": str,
        "fuente": str,
        "formato": str,        # "pdf", "html", "json", "csv", "pbf", ...
        "fenomeno": int | None,
        "texto_limpio": str,   # "" si el archivo no aporta señal semántica
    }

Un tileset PBF es la única excepción a "un archivo = un documento": vive
repartido en muchos .pbf (uno por zoom/tile), así que se agrupa por carpeta
antes de despachar (ver _agrupar_tilesets_pbf) y se trata como un solo
documento.
"""

from collections import defaultdict
from pathlib import Path
from typing import Iterator

from .adaptadores import (
    CSVExtractor,
    ExcelExtractor,
    ExtractorBase,
    HTMLExtractor,
    ImagenExtractor,
    JSONExtractor,
    PBFExtractor,
    PDFExtractor,
    TextoExtractor,
)
from .registro import RegistroDocumentos, inferir_fenomeno
from .texto_utils import clean_text

# Adaptador -> extensiones que maneja. El formato reportado en la salida se
# deriva de la extensión (xlsx/xls -> "xlsx", txt/md conservan su propio
# nombre, etc.).
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
    """Un tileset PBF cuelga de 'tiles/<z>/<x>/archivo.pbf'; su raíz es la
    carpeta que contiene 'tiles' (el dataset). Si no hay carpeta 'tiles' en
    la ruta, cada carpeta de .pbf sueltos se trata como su propio tileset."""
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
                 texto_crudo: str, ruta_relativa: Path) -> dict:
    return {
        "doc_id": registro.obtener_o_crear(fuente),
        "fuente": fuente,
        "formato": formato,
        "fenomeno": inferir_fenomeno(ruta_relativa),
        "texto_limpio": clean_text(texto_crudo),
    }


def generar_documentos(input_dir: str, registry_path: str = "doc_registry.json",
                        on_error=None) -> Iterator[dict]:
    """Generador ciego al formato: recorre `input_dir` y hace yield de un
    documento estandarizado por cada archivo/tileset soportado.

    `on_error(fuente, excepcion)` es opcional; si se pasa, se invoca cuando
    un archivo falla en vez de propagar la excepción (el documento se sigue
    entregando, con texto_limpio="").
    """
    input_dir = Path(input_dir)
    registro = RegistroDocumentos(registry_path)
    pbf_extractor = PBFExtractor()

    # 1. Tilesets PBF, agrupados (un tileset completo = un documento).
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

    # 2. Resto de archivos soportados, uno por uno.
    for file_path in sorted(input_dir.rglob("*")):
        if not file_path.is_file() or file_path in archivos_pbf_ya_procesados:
            continue

        ext = file_path.suffix.lower()
        adaptador = _DISPATCH.get(ext)
        if adaptador is None:
            continue  # formato no soportado todavía: se ignora, no se rompe el pipeline

        ruta_relativa = file_path.relative_to(input_dir)
        fuente = str(ruta_relativa)
        try:
            texto_crudo = adaptador.extraer(file_path)
        except Exception as e:
            if on_error:
                on_error(fuente, e)
            texto_crudo = ""

        yield _empaquetar(registro, fuente, _FORMATO_POR_EXT[ext], texto_crudo, ruta_relativa)

    registro.guardar()
