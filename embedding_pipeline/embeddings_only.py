"""Self-contained embedding-only step: text -> dense/sparse vectors, for
multilingual-e5-large and BAAI/bge-m3 (architectures A/B/C's two models).

Deliberately does NOT include chunking, FAISS/sparse indexing, RRF fusion, or
NDCG/F1 scoring -- see embedding_pipeline.py in this same directory (kept on
the `embedding` branch, not pushed to master) for the full pipeline with
those pieces and its test suite. This file is the minimal, embeddings-only
cut: point it at already-chunked text and it gives you vectors back, nothing
more.

Two details the comparison this was ported from hinges on, kept explicit:
  1. multilingual-e5-large's mandatory "query: " / "passage: " prefixes are
     applied by construction -- there is no code path that encodes an
     unprefixed string.
  2. BGE-M3 returns dense AND sparse from ONE forward pass
     (return_dense=True, return_sparse=True).

Input: a JSONL file, one record per line, each with a text field (default
key "texto"; override with --text-field). Typically a chunks.jsonl produced
by a chunker elsewhere in the pipeline -- any {"texto": "..."} records work.

Runs on Colab (Linux + GPU) or any Linux/macOS box with the two libraries
installed. Does NOT import on a local Windows box: sentence-transformers/
FlagEmbedding pull in pyarrow, which trips a Windows Application Control
policy (`DLL load failed while importing lib`). `python embeddings_only.py
selftest` is the one subcommand that runs anywhere -- stub encoders, no ML
library touched, no model download.

Usage:
    python embeddings_only.py selftest
    python embeddings_only.py verify
    python embeddings_only.py encode --model e5 --mode passage \
        --in chunks.jsonl --out-dir out/
    python embeddings_only.py encode --model bge --in chunks.jsonl --out-dir out/
"""

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np

E5_MODEL = "intfloat/multilingual-e5-large"
BGE_MODEL = "BAAI/bge-m3"
MAX_LEN = 512  # spec section 4.3 practical ceiling; e5's native ceiling too


def pick_device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


class E5Dense:
    """multilingual-e5-large, 1024-dim."""

    name = "multilingual-e5-large"
    query_prefix, passage_prefix = "query: ", "passage: "

    def __init__(self, device=None, use_fp16=True, max_len=MAX_LEN):
        from sentence_transformers import SentenceTransformer
        self.device = device or pick_device()
        self.model = SentenceTransformer(E5_MODEL, device=self.device)
        self.model.max_seq_length = max_len
        if use_fp16 and self.device == "cuda":
            self.model = self.model.half()
        self.tok = self.model.tokenizer

    def count_tokens(self, text):
        return len(self.tok.encode(text, add_special_tokens=False))

    def _encode(self, texts, prefix, batch_size):
        # normalize_embeddings=True -> cosine == inner product (spec section 8.2)
        vecs = self.model.encode([prefix + t for t in texts], batch_size=batch_size,
                                 normalize_embeddings=True, convert_to_numpy=True,
                                 show_progress_bar=False)
        return vecs.astype("float32")

    def encode_passages(self, texts, batch_size=8):
        return self._encode(texts, self.passage_prefix, batch_size)

    def encode_queries(self, texts, batch_size=8):
        return self._encode(texts, self.query_prefix, batch_size)


class BGEM3:
    """BGE-M3 dense + sparse via FlagEmbedding. Both heads, one forward pass."""

    name = "bge-m3"

    def __init__(self, device=None, use_fp16=True):
        from FlagEmbedding import BGEM3FlagModel
        self.device = device or pick_device()
        self.model = BGEM3FlagModel(BGE_MODEL, use_fp16=use_fp16 and self.device == "cuda",
                                    devices=self.device)
        self.tok = self.model.tokenizer

    def count_tokens(self, text):
        return len(self.tok.encode(text, add_special_tokens=False))

    def encode(self, texts, batch_size=8, want_dense=True, want_sparse=True):
        """Returns (dense [n,1024] or None, sparse list[dict tokid->weight] or None)."""
        out = self.model.encode(list(texts), batch_size=batch_size, max_length=MAX_LEN,
                                return_dense=want_dense, return_sparse=want_sparse,
                                return_colbert_vecs=False)
        dense = out["dense_vecs"].astype("float32") if want_dense else None
        sparse = None
        if want_sparse:
            # FlagEmbedding keys lexical weights by token-id STRING; normalise to int
            # so downstream consumers (an inverted index, a cached pickle) stay type-stable.
            sparse = [{int(k): float(v) for k, v in lw.items() if float(v) > 0}
                      for lw in out["lexical_weights"]]
        return dense, sparse


