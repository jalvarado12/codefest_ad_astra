# Spec 03 — Next steps, debt, and retrieval-only improvements

Two things live here: the debt carried by the decisions taken on 2026-08-12, and the levers
still available to improve **retrieval only**, within the challenge rules.

Every improvement listed is encoder-only or arithmetic. §8.3 bans generative decoder models
anywhere in retrieval — no LLM reranking, no query expansion or reformulation, no
generation-based filtering, no synthesis of fragments. Nothing below crosses that line.

---

## Part A — Debt from the decisions taken

Ordered by likely cost to the score.

### 1. Tabular content is 61% of the index

**Decision: index the tables for now; test and handle later.**

**30** CSV/XLSX files hold 16,732,001 words → ~66,900 chunks, against ~38,000 for 759 PDFs and
~4,600 for 954 JSONs. Row text (`columna: valor | ...`) competes with prose for every top-k
slot.

Not a cost problem — ~110k chunks embeds in well under an hour on a T4 *at batch 64*
(`E5Dense` defaults to 8, which is several times slower and would break that estimate);
`dense.npy` is ~450 MB and flat search stays fast. It is a composition problem.

**Order changed after audit:** inspecting the single 7.1M-word CSV moves *before* the Colab
run, not after results. It is ~43% of tabular volume and ~26% of the whole index; if it is a
coordinate dump rather than prose it does not belong in a semantic index, and removing it cuts
a quarter of the encode. Minutes of looking against hours of GPU — see Spec 02 Step 6.

Then, once results exist: measure the share of top-10 fragments that are
`formato in (csv, xlsx)`, per query. If tabular chunks crowd out prose, act:

- **§8.7 post-filter by `formato`** — one line; `formato` is already in the metadata, and it
  can down-weight rather than exclude.
- **Separate indexes per format** with §8.4 fusion. More machinery; only if the filter proves
  too blunt.

### 2. No overlap on tabular formats

**Decision: overlap on prose only.** Correct for now — repeating rows adds no semantic bridge.
The debt is that it is unmeasured: if a tabular answer ever straddles two row-chunks, nothing
recovers it. Low expected impact, noted for completeness.

### 3. Overlap ratio is unvalidated

1–2 sentences (~10–20%) is a convention, not a measurement. Without ground truth there is no
way to tune it. If any validation signal becomes available, this is the first knob to sweep —
it is a single parameter and re-chunking is cheap. Note that changing it invalidates cache 2
by fingerprint, which is by design.

### 4. `num_tokens` vs the §4.3 512-token obligation — RESOLVED 2026-08-13

**This section previously ended with "never reintroduce a tokenizer into chunking." That
instruction was wrong and has been reversed.** It rested on a claim that the tokenizer would
not run on the development machine. Measured: `XLMRobertaTokenizer` loads locally at the
pinned revision in seconds, and the full model runs on CPU too. The only thing actually
missing was `pyarrow`.

The reasoning that followed from the false premise was sound but moot: chunking budgeted on
words alone so that local and Colab boundaries stayed byte-identical. Pinning the tokenizer
revision buys the same determinism without giving up the token cap — the same revision
produces the same counts everywhere.

**What the chunker does now.** Two caps at once, checked per candidate sentence:
`MAX_WORDS = 250` and `MAX_TOKENS = 506` (512 − 4 for `"passage: "` − 2 special). A chunk
closes *before* the sentence that would overflow either. Lowering `MAX_WORDS` was considered
and rejected as the remedy: 100% of over-ceiling chunks were in the unsplit-block path, so a
smaller word budget would not have touched them.

Measured on real extracted text, before and after:

| | p50 | p95 | p99 | max | over ceiling | text discarded |
|---|---|---|---|---|---|---|
| words only | 295 | 1,141 | 5,098 | 8,947 | 29.1% | 43.8% |
| dual cap | 357 | 443 | 499 | **504** | **0%** | **0%** |

**One measurement error this exposed.** `num_tokens` was being counted with
`add_special_tokens=False` and without the `"passage: "` prefix, so every recorded figure
understated what the encoder actually receives by 6 tokens. The stored field still counts raw
content — that is what Tabla 1 describes — but the *cap* now reserves those 6.

