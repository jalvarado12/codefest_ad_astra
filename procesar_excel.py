"""
===============================================================================
 PROCESAR_EXCEL.PY
===============================================================================
Script AUTÓNOMO que hace TODO el proceso para archivos Excel (.xlsx, .xls),
de principio a fin:

    1. Busca todos los .xlsx/.xls dentro de una carpeta (y sus
       subcarpetas, sin importar cuántos niveles tenga).
    2. Extrae el contenido de cada hoja: cada fila se convierte en texto
       como "columna: valor | columna: valor | ...".
    3. Lo limpia (quita basura, normaliza, colapsa espacios).
    4. Detecta el idioma predominante (es/en/pt).
    5. Le asigna un doc_id único e inmutable.
    6. Escribe todo a un archivo .jsonl (un documento por línea).

Es "autónomo" a propósito: no importa nada de otros scripts (ni siquiera
de procesar_pdf.py). Todo lo que necesita está en este único archivo.
Por eso vas a ver funciones de limpieza y de doc_id calcadas de los
otros scripts: es duplicación intencional, no un error — así cada
script se puede leer y correr sin depender de más piezas.

CÓMO SE USA
-----------
    python3 procesar_excel.py --input data_raw --output documentos_excel.jsonl

    --input     : carpeta raíz donde están tus Excel (busca en
                  subcarpetas también, sin importar la profundidad).
    --output    : archivo .jsonl de salida (default: documentos_excel.jsonl)
    --errors    : archivo .jsonl con los Excel que fallaron
                  (default: errores_excel.jsonl)
    --registry  : archivo donde se guardan los doc_id ya asignados
                  (default: doc_registry.json). IMPORTANTE: si también vas
                  a correr procesar_pdf.py / procesar_csv.py /
                  procesar_texto.py sobre el MISMO corpus, déjalos todos
                  apuntando al mismo --registry (el nombre por defecto ya
                  es igual en los 4 scripts) para que ningún doc_id se
                  repita entre formatos.
    --resume    : si la corrida se corta a mitad de camino, corre de
                  nuevo con esta bandera y no repite el trabajo ya hecho.

DEPENDENCIAS (instalar con pip)
--------------------------------
    pip install pandas openpyxl langdetect --break-system-packages
===============================================================================
"""

# ==============================================================================
# SECCIÓN 1: IMPORTS
# ==============================================================================
# pandas: lee el archivo Excel y lo convierte en una tabla (DataFrame) que
# podemos recorrer fila por fila. openpyxl es el "motor" que pandas usa por
# debajo para poder leer archivos .xlsx (hay que tenerlo instalado aunque
# no se importe directamente).
import pandas as pd

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


# ==============================================================================
# SECCIÓN 2: LIMPIEZA Y NORMALIZACIÓN DE TEXTO
# ==============================================================================
# (Idéntico a procesar_pdf.py: ver los comentarios allá para el detalle de
# por qué se hace cada paso. Aquí solo se repite para que este script sea
# autosuficiente.)

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
# SECCIÓN 3: EXTRACCIÓN DE TEXTO DEL EXCEL
# ==============================================================================
def _fila_a_texto(fila: dict, separador: str = " | ") -> str:
    """
    Convierte una fila (un diccionario columna -> valor) en un renglón de
    texto tipo 'columna: valor | columna: valor | ...'.

    Se omiten las celdas vacías (None, cadena vacía, o el texto literal
    "nan" que deja pandas cuando una celda no tiene dato), para no llenar
    el texto de ruido tipo "poblacion: nan".
    """
    pares = [
        f"{columna}: {valor}"
        for columna, valor in fila.items()
        if valor is not None and str(valor).strip() != "" and str(valor).lower() != "nan"
    ]
    return separador.join(pares)