ENCODERS = {"e5": E5Dense, "bge": BGEM3}


def sparse_dot(q, d):
    """Lexical matching score: sum of q_w * d_w over shared token ids."""
    if len(q) > len(d):
        q, d = d, q
    return sum(w * d[t] for t, w in q.items() if t in d)


# ------------------------------------------------------------------ I/O ---

def load_records(path, text_field="texto"):
    """Read a JSONL (or JSON array) of records; return (texts, records)."""
    path = Path(path)
    if path.suffix == ".jsonl":
        records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        records = json.loads(path.read_text(encoding="utf-8"))
    texts = [r[text_field] for r in records]
    return texts, records


def encode_texts(model_key, texts, mode="passage", batch_size=8):
    """Encode a list of texts with one model. Returns (dense, sparse|None)."""
    model = ENCODERS[model_key]()
    try:
        if model_key == "bge":
            dense, sparse = model.encode(texts, batch_size=batch_size)
        else:
            call = model.encode_queries if mode == "query" else model.encode_passages
            dense, sparse = call(texts, batch_size=batch_size), None
    finally:
        del model
    return dense, sparse


def write_vectors(out_dir, model_key, dense, sparse):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dense_path = out_dir / f"{model_key}_dense.npy"
    np.save(dense_path, dense)
    paths = {"dense": str(dense_path)}
    if sparse is not None:
        sparse_path = out_dir / f"{model_key}_sparse.pkl"
        sparse_path.write_bytes(pickle.dumps(sparse))
        paths["sparse"] = str(sparse_path)
    return paths


# ----------------------------------------------------------------- CLI ---

def cmd_encode(argv=None):
    ap = argparse.ArgumentParser(description="Encode a JSONL of texts into dense (+sparse) vectors.")
    ap.add_argument("--model", choices=list(ENCODERS), required=True)
    ap.add_argument("--mode", choices=["passage", "query"], default="passage",
                    help="e5 only; ignored for bge (both heads share one pass)")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--text-field", default="texto")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--batch-size", type=int, default=8)
    args = ap.parse_args(argv)

    texts, _ = load_records(args.in_path, args.text_field)
    t0 = time.time()
    dense, sparse = encode_texts(args.model, texts, args.mode, args.batch_size)
    paths = write_vectors(args.out_dir, args.model, dense, sparse)
    print(f"{args.model}: encoded {len(texts)} texts in {time.time()-t0:.1f}s -> {paths}")


# ------------------------------------------------------------ self-check --

