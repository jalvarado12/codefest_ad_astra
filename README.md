# AD Astra — CODEFEST AD ASTRA 2026, Etapa 1

Vector knowledge base over an ES/EN/PT corpus (AI in defense, LEO space security /
space debris, Latin American territorial dynamics) for Universidad de los Andes /
Fuerza Aeroespacial Colombiana. This README documents every Python module in the
repository: what it does, how to call it, what it's responsible for, and why it
exists. For the competition spec itself see `ad_astra.md`; for the architecture
decision trail see `embedding_research_report.md` and
`final-architecture-decision-report.md`.

## Repository layout

```
AD_Astra/
  json_extract.py            deterministic JSON text extractor (submission-grade)
  catalog_metadata.py        resolves catalog/registry JSON files to real documents
  sample_json_corpus.py      extracts the full JSON corpus for chunking hand-off
  sort_by_extension.py       dev-only corpus copy, split by file extension
  arch_test/
    chunker.py                reference sentence-safe chunker
    corpus.py                 multi-format extraction + stratified sampler
    encoders.py                encoder wrappers (e5-large, BGE-M3, MiniLM)
    harness.py                 shared machinery: FAISS, sparse index, RRF, scoring, CLI
    metrics.py                  NDCG@10 / F1@3 from the spec's own formulas
    validation.py                validation-set drafting, pooling, human-confirmation gate
    colab_job.py                  VM-side staged driver (selftest/verify/bench/pipeline/score)
    arch_a_e5_dense.py             architecture A entry point
    arch_b_bgem3_hybrid.py          architecture B entry point
    arch_c_e5_plus_bgem3_sparse.py   architecture C entry point
    arch_d_minilm_dense.py           architecture D entry point
    arch_e_minilm_plus_bgem3_sparse.py architecture E entry point
    _winshim/termios.py             Windows stub so the Colab CLI imports locally
    _winshim/tty.py                  Windows stub, depends on termios.py above
```

Two execution environments matter throughout: **this machine** (Windows, no GPU,
some heavy ML libraries cannot even import — see `encoders.py`) and **Colab**
(Linux + GPU, where every model actually runs). Files are written to work
correctly in whichever environment they're meant for; several self-check without
touching a model at all.

---

## Root-level tools

### `json_extract.py`

**Functionality.** Deterministic, tiered extractor that turns one corpus JSON
file into `(texto, meta, traza)` — body text, descriptive metadata, and a
provenance trace of what happened. It walks the parsed JSON in insertion order,
classifies every key as body text, metadata, noise (link/asset containers), or
unknown, and assembles the body by joining recognized text fields in document
order while deduplicating (a `body_paragraphs` list and a `body_text` blob that
duplicates it are not both emitted). Three possible outcomes, always recorded:
tier 1 (alias table matched cleanly), tier 2 (fallback filtered sweep rescued
text the alias table missed), tier 3 (`JsonSinTexto` — parsed but no usable
prose, or `JsonIlegible` — not parseable as JSON or NDJSON at all).

**Syntax / usage.**
```bash
python json_extract.py                    # runs the 17-fixture self-check
python json_extract.py --run <DIR> [--log PATH]   # extract every .json under DIR
python json_extract.py --resumen <LOG>     # print a schema census from a provenance log
```
Programmatic API:
```python
from json_extract import extract_json, JsonIlegible, JsonSinTexto

texto, meta, traza = extract_json(path)   # raises JsonIlegible / JsonSinTexto
```
`extract_json` is a pure function of the file's bytes — no model, network,
clock, or filesystem-order dependence — because spec section 1.4 requires
`generador.py` to reproduce results exactly.

**Scope.** JSON files only. Does not touch PDF/HTML/CSV/XLSX/images — that's
`corpus.py`'s job, which delegates its own `_json()` extractor to this module so
the harness and the eventual submission extractor can never drift apart.

**Importance.** This is the field-classification core the whole pipeline's JSON
handling rests on. It was validated against the full 964-file corpus census
(see `json-extraction-audit-doubts.md`, `json-schema-survey.md`): 30 URL leaks
and 2 date leaks were found and are documented as either fixed or verified
benign (legitimate embedded citations). Any change here has to be re-validated
against that census, not just the 17 fixtures in `_selfcheck`.

