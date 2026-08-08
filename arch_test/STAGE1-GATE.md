# Stage 1 status — architecture comparison harness

**Date: 2026-08-05. Stage 2 is NOT started and cannot start yet.**

Two blockers, both of them facts the orchestration prompt got wrong about this machine.

---

## Blocker 1 — the ADL corpus is not here

The repo contains four Markdown files and nothing else. There is no corpus directory anywhere under
the user profile (`AD_Astra` in Downloads is the same folder — Windows paths are case-insensitive;
`webpage_arch` is an unrelated web project). No PDF, HTML, JSON, CSV, XLSX or PBF corpus exists on
disk.

So the three Stage 1 deliverables that need real documents — the stratified sample, the draft
validation query set, and the pooled candidate judgments — **cannot be produced**. Everything that
does not need the corpus was built and verified instead.

Also worth correcting: this directory is **not a git repository** (`fatal: not a git repository`). The
no-commit constraint is satisfied, but not for the stated reason.

**What I need from you:** the corpus path, or the corpus dropped into a folder. Then Tasks 05 → 06 run
unattended and produce the sample plus the draft validation set for your review.

---

## Blocker 2 — I gave you a cost estimate that was ~10× too optimistic

When I asked about sample size I estimated 0.2–0.4 s per chunk per model. That was based on short
sentences. Measured on real ~450-token chunks on this CPU (i5-6300U, 2 cores, no GPU):

| | measured | for 3000 chunks |
|---|---|---|
| multilingual-e5-large | **4.09 s/chunk** | 3.4 h |
| bge-m3 (dense+sparse, one pass) | **3.66 s/chunk** | 3.0 h |

You chose **~100 docs / ~3000 chunks** against the wrong estimate. What that choice actually costs:

- **~6.5 hours** of encoding wall-clock, once, for all three architectures (the two passes are shared —
  C reuses A's dense and B's sparse output). Plus extraction and chunking.
- Reported indexing cost stays honest: A = 3.4 h, B = 3.0 h, **C = 6.5 h** (C is charged both passes
  even though it reads them from cache, or its headline 2× drawback would disappear into an
  implementation detail).
- Peak RSS per model pass is **~1.96 GB** (measured), fine against 7.9 GB — one model at a time.
- Encoding is cached to disk and resumable, so an interrupted run does not start over.

That is a long but survivable overnight run. **Confirm 3000 chunks or cut it** — at ~1000 chunks the
whole thing is ~2.2 h, at the price of noisier NDCG. Your call; I'll take the answer at the same time
as the corpus path.

---

## What is built and verified

| Component | File | State |
|---|---|---|
| Reference chunker (spec §3.3 + Table 1) | `chunker.py` | self-check passes |
| Encoder wrappers (e5 prefixes, BGE-M3 sparse head) | `encoders.py` | both models load, sanity checks pass |
| Corpus extraction + stratified sampler | `corpus.py` | imports clean, awaits corpus |
| A/B/C harness, FAISS, RRF, spec §9.3 output | `harness.py` | smoke-tested end to end |
| NDCG@10 / F1@3 from the spec's own formulas | `metrics.py` | self-check against hand-computed values |
| Validation drafting + the confirmation gate | `validation.py` | gate logic self-tested |

**Smoke test** (3 local Markdown docs, 15 chunks, 2 queries — plumbing proof, not results):
A indexing 55.97 s / query 0.653 s; B 69.13 s / 0.652 s; C 125.10 s / 1.416 s. C ≈ A + B confirms the
two-pass accounting fires. All three outputs pass the §9.3 schema check. Artifacts quarantined in
`data/_smoke/`.

Two real defects the smoke test caught and that are now fixed: `to_documents` could return fewer than
the mandatory 3 documents, and phase memory was recorded at start/end — which missed the peak entirely
and understated the footprint 4× (466 MB reported vs 1.96 GB actual).

## Three environment findings worth keeping

1. **FlagEmbedding and sentence-transformers do not work on this machine.** Their `sklearn → pyarrow`
   chain trips a Windows Application Control policy: `DLL load failed while importing lib: Una
   directiva de Control de aplicaciones bloqueó este archivo`. Uninstalling `pyarrow` (unused here)
   fixed transformers and sklearn. Both models are driven through plain `transformers`, and BGE-M3's
   sparse head is implemented directly — which also settles the shortlist report's open question about
   how that sparse output is computed and compared: `Linear(hidden,1) + ReLU`, max-pooled per token id,
   special tokens dropped, retrieved through an inverted index scored by dot product over shared ids.
2. **No GPU, 2 physical cores.** Every timing in the final report will be CPU-only on 2015-era mobile
   silicon and must be read as such — it says nothing about what the same architectures cost on the
   competition's hardware.
3. OCR (tesseract) and PBF reading are not installed. Those files will be listed in `skipped.json` with
   a reason rather than silently dropped; if the corpus leans on images or map tiles, tell me and I'll
   install the readers before sampling.

## The gate still holds

Nothing downstream can score against unreviewed labels, and this is enforced in code rather than by
discipline: `validation.py confirm` refuses any draft with an unreviewed query and has no `--force`,
and `harness.py --stage run` exits if `validation_confirmed.json` is missing. No metric has been
computed against any draft judgment, because no draft judgments exist yet.
