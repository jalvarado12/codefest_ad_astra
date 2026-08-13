"""A/B the old chunker against the new one, end to end, on CPU.

Colab is unavailable (session lost, GPU quota), so this runs the same comparison
locally on the text the last GPU run already extracted: _gentest/metadata.jsonl
holds real chunk text with real Tabla-1 fields. Document text is reconstructed by
concatenating each doc's chunks in posicion order, then chunked both ways and
encoded with the same pinned e5-large.

ANTES   = the old paragraph-granularity chunks, exactly as the last run indexed them.
DESPUES = the same text re-chunked at sentence granularity under the dual cap.

Both go through the real generador.py and the real validator, so the comparison
covers retrieval output, not just chunk statistics.
"""
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chunker

E5_ID = "intfloat/multilingual-e5-large"
E5_REV = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"

VIEJO = ROOT / "_gentest" / "metadata.jsonl"
QUERIES = ROOT / "Extracto_Preguntas_50_v2.pdf"
OUT = ROOT / "_gentest" / "ab"
OUT.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(msg, flush=True)


t0 = time.time()

log("== loading encoder (CPU) ==")
model = SentenceTransformer(E5_ID, revision=E5_REV, device="cpu")
model.max_seq_length = 512
tok = model.tokenizer
contar_tokens = lambda t: len(tok.encode(t, add_special_tokens=False))
log(f"encoder ready in {time.time()-t0:.0f}s")


# ---------------------------------------------------------------- ANTES
antes = [json.loads(l) for l in VIEJO.open(encoding="utf-8")]
log(f"ANTES chunks={len(antes)}")

# doc-level fields, taken from any chunk of that document
docs = {}
textos = defaultdict(list)
for r in sorted(antes, key=lambda r: (r["doc_id"], r["posicion"])):
    docs.setdefault(r["doc_id"], r)
    textos[r["doc_id"]].append(r["texto"])
log(f"docs={len(docs)}")


# ---------------------------------------------------------------- DESPUES
log("== re-chunking with the dual cap ==")
despues = []
for doc_id, piezas in textos.items():
    base = docs[doc_id]
    res = chunker.procesar_documento(
        {"doc_id": doc_id, "fuente": base["fuente"], "texto": "\n\n".join(piezas)},
        contar_tokens=contar_tokens)
    for c in res["chunks"]:
        despues.append({
            "doc_id": doc_id,
            "chunk_id": c["chunk_id"],
            "fuente": base["fuente"],
            "nombre_archivo": base["nombre_archivo"],
            "formato": base["formato"],
            "fenomeno": base["fenomeno"],
            "posicion": c["posicion"],
            "texto": c["text"],
            "n_words": c["n_words"],
            "title": c.get("title", ""),
        })
log(f"DESPUES chunks={len(despues)}")


def estadisticas(nombre, chunks):
    nt = sorted(contar_tokens(c["texto"]) for c in chunks)
    sobre = [n for n in nt if n > chunker.MAX_TOKENS]
    # what the encoder actually drops: everything past 512 of (prefix + content + specials)
    perdido = sum(max(0, n + 6 - 512) for n in nt)
    total = sum(nt)
    palabras = sorted(len(c["texto"].split()) for c in chunks)

    def pct(xs, p):
        return xs[min(len(xs) - 1, int(len(xs) * p))]

    log(f"{nombre:8s} n={len(chunks):5d} "
        f"tokens p50={pct(nt,.5):4d} p95={pct(nt,.95):4d} p99={pct(nt,.99):4d} max={nt[-1]:5d} "
        f"| over-cap {len(sobre):4d} ({100*len(sobre)/len(nt):5.1f}%) "
        f"| truncado {100*perdido/total:5.2f}% "
        f"| palabras p95={pct(palabras,.95):4d} max={palabras[-1]:5d} "
        f"over-250 {sum(1 for w in palabras if w > 250)}")


log("== chunk statistics ==")
estadisticas("ANTES", antes)
estadisticas("DESPUES", despues)


