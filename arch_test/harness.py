"""Shared machinery for the architecture comparison. See ARCHS for the lineup.

Each architecture has its own runnable entry point (`arch_a_e5_dense.py` and
friends); this module holds everything they share so nothing that could bias the
comparison lives in a per-architecture file.

All architectures read the SAME chunks.jsonl produced by one run of the
reference chunker, so no preprocessing difference can leak into the comparison.

Encodings are cached to disk because a full run is measured in hours on CPU and
must survive an interruption. Caching changes wall-clock, not accounting: an
architecture is charged for every model pass it needs (C = e5 + bge, E = minilm
+ bge) even when it reads both from cache, so a two-model architecture's ~2x
indexing cost cannot disappear into a caching detail. Cached-vs-fresh is
recorded per phase in timings.json so a resumed run cannot be mistaken for a
fresh measurement.

Sparse retrieval uses an inverted index (token id -> [(chunk, weight)]) scored
by dot product over shared token ids -- the same lexical-matching definition
FlagEmbedding uses for BGE-M3, computed here directly so that the sparse side
of B and C is auditable rather than a library black box.
"""

import argparse
import gc
import json
import pickle
import time
from collections import defaultdict
from pathlib import Path

import faiss
import numpy as np
import psutil

from chunker import chunk_document, split_sentences
from corpus import extract
from encoders import BGEM3, E5Dense, MiniLMDense
from metrics import evaluate_run

RRF_K0 = 60  # spec section 8.4 eq. 7 smoothing constant
TOP_K = 100  # candidate depth per retriever before fusion
DATA = Path(__file__).parent / "data"

ENCODERS = {"e5": E5Dense, "bge": BGEM3, "minilm": MiniLMDense}

# arch -> (dense encoder key, sparse encoder key or None). Sparse is always
# BGE-M3's lexical head; when it is fused, it is fused with RRF.
ARCHS = {
    "A": ("e5", None),        # multilingual-e5-large dense only
    "B": ("bge", "bge"),      # BGE-M3 dense + sparse, ONE forward pass
    "C": ("e5", "bge"),       # e5 dense + BGE-M3 sparse, two models
    "D": ("minilm", None),    # all-MiniLM-L6-v2 dense only
    "E": ("minilm", "bge"),   # MiniLM dense + BGE-M3 sparse, two models
}

try:
    import torch
    _CUDA = torch.cuda.is_available()
except ImportError:
    _CUDA = False

DEFAULT_BATCH = 32 if _CUDA else 4


def rss_mb():
    return psutil.Process().memory_info().rss / 1024 ** 2


class Phase:
    """Times a phase and records PEAK RSS over it.

    Peak matters, not start/end: a phase loads a ~2.3 GB model, encodes, then
    frees it before exiting, so an end-of-phase reading would report a few
    hundred MB and understate the real footprint by an order of magnitude. A
    background sampler polls RSS instead.
    """

    def __init__(self, log, name, cached=False, interval=0.5):
        self.log, self.name, self.cached, self.interval = log, name, cached, interval

    def _sample(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, rss_mb())
            self._stop.wait(self.interval)

    def __enter__(self):
        import threading
        gc.collect()
        if _CUDA:
            import torch
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
        self.t0, self.rss0 = time.perf_counter(), rss_mb()
        self.peak = self.rss0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.peak = max(self.peak, rss_mb())
        self._stop.set()
        self._thread.join(timeout=2)
        entry = {
            "seconds": round(time.perf_counter() - self.t0, 2),
            "rss_start_mb": round(self.rss0, 1),
            "rss_peak_mb": round(self.peak, 1),
            "rss_end_mb": round(rss_mb(), 1),
            "from_cache": self.cached,
        }
        if _CUDA:
            import torch
            entry["gpu_peak_mb"] = round(torch.cuda.max_memory_allocated() / 1024 ** 2, 1)
        self.log[self.name] = entry


# ---------------------------------------------------------------- chunking

