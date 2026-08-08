# Task 08 — Build and run architectures A, B and C

**Status: CODE DONE and SMOKE-TESTED end to end; MEASUREMENT RUN GATED on Task 07.**

## Goal
Run all three architectures over one identical chunk set and the confirmed validation queries,
recording NDCG@10, F1@3, indexing time, query latency and memory.

## Context
- **A** — e5-large dense only, `IndexFlatIP`.
- **B** — BGE-M3 dense + sparse from one forward pass, RRF-fused across its own two heads.
- **C** — e5-large dense + BGE-M3 sparse (two models), RRF-fused.
RRF with `k0 = 60` per spec §8.4 eq. 7, in all fused cases.

## Graphify queries to run first
N/A — no graph exists. Read `arch_test/harness.py` and `arch_test/encoders.py` by name; do not
rediscover them by directory search.

## Read only these files
- `arch_test/harness.py`, `arch_test/encoders.py`, `arch_test/metrics.py`
- `ad_astra.md` §5.2, §8.2, §8.4, §8.6, §9.2.1, §9.3
- `final-architecture-decision-report.md` lines 52–131

## Create/modify exactly these files
- `arch_test/data/chunks.jsonl`, `e5_dense.npy`, `bge_dense.npy`, `bge_sparse.pkl`
- `arch_test/data/results_A.jsonl`, `results_B.jsonl`, `results_C.jsonl`
- `arch_test/data/timings.json`

## Detailed instructions
1. `python arch_test/harness.py --stage chunk` — one canonical chunk set for all three.
2. `python arch_test/harness.py --stage encode` — the expensive part; encodings cache to disk so an
   interrupted run resumes. Expect this to run for hours (see cost note below).
3. `python arch_test/harness.py --stage run` — builds indices, runs the confirmed queries, writes
   spec-§9.3-shaped results and `timings.json`.
4. Score with `metrics.py` against `validation_confirmed.json`.

## Do NOT
- **Do not report C's indexing cost as the cached cost.** `indexing_cost()` charges C both forward
  passes (`e5_pass + bge_pass`) even when it reuses A's dense and B's sparse encodings, because C's
  headline drawback is exactly that ~2× cost.
- Do not let A, B and C see different chunks — all three read the same `chunks.jsonl`.
- Do not run with `validation_draft.json`; the harness refuses, and it should stay that way.
- No decoder model, no proprietary API, anywhere in the pipelines.

## Acceptance criteria
Three `results_*.jsonl` files, each line carrying exactly 3 documents and exactly 10 fragments, every
fragment ≤ 250 words and sentence-complete; `timings.json` with per-architecture indexing cost, query
latency (mean/p50/max) and RSS.

## Verify
Smoke test already run end to end on the 3 local Markdown documents (15 chunks, 2 queries):
- A: indexing 55.97 s, query 0.653 s mean
- B: indexing 69.13 s, query 0.652 s mean
- C: indexing 125.10 s, query 1.416 s mean — and 125.10 ≈ 55.97 + 69.13, confirming the two-pass
  accounting rule actually fires.
- All three outputs pass the §9.3 schema check (3 docs / 10 fragments / max 250 words / ranks 1–10).
- One real bug found and fixed by that smoke test: `to_documents` returned fewer than 3 documents when
  the candidate pool held fewer than 3 distinct sources; it now pads to exactly 3.
Smoke artifacts are quarantined in `arch_test/data/_smoke/` and are **not** results.

## Update graphify / CLAUDE.md
Record the measured numbers once the real run completes.
