# Task 12 — JSON schema census from the real corpus

**Status: DONE (2026-08-06, executed in-session).** Corpus arrived at `corpus_by_type/json/` (964 files).
Census run, all 26 tier-3 files individually adjudicated, alias table needed **no** additions. The
grounding criterion in `json-extraction-strategy.md` flipped from FAILS to passes.

One item deliberately left open for a human: 12 documents (11 empty scrapes + `ESA_space-debris-by-the-numbers`)
are excluded by `corpus.extract()`'s global 30-word floor at `corpus.py:174`. Changing that floor is a
corpus-wide decision affecting every format, not a JSON one.

## Goal

Turn Task 11's provenance log into the real schema survey, decide the fate of every file the extractor
could not read, and tune the alias table from observed evidence only.

This task exists because the design in `json-extraction-strategy.md` was accepted with **one definition-of-done
criterion failing**: it is not grounded in a real sample of the corpus's own `.json` files, because there
was no corpus to sample. This task is what flips that criterion, or records why it still cannot be
flipped.

## Context

The extractor built in Task 11 is self-surveying by design: every document records which tier fired, which
dotted key paths emitted text, which metadata keys were seen, and — for failures — the file's full
top-level key set. That was a deliberate substitution for a survey pre-pass that could not be run. The
substitution is a mitigation, not evidence. **Until this task runs, the alias table's coverage is an
untested guess and the nesting-frequency question that would justify the recursive traversal's complexity
is unanswered.** Both are stated as open in `json-extraction-strategy.md`.

Prior state worth knowing: Task 05 (corpus extraction and stratified sample) is also blocked on the same
missing corpus, and `arch_test/corpus.py` already routes unreadable files to `skipped.json` with a reason
rather than dropping them. This task's review reads both that file and the JSON provenance log.

## Graphify queries to run first

N/A — no graph exists. Read the files listed below directly.

## Read only these files

- `json-schema-survey.md` — whole file. Its census tables are what this task fills in.
- `json-extraction-strategy.md` — the "what happens on a shape the strategy does not recognize" section
  and the status table.
- `json_extract.py` — the alias tables and the tier-2 filter constants, which are the only things this
  task is permitted to change.
- `arch_test/data/extraccion_json.jsonl` — the provenance log, after the run.
- `arch_test/data/skipped.json` — for JSON files that failed before reaching the extractor.

## Create/modify exactly these files

- **Modify** `json-schema-survey.md` — fill every `_TBD_` with real numbers.
- **Modify** `json_extract.py` — alias tables only, and only with keys actually observed.
- **Modify** `json-extraction-strategy.md` — the status table's grounding row.
- **Modify** `CLAUDE.md` — record the real tier distribution and any alias additions.

## Detailed instructions

1. Confirm the corpus root exists and note its path. If it does not, stop here and report the task as
   still blocked — do not substitute the smoke fixtures in `arch_test/data/_smoke/` for real data.
2. Run `python arch_test/corpus.py <CORPUS_ROOT> --n-docs 100`. The JSON provenance log is written as a
   side effect of extraction.
3. Run `python json_extract.py --resumen arch_test/data/extraccion_json.jsonl`.
4. **Reconcile the totals first.** Tier 1 + tier 2 + `JsonSinTexto` + `JsonIlegible` must equal the number
   of `.json` files under the corpus root. A shortfall is an accounting bug in Task 11's logging and must
   be fixed before anything below is trustworthy — the coverage guarantee is exactly this sum.
5. **Read every tier-3 (`JsonSinTexto`) failure individually.** For each, open the file, look at its
   top-level key set, and record a verdict: *missing alias* (its body lives under a key the table does not
   know) or *genuinely textless* (a manifest, a config, a pure-numeric dataset). No tier-3 file may be left
   unexamined. This is the coverage guarantee being cashed in, and it is a human judgement, not a
   heuristic.
6. **Read a sample of tier-2 documents** — at least ten, or all of them if fewer. Confirm the filtered
   fallback produced readable prose and not id soup. If urls, dates or slugs appear in the body, the
   tier-2 filter needs tightening; that is a real §2.1 violation, not a cosmetic one.
7. **Extend the alias tables only from key names actually observed** in steps 5 and 6. Record the evidence
   (which files) for each addition in the survey's final table. Never add a key speculatively — an
   unused alias is untested surface that will not be exercised again before submission.
8. Re-run steps 2-4 and confirm the tier distribution improved: tier 3 should be empty or contain only
   files judged genuinely textless, and tier 2 should have shrunk.
9. Fill the census tables in `json-schema-survey.md`, including the nesting table — that is the one that
   retroactively decides whether the recursive traversal earned its complexity or whether flat-key mapping
   would have covered the real corpus. Record the answer either way; a negative result is a finding.

## Do NOT

- Do **not** run this against `arch_test/data/_smoke/` or against hand-made files. The whole point of this
  task is real corpus evidence; synthetic input would reproduce the exact gap it is meant to close.
- Do **not** add alias keys that were not observed, and do **not** loosen the tier-2 length or pattern
  filters to make tier-3 numbers look better. A file recovering as id soup is worse than a file flagged.
- Do **not** change the walk logic, the tier structure, the word floor, or the load stage. If those turn
  out wrong, that is a finding to report, not a change to make inside this task.
- Do **not** introduce an LLM step to classify the leftovers. Rejected in the design on §1.4 determinism
  and the §4.2/§8.3 decoder ban; a small tier-3 residue does not reopen that.
- Do **not** widen scope to other formats or to chunking.
- Do **not** commit or push.

## Acceptance criteria

- Every `_TBD_` in `json-schema-survey.md` is filled with a real number or a real key name.
- The four states sum to the corpus's `.json` file count.
- Every tier-3 file has an individual recorded verdict.
- Alias additions each cite the files that motivated them.
- The status table in `json-extraction-strategy.md` has its grounding row updated — to **passes**, or to a
  specific written statement of what is still unresolved. Do not mark it passing on partial evidence.

## Verify

- `python json_extract.py` — Task 11's self-check still passes after any alias-table edit.
- Re-run the census and confirm the reported distribution matches what is written in the survey.

## Update graphify / CLAUDE.md

Record the real `.json` file count, the tier distribution before and after tuning, any alias-table
additions, and the nesting-frequency finding. If the nesting table shows flat structures dominate, say so
plainly — that is useful information about the corpus even though it means the recursion was insurance
rather than necessity.