def build_chunks(sample_path=DATA / "sample.json", out=DATA / "chunks.jsonl"):
    """One canonical chunk set for all three architectures."""
    sample = json.loads(Path(sample_path).read_text(encoding="utf-8"))
    e5 = E5Dense()  # its tokenizer defines the token budget; both models use XLM-R vocab
    rows, failed = [], []
    for doc in sample:
        try:
            text, _ = extract(doc["path"])
        except Exception as e:
            failed.append((doc["fuente"], str(e)))
            continue
        for c in chunk_document(text, doc["doc_id"], doc["fuente"], doc["formato"],
                                doc["fenomeno"], e5.count_tokens):
            c["idioma"] = doc["idioma"]
            rows.append(c)
    del e5
    gc.collect()
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if failed:
        (DATA / "chunking_failures.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")
    print(f"{len(rows)} chunks from {len(sample) - len(failed)} docs "
          f"({len(failed)} unreadable), median {np.median([r['num_tokens'] for r in rows]):.0f} tokens")
    return rows


def load_chunks(path=DATA / "chunks.jsonl"):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------- encoding

def encode_cached(key, texts, timings, batch_size=DEFAULT_BATCH):
    """One model's pass over every chunk, cached to disk. Returns (dense, sparse|None).

    This is where essentially all the compute goes. BGE-M3 yields dense AND
    sparse from the same pass, which is why B costs ~1x A rather than 2x.
    """
    dpath, spath = DATA / f"{key}_dense.npy", DATA / f"{key}_sparse.pkl"
    wants_sparse = key == "bge"

    if dpath.exists() and (spath.exists() or not wants_sparse):
        with Phase(timings, f"encode_{key}", cached=True):
            dense = np.load(dpath)
            sparse = pickle.loads(spath.read_bytes()) if wants_sparse else None
        assert len(dense) == len(texts), f"cached {key} encodings do not match current chunk set"
        return dense, sparse

    with Phase(timings, f"encode_{key}"):
        model = ENCODERS[key]()
        if wants_sparse:
            dense, sparse = model.encode(texts, batch_size=batch_size)
        else:
            dense, sparse = model.encode_passages(texts, batch_size=batch_size), None
        del model
        gc.collect()
    np.save(dpath, dense)
    if wants_sparse:
        spath.write_bytes(pickle.dumps(sparse))
    return dense, sparse


def encode_all(chunks, timings, batch_size=DEFAULT_BATCH, keys=None):
    """Encode every chunk with each requested model. Returns {key: (dense, sparse)}."""
    texts = [c["texto"] for c in chunks]
    return {k: encode_cached(k, texts, timings, batch_size)
            for k in (keys or list(ENCODERS))}


def encode_for(arch, chunks, timings, batch_size=DEFAULT_BATCH):
    """Encode only the models architecture `arch` actually needs."""
    return encode_all(chunks, timings, batch_size,
                      keys=list(dict.fromkeys(k for k in ARCHS[arch] if k)))


# ---------------------------------------------------------------- retrieval

def build_faiss(vectors):
    """IndexFlatIP over L2-normalized vectors == exact cosine (spec sections 5.2, 8.2)."""
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


class SparseIndex:
    """Inverted index over BGE-M3 lexical weights."""

    def __init__(self, sparse_vectors):
        self.n = len(sparse_vectors)
        self.postings = defaultdict(list)
        for i, vec in enumerate(sparse_vectors):
            for tid, w in vec.items():
                self.postings[tid].append((i, w))

    def search(self, query_vec, k):
        scores = defaultdict(float)
        for tid, qw in query_vec.items():
            for i, dw in self.postings.get(tid, ()):
                scores[i] += qw * dw
        top = sorted(scores.items(), key=lambda x: -x[1])[:k]
        return [i for i, _ in top], [s for _, s in top]

    def nbytes(self):
        return sum(len(v) for v in self.postings.values()) * 12  # int32 id + float32 w + overhead


def rrf(rank_lists, k0=RRF_K0):
    """Spec section 8.4 eq. 7: score = sum_j 1 / (k0 + rank_j), ranks start at 1."""
    fused = defaultdict(float)
    for ranked in rank_lists:
        for rank, item in enumerate(ranked, start=1):
            fused[item] += 1.0 / (k0 + rank)
    return [i for i, _ in sorted(fused.items(), key=lambda x: (-x[1], x[0]))]


# ---------------------------------------------------------------- output shaping

