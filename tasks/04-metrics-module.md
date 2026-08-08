# Task 04 — Metrics module (NDCG@10, F1@3)

**Status: DONE (2026-08-05, executed in-session)**

Built early although it is a Stage 2 deliverable: it depends on the spec only, not on the corpus or on
the validation set, so it was free to do while blocked. **No metric has been computed against any
unconfirmed judgments** — the gate is intact.

## Goal
NDCG@10 and F1@3 exactly as the CODEFEST spec defines them, not as a library defines them.

## Context
Library defaults diverge from the spec: sklearn's `ndcg_score` applies its own tie-averaging, and
several IR toolkits use exponential gain `2^r − 1` where the spec writes linear gain `r_i`.

## Graphify queries to run first
N/A — no graph exists.

## Read only these files
- `ad_astra.md` §10.2.1 (eqs. 8–10), §10.2.2 (eqs. 11–14), and the §10.2.1 matching-key box

## Create/modify exactly these files
- `arch_test/metrics.py`

## Detailed instructions
1. `DCG@k = Σ r_i / log2(i+1)`, i starting at 1; `NDCG@k = DCG@k / IDCG@k` where IDCG uses the full
   ground-truth relevance list (so relevant fragments the system missed still enlarge IDCG).
2. `P@3 = hits/3`, `R@3 = hits/min(|D*|,3)`, `F1 = 2PR/(P+R)`, 0 when `P+R == 0`.
3. Document matching goes through **`fuente`**, never the team-assigned `doc_id` (§10.2.1 box).
4. Mean over the query set; a judged query the system did not answer scores 0, not skipped.

## Do NOT
- Do not substitute a library implementation.
- Do not use exponential gain.
- Do not match documents on `doc_id`.

## Acceptance criteria
`python arch_test/metrics.py` prints the self-check OK line.

## Verify
Self-check asserts hand-computed values: `DCG([3,0,2]) == 4.0` exactly;
`NDCG([3,0,2] | ideal [3,2]) == 4/(3+2/log2 3) == 0.93855745`; perfect ranking `== 1.0`; rank-order
sensitivity (`DCG([1,3]) < DCG([3,1])`); missed relevant fragments push NDCG below 1;
`F1@3` cases 0.4 / 1.0 / 0.5 / 0.0 and order-invariance. Passing.

## Update graphify / CLAUDE.md
Recorded in CLAUDE.md under the harness section.
