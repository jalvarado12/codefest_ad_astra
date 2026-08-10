import re
import json
import argparse
import unicodedata
import traceback
from collections import Counter, defaultdict
from pathlib import Path

import mapbox_vector_tile

from langdetect import detect, DetectorFactory, LangDetectException
DetectorFactory.seed = 0


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
# EXTRACCIÓN DE TEXTO DE TILESETS PBF (Mapbox Vector Tile)
# ==============================================================================
#
# Un tileset PBF no es un único archivo: es una carpeta con la estructura
# .../tiles/<z>/<x>/<nombre>_<y>.pbf, donde el mismo elemento del mapa
# (un municipio, una zona) aparece repetido en cada nivel de zoom <z> y en
# cada tile <x>/<y> que lo cruza. Por eso el "documento" real no es el
# archivo .pbf individual, sino el tileset completo (la carpeta "tiles" y
# todo lo que cuelga de ella): se decodifican TODOS los .pbf del tileset,
# se recorren sus capas y, dentro de cada capa, sus elementos, y se
# deduplican por id de elemento para quedarnos con una sola versión de
# cada uno.


def _localizar_raiz_tileset(pbf_path: Path, input_dir: Path) -> Path:
    """Encuentra la carpeta que agrupa un tileset completo.

    Si el .pbf cuelga de una carpeta 'tiles/<z>/<x>/archivo.pbf', la raíz del
    tileset es la carpeta que contiene 'tiles' (el dataset). Si no existe una
    carpeta 'tiles' en la ruta, se usa la carpeta inmediata del archivo como
    raíz (cada carpeta de .pbf sueltos se trata como su propio tileset).
    """
    for ancestro in pbf_path.parents:
        if ancestro == input_dir:
            break
        if ancestro.name.lower() == "tiles":
            return ancestro.parent
    return pbf_path.parent


def _elemento_a_texto(capa: str, atributos: dict, separador: str = " | ") -> str:
    """Convierte los atributos de un elemento en 'atributo: valor | ...',
    omitiendo valores vacíos."""
    pares = [f"capa: {capa}"] + [
        f"{atributo}: {valor}"
        for atributo, valor in atributos.items()
        if valor is not None and str(valor).strip() != ""
    ]
    return separador.join(pares)


def extraer_texto_tileset(tileset_root: Path, archivos_pbf: list[Path]) -> tuple[str, dict]:
    """Decodifica todos los .pbf de un tileset, recorre sus capas y elementos,
    y devuelve el texto deduplicado (una línea por elemento único) junto con
    metadata del proceso."""

    # elementos_unicos[(capa, id_elemento)] = texto ya generado para ese elemento
    elementos_unicos: dict[tuple, str] = {}
    conteo_por_capa = Counter()
    zoom_levels = set()
    archivos_con_error = []

    for pbf_path in sorted(archivos_pbf):
        try:
            zoom_levels.add(pbf_path.relative_to(tileset_root).parts[1])  # tiles/<z>/...
        except (IndexError, ValueError):
            pass

        try:
            datos_crudos = pbf_path.read_bytes()
            tile = mapbox_vector_tile.decode(datos_crudos)
        except Exception as e:
            archivos_con_error.append(f"{pbf_path.name}: {e}")
            continue

        for nombre_capa, capa in tile.items():
            for elemento in capa.get("features", []):
                atributos = elemento.get("properties", {})
                # Identificador estable del elemento dentro de la capa: se
                # prioriza un campo 'fid'/'id' propio de los atributos (el
                # mismo en todos los niveles de zoom); si no existe, se cae
                # a los propios atributos como huella única.
                id_elemento = (
                    atributos.get("fid")
                    or atributos.get("id")
                    or atributos.get("FID")
                    or tuple(sorted(atributos.items(), key=lambda kv: kv[0]))
                )
                clave = (nombre_capa, id_elemento)
                if clave in elementos_unicos:
                    continue
                elementos_unicos[clave] = _elemento_a_texto(nombre_capa, atributos)
                conteo_por_capa[nombre_capa] += 1

    texto_completo = "\n".join(elementos_unicos.values())
    metadata_extra = {
        "num_archivos_pbf": len(archivos_pbf),
        "niveles_zoom": sorted(zoom_levels, key=lambda z: int(z) if z.isdigit() else z),
        "capas": dict(conteo_por_capa),
        "num_elementos_unicos": len(elementos_unicos),
        "archivos_con_error": archivos_con_error,
    }
    return texto_completo, metadata_extra


