"""
CLI de conveniencia sobre el pipeline unificado (extraccion/).

El pipeline en sí (`extraccion.generar_documentos`) es un generador que vive
100% en memoria: la capa de chunking puede consumirlo directamente, documento
por documento, sin pasar por disco. Este script solo lo recorre y vuelca la
salida a un .jsonl para inspección/depuración manual.
"""

import argparse
import json
import time

from extraccion import generar_documentos


def main():
    parser = argparse.ArgumentParser(
        description="Corre el pipeline unificado de extracción sobre todo el "
                    "corpus (CODEFEST AD ASTRA 2026) y vuelca el resultado a .jsonl."
    )
    parser.add_argument("--input", default="CORPUS CODEFEST AD ASTRA 2026",
                         help="Carpeta raíz del corpus")
    parser.add_argument("--output", default="documentos.jsonl",
                         help="Archivo .jsonl de salida")
    parser.add_argument("--registry", default="doc_registry.json",
                         help="Archivo de registro de doc_id")
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
