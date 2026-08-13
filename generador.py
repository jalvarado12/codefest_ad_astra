"""generador.py -- CODEFEST AD ASTRA 2026, Etapa 1.

Standalone retrieval script (spec Sec. 1.4, 9). Reads a FAISS index +
metadata.jsonl, encodes the evaluation queries with the real
intfloat/multilingual-e5-large, retrieves, aggregates to 3 documents / 10
fragments per query with max-pooling, and writes resultados.jsonl.

Self-contained by design: entrega/ ships only this file plus artifacts
(index.faiss, metadata.jsonl), so this file imports nothing from the rest of
the repo -- "Si no es posible reproducir los resultados, se excluira de la
evaluacion" (Sec. 1.4). The ~30-line E5 wrapper below is the one piece that
would otherwise live in embedding_pipeline/embeddings_only.py; it is
duplicated here on purpose.

Usage:
    python generador.py --index base_vectorial/encoder_multilingual-e5-large/index.faiss \\
        --metadata base_vectorial/encoder_multilingual-e5-large/metadata.jsonl \\
        --queries Extracto_Preguntas_50_v2.pdf --out entrega/resultados.jsonl \\
        --k 50 --threshold -1.0
    python generador.py selftest
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import faiss
import numpy as np

# --------------------------------------------------------------- constants --

# Pinned so a re-run reproduces the same weights even if the HF repo moves on
# (Sec. 1.4 makes reproduction a pass/fail gate). Verified via the HF resolve
# API's X-Repo-Commit header for "main" on 2026-08-12:
#   curl -sI https://huggingface.co/intfloat/multilingual-e5-large/resolve/main/config.json
E5_MODEL_ID = "intfloat/multilingual-e5-large"
E5_MODEL_REVISION = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"
MAX_LEN = 512  # Sec. 4.3 practical ceiling; e5's native ceiling too

DEFAULT_K = 50
DEFAULT_THRESHOLD = -1.0  # cosine on normalised vectors spans [-1, 1]; 0.0 is a filter, not "off"
FRAGMENT_WORD_LIMIT = 250
N_DOCUMENTS = 3
N_FRAGMENTS = 10


# ------------------------------------------------------------- E5 wrapper --

class E5Dense:
    """intfloat/multilingual-e5-large, 1024-dim, inlined so this file has zero
    dependency on the rest of the repo. GPU -> fp16, CPU -> fp32 (unchanged).

    encode_queries applies the mandatory "query: " prefix; encode_passages
    applies "passage: ". Mixing them up degrades every result silently, so
    generar() below only ever calls encode_queries.
    """

    query_prefix, passage_prefix = "query: ", "passage: "

    def __init__(self, device=None, max_len=MAX_LEN):
        import torch
        from sentence_transformers import SentenceTransformer

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = SentenceTransformer(E5_MODEL_ID, revision=E5_MODEL_REVISION,
                                          device=self.device)
        self.model.max_seq_length = max_len
        if self.device == "cuda":
            self.model = self.model.half()  # fp16 on GPU; fp32 default on CPU

    def _encode(self, texts, prefix, batch_size):
        # normalize_embeddings=True -> cosine == inner product (Sec. 8.2)
        vecs = self.model.encode([prefix + t for t in texts], batch_size=batch_size,
                                  normalize_embeddings=True, convert_to_numpy=True,
                                  show_progress_bar=False)
        return vecs.astype("float32")

    def encode_queries(self, texts, batch_size=32):
        return self._encode(texts, self.query_prefix, batch_size)

    def encode_passages(self, texts, batch_size=64):
        return self._encode(texts, self.passage_prefix, batch_size)


# ---------------------------------------------------------- query loading --
# Isolated so a different delivered query format costs one edit here, not a
# rewrite of generar().

def cargar_consultas(path):
    """Load evaluation queries. Returns [(query_id, text), ...] sorted
    q001..q050. Asserts exactly 50, no duplicates, ids are q001-q050."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        queries = _cargar_consultas_pdf(path)
    elif suffix == ".jsonl":
        queries = _cargar_consultas_jsonl(path)
    elif suffix == ".csv":
        queries = _cargar_consultas_csv(path)
    else:
        raise ValueError(f"formato de consultas no soportado: {suffix}")

    ids = [qid for qid, _ in queries]
    assert len(queries) == 50, f"se esperaban 50 consultas, se obtuvieron {len(queries)}"
    assert len(set(ids)) == 50, "ids de consulta duplicados"
    esperados = {f"q{i:03d}" for i in range(1, 51)}
    assert set(ids) == esperados, f"ids distintos de q001..q050: {set(ids) ^ esperados}"
    queries.sort(key=lambda t: t[0])
    return queries


