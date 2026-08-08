# Task 07 — Human confirmation gate (STAGE BOUNDARY)

**Status: BLOCKED — cannot be presented until Tasks 05 and 06 have run against the real corpus.**

## Goal
Present the sample's scope and the DRAFT validation set to the human, and obtain confirmation before
any Stage 2 measurement happens.

## Context
This is the gate that keeps the comparison honest. It is not a "proceed if it looks safe" judgment
call. It holds regardless of time pressure or how good the draft looks.

## Graphify queries to run first
N/A — no graph exists.

## Read only these files
- `arch_test/data/sample.json`, `arch_test/data/skipped.json`
- `arch_test/data/validation_draft.json`

## Create/modify exactly these files
- `arch_test/data/validation_confirmed.json` (created **only** by `validation.py confirm`)

## Detailed instructions
1. Present to the human: sample size, phenomenon × language grid, format mix, skipped-file count and
   reasons, chunk count, and the full draft query set with its DRAFT judgments.
2. The human reviews each query's judgments, edits what is wrong, and sets `reviewed: true` per query.
3. `python arch_test/validation.py confirm --reviewer "<name>"`.

## Do NOT
- **Do not begin any Stage 2 task before `validation_confirmed.json` exists.**
- Do not bypass the reviewer requirement. There is deliberately no `--force`.
- Do not hand-author `validation_confirmed.json`.

## Acceptance criteria
`validation_confirmed.json` exists with `status: "CONFIRMED"`, a non-empty `confirmed_by`, and every
query marked `reviewed: true`.

## Verify
Enforcement is mechanical, not a matter of discipline:
- `validation.py confirm` refuses to promote a draft with any unreviewed query (`SystemExit`, no
  `--force` flag exists).
- `harness.py --stage run` exits with an explicit refusal if `validation_confirmed.json` is absent, so
  the metrics path cannot be reached with DRAFT labels.

## Update graphify / CLAUDE.md
Record who confirmed the set and on what date.
