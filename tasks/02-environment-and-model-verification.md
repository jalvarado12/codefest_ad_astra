# Task 02 — Environment setup and model load verification

**Status: DONE (2026-08-05, executed in-session)**

## Goal
Install the dependency stack and prove both encoders load and produce correct output on trivial input,
before any corpus work depends on them.

## Context
Architectures A and C need `intfloat/multilingual-e5-large`; B and C need `BAAI/bge-m3` including its
sparse head. Task 01 established CPU-only, 7.9 GB RAM — models must be loaded one at a time.

## Graphify queries to run first
N/A — no graph exists.

## Read only these files
- `final-architecture-decision-report.md` lines 63–110 (the A/B/C definitions)
- `ad_astra.md` §4.2–4.3, §8.2 (encoder-only rule, normalization)

## Create/modify exactly these files
- `arch_test/encoders.py`

## Detailed instructions
1. Install torch (CPU wheel), transformers, faiss-cpu; extraction deps (pypdf, beautifulsoup4, lxml,
   openpyxl, langdetect, psutil).
2. Wrap both models with plain `transformers`:
   - e5: mean pooling + L2 norm, `"query: "` / `"passage: "` prefixes applied by construction.
   - BGE-M3: CLS dense + sparse head (`sparse_linear.pt`, ReLU, max-pool per token id, special tokens
     dropped), both from ONE forward pass.
3. Verify: correct shapes, unit norms, cross-lingual sanity (ES/EN/PT beat an off-topic sentence),
   sparse lexical sanity.

## Do NOT
- Do not use a decoder/generative model anywhere.
- Do not call any proprietary API.
- Do not encode a string through e5 without its prefix.

## Acceptance criteria
`python arch_test/encoders.py` prints `environment check OK`.

## Verify
Result, as measured:
- **FlagEmbedding and sentence-transformers are unusable on this machine**: their `sklearn → pyarrow`
  import chain hits a Windows Application Control policy
  (`ImportError: DLL load failed ... Una directiva de Control de aplicaciones bloqueó este archivo`).
  `transformers` 5.14.1 hit the same wall via `generation/candidate_generator.py`.
  **Fix: uninstalled `pyarrow`** (unused by this pipeline); sklearn and transformers then import fine.
  Consequence: both models are driven directly through `transformers`, and BGE-M3's sparse head is
  implemented explicitly rather than taken from FlagEmbedding — this also resolves the report's open
  question about how BGE-M3 sparse is meant to be computed and compared.
- Both models load (~52 s each) and pass every sanity assertion.
- **Measured throughput on full-size ~450-token chunks: e5-large 4094 ms/chunk, bge-m3 3656 ms/chunk**
  (batch 4, CPU). Trivial-input figures (376 / 495 ms) badly understate real cost.

## Update graphify / CLAUDE.md
Recorded in CLAUDE.md: pyarrow conflict, the transformers-only decision, measured throughput.
