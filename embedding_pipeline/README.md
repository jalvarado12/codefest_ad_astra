# embedding_pipeline

> **Two files, two audiences.** `embedding_pipeline.py` is the full pipeline
> with its test suite (chunk -> encode -> FAISS -> retrieve -> score) and
> lives on the `embedding` branch only -- it is not pushed to `master`.
> `embeddings_only.py` is a smaller cut of the same encoder logic with
> chunking, FAISS/sparse indexing, RRF fusion and scoring stripped out --
> **just text-in, vectors-out** -- and that is the file that goes to
> `master`. See "Which file do I want?" below.

One self-contained script: chunk -> encode -> FAISS/sparse index -> retrieve
-> score, for architectures **A** (multilingual-e5-large dense), **B**
(BAAI/bge-m3 dense+sparse hybrid) and **C** (e5 dense + bge-m3 sparse) -- the
three finalists in `final-architecture-decision-report.md`.

It is a **port**, not a redesign: every function in `embedding_pipeline.py`
is lifted from `arch_test/{chunker,encoders,harness,metrics}.py` and merged
into one file so the embedding process is not orchestrated across five
imports. Two things were dropped on purpose:

- **Architectures D/E** (all-MiniLM-L6-v2 trial arms) -- out of scope, only
  A/B/C are finalists.
- **Multi-format corpus extraction** (PDF/HTML/CSV/XLSX/OCR/PBF). That logic
  lives on the `master` branch under `extraccion/` (`generar_documentos()`,
  an Adapter-pattern pipeline) and is owned there. This script does not
  duplicate it -- see "Input contract" below for the seam between the two.

## What's here

| File | Purpose |
|---|---|
| `embedding_pipeline.py` | The whole pipeline (chunk, encode, FAISS/sparse index, retrieve, score) + `selftest`/`verify`. `embedding` branch only. |
| `embeddings_only.py` | Just the two encoder classes (`E5Dense`, `BGEM3`) + a CLI to encode a JSONL of texts to `.npy`/`.pkl`. No chunking, no FAISS, no scoring. Goes to `master`. |
| `requirements.txt` | Full deps, for `embedding_pipeline.py` (`sentence-transformers`, `FlagEmbedding`, `faiss-cpu`, `psutil`). |
| `requirements_embeddings_only.txt` | Trimmed deps, for `embeddings_only.py` (`sentence-transformers`, `FlagEmbedding` only -- no `faiss-cpu`). |

## Which file do I want?

- Building or querying an actual FAISS index, need the sentence-complete
  chunker, or want the `selftest`/`verify` self-checks? Use
  **`embedding_pipeline.py`** (on `embedding`).
- Already have chunked text from somewhere else and just need dense/sparse
  vectors out, with no FAISS/chunking dependency pulled in? Use
  **`embeddings_only.py`** (on `master`):

  ```bash
  python embeddings_only.py selftest                     # no GPU, no download
  python embeddings_only.py encode --model e5 --mode passage \
      --in chunks.jsonl --out-dir out/
  python embeddings_only.py encode --model bge --in chunks.jsonl --out-dir out/
  ```

  Both `encode` calls read the same `--text-field texto` (default) from the
  input JSONL; `e5` needs `--mode query`/`passage` (the spec-mandated
  prefix), `bge` always returns both heads from one pass regardless of mode.

## Input contract

A JSONL file, one pre-extracted document per line:

```json
{"doc_id": "DOC-0001", "fuente": "debris_report.pdf", "formato": "pdf",
 "fenomeno": 2, "texto_limpio": "..."}
```

This matches the schema `extraccion.pipeline.generar_documentos()` yields on
`master`. (`"texto"` is also accepted as a fallback key for documents
produced outside that pipeline.) Point `build` at a JSONL dump of that
generator's output and it takes over from there -- chunking, encoding,
indexing, retrieval, scoring.

## Environment

Targets Colab (Linux + GPU). It does **not** import on a local Windows box:
`sentence-transformers`/`FlagEmbedding` pull in `pyarrow`, which trips a
Windows Application Control policy (`DLL load failed while importing lib`).
`python embedding_pipeline.py selftest` is the one subcommand that runs
anywhere -- it uses stub encoders and touches no ML library.

```bash
pip install -r requirements.txt
```

## Architectures

| Arch | Dense | Sparse | Cost | Notes |
|---|---|---|---|---|
| A | e5-large | -- | 1x | Baseline; mandatory `query:`/`passage:` prefixes applied by construction. |
| B | bge-m3 | bge-m3 | ~1x | Both heads from ONE forward pass, RRF-fused internally. |
| C | e5-large | bge-m3 | ~2x | Two full model passes; no fusion shortcuts. |

## CLI

```bash
# Wiring self-check -- no GPU, no model download, runs anywhere
python embedding_pipeline.py selftest

# Real-model sanity check (loads actual weights) -- Colab only
python embedding_pipeline.py verify

# Build: chunk + encode + FAISS index, written to the spec deliverable layout
python embedding_pipeline.py build A --docs documentos.jsonl --chunk \
    --out-dir base_vectorial/encoder_A

# Query: retrieve + shape output (+ score, if a CONFIRMED validation set exists)
python embedding_pipeline.py query A --queries validation_confirmed.json

# Query without a confirmed validation set (no NDCG/F1, just resultados-shaped output)
python embedding_pipeline.py query A --unscored --queries queries.json
```

## Outputs

- `data/chunks.jsonl` -- chunk metadata (spec Table 1 fields); line order is
  insertion order, which is also FAISS internal id order.
- `<out-dir>/index.faiss`, `<out-dir>/metadata.jsonl` -- the
  `base_vectorial/encoder_<nombre>/` deliverable layout, loadable with a
  plain `faiss.read_index()`.
- `data/results_<arch>.jsonl` -- one line per query, in the `resultados.jsonl`
  shape (`documents` top-3, `fragments` top-10, `<=250` words, sentence-safe).
- `data/summary.json`, `data/timings_*.json` -- indexing cost, query latency,
  NDCG@10/F1@3 when a confirmed validation set was supplied.

## Verified on Colab (2026-08-10, T4 session via the `colab` CLI)

- `selftest`: A/B/C wiring, index persistence round-trip -- pass.
- `verify`: real e5-large + bge-m3 load, encode, L2-normalize, cross-lingual /
  sparse sanity -- pass.
- `build A` + `query A` against a 3-document synthetic corpus: real
  `index.faiss` + `metadata.jsonl` written; retrieval correctly ranked the
  space-debris document first for a space-debris query and the AI/defense
  document first for an AI/defense query.

## Relationship to `arch_test/`

`arch_test/{chunker,encoders,harness,metrics}.py` remain the 5-architecture
(A-E) comparison harness that produced the numbers behind the shortlist
decision -- keep those for the record. This directory is the single-file,
finalists-only (A/B/C) tool for actually building and querying an index for
the submission. Nothing under `arch_test/` was deleted or modified when this
was split out.
