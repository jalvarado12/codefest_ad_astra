"""Validate resultados.jsonl against the §9.3.1 / §9.3.2 schema, strictly.

§9.3.2: objects with missing fields, arrays of the wrong length, or fragments
over 250 words are "penalizados o descartados". So every one of those is a hard
assertion here, not a warning.

Also does the things a schema check cannot: confirms retrieval actually retrieved
something sensible, and that doc_ids/chunk_ids resolve back to the index.
"""
import json
import sys
from collections import Counter
from pathlib import Path

res_path = Path(sys.argv[1] if len(sys.argv) > 1 else "_gentest/resultados.jsonl")
meta_path = Path(sys.argv[2] if len(sys.argv) > 2 else "_gentest/metadata.jsonl")

rows = [json.loads(l) for l in res_path.read_text(encoding="utf-8").splitlines() if l.strip()]
meta = [json.loads(l) for l in meta_path.read_text(encoding="utf-8").splitlines() if l.strip()]
by_chunk = {m["chunk_id"]: m for m in meta}
known_docs = {m["doc_id"] for m in meta}

fails = []
def check(cond, msg):
    if not cond:
        fails.append(msg)

print("=== schema (§9.3.1) ===")
check(len(rows) == 50, f"expected 50 lines, got {len(rows)}")
qids = [r.get("query_id") for r in rows]
check(qids == [f"q{i:03d}" for i in range(1, 51)], "query_ids are not q001..q050 in order")

for r in rows:
    q = r.get("query_id", "?")
    check(set(r) >= {"query_id", "documents", "fragments"}, f"{q}: missing top-level fields")
    docs, frags = r.get("documents", []), r.get("fragments", [])
    check(len(docs) == 3, f"{q}: {len(docs)} documents (must be 3)")
    check(len(frags) == 10, f"{q}: {len(frags)} fragments (must be 10)")
    check([d.get("rank") for d in docs] == [1, 2, 3], f"{q}: document ranks not 1,2,3")
    check([f.get("rank") for f in frags] == list(range(1, 11)), f"{q}: fragment ranks not 1..10")
    for d in docs:
        check(set(d) >= {"rank", "doc_id"}, f"{q}: document missing fields")
    for f in frags:
        check(set(f) >= {"rank", "chunk_id", "doc_id", "text"}, f"{q}: fragment missing fields")
        n = len(f.get("text", "").split())
        check(n <= 250, f"{q} rank{f.get('rank')}: {n} words > 250")
        check("texto" not in f, f"{q}: fragment uses 'texto'; §9 Tabla 2 requires 'text'")

print(f"  lines={len(rows)}  {'OK' if not fails else f'{len(fails)} FAILURES'}")

print("\n=== traceability ===")
bad_doc = [f"{r['query_id']}:{d['doc_id']}" for r in rows for d in r["documents"]
           if d["doc_id"] not in known_docs]
bad_chunk = [f"{r['query_id']}:{f['chunk_id']}" for r in rows for f in r["fragments"]
             if f["chunk_id"] not in by_chunk]
check(not bad_doc, f"doc_ids absent from the index: {bad_doc[:5]}")
check(not bad_chunk, f"chunk_ids absent from the index: {bad_chunk[:5]}")
print(f"  unknown doc_ids={len(bad_doc)}  unknown chunk_ids={len(bad_chunk)}")

mism = []
for r in rows:
    for f in r["fragments"]:
        m = by_chunk.get(f["chunk_id"])
        if m and m["doc_id"] != f["doc_id"]:
            mism.append(f"{r['query_id']}:{f['chunk_id']}")
check(not mism, f"fragment doc_id disagrees with the index: {mism[:5]}")
print(f"  doc_id/chunk_id disagreements={len(mism)}")

print("\n=== retrieval sanity (not schema, but tells you if it actually worked) ===")
uniq_docs = {d["doc_id"] for r in rows for d in r["documents"]}
uniq_chunks = {f["chunk_id"] for r in rows for f in r["fragments"]}
print(f"  distinct documents returned across 50 queries: {len(uniq_docs)} (index has {len(known_docs)})")
print(f"  distinct chunks returned: {len(uniq_chunks)} (index has {len(by_chunk)})")
fmt = Counter(by_chunk[c]["formato"] for c in uniq_chunks if c in by_chunk)
print(f"  formats among returned fragments: {dict(fmt)}")
fen = Counter(by_chunk[c].get("fenomeno") for c in uniq_chunks if c in by_chunk)
print(f"  fenomeno among returned fragments: {dict(fen)}")

# a single document winning every query means retrieval is not discriminating
top1 = Counter(r["documents"][0]["doc_id"] for r in rows)
dom, cnt = top1.most_common(1)[0]
print(f"  most frequent rank-1 document: {dom} on {cnt}/50 queries")
if cnt > 25:
    print("    ^ WARNING: one document dominates rank 1; retrieval may not be discriminating")

wl = [len(f["text"].split()) for r in rows for f in r["fragments"]]
wl.sort()
print(f"  fragment words: min={wl[0]} p50={wl[len(wl)//2]} max={wl[-1]}")

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails[:25]:
        print("   -", f)
    sys.exit(1)
print("resultados.jsonl VALID against §9.3.1/§9.3.2")