def extraer_texto_excel(file_path: Path, sheet_name=0) -> tuple[str, dict]:
    """
    Lee una hoja de un archivo Excel y devuelve (texto_crudo, metadata_extra).

    Cómo funciona:
      - pd.read_excel() lee la hoja indicada (por defecto la primera,
        sheet_name=0) y la convierte en un DataFrame, con dtype=str para
        que todos los valores lleguen como texto (evita que pandas
        interprete de más, por ejemplo convirtiendo un código postal en
        número y perdiendo ceros a la izquierda).
      - fillna("") reemplaza las celdas vacías (NaN) por texto vacío.
      - to_dict(orient="records") convierte el DataFrame en una lista de
        diccionarios, uno por fila: [{"columna1": "valor", ...}, ...]
      - Cada fila se convierte a texto con _fila_a_texto() y se unen
        todas con saltos de línea.

    Cada fila queda como una unidad de texto independiente dentro del
    documento; si más adelante alguien fragmenta (chunking) este texto,
    lo natural es que cada fila termine siendo su propio fragmento.
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str)
    df = df.fillna("")

    filas_texto = [_fila_a_texto(fila) for fila in df.to_dict(orient="records")]
    texto_completo = "\n".join(f for f in filas_texto if f)

    metadata_extra = {
        "num_filas": len(df),
        "columnas": list(df.columns),
    }
    return texto_completo, metadata_extra


# ==============================================================================
# SECCIÓN 4: ASIGNACIÓN DE doc_id (registro persistente)
# ==============================================================================
# (Idéntico a procesar_pdf.py: ver los comentarios allá para el porqué.)
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
# SECCIÓN 5: INFERENCIA DEL FENÓMENO (1, 2 o 3) A PARTIR DE LA RUTA
# ==============================================================================
# Dos patrones porque en la práctica hemos visto dos convenciones:
#   1) "fenomeno_1", "Fenómeno 2", "fenomeno-3"
#   2) "F1_algo", "F2_otro", "F3_mas"  <- la que realmente usa el corpus
#      de CODEFEST (ej. "F1_IA_y_Capacidades_Estrategicas")
# Si tu corpus usa una tercera convención, agrega un patrón más a esta lista.
FENOMENO_PATTERNS = [
    re.compile(r"fen[oó]meno[\s_-]*([123])", re.IGNORECASE),
    re.compile(r"^f([123])[_\s-]", re.IGNORECASE),
]


def inferir_fenomeno(ruta_relativa: Path) -> int | None:
    """
    Recorre cada segmento de la ruta (cada nombre de carpeta) probando
    todos los patrones de FENOMENO_PATTERNS, en orden. Devuelve 1, 2 o 3
    en cuanto alguno coincide, o None si ninguno coincide en ningún
    segmento (para que quede como advertencia explícita en vez de
    asumir un valor incorrecto).
    """
    for segmento in ruta_relativa.parts:
        for patron in FENOMENO_PATTERNS:
            coincidencia = patron.search(segmento)
            if coincidencia:
                return int(coincidencia.group(1))
    return None


# ==============================================================================
# SECCIÓN 6: PIPELINE PRINCIPAL
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


def procesar_corpus_excel(input_dir: str, output_path: str, errors_path: str,
                           registry_path: str, resume: bool, progress_every: int):
    input_dir = Path(input_dir)
    registro = RegistroDocumentos(registry_path)

    ya_procesadas = _leer_fuentes_ya_procesadas(output_path) if resume else set()
    modo_salida = "a" if resume else "w"
    modo_errores = "a" if resume else "w"

    n_ok, n_err, n_saltados = 0, 0, 0
    t0 = time.time()

    # Buscamos ambas extensiones de Excel: la moderna (.xlsx) y la
    # antigua (.xls). rglob busca de forma recursiva en todas las
    # subcarpetas, sin importar la profundidad.
    archivos_excel = sorted(
        list(input_dir.rglob("*.xlsx")) + list(input_dir.rglob("*.xls"))
    )
    total = len(archivos_excel)
    print(f"Archivos Excel encontrados bajo '{input_dir}': {total}")

    with open(output_path, modo_salida, encoding="utf-8") as out_f, \
         open(errors_path, modo_errores, encoding="utf-8") as err_f:

        for i, file_path in enumerate(archivos_excel, start=1):
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
                texto_crudo, meta_extra = extraer_texto_excel(file_path)
                texto_limpio = clean_text(texto_crudo)
                idioma = detect_language(texto_limpio)
                doc_id = registro.obtener_o_crear(ruta_relativa_str)
                fenomeno = inferir_fenomeno(ruta_relativa)

                registro_json = {
                    "doc_id": doc_id,
                    "fuente": ruta_relativa_str,
                    "formato": "xlsx",
                    "fenomeno": fenomeno,
                    "idioma": idioma,
                    "num_caracteres": len(texto_limpio),
                    "texto": texto_limpio,
                    "meta_extra": meta_extra,
                }
                out_f.write(json.dumps(registro_json, ensure_ascii=False) + "\n")
                n_ok += 1

            except Exception as e:
                # Motivos típicos de fallo aquí: archivo .xls antiguo sin
                # el motor correcto instalado, archivo protegido con
                # contraseña, o un archivo con extensión .xlsx que en
                # realidad no es un Excel válido.
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
        description="Extracción y limpieza de archivos Excel (CODEFEST AD ASTRA 2026)."
    )
    parser.add_argument("--input", default="data_raw",
                         help="Carpeta raíz donde buscar archivos .xlsx/.xls (recursivo)")
    parser.add_argument("--output", default="documentos_excel.jsonl",
                         help="Archivo .jsonl de salida")
    parser.add_argument("--errors", default="errores_excel.jsonl",
                         help="Archivo .jsonl con los Excel que fallaron")
    parser.add_argument("--registry", default="doc_registry.json",
                         help="Archivo de registro de doc_id (compártelo entre "
                              "los 4 scripts para evitar IDs duplicados)")
    parser.add_argument("--resume", action="store_true",
                         help="No reprocesar archivos que ya estén en --output")
    parser.add_argument("--progress-every", type=int, default=50,
                         help="Cada cuántos archivos mostrar avance y guardar checkpoint")
    args = parser.parse_args()

    procesar_corpus_excel(
        input_dir=args.input,
        output_path=args.output,
        errors_path=args.errors,
        registry_path=args.registry,
        resume=args.resume,
        progress_every=args.progress_every,
    )
