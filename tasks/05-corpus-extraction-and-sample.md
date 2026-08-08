# Task 05 — Corpus extraction and stratified sample

**Status: CODE DONE, EXECUTION BLOCKED — the ADL corpus is not on this machine (see Task 01).**

## Goal
Walk the real corpus, extract text from every ADL format, and draw a stratified sample of ~100
documents balanced across phenomenon × language.

## Context
Sample size **~100 documents / ~3000 chunks** was chosen by the human. See the cost warning in
`arch_test/STAGE1-GATE.md` — that choice was made against a throughput estimate that later measurement
showed to be ~10× too optimistic.

## Graphify queries to run first
N/A — no graph exists. Read `arch_test/corpus.py` directly; do not go searching for it.

## Read only these files
- `ad_astra.md` §2.1 (per-format extraction), §2.2 (cleaning), §2.3 (doc_id)
- `arch_test/corpus.py` (whole file, ~280 lines)

## Create/modify exactly these files
- `arch_test/data/manifest_full.json`
- `arch_test/data/sample.json`
- `arch_test/data/skipped.json`

## Detailed instructions
Run: `python arch_test/corpus.py <CORPUS_ROOT> --n-docs 100`
1. Extraction is implemented per format: PDF (pypdf + running-header/footer removal), HTML (bs4, strip
   script/style/nav/footer), JSON (explicit body fields, descriptive fields kept as metadata), CSV/XLSX
   (`column: value` pairs per row), MD/TXT raw, images (OCR — requires the tesseract binary), PBF
   (requires a vector-tile reader).
2. Phenomenon comes from the path (`fenomeno_2`, `phenomenon-2`, `f2`) when the corpus is foldered that
   way, else from a keyword heuristic. **Check which mode actually fired** and say so in the report.
3. Language via langdetect, restricted to reporting es/en/pt (anything else surfaces in the log).
4. Sampling round-robins over phenomenon × language cells and prefers mid-length documents.

## Do NOT
- Do not let unreadable files vanish silently — they land in `skipped.json` with a reason and are
  counted in the run output. A sample that quietly lost every PDF would invalidate the comparison.
- Do not assume the ~2000-document figure or an even format mix; report what is actually there.

## Acceptance criteria
`manifest_full.json`, `sample.json`, `skipped.json` exist; the printed phenomenon × language grid shows
all three phenomena and all three languages populated; the skip list has been read, not just written.

## Verify
Not yet run — no corpus. Extraction code imports cleanly and every dependency is installed
(`pypdf`, `bs4`, `lxml`, `openpyxl`, `langdetect`, `psutil`). OCR and PBF will report themselves as
unavailable on this machine unless the tesseract binary / a vector-tile reader is installed; those
files will appear in `skipped.json` rather than being dropped.

## Update graphify / CLAUDE.md
On execution, record the real corpus size, format mix, skip count, and the sample grid in CLAUDE.md.