def to_fragments(chunk_indices, chunks, limit=10, max_words=250):
    """Spec section 9.2/9.2.1: 10 fragments, each <= 250 words, sentence-complete.

    An oversized chunk is split into sub-fragments that keep the ORIGINAL
    chunk_id (traceability, not matching) and each take their own rank slot.
    """
    out = []
    for idx in chunk_indices:
        c = chunks[idx]
        words = c["texto"].split()
        pieces = [c["texto"]]
        if len(words) > max_words:
            pieces, buf, n = [], [], 0
            for sent in split_sentences(c["texto"]):
                sn = len(sent.split())
                if buf and n + sn > max_words:
                    pieces.append(" ".join(buf))
                    buf, n = [], 0
                buf.append(sent)
                n += sn
            if buf:
                pieces.append(" ".join(buf))
        for piece in pieces:
            out.append({"rank": len(out) + 1, "chunk_id": c["chunk_id"],
                        "doc_id": c["doc_id"], "fuente": c["fuente"],
                        "text": " ".join(piece.split()[:max_words])})
            if len(out) == limit:
                return out
    return out


def to_documents(chunk_indices, chunks, scores=None, limit=3):
    """Max-pooling aggregation (spec section 8.6), keyed on `fuente` per section 10.2.1.

    Always emits exactly `limit` documents. If the candidate pool contains fewer
    distinct documents than that, it is padded with unseen documents from the
    corpus: the spec rejects a documents array whose length is not 3, and F1@3
    divides by 3 regardless, so padding can only help and never costs precision.
    """
    best = {}
    for pos, idx in enumerate(chunk_indices):
        c = chunks[idx]
        s = -pos if scores is None else scores[pos]  # rank-based fallback
        if c["fuente"] not in best or s > best[c["fuente"]][0]:
            best[c["fuente"]] = (s, c["doc_id"])
    ranked = sorted(best.items(), key=lambda x: -x[1][0])[:limit]
    out = [{"rank": i, "doc_id": v[1], "fuente": f} for i, (f, v) in enumerate(ranked, start=1)]
    if len(out) < limit:
        seen = {d["fuente"] for d in out}
        for c in chunks:
            if c["fuente"] not in seen:
                seen.add(c["fuente"])
                out.append({"rank": len(out) + 1, "doc_id": c["doc_id"], "fuente": c["fuente"]})
                if len(out) == limit:
                    break
    return out


# ---------------------------------------------------------------- architectures

def run_architecture(arch, queries, chunks, enc, timings):
    """Run one architecture over the query set; returns results + per-query latency.

    `enc` is {key: (dense, sparse)} from encode_all/encode_for and must contain
    at least the keys ARCHS[arch] names.
    """
    dense_key, sparse_key = ARCHS[arch]
    dense = enc[dense_key][0]
    sparse = enc[sparse_key][1] if sparse_key else None

    with Phase(timings, f"{arch}_index"):
        index = build_faiss(dense)
        sparse_index = SparseIndex(sparse) if sparse_key else None
    index_bytes = dense.nbytes + (sparse_index.nbytes() if sparse_index else 0)

    # Query-side models: exactly the ones the index was built from. A two-model
    # architecture pays to keep both resident, which is part of what is measured.
    models = {k: ENCODERS[k]() for k in dict.fromkeys(k for k in (dense_key, sparse_key) if k)}
    results, latencies = {}, []
    for q in queries:
        t0 = time.perf_counter()
        if dense_key == "bge":
            qd, qs = models["bge"].encode([q["texto"]])  # both heads, one pass
        else:
            qd = models[dense_key].encode_queries([q["texto"]], batch_size=1)
            qs = models["bge"].encode([q["texto"]], want_dense=False)[1] if sparse_key else None
        _, idx = index.search(qd, TOP_K)
        order = [int(i) for i in idx[0] if i >= 0]
        if sparse_index is not None:
            order = rrf([order, sparse_index.search(qs[0], TOP_K)[0]])
        latencies.append(time.perf_counter() - t0)
        results[q["query_id"]] = _shape(order, chunks)

    models.clear()
    gc.collect()
    timings[f"{arch}_query_latency_s"] = {
        "mean": round(float(np.mean(latencies)), 3),
        "p50": round(float(np.percentile(latencies, 50)), 3),
        "max": round(float(np.max(latencies)), 3),
        "n_queries": len(latencies),
    }
    timings[f"{arch}_index_bytes"] = int(index_bytes)
    return results


