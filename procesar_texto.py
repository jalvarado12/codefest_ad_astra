import re
import unicodedata
from collections import Counter
from pathlib import Path
import json
import argparse
import time
import traceback

from langdetect import detect, DetectorFactory, LangDetectException
DetectorFactory.seed = 0


# LIMPIEZA Y NORMALIZACIÓN DE TEXTO

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


# EXTRACCIÓN DE TEXTO 
def extraer_texto_plano(file_path: Path) -> tuple[str, dict]:
   
    try:
        texto = file_path.read_text(encoding="utf-8")
        codificacion_usada = "utf-8"
    except UnicodeDecodeError:
        texto = file_path.read_text(encoding="latin-1")
        codificacion_usada = "latin-1 (fallback, revisar si el texto se ve bien)"

    metadata_extra = {
        "codificacion_detectada": codificacion_usada,
        "num_lineas": texto.count("\n") + 1,
    }
    return texto, metadata_extra


# SECCIÓN 4: ASIGNACIÓN DE doc_id 
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


# INFERENCIA DEL FENÓMENO (1, 2 o 3) A PARTIR DE LA RUTA

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


# PIPELINE PRINCIPAL
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


def procesar_corpus_texto(input_dir: str, output_path: str, errors_path: str,
                           registry_path: str, resume: bool, progress_every: int):
    input_dir = Path(input_dir)
    registro = RegistroDocumentos(registry_path)

    ya_procesadas = _leer_fuentes_ya_procesadas(output_path) if resume else set()
    modo_salida = "a" if resume else "w"
    modo_errores = "a" if resume else "w"

    n_ok, n_err, n_saltados = 0, 0, 0
    t0 = time.time()

    archivos_texto = sorted(
        list(input_dir.rglob("*.txt")) + list(input_dir.rglob("*.md"))
    )
    total = len(archivos_texto)
    print(f"Archivos de texto encontrados bajo '{input_dir}': {total}")

    with open(output_path, modo_salida, encoding="utf-8") as out_f, \
         open(errors_path, modo_errores, encoding="utf-8") as err_f:

        for i, file_path in enumerate(archivos_texto, start=1):
            ruta_relativa = file_path.relative_to(input_dir)
            ruta_relativa_str = str(ruta_relativa)

            if resume and ruta_relativa_str in ya_procesadas:
                n_saltados += 1
                continue

            if progress_every and i % progress_every == 0:
                elapsed = time.time() - t0
                print(f"  [{i}/{total}] ok={n_ok} errores={n_err} "
                      f"saltados={n_saltados}  ({elapsed:.0f}s)")
                out_f.flush()
                err_f.flush()
                registro.guardar()

            try:
                texto_crudo, meta_extra = extraer_texto_plano(file_path)
                texto_limpio = clean_text(texto_crudo)
                idioma = detect_language(texto_limpio)
                doc_id = registro.obtener_o_crear(ruta_relativa_str)
                fenomeno = inferir_fenomeno(ruta_relativa)

                formato = "md" if file_path.suffix.lower() == ".md" else "txt"

                registro_json = {
                    "doc_id": doc_id,
                    "fuente": ruta_relativa_str,
                    "formato": formato,
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


# LÍNEA DE COMANDOS
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extracción y limpieza de archivos de texto plano .txt/.md "
                    "(CODEFEST AD ASTRA 2026)."
    )
    parser.add_argument("--input", default="data_raw",
                         help="Carpeta raíz donde buscar archivos .txt/.md (recursivo)")
    parser.add_argument("--output", default="documentos_texto.jsonl",
                         help="Archivo .jsonl de salida")
    parser.add_argument("--errors", default="errores_texto.jsonl",
                         help="Archivo .jsonl con los archivos que fallaron")
    parser.add_argument("--registry", default="doc_registry.json",
                         help="Archivo de registro de doc_id (compártelo entre "
                              "los 4 scripts para evitar IDs duplicados)")
    parser.add_argument("--resume", action="store_true",
                         help="No reprocesar archivos que ya estén en --output")
    parser.add_argument("--progress-every", type=int, default=50,
                         help="Cada cuántos archivos mostrar avance y guardar checkpoint")
    args = parser.parse_args()

    procesar_corpus_texto(
        input_dir=args.input,
        output_path=args.output,
        errors_path=args.errors,
        registry_path=args.registry,
        resume=args.resume,
        progress_every=args.progress_every,
    )
