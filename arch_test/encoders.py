"""Encoder wrappers for architectures A through E.

Standard stack: sentence-transformers for the dense-only models
(multilingual-e5-large, all-MiniLM-L6-v2), FlagEmbedding's BGEM3FlagModel for
bge-m3. Both are the libraries each model's own card documents, so behaviour
matches the published benchmarks rather than a hand-rolled reimplementation of
pooling and the sparse head.

This targets Colab (Linux + GPU). It does NOT import on the local Windows box,
where sklearn -> pyarrow trips an Application Control policy; that is expected
and is why execution moved to Colab.

Two details the comparison hinges on, kept explicit:
  1. multilingual-e5-large's mandatory "query: " / "passage: " prefixes are
     applied by construction -- there is no code path that encodes an
     unprefixed string.
  2. BGE-M3 returns dense AND sparse from ONE forward pass
     (return_dense=True, return_sparse=True), which is exactly why architecture
     B costs ~1x architecture A instead of 2x.
"""

import time

import numpy as np

E5_MODEL = "intfloat/multilingual-e5-large"
BGE_MODEL = "BAAI/bge-m3"
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MAX_LEN = 512  # spec section 4.3 practical ceiling; e5's native ceiling too
MINILM_MAX_LEN = 256  # MiniLM's trained ceiling; see MiniLMDense


def pick_device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


class _STDense:
    """A dense sentence-transformers encoder with fixed query/passage prefixes.

    Subclasses set the model id, token ceiling and prefixes; the prefixes are
    applied by construction so no code path can encode an unprefixed string.
    """

    name = model_id = ""
    max_len = MAX_LEN
    query_prefix = passage_prefix = ""

    def __init__(self, device=None, use_fp16=True, max_len=None):
        from sentence_transformers import SentenceTransformer
        self.device = device or pick_device()
        self.model = SentenceTransformer(self.model_id, device=self.device)
        self.model.max_seq_length = max_len or self.max_len
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


class E5Dense(_STDense):
    """multilingual-e5-large, 1024-dim. Used by A and C."""

    name = "multilingual-e5-large"
    model_id = E5_MODEL
    query_prefix, passage_prefix = "query: ", "passage: "


class MiniLMDense(_STDense):
    """all-MiniLM-L6-v2, 384-dim. Used by D and E.

    Spec-valid: encoder-only (6-layer BERT distillation), Apache-2.0, on the
    Hub -- no decoder lineage, so section 4.2 is satisfied. Two properties are
    measured rather than hidden, because they are the reason it is on trial:

      1. ENGLISH-ONLY training data. The corpus is ES/EN/PT, so the Spanish and
         Portuguese chunks are out of distribution. Its tokenizer is a 30k
         English WordPiece vocab, which also shreds Spanish and Portuguese into
         far more pieces than XLM-R does.
      2. Trained at 256 tokens, not 512. The shared chunker cuts to a ~450-token
         XLM-R budget, so the back half of a full-size chunk is truncated away.
         Raising max_len to 512 is mechanically possible but off-distribution;
         the native ceiling is what gets measured, and `max_len` stays a knob.

    Both cost it recall it will not get back. It is here because it is ~10x
    cheaper and ~2.7x smaller per vector than e5-large, and that trade is worth
    a number instead of an opinion.
    """

    name = "all-MiniLM-L6-v2"
    model_id = MINILM_MODEL
    max_len = MINILM_MAX_LEN


class BGEM3:
    """BGE-M3 dense + sparse via FlagEmbedding. Used by B (both heads) and C (sparse)."""

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
        """Returns (dense [n,1024] or None, sparse list[dict tokid->weight] or None).

        Both heads come out of the same forward pass.
        """
        out = self.model.encode(list(texts), batch_size=batch_size, max_length=MAX_LEN,
                                return_dense=want_dense, return_sparse=want_sparse,
                                return_colbert_vecs=False)
        dense = out["dense_vecs"].astype("float32") if want_dense else None
        sparse = None
        if want_sparse:
            # FlagEmbedding keys lexical weights by token-id STRING; normalise to int
            # so the inverted index and the cached pickles stay type-stable.
            sparse = [{int(k): float(v) for k, v in lw.items() if float(v) > 0}
                      for lw in out["lexical_weights"]]
        return dense, sparse


def sparse_dot(q, d):
    """Lexical matching score: sum of q_w * d_w over shared token ids.

    Same definition as FlagEmbedding's compute_lexical_matching_score, computed
    here so the inverted index in harness.py and this scalar path cannot drift.
    """
    if len(q) > len(d):
        q, d = d, q
    return sum(w * d[t] for t, w in q.items() if t in d)


def _verify():
    """Environment check: both models load and produce sane output."""
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

    t0 = time.time()
    mini = MiniLMDense()
    print(f"minilm loaded in {time.time()-t0:.1f}s")
    d = mini.encode_passages(texts)
    assert d.shape == (4, 384), d.shape
    assert np.allclose(np.linalg.norm(d, axis=1), 1.0, atol=1e-2), "not L2-normalized"
    q = mini.encode_queries(["¿Qué riesgos genera la basura espacial en LEO?"])
    sims = (q @ d.T)[0]
    # NOT asserted: an English-only encoder is allowed to fail the cross-lingual
    # check here. Printing it is the point -- the number is the finding for D/E.
    print(f"minilm sims ES/EN/PT/off-topic: {np.round(sims, 3)}"
          f"{'  <-- off-topic outranks a relevant chunk' if sims[3] >= min(sims[:3]) else ''}")
    print(f"minilm tokens for the ES sentence: {mini.count_tokens(texts[0])} "
          f"(English WordPiece over Spanish)")
    print("environment check OK")


if __name__ == "__main__":
    _verify()