# ---------------------------------------------------------------- index + run
def construir(nombre, chunks):
    carpeta = OUT / nombre
    carpeta.mkdir(parents=True, exist_ok=True)

    # Encoding e5-large on CPU costs ~20 min per side. REUSE=antes,despues skips
    # any side whose index already matches the chunk count it would produce.
    if nombre in os.environ.get("REUSE", "").split(","):
        idx = carpeta / "index.faiss"
        if idx.exists() and faiss.read_index(str(idx)).ntotal == len(chunks):
            log(f"== reusing {nombre} index ({len(chunks)} chunks) ==")
            return carpeta
        log(f"== {nombre} index missing or stale, rebuilding ==")

    for c in chunks:
        c["num_tokens"] = contar_tokens(c["texto"])

    log(f"== encoding {nombre} ({len(chunks)} chunks, CPU) ==")
    t = time.time()
    vecs = model.encode(["passage: " + c["texto"] for c in chunks],
                        batch_size=8, normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False).astype("float32")
    log(f"encoded {nombre} {vecs.shape} in {time.time()-t:.0f}s")

    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    faiss.write_index(index, str(carpeta / "index.faiss"))

    with (carpeta / "metadata.jsonl").open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    log(f"{nombre} index.ntotal={index.ntotal} metadata={len(chunks)} "
        f"aligned={index.ntotal == len(chunks)}")
    return carpeta


def ejecutar(nombre, carpeta):
    salida = carpeta / "resultados.jsonl"
    log(f"== generador {nombre} ==")
    r = subprocess.run([sys.executable, str(ROOT / "generador.py"),
                        "--index", str(carpeta / "index.faiss"),
                        "--metadata", str(carpeta / "metadata.jsonl"),
                        "--queries", str(QUERIES),
                        "--out", str(salida),
                        "--k", "50"], capture_output=True, text=True)
    log(f"generador {nombre} rc={r.returncode}")
    if r.returncode != 0:
        log(f"STDERR {nombre}: {r.stderr[-2000:]}")
        return None

    v = subprocess.run([sys.executable, str(ROOT / "_gentest" / "validate_resultados.py"),
                        str(salida), str(carpeta / "metadata.jsonl")],
                       capture_output=True, text=True)
    log(f"validador {nombre} rc={v.returncode}")
    log(v.stdout.strip()[-1500:])
    if v.returncode != 0:
        log(f"VALIDADOR STDERR {nombre}: {v.stderr[-1000:]}")
    return salida


rutas = {}
for nombre, chunks in (("antes", antes), ("despues", despues)):
    rutas[nombre] = ejecutar(nombre, construir(nombre, chunks))


# ---------------------------------------------------------------- diff
if all(rutas.values()):
    log("== retrieval diff ==")
    a = {json.loads(l)["query_id"]: json.loads(l) for l in rutas["antes"].open(encoding="utf-8")}
    b = {json.loads(l)["query_id"]: json.loads(l) for l in rutas["despues"].open(encoding="utf-8")}

    docs_iguales = frags_solapan = 0
    palabras_a = palabras_b = 0
    for q in sorted(a):
        da = [d["doc_id"] for d in a[q]["documents"]]
        db = [d["doc_id"] for d in b[q]["documents"]]
        docs_iguales += da == db

        fa = {f["text"] for f in a[q]["fragments"]}
        fb = {f["text"] for f in b[q]["fragments"]}
        frags_solapan += len(fa & fb)

        palabras_a += sum(len(f["text"].split()) for f in a[q]["fragments"])
        palabras_b += sum(len(f["text"].split()) for f in b[q]["fragments"])

    n = len(a)
    log(f"queries={n}")
    log(f"top-3 documentos identicos: {docs_iguales}/{n} ({100*docs_iguales/n:.0f}%)")
    log(f"fragmentos con texto identico: {frags_solapan}/{10*n} ({100*frags_solapan/(10*n):.0f}%)")
    log(f"palabras medias por fragmento  ANTES {palabras_a/(10*n):.0f}  DESPUES {palabras_b/(10*n):.0f}")

    solapa_docs = sum(len({d["doc_id"] for d in a[q]["documents"]} &
                          {d["doc_id"] for d in b[q]["documents"]}) for q in a)
    log(f"documentos compartidos (sin orden): {solapa_docs}/{3*n} ({100*solapa_docs/(3*n):.0f}%)")

log(f"TOTAL {time.time()-t0:.0f}s")
