# Task 09 — Results report

**Status: BLOCKED — gated on Task 08's measurement run.**

## Goal
`architecture-test-results.md`: measured NDCG@10, F1@3, indexing time, query latency and memory for A,
B and C side by side, with scope stated up front.

## Graphify queries to run first
N/A — no graph exists.

## Read only these files
- `arch_test/data/timings.json`, `results_A.jsonl`, `results_B.jsonl`, `results_C.jsonl`
- `arch_test/data/sample.json`, `validation_confirmed.json`, `skipped.json`
- `final-architecture-decision-report.md` §4 (the open questions this run is answering)

## Create/modify exactly these files
- `architecture-test-results.md`

## Detailed instructions
1. Open with scope: number of documents sampled, number of chunks, number of validation queries, how
   many judgments, who confirmed them and when, and the hardware the timings came from.
2. Table: A / B / C × {NDCG@10, F1@3, indexing seconds, query latency mean and p50, peak RSS, index
   bytes}.
3. State plainly which of the shortlist report's open questions this does and does not settle. It gives
   corpus-matched evidence at this sample size; it does **not** give full-corpus or full-50-query
   confidence, and with ~18 queries the difference between two close architectures is inside the noise.
4. Report Portuguese separately if the query set supports it — PT being unmeasured is the report's
   sharpest open question, and a per-language breakdown is the only thing here that speaks to it.
5. Record every reduction taken against the original plan and why (sample size cuts, skipped formats,
   OCR/PBF unavailability).

## Do NOT
- Do not phrase results as if they generalise to the full corpus or the competition's 50 queries.
- Do not carry any timing or memory figure over from the shortlist report's cited literature — every
  number in this report must have been measured in this run.
- Do not recommend a final architecture; the report presents measurements, the team decides.

## Acceptance criteria
Report exists; sample size and validation set size appear before any metric; every figure traceable to
`timings.json` or a metrics computation over `results_*.jsonl`.

## Verify
Cross-check each table cell against the source JSON; confirm no literature figure has leaked in.

## Update graphify / CLAUDE.md
Link the results report from CLAUDE.md and note that the architecture decision now has measured input.