# ==============================================================================
# ASIGNACIÓN DE doc_id
# ==============================================================================
class RegistroDocumentos:
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


# ==============================================================================
# INFERENCIA DEL FENÓMENO (1, 2 o 3) A PARTIR DE LA RUTA
# ==============================================================================
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
# PIPELINE PRINCIPAL
# ==============================================================================
def _leer_fuentes_ya_procesadas(output_path: str) -> set:
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


def _agrupar_tilesets(input_dir: Path) -> dict[Path, list[Path]]:
    """Agrupa todos los .pbf del corpus por la raíz de su tileset."""
    grupos: dict[Path, list[Path]] = defaultdict(list)
    for pbf_path in input_dir.rglob("*.pbf"):
        raiz = _localizar_raiz_tileset(pbf_path, input_dir)
        grupos[raiz].append(pbf_path)
    return grupos


def procesar_corpus_pbf(input_dir: str, output_path: str, errors_path: str,
                         registry_path: str, resume: bool, progress_every: int):
    input_dir = Path(input_dir)
    registro = RegistroDocumentos(registry_path)

    ya_procesadas = _leer_fuentes_ya_procesadas(output_path) if resume else set()
    modo_salida = "a" if resume else "w"
    modo_errores = "a" if resume else "w"

    n_ok, n_err, n_saltados = 0, 0, 0

    tilesets = _agrupar_tilesets(input_dir)
    total = len(tilesets)
    print(f"Tilesets PBF encontrados bajo '{input_dir}': {total} "
          f"({sum(len(v) for v in tilesets.values())} archivos .pbf en total)")

    with open(output_path, modo_salida, encoding="utf-8") as out_f, \
         open(errors_path, modo_errores, encoding="utf-8") as err_f:

        for i, (tileset_root, archivos_pbf) in enumerate(sorted(tilesets.items()), start=1):
            ruta_relativa = tileset_root.relative_to(input_dir)
            ruta_relativa_str = str(ruta_relativa)

            if resume and ruta_relativa_str in ya_procesadas:
                n_saltados += 1
                continue

            if progress_every and i % progress_every == 0:
                print(f"  [{i}/{total}] ok={n_ok} errores={n_err} saltados={n_saltados}")
                out_f.flush()
                err_f.flush()
                registro.guardar()

            try:
                texto_crudo, meta_extra = extraer_texto_tileset(tileset_root, archivos_pbf)
                texto_limpio = clean_text(texto_crudo)
                idioma = detect_language(texto_limpio)
                doc_id = registro.obtener_o_crear(ruta_relativa_str)
                fenomeno = inferir_fenomeno(ruta_relativa)

                registro_json = {
                    "doc_id": doc_id,
                    "fuente": ruta_relativa_str,
                    "formato": "pbf",
                    "fenomeno": fenomeno,
                    "idioma": idioma,
                    "num_caracteres": len(texto_limpio),
                    "texto": texto_limpio,
                    "meta_extra": meta_extra,
                }
                out_f.write(json.dumps(registro_json, ensure_ascii=False) + "\n")
                n_ok += 1

            except Exception as e:
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
# LÍNEA DE COMANDOS
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extracción y limpieza de tilesets PBF (Mapbox Vector Tile) "
                    "(CODEFEST AD ASTRA 2026)."
    )
    parser.add_argument("--input", default="data_raw",
                         help="Carpeta raíz donde buscar archivos .pbf (recursivo)")
    parser.add_argument("--output", default="documentos_pbf.jsonl",
                         help="Archivo .jsonl de salida")
    parser.add_argument("--errors", default="errores_pbf.jsonl",
                         help="Archivo .jsonl con los tilesets que fallaron")
    parser.add_argument("--registry", default="doc_registry.json",
                         help="Archivo de registro de doc_id (compártelo entre "
                              "los scripts procesar_*.py para evitar IDs duplicados)")
    parser.add_argument("--resume", action="store_true",
                         help="No reprocesar tilesets que ya estén en --output")
    parser.add_argument("--progress-every", type=int, default=10,
                         help="Cada cuántos tilesets mostrar avance y guardar checkpoint")
    args = parser.parse_args()

    procesar_corpus_pbf(
        input_dir=args.input,
        output_path=args.output,
        errors_path=args.errors,
        registry_path=args.registry,
        resume=args.resume,
        progress_every=args.progress_every,
    )