def _cargar_consultas_pdf(path):
    """PDF: PyMuPDF text, regex ^(qNNN)\\s+, join continuation lines until the
    next qNNN marker (format: 'qNNN <question wrapped across lines>')."""
    import pymupdf

    doc = pymupdf.open(str(path))
    full_text = "".join(page.get_text() for page in doc)
    doc.close()
    marks = list(re.finditer(r"^(q\d{3})\s+", full_text, re.MULTILINE))
    queries = []
    for i, m in enumerate(marks):
        qid = m.group(1)
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(full_text)
        text = " ".join(full_text[start:end].split())  # collapse wraps/whitespace
        queries.append((qid, text))
    return queries


def _cargar_consultas_jsonl(path):
    queries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        qid = rec.get("query_id") or rec.get("id") or rec.get("qid")
        text = rec.get("text") or rec.get("query") or rec.get("pregunta")
        queries.append((qid, text))
    return queries


def _cargar_consultas_csv(path):
    queries = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            qid = row.get("query_id") or row.get("id") or row.get("qid")
            text = row.get("text") or row.get("query") or row.get("pregunta")
            queries.append((qid, text))
    return queries


# --------------------------------------------------------- metadata loading --

def cargar_metadata(path):
    """metadata.jsonl as a POSITIONAL list -- list index i IS the FAISS
    internal id i (Sec. 1.4's ordering invariant). Never key this by
    chunk_id; that discards the invariant metadata.jsonl was generated to
    preserve."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if records:
        # cheap sanity check against the field-name trap (Tabla 1 vs Tabla 2)
        assert "texto" in records[0], "metadata.jsonl debe usar la clave 'texto' (Tabla 1)"
        assert "text" not in records[0], "metadata.jsonl no debe usar 'text' (esa es de resultados.jsonl)"
    return records


# ------------------------------------------------------ sentence splitting --
# Sec. 9.2.1: an oversized chunk must be split at sentence boundaries, never
# mid-sentence. After the chunker's own MAX_WORDS=250 budget this is mostly a
# pass-through; this is the residue path. Ported verbatim from
# origin/embedding:arch_test/chunker.py's split_sentences() (ES/EN/PT
# terminators + abbreviation list, already tested there) rather than a naive
# ". " split, so "Sr. Perez dijo..." is not cut after "Sr.".

_SENT_END = re.compile(
    r"""(?<=[.!?…])            # terminator
        ["'»”’\)\]]*  # optional closers
        \s+                         # the break itself
        (?=["'«“¿¡(\[—-]?[A-ZÀ-Ü0-9])""",
    re.VERBOSE,
)

_ABBREV = {
    "sr", "sra", "srta", "dr", "dra", "prof", "ing", "lic", "gral", "cnel",
    "ee", "uu", "ee.uu", "etc", "vs", "art", "num", "no", "pag", "pp", "fig",
    "cap", "aprox", "mr", "mrs", "ms", "jr", "st", "inc", "ltd",
    "eg", "ie", "cf", "al", "ed", "eds", "vol", "min", "max", "seg",
}


def _split_sentences(text):
    parts, start = [], 0
    for m in _SENT_END.finditer(text):
        cut = m.start()
        head = text[start:cut]
        tail_word = re.split(r"[\s(\[]", head.strip())[-1].rstrip(".!?…").lower()
        if tail_word in _ABBREV or (len(tail_word) == 1 and tail_word.isalpha()):
            continue
        if head.strip():
            parts.append(head.strip())
        start = m.end()
    rest = text[start:].strip()
    if rest:
        parts.append(rest)
    return parts if parts else [text]


def _split_to_word_limit(text, limit=FRAGMENT_WORD_LIMIT):
    """Split text into pieces of <= limit words, cutting only at sentence
    boundaries. A single sentence longer than the limit on its own is a data
    problem (per the chunker's own ruling): hard-cut it and log."""
    if len(text.split()) <= limit:
        return [text]

    pieces, cur, cur_n = [], [], 0
    for sent in _split_sentences(text):
        n = len(sent.split())
        if n > limit:
            if cur:
                pieces.append(" ".join(cur))
                cur, cur_n = [], 0
            words = sent.split()
            for i in range(0, len(words), limit):
                pieces.append(" ".join(words[i:i + limit]))
            print(f"ADVERTENCIA: oracion de {n} palabras excede el limite de {limit}; "
                  f"corte duro aplicado", file=sys.stderr)
            continue
        if cur_n + n > limit:
            pieces.append(" ".join(cur))
            cur, cur_n = [sent], n
        else:
            cur.append(sent)
            cur_n += n
    if cur:
        pieces.append(" ".join(cur))
    return pieces


# -------------------------------------------------------------- retrieval --

def _hits(index, meta, qvec, k):
    """index.search + the -1 guard. FAISS returns -1 when fewer than k
    neighbours exist; meta[-1] would silently return the LAST chunk rather
    than raising, so every lookup is guarded here, independent of whatever k
    the caller asked for."""
    k = min(k, index.ntotal)
    if k <= 0:
        return []
    scores, ids = index.search(np.asarray(qvec, dtype="float32").reshape(1, -1), k)
    hits = []
    for idx, score in zip(ids[0], scores[0]):
        if idx < 0:
            continue
        hits.append((float(score), int(idx), meta[int(idx)]))
    return hits


def _pad_to(items, n, filler):
    """Deterministic padding so the 3/10 quota can never break: repeat the
    last (lowest-ranked) available item. If nothing at all is available,
    fabricate the filler and log loudly -- this should never happen against
    the real ~110k-chunk corpus."""
    if not items:
        print(f"ADVERTENCIA: no hay elementos disponibles para completar la cuota de {n}",
              file=sys.stderr)
        items = [filler]
    out = list(items)
    while len(out) < n:
        out.append(out[-1])
    return out[:n]


def agregar_documentos(hits_sorted, quota=N_DOCUMENTS):
    """Max-pooling: a document's score is its single best chunk's score, not
    a sum (a table with hundreds of top-k chunks must not out-rank a single
    highly relevant document). hits_sorted is already globally sorted by
    (-score, chunk_id), so the FIRST occurrence of a doc_id in that order is
    exactly its best chunk -- deduping in place gives max-pooling with the
    mandated (-score, chunk_id) tie-break for free, both within a document
    (which chunk represents it) and across documents (final rank order)."""
    seen, order = set(), []
    for _, _, m in hits_sorted:
        doc_id = m["doc_id"]
        if doc_id not in seen:
            seen.add(doc_id)
            order.append(doc_id)
            if len(order) >= quota:
                break
    return order


def _fragmentos_de(hits_sorted, quota=N_FRAGMENTS, limit=FRAGMENT_WORD_LIMIT):
    """Emit fragments in (already-sorted) score order; oversized chunks split
    into sub-fragments that keep the parent chunk_id, each taking its own
    rank slot (Sec. 9.2.1). Stops as soon as the quota is reached."""
    fragments = []
    for _, _, m in hits_sorted:
        if len(fragments) >= quota:
            break
        for piece in _split_to_word_limit(m["texto"], limit):
            fragments.append({"chunk_id": m["chunk_id"], "doc_id": m["doc_id"], "text": piece})
            if len(fragments) >= quota:
                break
    return fragments[:quota]


def procesar_consulta(index, meta, qvec, k=DEFAULT_K, threshold=DEFAULT_THRESHOLD):
    """One query -> (documents, fragments), both fully ranked and padded to
    quota. Two independent safety nets, in order:

    1. Widen k (bounded at index.ntotal) until there are enough raw
       candidates to plausibly fill both quotas, or ntotal is exhausted.
    2. Prune below theta, but backfill from the unfiltered ranking whenever
       that would leave either quota short -- theta narrows results, it must
       never be able to break the schema.
    """
    ntotal = index.ntotal
    k_cur = min(max(k, 1), ntotal) if ntotal > 0 else 0
    hits = _hits(index, meta, qvec, k_cur)

    def n_docs(hs):
        return len({m["doc_id"] for _, _, m in hs})

    while k_cur < ntotal and (n_docs(hits) < N_DOCUMENTS or len(hits) < N_FRAGMENTS):
        k_next = min(ntotal, max(k_cur * 2, k_cur + 50))
        if k_next <= k_cur:
            break
        k_cur = k_next
        hits = _hits(index, meta, qvec, k_cur)

    hits_sorted = sorted(hits, key=lambda h: (-h[0], h[2]["chunk_id"]))
    above = [h for h in hits_sorted if h[0] >= threshold]

    docs = agregar_documentos(above, N_DOCUMENTS)
    if len(docs) < N_DOCUMENTS:
        docs = agregar_documentos(hits_sorted, N_DOCUMENTS)

    frags = _fragmentos_de(above, N_FRAGMENTS)
    if len(frags) < N_FRAGMENTS:
        frags = _fragmentos_de(hits_sorted, N_FRAGMENTS)

    docs = _pad_to(docs, N_DOCUMENTS, "NONE")
    frags = _pad_to(frags, N_FRAGMENTS, {"chunk_id": "NONE", "doc_id": "NONE", "text": ""})

    documents = [{"rank": i + 1, "doc_id": d} for i, d in enumerate(docs)]
    fragments = [{"rank": i + 1, "chunk_id": f["chunk_id"], "doc_id": f["doc_id"], "text": f["text"]}
                 for i, f in enumerate(frags)]
    return documents, fragments


# --------------------------------------------------------------- pipeline --

def generar(index_path, metadata_path, queries_path, out_path,
            k=DEFAULT_K, threshold=DEFAULT_THRESHOLD, batch_size=32, encoder=None):
    index = faiss.read_index(str(index_path))
    meta = cargar_metadata(metadata_path)
    queries = cargar_consultas(queries_path)
    if encoder is None:
        encoder = E5Dense()

    qvecs = encoder.encode_queries([t for _, t in queries], batch_size=batch_size)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for (qid, _), qvec in zip(queries, qvecs):
            documents, fragments = procesar_consulta(index, meta, qvec, k, threshold)
            record = {"query_id": qid, "documents": documents, "fragments": fragments}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out_path


# --------------------------------------------------------------------- CLI --

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "selftest":
        selftest()
        return

    ap = argparse.ArgumentParser(description="Genera resultados.jsonl (CODEFEST AD ASTRA 2026)")
    ap.add_argument("--index", required=True)
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--out", default="entrega/resultados.jsonl")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--stub-encoder", action="store_true",
                     help="internal: deterministic stub instead of the real E5 model. "
                          "Selftest/CI only -- never use for a real submission.")
    args = ap.parse_args(argv)

    encoder = _StubEncoder() if args.stub_encoder else None
    out = generar(args.index, args.metadata, args.queries, args.out,
                  k=args.k, threshold=args.threshold, batch_size=args.batch_size,
                  encoder=encoder)
    print(f"escrito {out}")


# ------------------------------------------------------------ self-check ---

class _StubEncoder:
    """Deterministic, dependency-free stand-in for E5Dense -- selftest only.
    Derives a unit vector per text from an md5 hash (not the randomized
    builtin hash()) so it is reproducible regardless of PYTHONHASHSEED.
    encode_passages raises: if generar()'s query path ever called it by
    mistake, the selftest would fail loudly instead of silently degrading."""

    query_prefix, passage_prefix = "query: ", "passage: "

    def __init__(self, dim=16):
        self.dim = dim

    def _vec(self, text):
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
        v = np.random.default_rng(seed).normal(size=self.dim).astype("float32")
        return v / np.linalg.norm(v)

    def encode_queries(self, texts, batch_size=32):
        return np.array([self._vec(t) for t in texts], dtype="float32")

    def encode_passages(self, texts, batch_size=64):
        raise AssertionError("encode_passages must never be called for query-time encoding")


def _synthetic_index(rows):
    """rows: [(vector, doc_id, chunk_id, texto), ...], vectors pre-normalised.
    Returns (faiss.IndexFlatIP, metadata-as-positional-list)."""
    dim = len(rows[0][0])
    index = faiss.IndexFlatIP(dim)
    index.add(np.array([r[0] for r in rows], dtype="float32"))
    meta = [{"doc_id": doc_id, "chunk_id": chunk_id, "fuente": f"synthetic/{doc_id}.txt",
             "formato": "txt", "fenomeno": 1, "posicion": i, "num_tokens": len(texto.split()),
             "texto": texto}
            for i, (_, doc_id, chunk_id, texto) in enumerate(rows)]
    return index, meta


def _build_synthetic_queries_pdf(path, n=50):
    """A minimal synthetic PDF in the qNNN-wrapped-across-lines format, built
    with PyMuPDF so the selftest never depends on a bundled fixture file."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for i in range(1, n + 1):
        qid = f"q{i:03d}"
        if y > 760:
            page = doc.new_page()
            y = 50
        page.insert_text((50, y), f"{qid} Pregunta sintetica numero {i} sobre")
        y += 15
        page.insert_text((60, y), "un tema de prueba con palabras de relleno para envolver.")
        y += 20
    doc.save(str(path))
    doc.close()


def selftest():
    import shutil
    import subprocess
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="generador_selftest_"))
    try:
        # -- 6. Max-pooling: one 0.9 chunk outranks fifty 0.7 chunks --------
        q = np.array([1.0, 0.0], dtype="float32")
        rows = [(np.array([0.9, np.sqrt(1 - 0.9**2)], "float32"), "DOC-A", "DOC-A-chunk-00000", "texto A")]
        for i in range(50):
            rows.append((np.array([0.7, np.sqrt(1 - 0.7**2)], "float32"), "DOC-B",
                        f"DOC-B-chunk-{i:05d}", f"texto B {i}"))
        index, meta = _synthetic_index(rows)
        documents, fragments = procesar_consulta(index, meta, q, k=51, threshold=DEFAULT_THRESHOLD)
        assert documents[0]["doc_id"] == "DOC-A", \
            f"max-pooling failed: {documents}"
        assert documents[1]["doc_id"] == "DOC-B"
        print("OK 6: max-pooling (0.9 chunk outranks fifty 0.7 chunks)")

        # -- 2, 3. Oversized chunk splits at sentence boundaries, sub-frags -
        # keep the parent chunk_id -------------------------------------------
        long_sentences = [f"Esta es la oracion numero {i} de un texto largo de prueba." for i in range(80)]
        long_text = " ".join(long_sentences)  # ~880 words, well over 250
        assert len(long_text.split()) > 250
        pieces = _split_to_word_limit(long_text, FRAGMENT_WORD_LIMIT)
        assert len(pieces) > 1
        for p in pieces:
            assert len(p.split()) <= FRAGMENT_WORD_LIMIT, f"piece too long: {len(p.split())}"
        rejoined_words = sum(len(p.split()) for p in pieces)
        assert rejoined_words == len(long_text.split()), "splitting lost or duplicated words"
        # a single pathological "sentence" longer than the limit: hard-cut path
        one_giant_sentence = "palabra " * 300
        pieces2 = _split_to_word_limit(one_giant_sentence.strip() + ".", FRAGMENT_WORD_LIMIT)
        assert all(len(p.split()) <= FRAGMENT_WORD_LIMIT for p in pieces2)
        assert sum(len(p.split()) for p in pieces2) == 300
        print("OK 2: fragment splitting respects the 250-word cap, no words lost")

        rows_long = [(q, "DOC-LONG", "DOC-LONG-chunk-00000", long_text)]
        index_l, meta_l = _synthetic_index(rows_long)
        _, fragments_l = procesar_consulta(index_l, meta_l, q, k=1, threshold=DEFAULT_THRESHOLD)
        chunk_ids = {f["chunk_id"] for f in fragments_l if f["chunk_id"] != "NONE"}
        assert chunk_ids == {"DOC-LONG-chunk-00000"}, chunk_ids
        ranks = [f["rank"] for f in fragments_l]
        assert ranks == list(range(1, N_FRAGMENTS + 1)), ranks
        assert all(len(f["text"].split()) <= FRAGMENT_WORD_LIMIT for f in fragments_l)
        print("OK 3: sub-fragments of one oversized chunk share the parent chunk_id, own ranks")

        # -- 7. theta high enough to filter everything -> still exactly 3/10 -
        rows2 = []
        for i in range(6):
            v = np.array([0.5, np.sqrt(1 - 0.5**2)], "float32")
            rows2.append((v, f"DOC-{i}", f"DOC-{i}-chunk-00000", f"texto doc {i}"))
        index2, meta2 = _synthetic_index(rows2)
        documents2, fragments2 = procesar_consulta(index2, meta2, q, k=6, threshold=2.0)  # impossible cosine
        assert len(documents2) == N_DOCUMENTS and len(fragments2) == N_FRAGMENTS
        assert [d["rank"] for d in documents2] == [1, 2, 3]
        assert [f["rank"] for f in fragments2] == list(range(1, 11))
        print("OK 7: theta filtering everything still backfills to exactly 3/10")

        # -- 8. FAISS -1 -> no crash, no wrong chunk (guard tested directly) -
        rows3 = [(np.array([1.0, 0.0], "float32"), "DOC-X", "DOC-X-chunk-00000", "solo hay dos"),
                 (np.array([0.0, 1.0], "float32"), "DOC-Y", "DOC-Y-chunk-00000", "chunks aqui")]
        index3, meta3 = _synthetic_index(rows3)
        # ask for k=5 directly against the raw search helper, bypassing our
        # own capping logic, to prove the guard holds independent of the caller
        raw_hits = _hits(index3, meta3, q, 5)
        assert len(raw_hits) == 2, f"expected exactly 2 valid hits, got {len(raw_hits)}"
        assert {h[2]["chunk_id"] for h in raw_hits} == {"DOC-X-chunk-00000", "DOC-Y-chunk-00000"}
        print("OK 8: FAISS -1 guarded in _hits(), no crash, no phantom last-chunk")

        # -- 9. k capped at ntotal terminates (pathological: all hits, one doc)
        rows4 = [(np.array([1.0, 0.0], "float32"), "DOC-ONLY", f"DOC-ONLY-chunk-{i:05d}", f"t{i}")
                 for i in range(4)]
        index4, meta4 = _synthetic_index(rows4)
        documents4, fragments4 = procesar_consulta(index4, meta4, q, k=1, threshold=DEFAULT_THRESHOLD)
        assert len(documents4) == N_DOCUMENTS  # widen loop terminated at ntotal=4, then padded
        assert len(fragments4) == N_FRAGMENTS
        assert documents4[0]["doc_id"] == "DOC-ONLY"
        print("OK 9: widen-k retry bounded at index.ntotal, terminates and pads")

        # -- 10. output uses 'text', metadata uses 'texto' -------------------
        assert "texto" in meta3[0] and "text" not in meta3[0]
        assert "text" in fragments4[0] and "texto" not in fragments4[0]
        print("OK 10: field-name trap -- metadata 'texto', resultados 'text'")

        # -- real E5Dense prefix contract (no model download / no import) ---
        assert E5Dense.query_prefix == "query: "
        assert E5Dense.passage_prefix == "passage: "
        print("OK: E5Dense prefix contract (query: / passage: ) matches spec")

        # -- 1, 4, 5. full pipeline: 50 lines, 3/10, ranks, encode_queries --
        # only, loader returns exactly q001..q050 ---------------------------
        rng = np.random.default_rng(0)
        corpus_rows = []
        for d in range(6):
            for c in range(5):
                v = rng.normal(size=16).astype("float32")
                v /= np.linalg.norm(v)
                corpus_rows.append((v, f"DOC-{d:03d}", f"DOC-{d:03d}-chunk-{c:05d}",
                                    f"texto de prueba documento {d} fragmento {c} palabras cortas."))
        index_full, meta_full = _synthetic_index(corpus_rows)
        index_path = tmp / "index.faiss"
        meta_path = tmp / "metadata.jsonl"
        faiss.write_index(index_full, str(index_path))
        with open(meta_path, "w", encoding="utf-8") as f:
            for m in meta_full:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

        queries_pdf = tmp / "queries.pdf"
        _build_synthetic_queries_pdf(queries_pdf, n=50)
        loaded = cargar_consultas(queries_pdf)
        assert [qid for qid, _ in loaded] == [f"q{i:03d}" for i in range(1, 51)]
        print("OK 5: query loader returns exactly q001..q050, sorted")

        out_path = tmp / "resultados.jsonl"
        stub = _StubEncoder()  # raises if encode_passages is ever touched -> proves #4
        generar(index_path, meta_path, queries_pdf, out_path, k=50,
               threshold=DEFAULT_THRESHOLD, encoder=stub)

        lines = out_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 50, f"expected 50 lines, got {len(lines)}"
        seen_qids = []
        for line in lines:
            rec = json.loads(line)
            for field in ("query_id", "documents", "fragments"):
                assert field in rec, f"missing field {field}"
            assert len(rec["documents"]) == N_DOCUMENTS
            assert len(rec["fragments"]) == N_FRAGMENTS
            assert [d["rank"] for d in rec["documents"]] == [1, 2, 3]
            assert [f["rank"] for f in rec["fragments"]] == list(range(1, 11))
            for d in rec["documents"]:
                assert set(d.keys()) == {"rank", "doc_id"}
            for fr in rec["fragments"]:
                assert set(fr.keys()) == {"rank", "chunk_id", "doc_id", "text"}
                assert len(fr["text"].split()) <= FRAGMENT_WORD_LIMIT
            seen_qids.append(rec["query_id"])
        assert seen_qids == [f"q{i:03d}" for i in range(1, 51)], "output not in q001..q050 order"
        print("OK 1: exactly 50 lines, 3 documents + 10 fragments, ranks 1..3/1..10, no missing fields")
        print("OK 4: full run completed with the stub's encode_passages() untouched "
              "(it raises on call) -- queries only ever went through encode_queries")

        # -- 11. standalone delivery: copy generador.py + synthetic artifacts
        # to an empty dir, run there as a subprocess, assert 50 valid lines --
        standalone_dir = tmp / "standalone"
        standalone_dir.mkdir()
        shutil.copy(Path(__file__).resolve(), standalone_dir / "generador.py")
        shutil.copy(index_path, standalone_dir / "index.faiss")
        shutil.copy(meta_path, standalone_dir / "metadata.jsonl")
        shutil.copy(queries_pdf, standalone_dir / "queries.pdf")
        result = subprocess.run(
            [sys.executable, "generador.py", "--index", "index.faiss",
             "--metadata", "metadata.jsonl", "--queries", "queries.pdf",
             "--out", "resultados.jsonl", "--k", "50", "--threshold", str(DEFAULT_THRESHOLD),
             "--stub-encoder"],
            cwd=str(standalone_dir), capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, f"standalone run failed:\n{result.stdout}\n{result.stderr}"
        out_lines = (standalone_dir / "resultados.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(out_lines) == 50
        for line in out_lines:
            rec = json.loads(line)
            assert len(rec["documents"]) == N_DOCUMENTS and len(rec["fragments"]) == N_FRAGMENTS
        print("OK 11: standalone delivery (generador.py + artifacts, nothing else) produces 50 valid lines")

        print("generador.py selftest OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
