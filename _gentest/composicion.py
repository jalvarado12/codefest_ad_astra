"""Index composition: how much of the index will tabular content be?

Debt item 1 said "index tables for now, measure the damage after the first real
run". This is that measurement, done on the tabular files alone so the ratio is
not diluted by prose.

Spec 00 §5 projected ~66,928 tabular chunks by dividing 16.73M words by 250. That
assumed the word cap binds. It usually does not: dense tabular rows hit the
506-token cap first, so a chunk holds fewer than 250 words and the real chunk
count is higher. This script measures words-per-chunk per format and reprojects.

    python composicion.py <corpus_dir> [<corpus_dir2> ...]
"""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chunker
import extraccion_final as ef

# Spec 00 §5, measured over the full corpus.
PALABRAS_CORPUS = {
    "csv": 16_523_437,
    "xlsx": 208_564,
}
# §4 extrapolations, not measurements -- carried so the shares add to something.
PALABRAS_ESTIMADAS = {
    "pdf": 9_500_000,
    "json": 1_150_000,
}


def main(directorios):
    import tempfile

    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(
        "intfloat/multilingual-e5-large",
        revision="3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3")
    contar = lambda t: len(tok.encode(t, add_special_tokens=False))

    chunker.reiniciar_contadores()

    palabras = defaultdict(int)
    chunks = defaultdict(int)
    tokens = defaultdict(list)
    por_archivo = []

    # the registry is throwaway here: this measures composition, not identity,
    # and ROOT is not writable when the script is run from /content on Colab
    registro = str(Path(tempfile.mkdtemp()) / "_reg_comp.json")

    for d in directorios:
        for doc in ef.generar_documentos(d, registro):
            res = chunker.procesar_documento(doc, contar_tokens=contar)
            f = doc["formato"]
            w = len(doc["texto_limpio"].split())
            palabras[f] += w
            chunks[f] += res["n_chunks"]
            tokens[f].extend(c["num_tokens"] for c in res["chunks"])
            por_archivo.append((doc["fuente"], f, w, res["n_chunks"]))

    print("=== por archivo ===")
    for fuente, f, w, n in sorted(por_archivo, key=lambda r: -r[2]):
        ratio = w / n if n else 0
        print(f"  {f:5s} {w:>9,} w -> {n:>5,} chunks  ({ratio:5.0f} w/chunk)  "
              f"{fuente[-58:]}")

    print("\n=== palabras por chunk, por formato ===")
    ratios = {}
    for f in sorted(palabras):
        if not chunks[f]:
            continue
        r = palabras[f] / chunks[f]
        ratios[f] = r
        ts = sorted(tokens[f])
        p50 = ts[len(ts) // 2]
        print(f"  {f:5s} {palabras[f]:>9,} w / {chunks[f]:>5,} chunks = "
              f"{r:5.1f} w/chunk   tokens p50={p50} max={ts[-1]}")

    print("\n=== reproyeccion del indice completo ===")
    print("  Spec 00 §5 asumio 250 w/chunk para tabular -> 66,928 chunks.")

    # A format whose sample is smaller than a single chunk yields a ratio that
    # measures the file, not the packing: a 15-word spreadsheet is 1 chunk at
    # "15 w/chunk", and extrapolating that to 208k words invents 14,000 chunks
    # out of nothing. Fall back to the csv ratio for those -- dense tabular rows
    # pack the same way regardless of which tool wrote them.
    fiable = {f: r for f, r in ratios.items() if chunks[f] >= 3}
    respaldo_tabular = fiable.get("csv")

    conocidos = {**PALABRAS_CORPUS}
    total_est = 0
    filas = []
    for f, w in list(conocidos.items()) + list(PALABRAS_ESTIMADAS.items()):
        r = fiable.get(f)
        medido = r is not None

        if r is None and f in PALABRAS_CORPUS and respaldo_tabular:
            r = respaldo_tabular
            medido = "csv"
        if r is None:
            r = 250.0

        n = round(w / r)
        total_est += n
        filas.append((f, w, r, n, medido))

    for f, w, r, n, medido in sorted(filas, key=lambda x: -x[3]):
        marca = ("medido" if medido is True
                 else f"ratio de {medido}" if medido
                 else "supuesto 250")
        print(f"  {f:5s} {w:>10,} w / {r:5.1f} = {n:>7,} chunks  "
              f"({100*n/total_est:4.1f}%)  [{marca}]")

    tab = sum(n for f, _, _, n, _ in filas if f in PALABRAS_CORPUS)
    print(f"\n  total proyectado : {total_est:,} chunks")
    print(f"  tabular          : {tab:,} ({100*tab/total_est:.1f}%)")
    print(f"  vs Spec 00 §5    : 66,928 tabular de ~110,000 (61%)")

    print(f"\nescalera: {dict(chunker.ESCALERA_HITS)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1:])