def _shape(order, chunks):
    return {"documents": to_documents(order, chunks), "fragments": to_fragments(order, chunks)}


def indexing_cost(arch, timings):
    """Full model-pass cost attributable to an architecture, per the report's rule.

    A two-model architecture (C = e5 + bge, E = minilm + bge) pays for BOTH
    forward passes even though this harness caches and reuses the single-model
    outputs -- otherwise its headline drawback (~2x indexing compute) would be
    hidden by an implementation detail.
    """
    keys = {k for k in ARCHS[arch] if k}
    passes = sum(timings[f"encode_{k}"]["seconds"] for k in keys)
    return round(passes + timings.get(f"{arch}_index", {}).get("seconds", 0.0), 2)


# ---------------------------------------------------------------- scoring

def load_confirmed(path):
    """Load the validation set, refusing anything a human has not confirmed."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"{p} not found. Scoring requires the HUMAN-CONFIRMED validation "
                         f"set; DRAFT judgments must never be scored against.")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("status") != "CONFIRMED":
        raise SystemExit(f"REFUSED: status is {doc.get('status')!r}, not CONFIRMED. The systems "
                         f"under test help produce the draft, so scoring it unreviewed is circular.")
    return doc["queries"]


def to_qrels(queries):
    return {q["query_id"]: {"fragments": {r["chunk_id"]: r["relevancia"] for r in q["relevantes"]},
                            "documents": q["documentos_relevantes"]} for q in queries}


def run_and_score(arch, queries, chunks, enc, timings, qrels=None):
    """Run one architecture, write results_<arch>.jsonl, return its summary row."""
    res = run_architecture(arch, queries, chunks, enc, timings)
    (DATA / f"results_{arch}.jsonl").write_text(
        "\n".join(json.dumps({"query_id": qid, **r}, ensure_ascii=False)
                  for qid, r in res.items()), encoding="utf-8")
    timings[f"{arch}_indexing_cost_s"] = indexing_cost(arch, timings)
    row = {"indexing_s": timings[f"{arch}_indexing_cost_s"],
           "index_bytes": timings[f"{arch}_index_bytes"],
           "query_latency_s": timings[f"{arch}_query_latency_s"]}
    if qrels:
        run = {qid: {"fragments": [f["chunk_id"] for f in r["fragments"]],
                     "documents": [d["fuente"] for d in r["documents"]]} for qid, r in res.items()}
        ndcg, f1, rows = evaluate_run(run, qrels)
        row |= {"ndcg@10": round(ndcg, 4), "f1@3": round(f1, 4), "per_query": rows}
        print(f"{arch}: NDCG@10={ndcg:.4f}  F1@3={f1:.4f}  "
              f"indexing {row['indexing_s']}s  query {row['query_latency_s']['mean']}s mean")
    else:
        print(f"{arch}: indexing {row['indexing_s']}s, "
              f"query {row['query_latency_s']['mean']}s mean (unscored -- no confirmed set)")
    return row


def merge_summary(arch, row):
    """Fold one architecture's row into data/summary.json without clobbering the rest."""
    path = DATA / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    summary[arch] = row
    path.write_text(json.dumps(summary, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- entry point

def cli(arch, argv=None):
    """Shared main() for the per-architecture scripts. See arch_a_e5_dense.py."""
    dense_key, sparse_key = ARCHS[arch]
    ap = argparse.ArgumentParser(description=f"architecture {arch}: dense={dense_key} "
                                             f"sparse={sparse_key or 'none'}")
    ap.add_argument("--chunk", action="store_true", help="(re)build chunks.jsonl first")
    ap.add_argument("--encode-only", action="store_true", help="stop after the model pass")
    ap.add_argument("--queries", default=str(DATA / "validation_confirmed.json"))
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--unscored", action="store_true",
                    help="produce results without a confirmed validation set (no metrics)")
    args = ap.parse_args(argv)

    DATA.mkdir(parents=True, exist_ok=True)
    if args.chunk:
        build_chunks()
    chunks = load_chunks()

    timings = {}
    enc = encode_for(arch, chunks, timings, args.batch_size)
    (DATA / f"timings_encode_{arch}.json").write_text(json.dumps(timings, indent=1))
    if args.encode_only:
        return

    queries = ([{"query_id": q["query_id"], "texto": q["texto"]}
                for q in json.loads(Path(args.queries).read_text(encoding="utf-8"))["queries"]]
               if args.unscored else load_confirmed(args.queries))
    row = run_and_score(arch, queries, chunks, enc, timings,
                        None if args.unscored else to_qrels(queries))
    (DATA / f"timings_{arch}.json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
    merge_summary(arch, row)


# ---------------------------------------------------------------- self-check

def _selftest():
    """Wiring check for all five architectures. Stub encoders, real everything else.

    Catches the failure this refactor can actually produce: an architecture
    silently retrieving with the wrong model, skipping its sparse half, or being
    charged the wrong set of forward passes. Runs in a second, needs no GPU and
    no model download -- the stubs give each model a distinct vector width, so a
    mis-wired dense_key shows up as the wrong index size.
    """
    import shutil
    import tempfile
    global DATA

    class _StubDense:
        dim = 8

        def __init__(self, *a, **k):
            pass

        def _vecs(self, texts):
            # one-hot on the leading "temaN" token: query temaN retrieves chunk temaN
            v = np.zeros((len(texts), self.dim), "float32")
            for i, t in enumerate(texts):
                v[i, int(t.split()[0][4:]) % self.dim] = 1.0
            return v

        encode_passages = encode_queries = lambda self, texts, batch_size=8: self._vecs(texts)

    class _StubE5(_StubDense):
        dim = 8

    class _StubMiniLM(_StubDense):
        dim = 4

    class _StubBGE(_StubDense):
        dim = 6

        def encode(self, texts, batch_size=8, want_dense=True, want_sparse=True):
            dense = self._vecs(texts) if want_dense else None
            sparse = ([{hash(w) % 1000: 1.0 for w in t.split()} for t in texts]
                      if want_sparse else None)
            return dense, sparse

    chunks = [{"doc_id": f"DOC-{i % 3}", "chunk_id": f"c{i}", "fuente": f"f{i % 3}.pdf",
               "texto": f"tema{i % 4} " + f"oración {i} de relleno. " * (300 if i == 0 else 3)}
              for i in range(12)]
    queries = [{"query_id": "q1", "texto": "tema2 consulta"}]

    saved_encoders, saved_data = dict(ENCODERS), DATA
    ENCODERS.update(e5=_StubE5, bge=_StubBGE, minilm=_StubMiniLM)
    DATA = Path(tempfile.mkdtemp())
    try:
        for arch in ARCHS:
            dense_key, sparse_key = ARCHS[arch]
            timings = {}
            enc = encode_for(arch, chunks, timings, batch_size=4)
            res = run_architecture(arch, queries, chunks, enc, timings)["q1"]

            assert len(res["documents"]) == 3, (arch, len(res["documents"]))
            assert len(res["fragments"]) == 10, (arch, len(res["fragments"]))
            assert [f["rank"] for f in res["fragments"]] == list(range(1, 11)), arch
            assert all(len(f["text"].split()) <= 250 for f in res["fragments"]), arch
            assert res["fragments"][0]["text"].startswith("tema2"), (arch, "wrong top hit")

            # index size proves WHICH dense model was used and whether sparse was fused
            dense_bytes = len(chunks) * ENCODERS[dense_key].dim * 4
            got = timings[f"{arch}_index_bytes"]
            if sparse_key:
                assert got > dense_bytes, (arch, "sparse half missing from the index")
            else:
                assert got == dense_bytes, (arch, got, dense_bytes)

            # cost attribution: charged for every model pass, cached or not
            timings.update({f"encode_{k}": {"seconds": s}
                            for k, s in (("e5", 100.0), ("bge", 10.0), ("minilm", 1.0))})
            timings[f"{arch}_index"] = {"seconds": 0.0}
            want = {"A": 100.0, "B": 10.0, "C": 110.0, "D": 1.0, "E": 11.0}[arch]
            assert indexing_cost(arch, timings) == want, (arch, indexing_cost(arch, timings), want)
            print(f"{arch}: dense={dense_key} sparse={sparse_key or 'none'} "
                  f"index={got}B cost={want}s  OK")
    finally:
        ENCODERS.clear()
        ENCODERS.update(saved_encoders)
        shutil.rmtree(DATA, ignore_errors=True)
        DATA = saved_data
    print("harness wiring OK")


if __name__ == "__main__":
    _selftest()
