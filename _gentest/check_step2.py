"""Step 2 + Step 1 check on the sample corpus: run the real extractor, chunk
with the real tokenizer, and assert the things the migration spec asks for.

Not a parity harness -- Step 2 deliberately changes csv/xlsx/pbf output, so
parity fails by design. This checks the properties instead.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chunker
import extraccion_final as ef

CORPUS = ROOT / "_gentest" / "corpus"

from transformers import AutoTokenizer

tok = AutoTokenizer.from_pretrained("intfloat/multilingual-e5-large",
                                    revision="3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3")
contar = lambda t: len(tok.encode(t, add_special_tokens=False))

chunker.reiniciar_contadores()

errores = []
docs = list(ef.generar_documentos(str(CORPUS), str(ROOT / "_gentest" / "doc_registry_test.json"),
                                  on_error=lambda f, e: errores.append((f, str(e)))))

print(f"documentos={len(docs)}  errores={len(errores)}")
for f, e in errores[:5]:
    print(f"   ERROR {f}: {e[:90]}")

por_formato = Counter(d["formato"] for d in docs)
print("por formato:", dict(por_formato))

vacios = [d for d in docs if not d["texto_limpio"].strip()]
print(f"documentos sin texto: {len(vacios)}")

# -- chunk everything ------------------------------------------------------
chunks = []
sin_chunks = []
for d in docs:
    res = chunker.procesar_documento(d, contar_tokens=contar)
    if res["n_chunks"] == 0:
        sin_chunks.append(d["fuente"])
    chunks.extend(res["chunks"])

print(f"chunks={len(chunks)}  documentos sin chunks={len(sin_chunks)} "
      f"(de los cuales sin texto: {sum(1 for f in sin_chunks if any(v['fuente']==f for v in vacios))})")

nt = sorted(c["num_tokens"] for c in chunks)
nw = sorted(c["n_words"] for c in chunks)


def pct(xs, p):
    return xs[min(len(xs) - 1, int(len(xs) * p))]


sobre = [n for n in nt if n > chunker.MAX_TOKENS]
print(f"tokens p50={pct(nt,.5)} p95={pct(nt,.95)} p99={pct(nt,.99)} max={nt[-1]} "
      f"| sobre cap {len(sobre)} ({100*len(sobre)/len(nt):.3f}%)")
print(f"palabras p50={pct(nw,.5)} p95={pct(nw,.95)} max={nw[-1]} "
      f"| sobre 250: {sum(1 for w in nw if w > 250)}")
print("escalera:", chunker.ESCALERA_HITS)

# per-format chunk counts, to see the csv/xlsx fix land
por_fmt_chunks = defaultdict(int)
for c in chunks:
    por_fmt_chunks[c["formato"]] += 1
print("chunks por formato:", dict(por_fmt_chunks))

# -- assertions ------------------------------------------------------------
fallos = []

if sobre:
    peor = max(chunks, key=lambda c: c["num_tokens"])
    fallos.append(f"{len(sobre)} chunks over {chunker.MAX_TOKENS} tokens "
                  f"(worst {peor['num_tokens']}t, {peor['formato']}, {peor['fuente'][:60]})")

if any(w > chunker.MAX_WORDS for w in nw):
    fallos.append(f"chunks over {chunker.MAX_WORDS} words: {sum(1 for w in nw if w > 250)}")

if any("\\" in c["fuente"] for c in chunks):
    fallos.append("non-POSIX fuente present")

if any(c.get("idioma") is None and c["formato"] == "pdf" for c in chunks[:0]):
    pass  # idioma may legitimately be None; only assert the key exists below

for c in chunks:
    faltan = chunker._TABLA_1 - set(c)
    if faltan:
        fallos.append(f"chunk {c['chunk_id']} missing {faltan}")
        break

excluidos = [d for d in docs if d["fuente"].rsplit("/", 1)[-1].lower() in ef._NO_CORPUS]
if excluidos:
    fallos.append(f"excluded files present: {[d['fuente'] for d in excluidos]}")

# every csv/xlsx must produce more than one chunk unless it is genuinely tiny
tabulares = defaultdict(list)
for c in chunks:
    if c["formato"] in ("csv", "xlsx", "pbf"):
        tabulares[c["fuente"]].append(c)
for fuente, cs in tabulares.items():
    palabras = sum(c["n_words"] for c in cs)
    if len(cs) == 1 and palabras > chunker.MAX_WORDS:
        fallos.append(f"tabular file still collapsed: {fuente} ({palabras} w in 1 chunk)")

idiomas = Counter(c.get("idioma") for c in chunks)
print("idiomas:", dict(idiomas))

catalogados = [c for c in chunks if any(k.startswith("catalogo_") for k in c)]
print(f"chunks con catalogo_*: {len(catalogados)}")

print()
if fallos:
    print("FALLA:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)

print("OK  Step 2 + Step 1 properties hold on the sample corpus")
