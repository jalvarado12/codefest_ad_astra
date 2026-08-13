"""Report the A/B between the old and new chunker from the artifacts ab_local.py
built: chunk statistics, schema validation, and how much the retrieval output
actually moved. Loads only the tokenizer, never the model, so it runs in seconds.
"""
import json
import subprocess
import sys
from pathlib import Path

from transformers import AutoTokenizer

# The validator prints section signs; the Windows console defaults to cp1252.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chunker

AB = ROOT / "_gentest" / "ab"
tok = AutoTokenizer.from_pretrained("intfloat/multilingual-e5-large",
                                    revision="3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3")
contar = lambda t: len(tok.encode(t, add_special_tokens=False))


def cargar(p):
    return [json.loads(l) for l in p.open(encoding="utf-8")]


def pct(xs, p):
    return xs[min(len(xs) - 1, int(len(xs) * p))]


print("=== chunk statistics ===")
for nombre in ("antes", "despues"):
    chunks = cargar(AB / nombre / "metadata.jsonl")
    nt = sorted(contar(c["texto"]) for c in chunks)
    sobre = [n for n in nt if n > chunker.MAX_TOKENS]
    # the encoder drops everything past 512 of (4 prefix + content + 2 special)
    perdido = sum(max(0, n + 6 - 512) for n in nt)
    palabras = sorted(len(c["texto"].split()) for c in chunks)

    print(f"{nombre:8s} n={len(chunks):5d} "
          f"tokens p50={pct(nt,.5):4d} p95={pct(nt,.95):4d} p99={pct(nt,.99):4d} max={nt[-1]:5d} "
          f"| sobre cap {len(sobre):4d} ({100*len(sobre)/len(nt):5.1f}%) "
          f"| texto truncado {100*perdido/sum(nt):5.2f}% "
          f"| palabras p95={pct(palabras,.95):4d} max={palabras[-1]:4d} "
          f"sobre-250 {sum(1 for w in palabras if w > 250)}")

print("\n=== validador §9.3.1/§9.3.2 ===")
for nombre in ("antes", "despues"):
    v = subprocess.run([sys.executable, str(ROOT / "_gentest" / "validate_resultados.py"),
                        str(AB / nombre / "resultados.jsonl"),
                        str(AB / nombre / "metadata.jsonl")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    veredicto = [l for l in v.stdout.splitlines() if "VALID" in l or "FALLA" in l or "ERROR" in l]
    print(f"{nombre:8s} rc={v.returncode}  {' | '.join(veredicto[-2:])}")
    for linea in v.stdout.splitlines():
        if "distinct" in linea or "fragment words" in linea or "rank-1" in linea:
            print(f"         {linea.strip()}")

print("\n=== retrieval diff ===")
a = {r["query_id"]: r for r in cargar(AB / "antes" / "resultados.jsonl")}
b = {r["query_id"]: r for r in cargar(AB / "despues" / "resultados.jsonl")}
n = len(a)

orden_igual = compartidos = solapan = 0
pal_a = pal_b = 0
for q in sorted(a):
    da = [d["doc_id"] for d in a[q]["documents"]]
    db = [d["doc_id"] for d in b[q]["documents"]]
    orden_igual += da == db
    compartidos += len(set(da) & set(db))

    ta = {f["text"] for f in a[q]["fragments"]}
    tb = {f["text"] for f in b[q]["fragments"]}
    solapan += len(ta & tb)

    pal_a += sum(len(f["text"].split()) for f in a[q]["fragments"])
    pal_b += sum(len(f["text"].split()) for f in b[q]["fragments"])

print(f"consultas: {n}")
print(f"top-3 identico (mismo orden):   {orden_igual}/{n} ({100*orden_igual/n:.0f}%)")
print(f"documentos compartidos:         {compartidos}/{3*n} ({100*compartidos/(3*n):.0f}%)")
print(f"fragmentos de texto identico:   {solapan}/{10*n} ({100*solapan/(10*n):.0f}%)")
print(f"palabras/fragmento  ANTES {pal_a/(10*n):.0f}   DESPUES {pal_b/(10*n):.0f}")

vacios_a = sum(1 for q in a for f in a[q]["fragments"] if f["chunk_id"] == "NONE")
vacios_b = sum(1 for q in b for f in b[q]["fragments"] if f["chunk_id"] == "NONE")
print(f"fragmentos de relleno (NONE):   ANTES {vacios_a}   DESPUES {vacios_b}")

docs_a = len({d["doc_id"] for q in a for d in a[q]["documents"]})
docs_b = len({d["doc_id"] for q in b for d in b[q]["documents"]})
chunks_a = len({f["chunk_id"] for q in a for f in a[q]["fragments"]})
chunks_b = len({f["chunk_id"] for q in b for f in b[q]["fragments"]})
print(f"cobertura: documentos distintos ANTES {docs_a} DESPUES {docs_b} "
      f"| chunks distintos ANTES {chunks_a} DESPUES {chunks_b}")
