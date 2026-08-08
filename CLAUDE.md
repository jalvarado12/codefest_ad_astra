# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

There is **no code here yet** — only three Markdown documents. This is the planning/research stage of a CODEFEST AD ASTRA 2026 Etapa 1 entry (Universidad de los Andes / Fuerza Aeroespacial Colombiana): build a **vector knowledge base** over an ES/EN/PT corpus of open-source documents on (1) AI in defense, (2) LEO space security / space debris, (3) Latin American territorial dynamics.

| File | What it is |
|---|---|
| `ad_astra.md` | The official competition spec (Spanish, PDF-to-Markdown; tables are mangled by the conversion — read the prose, not the table alignment). This is the **binding requirements document**. |
| `embedding_research_report.md` | Research deliverable: 11 encoder candidates surveyed + 6 multi-encoder fusion architectures, with sources. Trade-offs only, no recommendation. |
| `final-architecture-decision-report.md` | Narrows to 3 finalist architectures (A/B/C) with the full elimination trail. A decision aid — the choice is **not yet made**. |

When work starts, code goes in this directory; the spec's required delivery layout is under "Deliverables" below.

## Hard constraints from the spec (violating these disqualifies the entry)

- **Encoder-only models (BERT family), from HuggingFace, freely licensed.** Decoder/generative models (GPT, LLaMA, Claude, Gemini…) are banned *everywhere* in indexing and retrieval — no LLM reranking, no query rewriting/expansion, no generative filtering or summarization (spec §4.2, §8.3). Models with decoder lineage are a jury risk (EmbeddingGemma is flagged as a boundary case; Qwen3-Embedding / e5-mistral / NV-Embed / Llama-Embed-Nemotron are excluded outright).
- **FAISS is mandatory.** Serialize with `faiss.write_index()`; the file must load with a plain `faiss.read_index()` and no extra dependencies. `IndexFlatIP` over L2-normalized vectors (= cosine) is sufficient at this corpus size.
- **Linguistic completeness:** no chunk may contain a truncated sentence. A sentence starting in a chunk must end in that chunk; fixed-size chunking must back off to the last complete sentence inside the token budget (§3.3).
- **~512-token practical chunk ceiling**, which is what makes 512-token encoders a natural fit and makes 8192-token models' extra capacity unused.
- Retrieval may only use vectors, similarity scores, and metadata. Post-filters on metadata (`fenomeno`, language, date) or on score thresholds are allowed.

## Required output shapes

**Chunk metadata** (`metadata.jsonl`, one JSON object per line, **line order must match FAISS internal ids**) — mandatory fields: `doc_id`, `chunk_id`, `fuente`, `formato`, `fenomeno` (1/2/3), `posicion` (0-based), `num_tokens`, `texto`. Extra fields are allowed.

**Results** (`resultados.jsonl`, exactly 50 lines, `q001`–`q050`, in order):

```json
{"query_id": "q001",
 "documents": [{"rank": 1, "doc_id": "DOC-042"}, ...],   // exactly 3
 "fragments": [{"rank": 1, "chunk_id": "...", "doc_id": "...", "text": "..."}, ...]}  // exactly 10
```

Wrong element counts, missing fields, or fragments over **250 words** are penalized or discarded by the automatic evaluator. A retrieved chunk over 250 words must be split (still on sentence boundaries) with sub-fragments **sharing the original `chunk_id`** and each taking its own rank slot.

## Evaluation (drives every design trade-off)

- **NDCG@10** on fragments — graded relevance judged on the `text` field. `chunk_id` is traceability only, *not* the matching key.
- **F1@3** on documents — set metric, order-insensitive; ground truth matches on the **`fuente`** field (original ADL filename), *not* on the team-assigned `doc_id`.
- Two separate leaderboards combined by Borda count, so consistent performance on *both* metrics beats winning one.

Consequence: `fuente` must be exactly the ADL-provided filename/URL, and fragment `text` must read as self-contained relevant prose.

## Deliverables layout

```
entrega/
  resultados.jsonl
  generador.py          # must reproduce resultados.jsonl from the index + query file, or the entry is excluded
  informe_tecnico.pdf   # ≤8 pages: chunking strategy, encoder choice, FAISS index type, graph
  base_vectorial/
    encoder_<nombre>/{index.faiss, metadata.jsonl}   # one subfolder per encoder
    grafo/grafo.graphml                              # optional bonus (NetworkX/Neo4j/RDFLib export)
```

Bonus knowledge graph: multilingual NER + relation extraction, triples keeping `doc_id`/`chunk_id` provenance, fused into retrieval as if it were an extra index (RRF).

## Open architecture decision

Three finalists, per `final-architecture-decision-report.md`:

- **A — `intfloat/multilingual-e5-large` dense only.** 1× compute, no fusion code. Native 512-token ceiling matches the chunk budget. **Requires `"query:"` / `"passage:"` prefixes** — omitting them degrades quality. Cosine scores cluster in 0.7–1.0 (low InfoNCE temperature); only relative order matters.
- **B — `BAAI/bge-m3` native dense+sparse hybrid, fused with RRF.** Also ~1× compute because both representations come from one forward pass, and the two heads are co-trained via self-knowledge distillation. Best-supported option; the sparse head also recovers exact-term/acronym matches (LEO, orbital designators, unit names) that dense embeddings blur.
- **C — e5-large dense + BGE-M3 sparse.** ~2× compute (two full passes per chunk), and the only option with no supporting benchmark for the specific pairing.

