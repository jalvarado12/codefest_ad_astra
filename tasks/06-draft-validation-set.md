# Task 06 — Draft validation query set

**Status: CODE DONE, EXECUTION BLOCKED — depends on Task 05 (corpus).**

## Goal
A small validation set: natural-language queries stratified across phenomena and languages, each with
DRAFT candidate relevance judgments at fragment and document level.

## Context
Numbering check performed: no prior validation-set file exists anywhere in the repo, so this is the
first one — `arch_test/data/validation_draft.json`.

## Graphify queries to run first
N/A — no graph exists. Read `arch_test/validation.py` directly.

## Read only these files
- `ad_astra.md` §10.1 (evaluation set shape), §10.2.1 matching-key box
- `arch_test/validation.py` (whole file)

## Create/modify exactly these files
- `arch_test/data/validation_worksheet.json`
- `arch_test/data/validation_draft.json`

## Detailed instructions
1. `python arch_test/validation.py worksheet` — samples candidate chunks stratified over
   phenomenon × language, spread across distinct documents.
2. Author ~18 queries (2 per phenomenon × language cell) by reading the worksheet. Write realistic
   analyst questions, not keyword bags. Include some cross-lingual cases: a Spanish query whose best
   evidence is an English or Portuguese document — that is the §1.1 requirement the whole multilingual
   argument rests on. Save as `validation_draft.json` with `status: "DRAFT"`.
3. `python arch_test/validation.py pool --depth 20` — pools candidates from all three retrievers (RRF
   union) and attaches them as `candidates_DRAFT` with `relevancia_DRAFT: null` for a human to fill.
   Requires the encode stage (Task 08) to have produced the cached encodings first.
4. Relevance scale: integer 0–3. Document-level judgments are lists of **`fuente`** values.

## Do NOT
- **Do not treat any LLM-drafted query or judgment as ground truth.** Everything stays `DRAFT` until a
  human reviews it. Scoring the systems under test against labels those same systems helped produce,
  unreviewed, would make the comparison circular.
- Do not write `validation_confirmed.json` by hand — only `validation.py confirm` may create it.

## Acceptance criteria
`validation_draft.json` exists, `status: "DRAFT"`, every query carries `reviewed: false`, all three
languages and all three phenomena represented, and
`python arch_test/validation.py check arch_test/data/validation_draft.json` reports no schema problems.

## Verify
Gate logic self-tested now (`python arch_test/validation.py selftest` passes): it rejects a
non-CONFIRMED status, rejects unreviewed queries, rejects missing language/phenomenon coverage, and
rejects non-integer relevance values.

## Update graphify / CLAUDE.md
On execution, record the validation set size and its phenomenon/language grid.