def _selftest():
    """Wiring check: no GPU, no model download, runs anywhere.

    Confirms the CLI's read -> encode -> write round-trip and the e5 prefix
    contract, using stub encoders with a distinct vector width per model so a
    mis-wired --model flag would show up as the wrong shape.
    """
    import shutil
    import tempfile

    class _StubDense:
        dim = 8

        def __init__(self, *a, **k):
            pass

        def _vecs(self, texts):
            v = np.zeros((len(texts), self.dim), "float32")
            for i in range(len(texts)):
                v[i, i % self.dim] = 1.0
            return v

        encode_passages = encode_queries = lambda self, texts, batch_size=8: self._vecs(texts)

    class _StubE5(_StubDense):
        dim = 8

    class _StubBGE(_StubDense):
        dim = 6

        def encode(self, texts, batch_size=8, want_dense=True, want_sparse=True):
            dense = self._vecs(texts) if want_dense else None
            sparse = ([{hash(w) % 1000: 1.0 for w in t.split()} for t in texts]
                      if want_sparse else None)
            return dense, sparse

    tmp = Path(tempfile.mkdtemp())
    saved = dict(ENCODERS)
    ENCODERS.update(e5=_StubE5, bge=_StubBGE)
    try:
        records_path = tmp / "chunks.jsonl"
        records_path.write_text(
            "\n".join(json.dumps({"texto": f"oracion numero {i} de prueba."}) for i in range(5)),
            encoding="utf-8")

        for model_key, dim in (("e5", 8), ("bge", 6)):
            out_dir = tmp / f"out_{model_key}"
            cmd_encode(["--model", model_key, "--in", str(records_path),
                       "--out-dir", str(out_dir), "--batch-size", "2"])
            dense = np.load(out_dir / f"{model_key}_dense.npy")
            assert dense.shape == (5, dim), (model_key, dense.shape)
            assert dense.dtype == np.float32, (model_key, dense.dtype)
            sparse_path = out_dir / f"{model_key}_sparse.pkl"
            if model_key == "bge":
                assert sparse_path.exists()
                sparse = pickle.loads(sparse_path.read_bytes())
                assert len(sparse) == 5 and all(len(s) > 0 for s in sparse), model_key
            else:
                assert not sparse_path.exists(), "e5 must not write a sparse file"

        # spec-mandated prefixes, checked against the REAL classes (not swapped)
        assert saved["e5"].query_prefix == "query: ", "e5 query prefix drifted from spec"
        assert saved["e5"].passage_prefix == "passage: ", "e5 passage prefix drifted from spec"

        # sparse_dot: shared-key dot product, order-independent
        assert sparse_dot({1: 2.0, 2: 1.0}, {2: 3.0, 3: 5.0}) == 3.0
        assert sparse_dot({2: 3.0, 3: 5.0}, {1: 2.0, 2: 1.0}) == 3.0
    finally:
        ENCODERS.clear()
        ENCODERS.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    print("embeddings_only wiring OK (e5, bge)")


def _verify():
    """Environment check: both real models load and produce sane output.

    Needs the actual libraries and downloads real weights -- unlike
    selftest(), this does not run on the local Windows box.
    """
    import torch
    print(f"device: {pick_device()}  torch {torch.__version__}  "
          f"gpu: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'}")

    texts = ["La órbita baja terrestre está congestionada por desechos espaciales.",
             "Low Earth orbit is congested with space debris.",
             "A órbita baixa da Terra está congestionada por detritos espaciais.",
             "El presupuesto de café de la oficina aumentó un 3% este trimestre."]

    t0 = time.time()
    e5 = E5Dense()
    print(f"e5-large loaded in {time.time()-t0:.1f}s")
    d = e5.encode_passages(texts)
    assert d.shape == (4, 1024), d.shape
    assert np.allclose(np.linalg.norm(d, axis=1), 1.0, atol=1e-2), "not L2-normalized"
    q = e5.encode_queries(["¿Qué riesgos genera la basura espacial en LEO?"])
    sims = (q @ d.T)[0]
    assert sims[3] < min(sims[:3]), f"e5 cross-lingual sanity failed: {sims}"
    print(f"e5 sims ES/EN/PT/off-topic: {np.round(sims, 3)}")
    del e5

    t0 = time.time()
    bge = BGEM3()
    print(f"bge-m3 loaded in {time.time()-t0:.1f}s")
    dense, sparse = bge.encode(texts)
    assert dense.shape == (4, 1024), dense.shape
    assert np.allclose(np.linalg.norm(dense, axis=1), 1.0, atol=1e-2)
    assert all(len(s) > 0 for s in sparse), "empty sparse output"
    qd, qs = bge.encode(["¿Qué riesgos genera la basura espacial en LEO?"])
    dsims = (qd @ dense.T)[0]
    lex = [sparse_dot(qs[0], s) for s in sparse]
    assert dsims[3] < min(dsims[:3]), f"bge dense sanity failed: {dsims}"
    assert lex[0] == max(lex), f"bge sparse lexical sanity failed: {lex}"
    print(f"bge dense sims: {np.round(dsims, 3)}")
    print(f"bge sparse sims: {[round(x, 4) for x in lex]}  (nnz={[len(s) for s in sparse]})")
    del bge
    print("environment check OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["encode", "selftest", "verify"])
    args, rest = ap.parse_known_args()

    if args.command == "selftest":
        _selftest()
    elif args.command == "verify":
        _verify()
    else:
        cmd_encode(rest)


if __name__ == "__main__":
    main()
