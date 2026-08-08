# 12 files needing manual call

Source: tier-3 rows (no usable text, JsonSinTexto) in `arch_test/data/extraccion_json.jsonl`,
filtered to the two classes below (14 catalog/registry files excluded — those are legit
scraper machinery, not corpus content).

## 11 empty real-page scrapes (root keys look like a real article, body fields are empty)

- CENIA_dates.json
- CENIA_fechas.json
- CENIA_lineas-de-investigacion.json
- CENIA_research-lines.json
- SWF_11-2-july-2024-newsletter.json
- SWF_13-2-june-2024-newsletter.json
- SWF_13-august-2024-newseltter.json
- SWF_16-2-july-2026-newsletter.json
- SWF_17-october-2024-newsletter.json
- SWF_25-2-june-2026-newsletter.json
- SWF_september-2024-newsletter.json

CENIA files: `contenido_limitado` key present, all text fields empty — site itself
served no body at scrape time. SWF newsletters: full schema (body_text, body_paragraphs,
excerpt...) present, all empty — same story, upstream page had no body when scraped.

## 1 thin (real content, under 30-word floor)

- ESA_space-debris-by-the-numbers.json — has real phenomenon-2 (space debris) content,
  ~17 words, under MIN_WORDS=30 floor in json_extract.py / corpus.py:174.

## What to decide, per file

1. Re-scrape (URL still live) -> replaces the empty file, becomes tier-1 normally.
2. Confirm dead / no body upstream -> drop from corpus, note in submission as known gap.
3. ESA file only: lower MIN_WORDS floor just for it, or hand-write/merge its 17 words
   into metadata instead of body -- floor is corpus-wide, changing it affects all formats,
   not just this file.

No code changed. Files here are copies; originals untouched in corpus_by_type/json/.
