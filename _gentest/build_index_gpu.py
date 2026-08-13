"""Build a real FAISS index from the sample corpus, on GPU, to test generador.py.

This is a PROTOTYPE of pipeline_final.py Step 4, not the real thing: it exists
to produce genuine artifacts (index.faiss + metadata.jsonl) so generador.py can
be exercised end to end against real text and real e5-large vectors.

It fills in the Tabla-1 fields the current chunker does not yet emit
(doc_id/fuente/formato/fenomeno/num_tokens/texto), which is exactly the gap
Spec 02 Step 1 fix 1 describes. num_tokens is stamped here, at embed time,
with the live tokenizer -- per the spec's design.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/content")

import faiss
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

import chunker
import extraccion_final as ef

CORPUS = Path("/content/gentest/corpus")
OUTDIR = Path("/content/entrega/base_vectorial/encoder_multilingual-e5-large")
OUTDIR.mkdir(parents=True, exist_ok=True)

E5_ID = "intfloat/multilingual-e5-large"
E5_REV = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"

t0 = time.time()
print("== extracting ==", flush=True)
docs, errores = [], []
for d in ef.generar_documentos(str(CORPUS), "/content/doc_registry.json",
                               on_error=lambda f, e: errores.append((f, str(e)))):
    docs.append(d)
    print(f"   {d['formato']:7s} {d['fuente'][:64]:66s} {len(d['texto_limpio'].split()):7d}w", flush=True)
print(f"docs={len(docs)} errores={len(errores)} in {time.time()-t0:.0f}s", flush=True)

print("== loading encoder ==", flush=True)
dev = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(E5_ID, revision=E5_REV, device=dev)
model.max_seq_length = 512
if dev == "cuda":
    model = model.half()
tok = model.tokenizer

# Raw content tokens: no "passage: " prefix, no special tokens. chunker.MAX_TOKENS
# already reserves the 6 tokens those add.
contar_tokens = lambda t: len(tok.encode(t, add_special_tokens=False))

print("== chunking ==", flush=True)
chunks = []
for d in docs:
    if not d["texto_limpio"].strip():
        continue
    res = chunker.procesar_documento({"doc_id": d["doc_id"], "fuente": d["fuente"],
                                      "texto": d["texto_limpio"]},
                                     contar_tokens=contar_tokens)
    for c in res["chunks"]:
        chunks.append({
            "doc_id": d["doc_id"],
            "chunk_id": c["chunk_id"],
            "fuente": d["fuente"],
            "nombre_archivo": d["fuente"].rsplit("/", 1)[-1],
            "formato": d["formato"],
            "fenomeno": d["fenomeno"] if d["fenomeno"] is not None else 0,
            "posicion": c["posicion"],
            "texto": c["text"],
            "n_words": c["n_words"],
            "title": c.get("title", ""),
        })
print(f"chunks={len(chunks)} from {sum(1 for d in docs if d['texto_limpio'].strip())} non-empty docs", flush=True)
over = [c for c in chunks if c["n_words"] > 250]
print(f"chunks over 250 words: {len(over)} (max {max((c['n_words'] for c in chunks), default=0)})", flush=True)

print("== encoding on GPU ==", flush=True)
for c in chunks:                      # num_tokens with the real tokenizer
    c["num_tokens"] = contar_tokens(c["texto"])

t1 = time.time()
vecs = model.encode(["passage: " + c["texto"] for c in chunks],
                    batch_size=64, normalize_embeddings=True,
                    convert_to_numpy=True, show_progress_bar=False).astype("float32")
print(f"encoded {vecs.shape} in {time.time()-t1:.0f}s on {dev}", flush=True)

nt = sorted(c["num_tokens"] for c in chunks)
assert nt, "no chunks were produced - aborting before FAISS"
def pct(p):
    if not nt: return 0
    return nt[min(len(nt) - 1, int(len(nt) * p))]
print(f"num_tokens p50={pct(.5)} p95={pct(.95)} p99={pct(.99)} max={nt[-1]}"
      f"  (cap {chunker.MAX_TOKENS} = 512 - 4 prefix - 2 special)", flush=True)
sobre = [c for c in chunks if c["num_tokens"] > chunker.MAX_TOKENS]
print(f"chunks over the token cap: {len(sobre)} ({100*len(sobre)/len(chunks):.3f}%)"
      f" -- escalera residue, emitted intact per Sec. 3.3", flush=True)
for c in sobre[:10]:
    print(f"   {c['num_tokens']:6d}t {c['n_words']:5d}w {c['formato']:6s} {c['chunk_id']}", flush=True)

print("== FAISS ==", flush=True)
index = faiss.IndexFlatIP(vecs.shape[1])
index.add(vecs)
faiss.write_index(index, str(OUTDIR / "index.faiss"))

with (OUTDIR / "metadata.jsonl").open("w", encoding="utf-8") as fh:
    for c in chunks:
        fh.write(json.dumps(c, ensure_ascii=False) + "\n")
np.save("/content/dense.npy", vecs)
Path("/content/ids.json").write_text(
    json.dumps({"ids": [c["chunk_id"] for c in chunks]}, ensure_ascii=False), encoding="utf-8")

print(f"index.ntotal={index.ntotal}  metadata lines={len(chunks)}  aligned={index.ntotal==len(chunks)}", flush=True)
print(f"TOTAL {time.time()-t0:.0f}s", flush=True)