Caveats that apply to all three and should not be papered over in the informe técnico: **no evidence is domain-matched** (benchmarks are Wikipedia/MIRACL-general plus one finance-domain study), and **Portuguese retrieval quality is unmeasured for every candidate** — MIRACL and CLEF both exclude PT. A small hand-labeled validation set from the real corpus is worth more than any cited number.

Fusion default is **RRF (k=60)** — no score normalization needed, robust to different score scales. CombSUM/CombMNZ require per-index normalization and are fragile without it.

## Architecture comparison harness (`arch_test/`)

An empirical A/B/C comparison harness, built to replace the shortlist report's "inferred, unmeasured"
caveats with measured numbers. **Stage 1 complete, Stage 2 gated** — see `arch_test/STAGE1-GATE.md` and
`tasks/01`–`tasks/09`.

| File | Purpose |
|---|---|
| `chunker.py` | Reference chunker: sentence-complete cuts (§3.3), Table 1 metadata. Reference-only, not the submission chunker. |
| `encoders.py` | e5-large via sentence-transformers (prefixes by construction); BGE-M3 via FlagEmbedding (dense + sparse, one pass). |
| `corpus.py` | Per-format extraction (§2.1) + phenomenon × language stratified sampler. |
| `harness.py` | Shared machinery for A–E: the `ARCHS` routing table, FAISS `IndexFlatIP`, sparse inverted index, RRF k₀=60, §9.3 output, timing/peak-RSS/GPU-peak, scoring. `python harness.py` runs a stub-encoder wiring self-check (no GPU, no downloads). |
| `arch_{a..e}_*.py` | One runnable entry point per architecture — `arch_a_e5_dense.py`, `arch_b_bgem3_hybrid.py`, `arch_c_e5_plus_bgem3_sparse.py`, `arch_d_minilm_dense.py`, `arch_e_minilm_plus_bgem3_sparse.py`. Each encodes only the models it needs, writes `results_<X>.jsonl`, and folds its row into `summary.json`. |
| `metrics.py` | NDCG@10 and F1@3 from the spec's own formulas, verified against hand-computed values. |
| `validation.py` | Validation-set drafting, pooling, and the DRAFT → CONFIRMED gate. |
| `colab_job.py` | VM-side driver: `selftest,verify,bench,pipeline,score` stages. |
| `run_on_colab.sh` | Local orchestration: provision GPU session → install → upload → run → download → stop. |

**Execution target is Colab, not this machine.** `chunker.py`, `metrics.py` and `validation.py`
self-check locally (`python arch_test/<file>.py`, or `validation.py selftest`); anything touching the
models runs on the VM.

**Environment facts that will bite again:**
- **Locally, sklearn/FlagEmbedding/sentence-transformers cannot load**: their `pyarrow` dependency trips
  a Windows Application Control policy (`DLL load failed while importing lib`). This is why model
  execution moved to Colab. Do not try to "fix" `encoders.py` to import on Windows.
- The **Colab CLI needs the `_winshim/` `termios`+`tty` stubs on Windows** (`colab_cli.console` imports
  POSIX-only modules at module scope). `run_on_colab.sh` sets `PYTHONPATH` for this; it is a no-op on
  Linux. Auth is interactive once: `colab sessions`, then paste the code.
- CPU baseline, measured on this box before the move: **e5-large 4.09 s/chunk, bge-m3 3.66 s/chunk** at
  ~450 tokens, peak RSS ~1.96 GB per model. GPU figures come from the `bench` stage.
- The ADL corpus is **not on this machine**; `corpus.py` needs a path before anything can run.
- This directory is not a git repository.

**Two extra candidates on trial (D, E) — `sentence-transformers/all-MiniLM-L6-v2`:** spec-valid
(encoder-only 6-layer BERT distillation, Apache-2.0, on the Hub), but **English-only and trained at 256
tokens** against a ~450-token chunk budget on an ES/EN/PT corpus. D is the cheap floor (384-dim, 22M
params); E pairs it with BGE-M3's sparse head to test whether the lexical side carries the ES/PT
retrieval the dense side drops. Chunks stay tokenized by e5's XLM-R budget for all five, so MiniLM
simply truncates — that truncation is part of what is being measured, not a bug to patch.

**Rules the harness encodes, worth preserving in the submission:**
- A two-model architecture (C = e5 + bge, E = minilm + bge) is charged both forward passes even when it
  reuses cached encodings — otherwise its ~2× indexing cost disappears into a caching detail.
- Documents are matched and aggregated on `fuente`, never `doc_id` (§10.2.1).
- LLM-drafted relevance judgments are never scored against until a human confirms them; `validation.py
  confirm` has no `--force` and `harness.py` refuses to run without `validation_confirmed.json`.

## Conventions when writing code here

- Spec terminology is Spanish (`fragmento`/chunk, `documento`, `fuente`, `fenomeno`) and the required field names are Spanish — keep them exactly as spelled in the spec, even in otherwise-English code.
- Indexing time scales with **chunk** count, not document count (~2000 documents expected); measure on a sample before budgeting.
- Source formats to handle: PDF, HTML, JSON, CSV, XLSX, Markdown/TXT, images (OCR), PBF map tiles (dedupe repeated zoom levels).