---

### `catalog_metadata.py`

**Functionality.** The corpus contains 20 catalog/registry JSON files (scraper
bookkeeping: title/url/status per downloaded PDF) that either (a) fail
`json_extract`'s tier-3 floor and get silently dropped, losing real per-document
metadata the matching PDF itself often lacks, or (b) pass tier-1 and become fake
pseudo-documents. This module does neither: it matches each catalog entry to the
real file it describes (already present in the corpus in its own right) and
writes the catalog's metadata keyed to that file's `fuente`, for a downstream
indexing step to merge in. Matching is two-tier: first an explicit filename/URL
field on the entry (`FILENAME_FIELDS`, normalized and checked against a
by-extension index of every file on disk), then — if nothing named a filename
directly — a title-based fallback using `difflib`'s longest-common-substring
against normalized `.json` file stems (`MIN_TITLE_MATCH = 20` chars, so short
coincidental overlaps don't produce false matches). Entries that cannot be
matched (e.g. MAPPOEA: 68 of 78 entries are dead 404/503 scrapes) are simply
omitted, not fabricated.

**Syntax / usage.**
```bash
python catalog_metadata.py                         # runs the 2-fixture self-check
python catalog_metadata.py "<corpus_root>" [--out catalog]   # process the real corpus
```
Output: `<out>/catalog_metadata.jsonl`, one line per matched entry:
`{"catalogo": <filename>, "fuente": <matched document's relative path>, "metadata_catalogo": <original entry dict>}`.

**Scope.** Only the 20 filenames in the hardcoded `CATALOG_FILES` set (observed,
not speculative — see the module docstring and `json-extraction-audit-doubts.md`
finding #1). The corpus root is a CLI argument, not `corpus_by_type/`, because
that directory is a local dev convenience (`sort_by_extension.py`) that won't
exist in production.

**Importance.** Without this step, 6 catalog files leak into the index as
spurious documents with no real prose, and 14 more silently lose metadata
(author, country, year, tags) that has nowhere else to come from. **Not yet
wired into the main pipeline** — the catalog files still need to be excluded
from `corpus.py`'s document tree walk, and `metadata_catalogo` still needs to be
merged into the real documents' records. That's the next integration step.

---

### `sample_json_corpus.py`

**Functionality.** Extracts every JSON document in the real corpus
(`CORPUS CODEFEST AD ASTRA 2026/`) via `json_extract.extract_json`, excluding
the 20 catalog/registry files (imported from `catalog_metadata.CATALOG_FILES`),
and writes one JSON object per line to `sample_for_chunking.jsonl`. Each record
carries `doc_id` (assigned `DOC-0000`, `DOC-0001`, ... in walk order),
`fuente` (path relative to the corpus root, forward slashes), `formato`
(`"json"`), `fenomeno` (1/2/3, read from the top-level folder name), `texto`
(full extracted body text — not yet chunked), and `n_words`.

`fenomeno_from_top_folder` reads the phenomenon straight from the corpus's
real top-level folders — `F1_IA_y_Capacidades_Estrategicas`,
`F2_Seguridad_Entorno_Espacial`, `F3_Dinamicas_Territoriales` — via
`re.match(r"[Ff]([123])_", top)`. This exists as its own function rather than
reusing `corpus.py`'s `phenomenon_from_path` because that regex requires a word
boundary immediately after the digit (`\b`), which never matches when the digit
is followed by `_` (both are word characters) — it silently fails on every real
folder name and returns `fenomeno: 0` for everything. This module's regex is
the one that actually works against the production layout.

**Syntax / usage.**
```bash
python sample_json_corpus.py
```
No arguments; reads `CORPUS_ROOT = Path("CORPUS CODEFEST AD ASTRA 2026")`
relative to the current working directory and writes
`sample_for_chunking.jsonl` there too. Prints a summary: total extracted,
total skipped (tier-3/unparseable), and a per-(fenomeno, source-org) tally.

**Scope.** JSON documents only (same boundary as `json_extract.py`). Does not
chunk — it hands a partner (or `chunker.py`) full document text to chunk
downstream. Currently extracts the *entire* JSON corpus, not a stratified
subsample (an earlier version drew a 30-doc stratified sample; that mode was
removed at the user's request in favor of full extraction).

**Importance.** This is the JSON-corpus hand-off artifact: 932 documents
extracted, 12 skipped as tier-3/unparseable, matching the `json_extract.py`
audit baseline (938 with text out of 964 total JSON files, minus the 6
catalog files that leaked through tier-1 and are now correctly excluded).
Known caveat: some source files carry mojibake (e.g. a right single quote
rendered as `�`) because `json_extract._load` reads with
`errors="replace"` — this is pre-existing extractor behavior, not introduced
here, and should be flagged to whoever chunks the sample next.

---

### `sort_by_extension.py`

**Functionality.** Copies every file under `CORPUS CODEFEST AD ASTRA 2026/`
into `corpus_by_type/<extension>/`, flattening the source hierarchy so all
PDFs sit together, all JSONs sit together, etc. Filename collisions are
resolved by appending a counter (`stem__N.ext`). Carries no metadata — it's a
raw file copy for ad hoc extractor testing, not a manifest.

**Syntax / usage.**
```bash
python sort_by_extension.py
```
No arguments. Exits with an error if `CORPUS CODEFEST AD ASTRA 2026/` isn't
present in the working directory.

**Scope.** Local development convenience only.

**Importance.** `corpus_by_type/` is **not part of production** — the real
corpus is organized by phenomenon/organization, and `catalog_metadata.py`'s
docstring explicitly calls out that any script depending on `corpus_by_type/`
existing will break in production. Both `corpus_by_type/` and the real corpus
directory are gitignored (3 GB each) — this script exists only to regenerate
that convenience copy locally if needed, never to be committed.

---

## `arch_test/` — architecture comparison harness

Empirical A/B/C(/D/E) comparison built to replace the shortlist report's
"inferred, unmeasured" caveats with real numbers. Every file here targets
Colab for anything touching an actual model; `chunker.py`, `metrics.py`, and
`validation.py` self-check locally with no GPU or downloads required.

### `arch_test/chunker.py`

**Functionality.** Reference chunker implementing spec section 3.3 (linguistic
completeness — no chunk may contain a truncated sentence) and section 3.4
(mandatory chunk metadata). `split_sentences(text)` splits ES/EN/PT prose on
sentence terminators (`.!?…`) using a regex that respects closing
quotes/brackets and Spanish inverted punctuation (`¿¡`), while a hardcoded
abbreviation set (`_ABBREV`: `sr`, `dr`, `ee.uu`, `etc`, `vol`, ...) and a
single-letter-initial check stop it from splitting on `Dr.` or `A.` mid-name.
`clean(text)` NFC-normalizes, strips control characters, and collapses
redundant whitespace (spec section 2.2). `chunk_document(...)` greedily packs
sentences into chunks up to `max_tokens` (default 480 — deliberately under the
512 ceiling to leave headroom for the encoder's special tokens and e5's
`"passage: "` prefix), emits an oversized single sentence alone rather than
ever cutting mid-sentence, and merges a runt trailing chunk (< `min_tokens=32`)
into its predecessor when it still fits, to avoid noisy near-empty chunks in
the index.

**Syntax / usage.**
```bash
python chunker.py     # runs _demo(), the self-check
```
Programmatic API:
```python
from chunker import clean, split_sentences, chunk_document

chunks = chunk_document(text, doc_id, fuente, formato, fenomeno,
                        count_tokens, max_tokens=480, min_tokens=32)
# -> list[dict] with doc_id, chunk_id, fuente, formato, fenomeno, posicion,
#    num_tokens, texto, oversized  (spec Table 1 fields + one extra)
```
`count_tokens` is caller-supplied (a real tokenizer's `.encode` length in the
harness) so the chunker itself has zero model dependency.

**Scope.** **Reference-only.** The module docstring is explicit: this is not
the team's final submission chunker. It exists so architectures A, B, and C
(and D, E) are all fed byte-identical chunks — the one property the comparison
actually needs from a chunker. It implements only what affects chunk
boundaries, nothing more.

**Importance.** Every architecture's index is built from the exact same
`chunks.jsonl` this module produces (via `harness.build_chunks`), so nothing
about chunking itself can bias the encoder comparison. Its self-check asserts
the two properties the whole harness depends on: rejoining every chunk's text
reproduces the source exactly (no lost or reordered content), and no chunk
boundary ever falls mid-sentence.

### `arch_test/corpus.py`

**Functionality.** Two responsibilities: (1) per-format text extraction across
every format the spec requires — PDF (`pypdf`, with repeated running
header/footer stripping via `_drop_repeated_lines`, spec 2.2), HTML
(`BeautifulSoup`, stripping script/style/nav/footer/form), JSON (delegates to
`json_extract.extract_json`), CSV/TSV (`column: value` per row, sniffed
dialect), XLSX (`openpyxl`, `header: value` per row), images (OCR via
`pytesseract`, `spa+eng+por` language packs), and PBF map tiles (stub — raises
`RuntimeError`, no vector-tile reader wired in); and (2) a phenomenon ×
language stratified sampler over the resulting manifest.
`build_manifest(root, phenomenon_mode)` walks every file, extracts it, records
successes with fenomeno/idioma/word-count and failures in `SKIPPED` with a
reason (nothing is ever dropped silently). `stratified_sample(manifest,
n_docs)` round-robins across `(fenomeno, idioma)` cells so no single
combination (typically ES × phenomenon 3) dominates a fixed-size sample, and
within each cell prefers documents near the cell's median word count over
outlier stubs or tomes.

**Syntax / usage.**
```bash
python corpus.py <corpus_root> [--n-docs 100] [--out arch_test/data] \
                  [--phenomenon-mode auto|path|keywords]
```
Writes `manifest_full.json`, `sample.json`, `skipped.json`, and
`extraccion_json.jsonl` (the JSON-extraction provenance log, for
`json_extract.py --resumen`) under `--out`.

**Scope.** Full-corpus, all formats — this is the harness's only entry point
for anything that isn't a JSON file. Depends on `pypdf`, `bs4`/`lxml`,
`openpyxl`, `pytesseract`/`Pillow`, `langdetect` — libraries this Windows box
can generally still import (unlike `sentence-transformers`/`FlagEmbedding`,
see `encoders.py`).

**Importance — known defect.** `phenomenon_from_path` uses
`re.search(r"(?:fen[oó]meno|phenomenon|fenomeno|ph|f)[_\-\s]?([123])\b", rel)`.
Against the real corpus's actual folder names (`F1_IA_y_Capacidades_Estrategicas/...`),
this **does not match**: `\b` requires a word/non-word boundary immediately
after the captured digit, but the digit is followed by `_`, which is itself a
word character, so there's no boundary there. Every document falls through to
`guess_phenomenon`'s keyword-scoring fallback (or returns `fenomeno: 0` if
`phenomenon_mode="path"` only). `sample_json_corpus.py` was written with its
own corrected regex (`re.match(r"[Ff]([123])_", top)`) specifically because of
this. **This regex should be fixed here too** before `corpus.py` is relied on
against the production corpus root.

### `arch_test/encoders.py`

**Functionality.** Thin wrappers around each candidate encoder so every
architecture calls the same interface. `_STDense` (subclassed by `E5Dense` and
`MiniLMDense`) wraps `sentence-transformers`, applies query/passage prefixes
*by construction* (there is no code path that encodes an unprefixed string —
critical for e5, whose mandatory `"query: "`/`"passage: "` prefixes silently
degrade quality if omitted), and L2-normalizes output so cosine similarity
reduces to inner product (spec 8.2). `BGEM3` wraps `FlagEmbedding`'s
`BGEM3FlagModel` and returns dense **and** sparse (lexical-weight) vectors from
one forward pass (`return_dense=True, return_sparse=True`) — the reason
architecture B costs ~1× rather than 2×. `sparse_dot(q, d)` computes the same
lexical-matching dot product FlagEmbedding uses internally, defined locally so
the harness's inverted index can't drift from the library's own scoring.
`MiniLMDense` is documented as deliberately out-of-distribution: English-only
training data against an ES/EN/PT corpus, and a 256-token training ceiling
against the chunker's ~450-token budget — both measured, not hidden.

**Syntax / usage.**
```bash
python encoders.py     # runs _verify(): loads both models, sanity-checks output
```
Programmatic API:
```python
from encoders import E5Dense, BGEM3, MiniLMDense, sparse_dot

e5 = E5Dense()                       # or MiniLMDense()
vecs = e5.encode_passages(texts)     # or .encode_queries(texts)
n_tok = e5.count_tokens(text)

bge = BGEM3()
dense, sparse = bge.encode(texts, want_dense=True, want_sparse=True)
```

**Scope. Colab/Linux+GPU only.** The module docstring states plainly: this
does not import on the local Windows box. `sentence-transformers` and
`FlagEmbedding` pull in `sklearn`, whose `pyarrow` dependency trips a Windows
Application Control policy (`DLL load failed while importing lib`). Do not try
to "fix" this file to import locally — the fix was moving execution to Colab
entirely (`colab_job.py`, `run_on_colab.sh`).

**Importance.** This is where the encoder-choice trade-offs the whole
competition entry hinges on actually get instantiated and measured: prefix
correctness, normalization, and the dense+sparse-from-one-pass cost model that
makes architecture B ~1× instead of ~2×.

### `arch_test/harness.py`

**Functionality.** The shared machinery every `arch_*` entry point calls
into — deliberately centralized so nothing that could bias the comparison
lives in a per-architecture file. Responsibilities, in the order a run
touches them:

- **`ARCHS`** — the routing table: `{"A": ("e5", None), "B": ("bge", "bge"),
  "C": ("e5", "bge"), "D": ("minilm", None), "E": ("minilm", "bge")}`, mapping
  each architecture letter to its (dense encoder key, sparse encoder key).
- **`Phase`** — a context manager that times a block and samples peak RSS
  (and GPU peak, under CUDA) via a background polling thread, because a phase
  that loads a ~2.3 GB model and frees it before exiting would otherwise
  report a deceptively low end-of-phase reading.
- **`build_chunks` / `load_chunks`** — runs every sampled document through
  `chunker.chunk_document` once, writes `chunks.jsonl`; every architecture
  reads this same file, so no chunking difference can leak into the
  comparison.
- **`encode_cached` / `encode_all` / `encode_for`** — runs a model over every
  chunk's text, caching dense (`.npy`) and sparse (`.pkl`) output to disk so a
  multi-hour CPU run can survive interruption; `encode_for(arch, ...)`
  encodes only the models architecture `arch` actually needs.
- **`build_faiss`** — `faiss.IndexFlatIP` over the (already L2-normalized)
  dense vectors, i.e. exact cosine search (spec 5.2, 8.2).
- **`SparseIndex`** — a plain Python inverted index (`token_id -> [(chunk_idx,
  weight), ...]`) over BGE-M3's lexical weights, scored by the same dot
  product `encoders.sparse_dot` uses.
- **`rrf(rank_lists, k0=60)`** — Reciprocal Rank Fusion per spec eq. 7,
  `score = Σ 1/(k0 + rank)`, combining dense and sparse rankings without
  needing to normalize their differently-scaled raw scores.
- **`to_fragments` / `to_documents`** — shape retrieval output into the
  spec's exact §9.2/§9.2.1 form: exactly 10 fragments each ≤ 250 words
  (an oversized chunk is split on sentence boundaries into sub-fragments that
  keep the *original* `chunk_id` for traceability, each taking its own rank
  slot), and exactly 3 documents via max-pooling aggregation keyed on
  `fuente` (never `doc_id`, per spec 10.2.1) — padded with unseen documents
  if the candidate pool has fewer than 3 distinct sources, since a
  `documents` array of the wrong length is penalized regardless of quality.
- **`run_architecture`** — runs one architecture's full query loop: encodes
  each query with exactly the model(s) its index was built from, searches
  dense (and sparse, RRF-fused, if applicable) to depth 100, records
  per-query latency.
- **`indexing_cost`** — sums every forward-pass phase's `seconds` for the
  keys an architecture uses, **even when those passes were served from
  cache** — a two-model architecture (C, E) is charged both passes so its
  ~2× compute cost can't disappear into a caching implementation detail.
- **`load_confirmed` / `to_qrels`** — loads
  `data/validation_confirmed.json`, hard-refusing (`SystemExit`) if the file
  is missing or its `status` isn't `"CONFIRMED"`. This is the gate: scoring
  an LLM-drafted judgment set that the retrievers under test helped produce
  would be circular.
- **`run_and_score` / `merge_summary`** — runs one architecture end to end,
  writes `results_<arch>.jsonl`, scores it against qrels via
  `metrics.evaluate_run` if provided, and folds its summary row into
  `data/summary.json` without clobbering other architectures' rows.
- **`cli(arch, argv=None)`** — the shared `main()` every `arch_*.py` file
  calls with its own letter. Flags: `--chunk` (rebuild `chunks.jsonl`
  first), `--encode-only` (stop after the model pass, useful for warming the
  cache), `--queries PATH` (default `data/validation_confirmed.json`),
  `--batch-size N`, `--unscored` (produce `results_<arch>.jsonl` without a
  confirmed validation set — no metrics, useful before validation exists).

**Syntax / usage.**
```bash
python harness.py     # runs _selftest(): wiring check, all 5 architectures, stub encoders
```
Not normally invoked directly for a real run — `arch_a_e5_dense.py` etc. call
`harness.cli("A")` and so on. Direct API access:
```python
import harness
harness.build_chunks()
chunks = harness.load_chunks()
timings = {}
enc = harness.encode_for("B", chunks, timings)
row = harness.run_and_score("B", queries, chunks, enc, timings, qrels)
```

**Scope.** Everything shared across architectures: chunking orchestration,
encoding/caching, both index types, fusion, output shaping, cost accounting,
and scoring. Per-architecture files contain only routing (which letter) and
documentation of what that architecture is.

**Importance.** This is the comparison's fairness guarantee made concrete —
`_selftest()` (stub encoders, no GPU, runs in ~1 second) exists specifically
to catch the failure this kind of shared-machinery refactor can produce
silently: an architecture retrieving with the wrong model, skipping its
sparse half, or being charged the wrong set of forward passes. It asserts
index byte-size proves which dense model was used and whether sparse was
fused, and that cost attribution matches the documented rule (`{"A": 100.0,
"B": 10.0, "C": 110.0, "D": 1.0, "E": 11.0}` given synthetic per-model costs
of e5=100, bge=10, minilm=1).

### `arch_test/metrics.py`

**Functionality.** `NDCG@10` and `F1@3` implemented directly from the spec's
own formulas (`ad_astra.md` sections 10.2.1/10.2.2, equations 8–14) rather
than taken from a library — deliberately, because `sklearn.metrics.ndcg_score`
applies its own tie-averaging and several IR toolkits use exponential gain
(`2^r - 1`) where the spec writes linear gain (`r_i`), either of which would
move the reported numbers. `dcg_at_k` / `ndcg_at_k` implement eqs. 8–9;
`f1_at_3` implements eqs. 11–13 (`P@3 = |hit|/3`, `R@3 = |hit|/min(|D*|,3)`,
harmonic mean) as an order-insensitive set metric; `evaluate_run(results,
qrels, k=10)` scores a full run — a judged query the system never answered
scores 0, not skipped, and the mean is unweighted over the qrels query set.

**Syntax / usage.**
```bash
python metrics.py     # runs _demo(): every value checked against hand computation
```
Programmatic API:
```python
from metrics import dcg_at_k, ndcg_at_k, f1_at_3, evaluate_run

mean_ndcg, mean_f1, per_query_rows = evaluate_run(results, qrels, k=10)
```

**Scope.** Pure scoring functions only — no I/O beyond what's passed in. Two
matching rules baked in per the spec: fragment relevance judges the fragment
**text**, `chunk_id` is traceability only (sound as a proxy *inside this
harness*, where all architectures share one chunker's byte-identical chunks —
would not be sound against the organizers' own ground truth); document
matching goes through `fuente`, never the team's own `doc_id`.

**Importance.** These are the two numbers the entire competition entry is
optimized against (Borda-combined across two leaderboards). The self-check
hand-verifies specific values (e.g. `NDCG@10([3,0,2] vs [3,2] ideal) ≈
0.93855745`) so a subtle bug here can't silently misrank every architecture
comparison the harness produces.

### `arch_test/validation.py`

**Functionality.** Builds and gates the human-judged validation query set
used to score architectures, in five steps: (1) `worksheet()` samples
candidate chunks stratified over `(fenomeno, idioma)`, spread across distinct
documents, for a human to write realistic queries from; (2) a human authors
queries and saves them as `validation_draft.json` with `status: "DRAFT"`;
(3) `pool_candidates()` runs all three retrievers (e5 dense, BGE-M3 dense,
BGE-M3 sparse) to depth 20, RRF-fuses them (standard TREC-style pooling), and
attaches the union as `candidates_DRAFT` with `relevancia_DRAFT: null` for a
human to actually grade; (4) a human reviews and grades each candidate,
setting `reviewed: true` per query; (5) `confirm(reviewer)` promotes the
draft to `validation_confirmed.json` with `status: "CONFIRMED"`. `check(doc,
require_confirmed)` validates schema (no duplicate query IDs, every query has
text, relevance judgments are ints in `[0,3]`, document-level judgments
exist) and stratification coverage (all three languages, all three
phenomena present). `confirm` has **no `--force`**: it hard-refuses
(`SystemExit`) if any query lacks `reviewed: true`.

**Syntax / usage.**
```bash
python validation.py worksheet [--per-cell 6]
python validation.py pool [--depth 20]
python validation.py confirm --reviewer "Name"
python validation.py check <path.json>
python validation.py selftest      # equivalent to: python validation.py, direct self-check
```
`selftest`/direct-run exercises `_demo()` — the gate logic's own self-check,
described in the module docstring as "the one thing here that must not fail
open."

**Scope.** Validation-set lifecycle only; does not run architectures itself
(`pool_candidates` imports `harness`/`encoders` to search cached
encodings, but only to propose candidates, never to judge them).

**Importance.** This is the circularity firewall for the whole scoring
pipeline. `harness.load_confirmed` (called from every `arch_*.py` run) and
`colab_job.py`'s `score` stage both refuse to proceed without a file that
passed through this exact gate — an LLM-assisted draft that a human never
reviewed can never be scored against, because the retrievers under test
helped produce the pooled candidates in the first place.

### `arch_test/colab_job.py`

**Functionality.** The VM-side driver that actually runs on Colab. Five
independently-skippable stages, run in order: `selftest` (subprocess-runs
`chunker.py`, `metrics.py`, `validation.py selftest`, no models needed — fails
loudly if any fails); `verify` (calls `encoders._verify()` — loads both real
models, checks cross-lingual and sparse sanity); `bench` (measures real
throughput in ms/chunk at batch sizes 8 and 32 on actual ~450-token chunks
produced by the real chunker from `ad_astra.md` itself — not toy sentences,
because toy inputs understate cost several-fold and this number decides
whether the full-corpus run is affordable; projects total hours for 1000/
3000/6000 chunks per architecture using the `ARCHS`-style cost model);
`pipeline` (only if `/content/corpus` exists: builds the manifest, draws the
sample, chunks it, encodes every model — skips cleanly with a message
otherwise); `score` (only if `data/validation_confirmed.json` exists: scores
every requested architecture and writes `timings.json` — skips cleanly with
an explanation otherwise, never scores a draft).

**Syntax / usage.**
```bash
python colab_job.py [--stages selftest,verify,bench,pipeline,score] \
                     [--architectures A,B,C,D,E] [--n-docs 100] [--batch-size N]
```
Meant to be invoked on the Colab VM, typically via `run_on_colab.sh` (which
provisions the session, installs dependencies, uploads this file and its
siblings, runs it, downloads results, and stops the session).

**Scope.** Colab/GPU execution orchestration end to end — the only file in
this repo meant to be copied onto and executed on the remote VM as a single
entry point.

**Importance.** This is what turns "the harness works" into "the harness
produced numbers." Its staged, always-skip-cleanly design means a run with no
corpus uploaded yet, or no validation set confirmed yet, still produces
useful partial output (self-check pass/fail, verified model behavior,
throughput projections) instead of crashing partway through.

### `arch_test/arch_a_e5_dense.py` through `arch_e_minilm_plus_bgem3_sparse.py`

**Functionality.** Five nearly-identical entry points, each a thin wrapper:
```python
from harness import cli
if __name__ == "__main__":
    cli("<letter>")
```
All actual logic lives in `harness.py`; these files exist purely as separate,
individually-runnable, individually-documented commands — each docstring
states that architecture's index type, retrieval method, and cost multiple
relative to the ~1× baseline:

| File | Dense | Sparse | Fusion | Cost |
|---|---|---|---|---|
| `arch_a_e5_dense.py` | e5-large | — | — | 1× |
| `arch_b_bgem3_hybrid.py` | bge-m3 | bge-m3 | RRF | ~1× (one forward pass) |
| `arch_c_e5_plus_bgem3_sparse.py` | e5-large | bge-m3 | RRF | ~2× (two forward passes) |
| `arch_d_minilm_dense.py` | MiniLM-L6-v2 | — | — | ≪1× (22M params, 384-dim) |
| `arch_e_minilm_plus_bgem3_sparse.py` | MiniLM-L6-v2 | bge-m3 | RRF | ~1× + change |

**Syntax / usage** (identical shape for all five, letter changes which model(s)
run):
```bash
python arch_a_e5_dense.py [--chunk] [--encode-only] [--queries PATH] \
                           [--batch-size 32] [--unscored]
```

**Scope.** One architecture each, no shared state beyond what `harness.py`
manages.

**Importance.** These are the actual runnable comparison points — D and E are
explicitly "on trial": D establishes a cheap floor to see how much an
English-only, 256-token-trained encoder costs against an ES/EN/PT corpus at a
~450-token chunk budget; E tests whether BGE-M3's multilingual sparse head can
carry the Spanish/Portuguese retrieval that MiniLM's English-only dense side
drops (if D lags A but E doesn't lag B by nearly as much, the lexical head is
doing the multilingual work).

### `arch_test/_winshim/termios.py` and `arch_test/_winshim/tty.py`

**Functionality.** Empty-behavior stand-ins for the POSIX `termios` and `tty`
standard-library modules, which don't exist on Windows. `termios.py` defines
the `TCSANOW`/`TCSADRAIN`/`TCSAFLUSH` constants and an `error` exception class,
with `tcgetattr`/`tcsetattr` both raising `error` unconditionally. `tty.py`
imports `termios` (this shim, not the real one) and likewise makes
`setraw`/`setcbreak` raise.

**Syntax / usage.** Not imported directly — added to `PYTHONPATH` ahead of the
real standard library, by `run_on_colab.sh`, only on Windows (no-op on Linux).
Once on the path, any `import termios` / `import tty` elsewhere in the process
resolves to these instead of failing with `ModuleNotFoundError`.

**Scope.** Exists purely to satisfy module-scope imports; both raise if
actually called; that's intentional.

**Importance.** `colab_cli.console` (part of the `colab` CLI used to
provision/manage the Colab session from this machine) imports `termios`/`tty`
at module scope but only calls into them when stdin is a real interactive TTY
(`colab console`). Every non-interactive command this project's workflow
actually uses never reaches that code path, so an empty shim that raises on
the (unreached) real call is sufficient to let the whole `colab` CLI import
and run on Windows at all.

---

## Environment notes that will bite again

- **Locally**, `sklearn`/`FlagEmbedding`/`sentence-transformers` cannot
  import: their `pyarrow` dependency trips a Windows Application Control
  policy (`DLL load failed while importing lib`). This is *why* model
  execution moved to Colab — do not try to "fix" `encoders.py` to import on
  Windows.
- `json_extract.py`, `catalog_metadata.py`, `sample_json_corpus.py`,
  `sort_by_extension.py`, `chunker.py`, `metrics.py`, and `validation.py`
  are all pure-stdlib-or-light-dependency and run fine locally.
- `corpus.py` runs locally too (its non-JSON extractors use `pypdf`,
  `bs4`/`lxml`, `openpyxl`, `pytesseract`, `langdetect` — none of which trip
  the same policy) but has the `phenomenon_from_path` regex defect described
  above; it hasn't been exercised against the real corpus root yet.
- The real corpus (`CORPUS CODEFEST AD ASTRA 2026/`) and its dev-only mirror
  (`corpus_by_type/`) are both ~3 GB and gitignored — never expect them to be
  present after a fresh clone.