### 5. Escalera residue — the hard cut was reversed 2026-08-13

**Previously:** a unit still over the cap after all five levels was hard-cut at word 250,
breaking §3.3 to satisfy §9.3.2.

**Now:** it is emitted **intact and over the cap**. Re-reading the two clauses side by side,
they are not equally binding. §3.3 is labelled *"Requisito obligatorio"* and states flatly that
no fragment may contain an incomplete sentence. §4.3 only asks that fragments be
*"diseñados para no superar"* the limit — a design obligation, and `num_tokens` is a stored
field, not a graded one. Hard-cutting breaks the mandatory rule to satisfy the advisory one.
The sentence survives; the encoder truncates its tail.

The one case with no clean answer is a single sentence over **250 words**, where §9.2's hard cap
and §9.2.1's *"sin oraciones cortadas"* contradict each other outright. There §3.3 yields,
because §9.2 is the mechanically graded one. No such case exists in the corpus.

Per the user's ruling this remains a data problem — the corpus is given and cannot be changed.

**Measured residue after the escalera, on the 25-file sample: zero.** Per-level hits were
clauses 17, pipes 1, list markers 1, newlines 0, commas 0. Levels 4 and 5 have not yet fired;
Step 1 item 4 says to delete levels that never fire, but 25 files is too thin a sample to
retire them on. Decide at the full-corpus dry run — `run_manifest.json` records the counters
on every run.

Measured incidence before segmentation: PDF 0.105% (max 491 w), JSON 0.722% (max 386 w), CSV
0.045% (max 7,193 w). Inspecting every offender found **no confirmed case of genuine
>250-word prose in a single sentence** — PDF offenders are abbreviation tables, figure
captions and org charts separated by newlines; CSV offenders are `|` field runs. All are
reachable by levels 2–5 of the ladder, so the expected residue after segmentation is near
zero.

**Log every occurrence anyway.** The prediction is that this branch never fires; if it does,
that is new information about the data and belongs in the technical document.

### 6. Empty documents — reversal proposed, investigated, rejected. **Closed.**

Tier-3 JSON, the 20 catalog files, and OCR-blank scans produce zero chunks, so they are absent
from the index and can never be returned. Decision 5 accepted this as a recall ceiling. The
audit proposed reversing it with one synthetic metadata chunk per document. Two further
options were then considered and the whole set was characterised. Result: **decision 5 stands
unchanged, and nothing gets built.**

The population, all of it measured rather than assumed:

| Group | n | In ADL inventory | What text actually exists |
|---|---|---|---|
| `ceeep_*`, `ceobs_full_*`, `mapp_*`, `resdal_*`, `sipri_full_*` | 10 | **No** | Not ADL documents — cannot appear in the ground truth |
| `AMAZONUW_tiles-index`, `CEOBS/CSIS/DAIO/DEFENSA21 ×2/MAPPOEA/RESDAL/RUTAN/SIPRI catalogs` | 10 | Yes | Scraper bookkeeping: URLs, hashes, status codes |
| CENIA ×4 | 4 | Yes | 27–29 words, of which the body is a **site navigation menu** (`Home / About us / Operations / Research / Transfer / Students / News / Contact`) |
| SWF newsletters ×7 | 7 | Yes | 10–14 words: `July 2024 Newsletter` + `Explore some of our related publications below.` Metadata adds a real `date` and generic `topics` |
| ESA space-debris | 1 | Yes | 22 words: *"The latest figures related to space debris, provided by ESA's Space Debris Office at ESOC, Darmstadt, Germany."* |

**The deciding principle:** a chunk must carry information that could *answer* a question, not
merely signal that a document on the topic exists. The ESA text is the clearest case — it
names figures without containing any, so it would match space-debris queries and answer
nothing, consuming a fragment slot that a real chunk could hold. CENIA's navigation chrome is
exactly what the `MIN_WORDS` floor exists to block (`remove_repeated_lines` misses it because
the menu appears once within a single document). SWF is a date-stamped label plus taxonomy
tags.

Two rejected proposals, recorded so they are not revisited:

- **Synthetic metadata chunks** (the audit's) — substitutes a surrogate for content and can
  surface for queries the document cannot answer.
- **Lowering the tier-3 floor to recover sub-30-word text** — recovers navigation menus and
  meta-descriptions, i.e. precisely the junk the floor was added to exclude.

Also corrected while investigating: `title` is a **body** field in `json_extract`, not a
metadata field, so it lands in the extracted text rather than in `meta`. An earlier claim that
these documents had no title was wrong; they do, and it is still not worth indexing.

Nothing to build. Count them during the run and report the number.

### 7. `clasificar_bloques` removed — titles come from the catalog only

The intended replacement was `^#{1,6} ` markers from HTML and Markdown. **The corpus contains
zero HTML and zero Markdown files** (inventory `Tipo`: JSON 954, PDF 759, Otro 74, CSV 26,
Imagen 8, Excel 4, Texto 1), so that branch never fires — do not write it. Titles come solely
from the 313 catalog-matched documents.

Recoverable later without heuristics: PyMuPDF exposes per-span font size, so PDF headings can
be detected structurally rather than guessed. Real work, deferred.

### 8. Threshold θ is untested

Implemented as a parameter with a backfill floor so the 3/10 quota can never break.

**Default corrected to `-1.0`, not `0.0`.** Cosine on normalised vectors spans `[-1, 1]`, so
`0.0` silently prunes every negative-similarity hit — it is a filter, not "off". e5
similarities sit in a compressed high band, so 0.7 could plausibly cut everything or nothing.
Sweep only against real scores.

### 8b. Determinism across environments

The index is built fp16-on-GPU; a grader re-running `generador.py` on CPU gets fp32 and
slightly different similarities, so ties and near-ties reorder. §1.4 makes reproduction
pass/fail, so sort by `(-score, chunk_id)` in both aggregation and fragment selection.

Related and cheap: `requirements.txt` currently pins nothing and omits `torch` and
`sentence-transformers`. Pin exact versions and record the model revision — the technical
document needs the list anyway.

### 9. Language detection is advisory

Seeded and document-level with a ~50-word floor, so it is reproducible and abstains rather
than guessing on short text. Still unreliable on code-mixed documents, and every chunk of a
document inherits one label — a Spanish document quoting English at length is mislabelled for
those chunks. Only matters if a language post-filter is ever enabled.

### 10. `IndexFlatIP` with no ANN evaluated

Deliberate. §5.2 states a flat index is sufficient at this corpus size and returns exact
results; that holds at 110k vectors. IVF or HNSW would trade exactness for speed we do not
need. Recorded here for the technical document, not as work.

### 11. `chunk_id` width

`{i:05d}` supports 99,999 chunks per document. The largest projected single document is ~28k
chunks, so there is headroom. Never sort chunk ids as strings across documents anyway.

### 12. Two cleaning passes

`extraccion_final.clean_text()` is now the only cleaner; the chunker's was removed. They were
never equivalent — only the extractor's strips repeated-line boilerplate — so extractor
cleaning must run first. Recorded so nobody "optimises" it away.

---

## Part B — Retrieval-only improvements, ranked

All permitted under §8.3. Ranked by expected gain per unit of effort.

### B1. Architectures B and C (§4, §8.4) — highest ceiling

Only architecture A (`multilingual-e5-large`, dense) is being built. The team's own
`final-architecture-decision-report.md` rates **B (BGE-M3 native dense+sparse)** as the
best-supported option: dense and sparse come from one forward pass, the heads are co-trained
by self-knowledge distillation, and a financial-retrieval study measured BGE-M3's self-hybrid
above both dense-only baselines.

Cheap to add by construction: the text cache and the chunk stage are encoder-independent, so B
re-encodes but never re-extracts and never re-chunks. §1.4's layout already expects one
subfolder per encoder.

Caveat for a fair comparison: both architectures must index **identical chunks**. Since
chunking is word-based and tokenizer-free, this now holds automatically.

Fusion for multi-index results must be arithmetic — §8.4 names CombSUM and RRF. Both are a
few lines.

### B2. Doc-level aggregation sweep (§8.6)

Max-pooling is chosen and is the safe default under decision 1. Sum is actively dangerous
while tables are indexed. **Weighted mean remains untested** and is the one alternative worth
trying if validation data appears — for example a rank-damped mean, which captures "broadly on
topic" without letting chunk count dominate.

Half the leaderboard is F1@3, which this alone determines.

### B3. Post-filters (§8.7)

Permitted on metadata (`fenomeno`, `formato`, `idioma`, dates) and on vectors (a similarity
floor). Already plumbed via θ. The `formato` filter from A1 is the concrete first use.

**Boundary to respect:** the 50 evaluation questions are grouped by phenomenon
(q001–q022 ≈ F1, q023–q032 ≈ F2, q033+ ≈ F3). Deriving a `fenomeno` filter from the query
**number** is fitting to the evaluation set and would collapse if graders reorder or extend
the queries. Inferring phenomenon from query **content** at runtime is legitimate. Do not tune
anything per query id.

### B4. Overlap and `MAX_WORDS` sweep

Both are single parameters and re-chunking is milliseconds. The cheapest experiments available
— but meaningless without a relevance signal.

**Updated 2026-08-13.** Overlap is now *built* (`OVERLAP_ORACIONES = 1`, prose only); what stays
open is the number, not the mechanism. 1 is the conservative end of Decision 2's "1–2".

Correction: cache 2 no longer has a fingerprint. It keys on `sha256` of the chunk text, so a
parameter change re-encodes exactly the chunks whose text moved and reuses the rest — which is
precisely what makes these sweeps affordable rather than a full re-encode each time.

Two things to weigh when the sweep finally happens:

- Overlap inflates the index. At the measured tabular ratio the index is already ~136,500
  chunks; overlap adds to the prose share on top of that.
- Overlap lets two retrieved fragments carry the same sentence, wasting slots out of the ten.
  `generador` does not deduplicate fragment text. Worth measuring before raising the number.

### B5. Sub-fragment selection

When a returned chunk must be split, we currently emit its leading sentences. The most
query-relevant window may be later. Selecting it means encoding candidate windows at query
time and picking the best by cosine — pure vector arithmetic, permitted. Only affects the
residue after Step 1's segmentation, so expected impact is small.

### B6. Query-side ensembling — check before building

Encoding a query several ways (raw, with/without prefix variants) and fusing by RRF is
arithmetic, not generation, so §8.3 permits it. But e5's `"query: "` prefix is mandatory and
not optional, so the variation space is thin. Low priority; listed for completeness.

**Explicitly excluded:** LLM reranking, query rewriting or expansion with a decoder,
generation-based filtering, fragment summarisation. §8.3, unambiguous.

---

## Part C — Out of scope, still owed

| Item | Status |
|---|---|
| **Deliverable 3** — `informe_tecnico.pdf`, ≤8 pages | **Not started. Graded, and due.** Must justify chunking strategy (§3.2 requires the hybrid be justified explicitly), encoder choice, and FAISS index type. Most of the content already exists across these three specs. |
| **§7 knowledge graph** — `grafo/grafo.graphml` | Bonus points. Skipped. |
| Architectures B and C | Deferred; see B1. |

---

## Part D — Immediate order of work

1. Spec 02 Steps 0–2 (hygiene, chunker, extractor) — parallelisable.
2. Step 3a filesystem reconciliation. Cheap; it already found `FASE ORDENADA CODEFEST.xlsx`.
3. Steps 4–5 (`pipeline_final.py`, `generador.py`) with their self-checks, then Step 3b.
4. Step 6 dry run at `--sample 40`, which is now a **gate**, not a rehearsal — it must produce
   the `num_tokens` p99 (A4), the ladder hit counts, and a verdict on the 7.1M-word CSV before
   any GPU time is spent.
5. Step 7 Colab full run, then `generador.py` against the delivered artifacts.
6. Read the rest off `run_manifest.json`: hard-cut count (A5), empty-document count (A6),
   tabular share of top-10 (A1), stage timings.

### Robustness items folded into Spec 02

Recorded here so they are not lost if the specs diverge: FAISS `-1` guard, bounded widen-`k`
retries, encode checkpointing every ~5k chunks, `errores.jsonl` for per-file extraction
failures, `index.add` in slices, `np.lib.format.open_memmap` for incremental vector writes,
and `run_manifest.json` as the single observability artifact.
