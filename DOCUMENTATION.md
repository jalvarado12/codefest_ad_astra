# The CODEFEST AD ASTRA 2026 retrieval system — complete documentation

This document explains the whole system to somebody who has never seen it, and who does not
need to already know what an embedding or a vector index is. It covers what the system does,
why each stage exists, what every decision was weighed against, what is built today, and what
is deliberately left undone.

It is not a README. Reading it end to end should leave you able to defend every design choice
in the pipeline, and to find the weak spots without being told where they are.

---

## Table of contents

1. [What the competition asks for](#1-what-the-competition-asks-for)
2. [The core idea, for a newcomer](#2-the-core-idea-for-a-newcomer)
3. [Why there are two programs, not one](#3-why-there-are-two-programs-not-one)
4. [The corpus we were given](#4-the-corpus-we-were-given)
5. [Part I — The indexing pipeline](#5-part-i--the-indexing-pipeline)
6. [Part II — The generator](#6-part-ii--the-generator)
7. [The delivery](#7-the-delivery)
8. [Current state: what exists, what is tested, what does not exist](#8-current-state)
9. [Technical debt, with reasons](#9-technical-debt-with-reasons)
10. [How to run everything](#10-how-to-run-everything)
11. [Glossary](#11-glossary)
12. [Before delivery — the remaining gates](#12-before-delivery--the-remaining-gates)

---

## 1. What the competition asks for

CODEFEST AD ASTRA 2026, Stage 1. We are given a corpus of roughly 1,800 documents about three
themes ("fenómenos"):

- **F1** — artificial intelligence and strategic capabilities
- **F2** — space environment security
- **F3** — territorial dynamics

We must build a **knowledge base** that, given a natural-language question, returns the
documents and passages that answer it. Then 50 questions (`q001`–`q050`, in Spanish, English
and Portuguese) are run against it, and our answers are compared to a hidden ground truth.

Two scores, weighted equally:

- **NDCG@10** — how good our ten returned passages are, and in what order
- **F1@3** — how good our three returned documents are, as a set (order ignored)

### The single most important constraint

**No generative AI anywhere in the retrieval path** (spec §8.3). No GPT, no Claude, no Llama.
Not for re-ranking, not for rewriting the question, not for filtering, not for summarising.

This is not a stylistic preference — it changes the entire architecture. We cannot "ask a model
which document is relevant". Everything must be done with **encoder models** (which convert
text to numbers but generate nothing) and **arithmetic**.

### The other hard rules

| Rule | Where | Consequence |
|---|---|---|
| FAISS is mandatory for the vector index | §5.1 | No alternative library |
| `metadata.jsonl` line order must equal FAISS's internal ids | §1.4 | The invariant the whole design protects |
| Exactly 50 lines, exactly 3 documents, exactly 10 fragments per line | §9.3.1 | Wrong counts are discarded |
| Every returned fragment ≤ 250 **words** | §9.2 | Words, not tokens |
| No chunk may contain an incomplete sentence | §3.3 | Constrains where text may be cut |
| Eight mandatory metadata fields per chunk | §3.4 | Listed in §5.7 below |
| Ground truth matches on `fuente`, **not** `doc_id` | §10.2.1 | Explained in §5.4 — easy to get catastrophically wrong |

---

## 2. The core idea, for a newcomer

### The problem with searching by keyword

If somebody asks *"¿Qué riesgos genera la basura espacial?"* and a document says *"orbital
debris poses collision hazards"*, keyword search finds nothing. No words match. The document is
in English, the question in Spanish, and even within one language people phrase things
differently.

### Embeddings: turning meaning into coordinates

An **encoder model** reads a piece of text and outputs a list of numbers — here, 1,024 of them.
That list is called an **embedding** or **vector**. You can think of it as coordinates in a
1,024-dimensional space.

The useful property: the model is trained so that **texts with similar meaning land near each
other**, regardless of wording or language. "Basura espacial" and "orbital debris" end up close
together. "Space debris" and "coffee budget" end up far apart.

So the search becomes geometric: convert the question to a vector, then find the nearest
document vectors. No keyword matching involved.

### Measuring "near"

We use **cosine similarity** — the angle between two vectors, ignoring their length. It runs
from −1 (opposite) through 0 (unrelated) to 1 (identical direction).

A convenient trick: if you first **normalise** every vector to length 1, cosine similarity
becomes exactly the **dot product**, which is far cheaper to compute. That is why the pipeline
normalises everything and uses FAISS's `IndexFlatIP` (IP = inner product = dot product). Spec
§8.2 spells out this equivalence.

### Why documents are cut into chunks

A 189-page PDF cannot be one vector. Two reasons:

1. **A hard technical limit.** The encoder reads at most 512 tokens (roughly 350–400 words) at
   once. Anything beyond that is silently discarded — not an error, just gone.
2. **A meaning limit.** Even if it fit, one vector averaging 189 pages would represent nothing
   in particular. Vectors are precise only when the text is about one thing.

So documents are split into **chunks** of a few hundred words, and each chunk gets its own
vector. The spec calls these *fragmentos*.

### What FAISS is

A library for storing millions of vectors and answering *"which stored vectors are nearest to
this one?"* in milliseconds. It stores **only numbers** — no text, no filenames. It hands back
integer positions like "row 4,712".

That leads directly to the single most important structural fact in this system.

### The invariant everything depends on

FAISS says *"row 4,712 is the best match"*. It has no idea what that text says. Something must
map 4,712 back to the actual chunk.

That something is **`metadata.jsonl`** — a text file with one JSON object per line, holding
each chunk's text and metadata. The mapping is **position**: FAISS row 4,712 is line 4,712.

```
FAISS internal id  i
      ↕
dense.npy          row i        (the raw vectors)
      ↕
ids.json           entry i      (which chunk_id that row is)
      ↕
metadata.jsonl     line i       (that chunk's text and metadata)
```

If these ever drift out of alignment, the system returns confident, well-formatted, completely
wrong answers — with no error message. **Nothing else in this design is defended as carefully
as this ordering**, and §1.4 grades it directly.

The defence is simple: `metadata.jsonl` is never written by hand or assembled separately. It is
**generated by walking `ids.json` in order**. There is exactly one source of truth for order.

---

## 3. Why there are two programs, not one

| Program | Runs | Job |
|---|---|---|
| `pipeline_final.py` | Once, by us, before submission | corpus → chunks → vectors → `index.faiss` + `metadata.jsonl` |
| `generador.py` | By the judges | question → search the index → `resultados.jsonl` |

The split is not our invention — §1.4 item 4 says `generador.py` is a script that
***"utilice el índice"***, uses the index. The index is delivered *alongside* it as a separate
artifact.

This matters practically. Building the index takes about 70 minutes of GPU time and needs the
25 GB corpus. If `generador.py` rebuilt it, every judge would need the whole corpus and a GPU
just to score one entry. Instead, the expensive work happens once on our side, and the judges
receive a finished index plus a small script that reads it.

**Consequence:** `generador.py` must be *self-contained*. It cannot import anything else from
this repository, because only it and the artifacts get delivered. Its encoder wrapper is
inlined for exactly this reason.

---

## 4. The corpus we were given

Three numbers that are all different, and confusing them produces wrong estimates:

| Count | Value | What it is |
|---|---|---|
| File paths on disk | 1,848 | everything, including junk |
| Rows in ADL's inventory | 1,826 | what ADL considers documents |
| Unique filenames | 1,713 | **filenames are not unique** |

By format: **964 JSON**, **760 PDF**, **73 PBF** map tiles, **26 CSV**, **8 JPG**, **6 XLSX**
(only 4 are corpus content), **1 TXT**. And notably: **zero HTML, zero Markdown**, despite the
spec discussing both.

Three files sit inside the corpus tree but are **not** corpus content and must be excluded:

- `Extracto_Preguntas_50_v2.pdf` — the 50 evaluation questions. Indexing this would pollute
  retrieval with text that mirrors the queries.
- `Indice_Datos_Codefest.xlsx` — ADL's own inventory of the corpus.
- `FASE ORDENADA CODEFEST.xlsx` — an admin spreadsheet, found only by reconciling disk against
  the inventory.

### The filename trap

114 PDFs share a filename with another PDF — 47 distinct names, mostly CSET reports that exist
in both a `Reports/` and a `Translation/` folder. Same name, different documents.

This is not trivia. It caused a real measurement error during development: a test harness built
a `{filename: path}` dictionary, later files silently overwrote earlier ones, and the resulting
number was wrong for hours before anyone noticed. It is also the reason for a design decision
in §5.4.

---

## 5. Part I — The indexing pipeline

Overall flow:

```
corpus ──► extract ──► clean ──► identify ──► chunk ──► embed ──► FAISS ──► metadata.jsonl
             │                                                       │
        cache/textos.jsonl                            vectors/<enc>/{dense.npy, ids.json}
        (skips OCR on re-runs)                        (skips re-encoding on re-runs)
```

### 5.1 Extraction — seven formats, seven strategies

Implemented in `extraccion_final.py`, a single self-contained file. Each format gets an
adapter with one job: produce plain text. The design is the **Adapter pattern** — the
orchestrator never knows what format it is handling.

**PDF.** Read the embedded text layer with PyMuPDF, in `"blocks"` mode rather than plain text.
Blocks preserve reading order and naturally separate headers and footers, which the cleaner
then removes because they repeat identically across pages.

But **50 of the 760 PDFs are scans** — photographs of paper, with no text layer. For these the
adapter falls back to **OCR** (optical character recognition): render each page as an image and
have EasyOCR read the letters.

The fallback is decided **per document at runtime**, by checking whether the text layer yielded
fewer than 50 characters. No list of filenames is hardcoded anywhere. This matters because OCR
is enormously expensive:

| | seconds per page | 613 pages |
|---|---|---|
| CPU | 83 | ~14 hours |
| Colab T4 GPU | **6.81** | **69.5 minutes** |

Both figures are measured, not estimated. The 12.2× gap is why extraction runs on GPU, and is
the single biggest cost decision in the project.

**OCR call tuning.** `readtext()` is no longer called with EasyOCR's defaults. Four constants
near the top of `extraccion_final.py` override them:

| Constant | Value | Why |
|---|---|---|
| `OCR_CANVAS_SIZE` | 1280 | Cap EasyOCR applies to the image's longer side before CRAFT detection (library default 2560) |
| `OCR_BATCH_SIZE` | 16 | Text crops processed together by the recognizer |
| `OCR_WORKERS` | 0 | See below — measured, not assumed |
| `OCR_MAX_LADO_PX` | 2000 | Render cap computed from the page's real size, so PDF pages are never rasterized to more pixels than OCR will use |

`OCR_WORKERS = 0` is an A/B measurement against the same reader and the same pages, not a
default left alone: `workers=2` measured 20.88s/page against 2.75s/page at `workers=0` — 7.6×
worse. The cause is that `DataLoader(num_workers>0)` uses `spawn` on macOS rather than `fork`,
and `readtext()` is called once *per page*, so every page pays a full subprocess-startup cost for
a handful of text crops that never amortizes it. This was measured on macOS specifically; a
Linux run (which forks by default) might tolerate `workers>0` better, but re-measure before
changing it rather than assuming.

`dpi_ocr` now defaults to 150 (was 300) and `gpu_ocr` defaults to `True`, matching how
`pipeline_final.py` actually invokes the extractor. `_obtener_lector_easyocr` also logs the
EasyOCR device actually in use, because `gpu=True` silently falls back to CPU when no GPU is
visible — this used to be invisible.

**JSON.** By far the largest group (964 files) and the most variable. The spec (§2.1) says to
interpret the object and explicitly select the fields holding article text, keeping descriptive
fields (`url`, `date`, `authors`) as metadata rather than mixing them into the body.

Real corpus JSON does not follow one schema, so extraction runs in tiers:

- **Tier 1** — harvest known text-bearing field names (`title`, `body_text`, `body_paragraphs`,
  `abstract`, and Spanish/Portuguese equivalents), skipping known noise containers (`images`,
  `links`, `pdf_links`).
- **Tier 2** — if tier 1 produced under 30 words, sweep again with the noise filters relaxed.
- **Tier 3** — still nothing usable: raise `JsonSinTexto` and let the document through with
  empty text.

One subtlety worth understanding: 485 of 848 article files carry **both** `body_text` and
`body_paragraphs`, where the former is the latter joined together. Emitting both would double
the document. The paragraph list wins, because §2.1 asks for paragraph order to be preserved
and the list is where the real boundaries are.

**CSV and XLSX.** Read the header, then each row becomes `columna: valor | columna2: valor2`,
so every value keeps its column name as context. Empty cells are dropped.

Rows are joined with a **blank line**, not a newline, and that single character is load-bearing.
The chunker splits blocks on `"\n\n"`, so with a plain newline every tabular file arrived as one
block and became one chunk — worst measured case 4,170 words in a single chunk, of which the
encoder read 512 tokens. §2.1 prescribes the fix directly: *"Cada fila puede tratarse como una
unidad de fragmentación independiente."* The separator is the named constant `_UNION_FILAS`
precisely so it cannot drift back.

**Images.** OCR unconditionally. If the result is under 30 characters the image is decorative
and returns empty rather than OCR noise.

**PBF** (Mapbox vector map tiles). Decode the tile, walk its layers and features, and turn
attributes into `atributo: valor` text, one element per blank-line-separated block, duplicates
within the file removed.

**One `.pbf` file is one document**, not one document per tileset. §2.3 is explicit — *"un
documento corresponde a un archivo individual provisto por ADL"* — and ADL's inventory lists all
73 tiles as separate rows with their own `DOC_ID`. A tileset-level document would be named after
a *directory*, match no inventory row, and score zero on every PBF item under §10.2.1. The spec
is in genuine tension with itself here, since §2.1's *"quedarse con una sola versión para no
duplicar la data"* argues the other way; it is resolved on risk asymmetry, because 73
near-duplicate documents cost precision while one unmatchable document costs everything.

**TXT/MD.** Passed through; Markdown headings are left intact as structural signals.

### 5.2 Cleaning

**One cleaner, and only one.** `extraccion_final.clean_text()` is it. The chunker used to carry
its own near-duplicate `clean()`; it was removed (Decision 9), because two cleaners means two
definitions of what the text *is*, and the one that runs last silently wins.

Applied to every adapter's output:

1. Remove control characters and zero-width spaces
2. Normalise Unicode to NFC (so accented characters have one canonical encoding)
3. **Remove repeated lines** — a line that is short and appears three or more times is page
   furniture (headers, footers, nav menus), not content
4. Collapse redundant whitespace

Step 3 is why PDF extraction uses block mode: headers become their own blocks, repeat
identically, and get stripped automatically.

**Where step 3 fails, measured.** A running header that carries a page number is *not* an
identical repeat — `"cross-cutting considerations 21"` and `"cross-cutting considerations 22"`
are two distinct lines, each seen once, so a header appearing on 30 pages never reaches the
`min_repeats=3` threshold and survives into the text. It shows up as ~2% of prose chunks opening
with a lowercase header line. Stripping a trailing page number before counting would collapse
them; that is not done yet, because an over-eager rule would eat real content and the change
touches every PDF in the corpus. Cosmetic for compliance, real for retrieval quality.

### 5.3 The catalog problem

Twenty of the JSON files are not documents at all — they are the scraper's own bookkeeping:
lists of URLs, hashes and HTTP status codes for files it downloaded. Indexed naively, they
become fake documents made of title-salad.

They are recognised by name and forced to empty text. But their **contents are still useful**:
each entry describes a real corpus file, often with metadata that file itself lacks (author,
country, year). So a matching step pairs each catalog entry with the real document it describes
and attaches that metadata to it — 313 documents enriched, 358 entries matched.

Interestingly, **only 10 of those 20 catalog files appear in ADL's inventory**. The other 10 are
not ADL documents at all, which settles a question about them (see §9, debt 6).

### 5.4 Identity: `doc_id` versus `fuente`

Every document gets a `doc_id` like `DOC-0042`, assigned once and persisted in
`doc_registry.json` so it never changes between runs.

**But `doc_id` is not what gets graded.** §10.2.1:

> *"a nivel de documento el emparejamiento con el ground truth se realiza a través del campo
> `fuente` (archivo original provisto por ADL), no del `doc_id` arbitrario asignado por el
> equipo."*

The ground truth matches on **`fuente`** — the original ADL file. `doc_id` is internal
bookkeeping. So `fuente` correctness directly determines F1@3, which is half the score, and a
mistake there is invisible to every other test.

What should `fuente` contain? Table 1 describes it as *"Nombre o URL del archivo original"*,
which sounds like a filename. But as §4 showed, **filenames are not unique** — 114 documents
would collapse into 47 ambiguous keys.

The resolution rests on an asymmetry: **a path contains the name, but a name cannot be
recovered from a path.** So:

| Field | Value | Purpose |
|---|---|---|
| `fuente` | `F1_IA.../pdfs/Reports/CSET_....pdf` | unique across all 1,826 rows; the graded key |
| `nombre_archivo` | `CSET_....pdf` | covers a grader matching on bare filename |
| `adl_doc_id` | `F1-CSET-014` | ADL's own identifier, free to carry |

The spec permits extra fields, so all three ship. Paths use forward slashes — a Windows
backslash path would be a silent, total F1@3 failure.

### 5.5 Chunking

Handled by `chunker.py`, which does **chunking and nothing else**. Cleaning and structure
detection are extraction's job (§5.2); the chunker receives text that is already clean and
already blank-line separated into logical units.

The strategy is **sentence packing under two caps at once**:

1. Split the clean text into blocks on blank lines
2. Split each block into sentences
3. Pack sentences into a chunk until adding the next would exceed **250 words** *or*
   **506 tokens** — then close the chunk *before* that sentence

**Why 250 words?** §9.2 caps *returned* fragments at 250 words. If every chunk already respects
that limit, the generator can return chunks verbatim and never needs to split anything at query
time. One number, chosen once, deletes an entire subsystem downstream.

**Why 506 tokens?** The encoder truncates its input at 512. The `"passage: "` prefix costs 4
tokens and the two special tokens cost 2, leaving 506 for content. A 250-word Spanish chunk
runs about 600 tokens (measured ratio ≈ 2.43 tokens/word), so the word cap alone does **not**
keep chunks inside the encoder — this is why both caps are needed rather than one.

**Why both caps, and why this exact rule?** §3.3 prescribes it almost verbatim:

> *"si se fija un tamaño máximo de n tokens, el corte efectivo debe retroceder al final de la
> última oración completa que quepa dentro de ese límite"*

Closing before the overflowing sentence is not a design choice; it is the spec's own
instruction. Nothing is ever cut mid-sentence, and chunks simply come out shorter in Spanish.

**What this fixed.** The previous paragraph-packing chunker never split a paragraph, so one
3,000-word paragraph became one 3,000-word chunk. Measured on real extracted text: **29.1% of
chunks exceeded the encoder ceiling and 43.8% of all indexed text was silently discarded** —
no error, no warning, retrieval still returning plausible results from the surviving first 512
tokens while half the corpus was unsearchable. After the change, on the same text: **0% over
the cap, 0% discarded.**

**Sentence detection is a real trap.** A naive split on `.` breaks `El Dr. Pérez` into two
"sentences" — and a chunk boundary there would violate §3.3 on both sides. `separar_oraciones`
takes a boundary only when the preceding word is not an abbreviation, an initial or a decimal,
*and* the following text starts like a sentence. It is deliberately conservative in one
direction: missing a boundary costs a longer unit, which the escalera below handles; inventing
one corrupts the text.

**The escalera, for units no sentence boundary reaches.** A single "sentence" can still exceed
the cap — never genuine prose in this corpus, always an extraction artifact: an abbreviation
table, a figure caption, an org chart, a `| URL: … |` field run. Five levels are tried in order
of how much meaning the cut destroys:

| # | Level | Sample-corpus hits |
|---|---|---|
| 1 | clause boundaries `;` `:` | 17 |
| 2 | field separator `\|` | 1 |
| 3 | list markers `• - 1. a)` | 1 |
| 4 | single newlines | 0 |
| 5 | commas | 0 |

`chunker.ESCALERA_HITS` counts these per run and `run_manifest.json` records them, so levels
that never fire can be deleted rather than assumed useful.

**The residue, and which rule yields.** If no level splits a unit, it is emitted **intact and
over the cap**. This is deliberate. §3.3 is labelled *"Requisito obligatorio"* and is absolute;
§4.3 only asks that fragments be *"diseñados para no superar"* the limit. Cutting the sentence
would break the mandatory rule to satisfy the advisory one, so the sentence survives and the
encoder truncates it. Measured residue on the sample corpus: **zero**.

There is one case with no clean answer: a single sentence over **250 words**. §9.2 caps returned
fragments at 250 and §9.2.1 requires the split respect §3.3 — impossible when there is no
internal sentence boundary. The spec contradicts itself there; §3.3 yields because §9.2 is the
mechanically graded one. No such case was found in the corpus.

**Overlap.** Each chunk is seeded with the last `OVERLAP_ORACIONES` complete sentences of the
previous one. An answer that straddles a chunk boundary is otherwise split across two vectors
and retrieves poorly from both; the overlap makes it whole on at least one side.

Three properties make this safe rather than merely helpful:

- **Whole sentences only.** The unit carried across is already a complete sentence, so §3.3
  holds by construction. A unit produced by the escalera has no sentence terminator and is
  never carried — seeding on one would open the next chunk mid-sentence.
- **It counts toward both caps.** The overlap is paid for out of the 250-word and 506-token
  budgets, not added on top, so no chunk grows past either.
- **The seed is trimmed until the overflowing sentence still fits.** Otherwise the carried text
  would push that sentence out again and the packer would loop without progressing.

**Not applied to `csv`, `xlsx` or `pbf`.** Rows are independent records; repeating one carries
no context into the next chunk, and tabular content is already projected to dominate the index.
§3.2 permits the hybrid strategy, it does not require applying it uniformly — but it does
require the choice be justified explicitly in the technical document, which is what this
paragraph is for.

**The cost overlap introduces, stated plainly.** Two adjacent chunks now share a sentence. If
both are retrieved for the same question, two of the ten fragment slots carry overlapping text —
permitted by §9.3.1, which says nothing about duplication, but wasteful, and it can only hurt
NDCG@10. `generador._fragmentos_de` deduplicates nothing at chunk level today. The remedy is a
few lines (skip a fragment whose text is already substantially present in an earlier one), but
it is a retrieval-quality tradeoff with no ground truth to tune against, so it is recorded here
rather than guessed at.

#### Is the overlap right? Separate the mechanism from the setting

The mechanism is verified on real data. The setting is a bet. These are not the same claim and
the distinction matters when reading any later result.

**Confirmed, measured on the sample corpus:**

| | |
|---|---|
| Placement | 354 prose chunk boundaries carry the previous sentence, **0** tabular ones |
| Caps | 0 chunks over 506 tokens, 0 over 250 words — the overlap is paid out of the budget |
| §3.3 | Only sentence-final units are eligible as a seed, so an escalera fragment can never open a chunk |
| Continuity | Every chunk after the first begins with the previous chunk's last sentence — asserted per boundary, not sampled |
| Cost | +11.8% chunks (475 → 531), inside the 10–20% budgeted for prose |

**Not confirmed.** `OVERLAP_ORACIONES = 1` is a guess. Decision 2 permitted "1–2 sentences" and
1 is the conservative end, chosen because there is no relevance signal to justify 2. There is no
evidence here that 1 beats 0, or that 2 beats 1. This repository contains no ground truth, so
recall cannot be measured against anything.

So overlap is **correctly implemented**, not **empirically optimal**. It is a bet: roughly 12%
more index and some duplicate-fragment risk, in exchange for answers that straddle a boundary
staying retrievable. That bet is standard practice and §3.2 explicitly permits it — but it stays
a bet until something measures it.

**How to settle it cheaply, when there is something to measure against.** Cache 2 keys on the
`sha256` of the chunk text, so changing `OVERLAP_ORACIONES` re-encodes only the chunks whose
text actually moved and reuses every other vector. Run the corpus once at `overlap=1`, then
re-run at `overlap=0` and at `overlap=2`; each sweep costs minutes rather than a full encode.
Compare the resulting `resultados.jsonl` files against the graders' ground truth. Do the same
for `MAX_WORDS`, which is the other single parameter with the same property.

### 5.6 Embedding

Model: **`intfloat/multilingual-e5-large`**, pinned to revision
`3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3`. Encoder-only, self-hosted, Apache-2.0, and
multilingual — which matters because the corpus and the questions span Spanish, English and
Portuguese. Output: 1,024 dimensions.

**The prefix rule.** This model family requires a literal prefix on every input:

- `"passage: "` for text being indexed
- `"query: "` for a question being searched

Using the wrong one produces no error and no crash — just quietly worse results everywhere. It
is the single easiest way to lose the whole score, so the code makes the two paths separate
functions rather than a flag.

Vectors are L2-normalised at encoding time, so cosine similarity equals the dot product.

**Device selection.** `encoder_e5()` picks `cuda` → `mps` → `cpu`, in that order
(`torch.backends.mps.is_available()` covers Apple Silicon's Metal backend). The call is
harmless on non-Mac machines — it simply returns `False` there and the code falls through to
`cpu` exactly as before. fp16 casting now applies on `mps` as well as `cuda`: fp32 at
`batch_size=64` measured a real MPS out-of-memory on an 8GB Mac (`Insufficient Memory,
kIOGPUCommandBufferCallbackErrorOutOfMemory`) that hung the process rather than raising
cleanly; fp16 at the same batch size ran clean, measured.

`ejecutar()` also now loads the encoder **after** extraction rather than before. Extraction
includes OCR (EasyOCR on GPU/MPS), and having e5-large already resident in the same unified
memory while OCR runs measured real GPU/RAM contention on an 8GB machine — a page that takes
2.5–6.5s in isolation hung for over 9 minutes with both models loaded at once. Loading the
encoder only once extraction is done avoids that contention entirely.

### 5.7 What a chunk record looks like

```json
{
  "doc_id": "DOC-0042",
  "chunk_id": "DOC-0042-chunk-00007",
  "fuente": "F2_Seguridad_Entorno_Espacial/CSIS/pdfs/CSIS_space-threat.pdf",
  "formato": "pdf",
  "fenomeno": 2,
  "posicion": 7,
  "num_tokens": 331,
  "texto": "…",

  "nombre_archivo": "CSIS_space-threat.pdf",
  "adl_doc_id": "F2-CSIS-014",
  "idioma": "es",
  "n_words": 248,
  "title": "Capacidades antisatélite",
  "catalogo_year": "2024"
}
```

The first eight are mandatory (§3.4 Table 1). The rest are permitted extras that either enable
metadata filtering or preserve traceability.

`chunk_id` uses five digits (`00007`) because one CSV produces roughly 28,000 chunks and four
digits would stop sorting correctly past 9,999.

### 5.8 The FAISS index

`IndexFlatIP` — "flat" meaning exhaustive: every query is compared against every vector. That
sounds slow, but at ~110,000 vectors it takes milliseconds, and §5.2 of the spec explicitly
endorses a flat index at this scale. Approximate indexes (IVF, HNSW) trade exactness for speed
we do not need.

Vectors are inserted in `ids.json` order, then `metadata.jsonl` is written by walking that same
order. The invariant holds by construction rather than by discipline.

### 5.9 Caching, and what is deliberately *not* cached

Two caches, guarding the two expensive stages:

| Cache | Key | Guards |
|---|---|---|
| `cache/textos.jsonl` | SHA-256 of the source file's bytes | extraction, especially the ~14 CPU-hours of OCR |
| `vectors/<encoder>/` | SHA-256 of the chunk **text** | GPU encoding |

**Chunking is deliberately not cached.** It is deterministic and takes milliseconds, so caching
it would buy nothing — and would create a real hazard. Chunk ids are positional
(`DOC-0042-chunk-00007`), so they are *stable names for unstable content*: if a document is
re-extracted slightly differently, chunk 7 keeps its name but holds different text, and a
shortened document leaves orphan chunks that nothing ever overwrites. Treating chunks as
derived state, regenerated wholesale every run, makes that entire class of bug impossible.

Keying the vector cache on chunk **text** rather than chunk id follows the same logic: only
genuinely changed text is re-encoded, and changing the chunking strategy costs exactly what it
should.

### 5.10 Where Colab fits, and why the direction matters

Extraction needs a GPU (OCR). Encoding needs a GPU. Everything between them does not.

The correct order is therefore:

1. **Colab pass A** — extraction on GPU → produces `cache/textos.jsonl` → download it
2. **Locally** — chunk, test, iterate, at zero OCR cost and no GPU
3. **Colab pass B** — encode and index on GPU

The text cache is Colab's **output**, not its input. An earlier draft of the plan had this
backwards, which would have meant 14 hours of local CPU OCR to produce a file the GPU makes in
about an hour. The cache's SHA-256 key is environment-independent, so moving it between
machines is safe by construction.

**In practice `pipeline_final.py` does all three in one process**, because both caches make the
split optional rather than mandatory: run it on Colab, pull `cache/textos.jsonl` back, and every
later local run reuses the extraction for free. The pass A / pass B split is still the right
mental model for *where the cost is*, and still the fallback when a session dies mid-run — which
is what the resume path exists for.

**Colab sessions vanish when idle.** `colab sessions` reporting *"No active sessions found on
server"* is the normal state, not an outage; `colab new -s gentest --gpu T4` recreates one in
seconds. Uploads against a dead session fail with a bare `FAIL` per file, which reads like a
path bug and is not one. Two GPU runs were lost to this before it was understood.

---

## 6. Part II — The generator

`generador.py`. Input: an index, a metadata file, and 50 questions. Output: `resultados.jsonl`.
It must run standalone with no repository imports.

### 6.1 Loading the questions

The questions arrive as a 3-page PDF, formatted `qNNN <question text>` with questions wrapped
across lines. The loader extracts text with PyMuPDF, matches `^(q\d{3})\s+(.*)$`, and joins
continuation lines until the next `qNNN`.

It is isolated in one function, and also accepts `.jsonl` and `.csv`, so a different delivered
format costs a single edit. It asserts exactly 50 unique ids, `q001`–`q050`.

### 6.2 Loading the metadata

`metadata.jsonl` is read into a **list, indexed by position** — because position *is* the FAISS
id. It is deliberately *not* keyed by `chunk_id`, which would throw away the ordering the whole
design protects.

### 6.3 Searching

The question is encoded with the **`"query: "`** prefix and searched against the index for the
top `k` (default 50) nearest chunks. `k` is far above the 10 we need, for two reasons:
document-level aggregation needs many chunks to group, and some may be filtered later.

**A trap worth naming.** When you ask FAISS for more neighbours than it holds, it pads the
result with `-1`. In Python, `metadata[-1]` is not an error — it is the *last* element. Without
a guard, an exhausted search silently returns the final chunk of the corpus as a confident
match. The code checks `if idx < 0: continue` before every lookup.

### 6.4 From chunks to documents (§8.6)

FAISS returns chunks; the answer needs **3 documents**. Chunks are grouped by `doc_id` and each
document is scored. Three strategies are permitted:

| Strategy | Document score | Behaviour |
|---|---|---|
| **Max-pooling** ← chosen | its single best chunk | one precise passage wins |
| Sum | sum of all its retrieved chunks | rewards broad coverage |
| Weighted mean | average, rank-weighted | in between |

**Max-pooling is chosen, and the reason is concrete.** Around 61% of the index is tabular data
from 30 CSV/XLSX files. Under sum-aggregation, a single large CSV contributing hundreds of
mediocre chunks to the top-50 would outrank a document with one excellent passage, on chunk
count alone. Max-pooling is structurally immune to how many chunks a document happens to have.

The implementation is neater than it sounds: hits are sorted globally by `(-score, chunk_id)`,
so the **first occurrence** of any `doc_id` in that order is automatically its best chunk.
Deduplicating in place gives max-pooling *and* the deterministic tie-break for free.

**Why the tie-break matters.** The index is built in fp16 on a GPU; a judge re-running on CPU
gets fp32 and slightly different similarity values. Near-ties can reorder. Sorting by
`(-score, chunk_id)` makes the output stable across environments — the difference between
"reproduces" and "mostly reproduces", and §1.4 makes reproduction a pass/fail gate.

### 6.5 Selecting the ten fragments

Walk the sorted hits, emitting `{rank, chunk_id, doc_id, text}` until ten are collected.

If a chunk exceeds 250 words it is split into sub-fragments at sentence boundaries. Per §9.2.1,
**all sub-fragments keep the original `chunk_id`** and each occupies its own rank slot — the
spec explicitly blesses this, since relevance is judged on `text` and `chunk_id` serves only
traceability.

Word counting is `len(text.split())`. The spec says *palabras*; words and tokens differ by
roughly 1.3×, and the penalty is on words.

### 6.6 The threshold, and why it can never break the schema

An optional similarity floor θ discards weak matches. It is a **parameter defaulting to
`-1.0`**, which is genuinely "off" — note that `0.0` would *not* be, since cosine spans
[−1, 1] and zero would silently prune every negative-similarity hit.

The danger: §9.3.2 requires *exactly* 3 documents and *exactly* 10 fragments. A threshold that
filters too aggressively would produce a short list and get the line discarded. So θ prunes,
and then **backfills from the unfiltered ranking** whenever that would leave a quota short. The
filter can narrow results; it can never break the contract.

The same reasoning applies to widening `k`: if the top-50 does not contain three distinct
documents, `k` grows — but bounded at `index.ntotal`, then padded deterministically, so a small
index cannot cause an infinite loop.

### 6.7 The output

One JSON object per line, 50 lines exactly:

```json
{"query_id": "q001",
 "documents": [{"rank": 1, "doc_id": "DOC-0042"},
               {"rank": 2, "doc_id": "DOC-0017"},
               {"rank": 3, "doc_id": "DOC-0091"}],
 "fragments": [{"rank": 1, "chunk_id": "DOC-0042-chunk-00007",
                "doc_id": "DOC-0042", "text": "…"}]}
```

**A subtle trap:** `metadata.jsonl` uses the key **`texto`** (Table 1, Spanish); `resultados.jsonl`
uses **`text`** (Table 2, English). Different files, different key names, and no error if you
confuse them — just a discarded submission.

---

## 7. The delivery

```
entrega/
  resultados.jsonl          50 lines, one per question
  generador.py              standalone, no repo imports
  informe_tecnico.pdf       ≤8 pages, design justification
  base_vectorial/
    encoder_multilingual-e5-large/
      index.faiss           via faiss.write_index()
      metadata.jsonl        one line per chunk, in FAISS id order
```

The indexing pipeline is **not** part of the delivery. Only these four items are graded, and an
unexpected extra file in a validated tree is risk without reward. The pipeline is described in
the technical report instead, which is exactly what §1.4 item 3 asks for.

For what still has to happen before this tree can be produced for real, see
§12, [Before delivery](#12-before-delivery--the-remaining-gates).

---

## 8. Current state

### Built and verified

**`extraccion_final.py`** — complete. Verified against the entire corpus with byte-for-byte
parity against the previous implementation: 964 JSON (938 identical, 26 correctly rejected,
0 mismatches), all CSV, XLSX, TXT, JPG, a 15-PDF sample, and the PBF tileset. Both built-in
self-checks pass.

**`generador.py`** — built, and its 11-check selftest passes: schema, word cap, sub-fragment
parentage, prefix routing, query loading, max-pooling behaviour, threshold backfill, the FAISS
`-1` guard, bounded retries, the field-name trap, and standalone delivery from an empty
directory.

**OCR throughput** — measured at full coverage: all 50 scanned PDFs, all 613 pages, 6.81 s/page
on a T4, 12.2× faster than CPU, zero empty results, zero errors.

**The whole flow, end to end, on real data.** A 25-file stratified sample (11 PDF including 3
scanned, 8 JSON, 2 CSV, 2 JPG, 1 XLSX, 1 TXT, all three fenómenos) run through
extraction → chunking → real e5-large on a T4 → FAISS → `generador.py` invoked as a subprocess
against the artifacts it produced.

*First run, 2026-08-12, prototype builder and the paragraph chunker:*

| | |
|---|---|
| Extraction | 25/25 documents, **0 errors**, 381 s (mostly OCR) |
| Chunks | 326 from 23 non-empty documents (2 images correctly yielded nothing) |
| Encoding | 326 × 1024 vectors in **6 s** |
| Index alignment | `index.ntotal == metadata lines == 326` ✓ |
| `resultados.jsonl` | **VALID** against §9.3.1/§9.3.2 |

Retrieval behaved sensibly: 16 distinct documents and 98 distinct chunks across the 50
questions, results spread over all three fenómenos, the most frequent rank-1 document winning
15/50 (well below a domination threshold), and fragment lengths capped at exactly 250 words.

This is what surfaced debt item 8 below, which is the single most consequential finding in the
project so far.

*Second run, 2026-08-13, `pipeline_final.py` itself on a T4 — same corpus:*

| | |
|---|---|
| Selftests on the VM first | `chunker.py` and `pipeline_final.py` both pass before any GPU time is spent |
| Inventory reconciliation | 25 matched, **0 files without a row** |
| Whole pipeline | **439 s**, 25 documents, **0 errors** |
| Extraction | 389 s (OCR-dominated) |
| Chunking | 475 chunks in **2.6 s** |
| Encoding | 475 × 1024 in **10 s** at batch 64 |
| `num_tokens` | p50 357, p95 443, p99 499, max **504** — zero over the cap |
| Escalera | clauses 17, pipes 1, list markers 1, newlines 0, commas 0, **residue 0** |
| Index alignment | `index.ntotal == ids.json == metadata lines == 475` ✓ |
| `resultados.jsonl` | **VALID** against §9.3.1/§9.3.2 |

The chunk count rose 326 → 475 because chunks are now bounded by tokens as well as words, and
because tabular files no longer collapse into one chunk each. Nothing was lost; the same text
is spread over more, smaller, fully-encoded chunks.

Retrieval changed in the direction the fix predicts:

| | before | after |
|---|---|---|
| Distinct chunks returned across 50 queries | 99 | **125** |
| Shortest returned fragment | 2 words | **13 words** |
| Median returned fragment | 204 words | 233 words |
| Formats represented | pdf, json, txt | pdf, json, txt, **xlsx** |

The XLSX appearing at all is the clearest single signal: it was one collapsed chunk before, of
which the encoder read the first 512 tokens, and it never won a slot. Distinct documents moved
15 vs 16 — noise at this sample size, and there is no ground truth to call either better.

**`chunker.py`** — rewritten to chunking only, and its self-check passes: abbreviation,
initial and decimal boundaries; close-before-overflow under each cap independently; the
escalera on a pipe run; residue returned uncut; a blank-line-separated table yielding many
chunks rather than one; and the full Table 1 contract including `texto` (not `text`) and
integer types.

`clean()`, `clasificar_bloques()` and `agrupar_secciones()` are gone (Decisions 3 and 9).
Removing the classifier mattered: it labelled any block of ≤10 words a title, and on a
single-block CSV it labelled a 4,170-word table a title, which is how three documents briefly
vanished from the index during this work.

**`pipeline_final.py`** — built, with an 11-check selftest that runs offline against a fake
encoder, so caching, checkpointing, resume and alignment are all exercised without a GPU or a
2.2 GB download. It covers: identical reruns extracting and encoding nothing; the non-corpus
exclusion; editing one document re-extracting only that one; the checkpoint-prefix path and the
text-hash reuse path separately; §1.4 alignment; **resume after a simulated kill, then alignment
re-asserted**; a chunk's own vector retrieving itself at rank 1; the Table 1 contract on every
line; and `--rebuild` ignoring both caches.

**`inventario.py`** — Step 3 reconciliation. `Carpeta` + `Nombre estandarizado` reconstructs
`fuente` exactly, giving a unique key across all **1,826** rows and supplying `adl_doc_id`.
Reconciling the 25-file sample: **25/25 matched, 0 orphans.**

### Measured after the Step 1 + Step 2 fixes

Same sample corpus, real extraction, real tokenizer:

| | before | after |
|---|---|---|
| Chunks over the 512-token ceiling | 29.1% | **0%** |
| Indexed text silently truncated by the encoder | 43.8% | **0%** |
| Worst single chunk | 8,947 tokens / 4,170 words | 504 tokens / 250 words |
| Chunks over the §9.2 250-word cap | 114 of 326 | **0** |
| Escalera residue (emitted over the cap) | — | **0** |
| Documents reaching the index | 23 | 25 |

The worst offender was one 25 KB CSV that became a single 4,170-word chunk. It is now 23 chunks.
The cause was one character: `CSVExtractor` joined rows with `"\n"`, and `separar_bloques`
splits on `"\n\n"`, so every tabular file collapsed into a single block.

### Does not exist yet

**`informe_tecnico.pdf`** — not started. It is graded, and §3.2 makes justifying the chunking
strategy a hard requirement. Note that §3.2 requires the *hybrid* strategy be justified
explicitly, which now includes the dual cap and the escalera.

**A full-corpus run, measured end to end for extraction.** After the OCR tuning above (§5.1:
`canvas_size`, lower `dpi_ocr`, `workers=0`, the page-size render cap), a full-corpus extraction
run — all ~1,835 files, every format, OCR included — completed in **22 minutes total**, down
from the pre-tuning full-coverage OCR-only estimate of 69.5 minutes for just the 613 scanned
pages (§5.1, §8). This is the first full-corpus number in this document; everything else below
(chunk counts, retrieval behaviour, the 25→475-chunk sample tables) is still measured on the
25-file stratified sample and has not yet been re-run at full scale. Re-measuring chunking,
encoding and retrieval end to end on the full extraction output is what remains — see §12,
blocking item 2.

---

## 9. Technical debt, with reasons

Ordered by likely cost to the score. Every item is a deliberate decision, not an oversight.

**1. Tabular content will be ~69% of the index — MEASURED, and worse than projected.**

The projection was 61% (about 67,000 of ~110,000 chunks), computed by dividing 16.7 million
tabular words by the 250-word cap. That assumed the word cap binds. It does not: dense
`columna: valor | columna: valor` rows hit the **506-token cap first**, so a tabular chunk holds
about **178 words**, not 250. Measured on the sample CSVs and reprojected:

| format | words | w/chunk | chunks | share |
|---|---|---|---|---|
| csv | 16,523,437 | 178.2 *(measured)* | 92,724 | 67.9% |
| pdf | ~9,500,000 | 250 *(assumed)* | 38,000 | 27.8% |
| json | ~1,150,000 | 250 *(assumed)* | 4,600 | 3.4% |
| xlsx | 208,564 | 178.2 *(csv ratio)* | 1,170 | 0.9% |
| **total** | | | **~136,500** | |
| **tabular** | | | **93,894** | **68.8%** |

So the index is ~24% larger than planned and more table-dominated. **One CSV holds 7.1M words —
about 40,000 chunks, roughly 29% of the entire index on its own.** The PDF and JSON rows are
still extrapolations at 250 w/chunk and will come in higher too, since prose now carries
overlap; that moves the tabular *share* down somewhat but the absolute count up.

Retrieval on a 100%-tabular index was tested directly: `resultados.jsonl` is **VALID** against
§9.3.1/§9.3.2, so nothing about the schema breaks. But the validator's own sanity check fires —
`one document dominates rank 1` on 26 of 50 queries. Tabular chunks are retrievable; they are
just poor at discriminating between questions.

*Decision stands: index them, and decide with the full-corpus numbers.* The remedy is cheap and
already plumbed — `formato` is in the metadata, so a post-filter or a per-format cap on the ten
fragments is a few lines. What is **not** cheap is discovering it after the full encode, which
is why this was measured before it.

**2. Chunk overlap. [BUILT]** One complete sentence is carried into each following chunk on
prose formats; `csv`, `xlsx` and `pbf` get none. See §5.5 for why each of those two halves is
the way it is.

What remains open is not the mechanism but the **number**, and the fact that the benefit itself
is unmeasured. `OVERLAP_ORACIONES` is 1 because 1 is the smaller of the "1–2 sentences" the
decision allowed, and there is no relevance signal to justify 2. Nothing here shows that 1 beats
0. Both it and `MAX_WORDS` are single parameters that invalidate cache 2 cleanly by text hash,
so sweeping them costs minutes — and is meaningless until there is something to measure against.
See §5.5, *"Is the overlap right?"*, for what is confirmed versus what is assumed.

**3. Block classification is crude. [FIXED]** The chunker labelled any block of ≤10 words a
title, so `"El riesgo es alto."` became a section header — and a single-block CSV made a
4,170-word table a "title". Removed entirely along with `clean()` and `agrupar_secciones()`;
structure detection belongs to extraction and titles come from the catalog. Note the failure
mode this produced while it was still in place: because a title-only section emitted no chunks
under the new packer, three csv/xlsx documents disappeared from the index completely. Silent
document loss is the characteristic damage of heuristic classification.

**4. Document aggregation is untested.** Max-pooling is chosen on sound reasoning, but there is
no ground truth to validate it against. It determines F1@3, which is half the score.

**5. Empty documents can never be retrieved.** Catalog files and a handful of failed scrapes
produce zero chunks, so they are absent from the index entirely.
*Decision: leave them out.* This was investigated thoroughly. Two rescue proposals were tested
and both rejected: the sub-threshold text turns out to be site navigation menus (CENIA), a
date-stamped label (SWF newsletters), or a meta-description that names figures without
containing any (ESA). Ten of the twenty catalog files are not even in ADL's inventory, so they
cannot appear in the ground truth. **The principle: a chunk must carry information that could
*answer* a question, not merely signal that a document on the topic exists.**

**6. No post-filters yet.** Similarity threshold and metadata filters are permitted (§8.7) and
plumbed, but unjustifiable without validation data.

**7. Language detection is advisory.** `langdetect` is seeded for reproducibility and runs per
document with a word floor, abstaining rather than guessing on short text. It is still
unreliable on code-mixed documents, and every chunk inherits the document's label.

**8. MEASURED: 44% of indexed text never reached a vector. [FIXED]** This was a theoretical
risk until the end-to-end run measured it, and the answer was far worse than expected. It is
kept here in full because it is the most instructive finding in the project: the failure was
completely silent, and only a measurement found it.

`num_tokens` against the 512-token encoder ceiling, on a real 326-chunk index:

| p50 | p95 | p99 | max |
|---|---|---|---|
| 295 | 1,141 | 5,098 | **8,947** |

**94 of 326 chunks (29%) exceed 512 tokens**, and **71,821 of 163,815 tokens — 44% of all
indexed text — is silently discarded** by the encoder.

The cause is items 1 and the chunker's oversized-block path together: `MAX_WORDS = 250`
controls *packing*, but a single block larger than that is emitted whole. One 3,000-word
paragraph becomes one 3,000-word chunk. A CSV becomes one 4,170-word chunk.

This is a §4.3 violation, and it is invisible without measuring: retrieval still returns
valid, plausible answers because the surviving first 512 tokens are real text. Nothing errors.
Nearly half the corpus is simply not searchable.

**Resolved by two changes, one at each end.** `CSVExtractor`/`ExcelExtractor`/`PBFExtractor`
now separate rows with a blank line, so a tabular file is many blocks instead of one; and the
chunker packs sentences under a 506-token cap alongside the 250-word cap. Re-measured on the
same corpus:

| | p50 | p95 | p99 | max | over ceiling | discarded |
|---|---|---|---|---|---|---|
| before | 295 | 1,141 | 5,098 | 8,947 | 29.1% | 43.8% |
| after | 357 | 443 | 499 | **504** | **0%** | **0%** |

The lesson worth keeping: the reason this went unnoticed is that *nothing failed*. Retrieval
returned valid, plausible answers the whole time, because the surviving first 512 tokens are
real text. A silent 44% loss looks exactly like a working system from the outside.

**9. Flat index, no approximate alternative evaluated.** Deliberate — §5.2 endorses flat at this
scale and it stays fast at 110,000 vectors.

**10. Architectures B and C deferred.** Only the single-encoder dense architecture is being
built. A BGE-M3 dense+sparse hybrid is rated better-supported by the team's own research, and
is cheap to add later: the text cache is encoder-independent, so adding a second encoder
re-encodes but never re-extracts.

---

## 10. How to run everything

```bash
# Component self-checks — no GPU, no model download, no network
python extraccion_final.py            # 17 JSON fixtures + catalog matching
python chunker.py                     # caps, sentence boundaries, escalera, Table 1
python pipeline_final.py selftest     # 11 checks incl. resume and §1.4 alignment
python generador.py selftest          # 11 checks incl. standalone delivery
python inventario.py selftest         # the 1,826-row join key

# Reconcile a corpus tree against ADL's inventory
python inventario.py "CORPUS CODEFEST AD ASTRA 2026"

# Index a corpus. --sample N for a dry run; both caches make reruns cheap.
python pipeline_final.py --corpus "CORPUS CODEFEST AD ASTRA 2026" --batch-size 64

# Produce the answers
python generador.py \
  --index    entrega/base_vectorial/encoder_multilingual-e5-large/index.faiss \
  --metadata entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl \
  --queries  Extracto_Preguntas_50_v2.pdf \
  --out      entrega/resultados.jsonl

# The whole thing on a Colab T4: uploads, selftests, reconciliation, index,
# generador, validation, then pulls the artifacts back
./_gentest/run_step7.sh
```

`pipeline_final.py` writes `run_manifest.json` next to the delivery. It carries every number
Spec 03 asks to be measured — per-stage wall time, escalera hits per level, `num_tokens`
percentiles, chunks per format, documents with zero chunks, library versions and the pinned
model revision — so a run can be audited after the fact instead of re-run.

### Environment traps that will cost you an afternoon

- **`sentence-transformers` needs `pyarrow`**, which is not installed by default here.
  `pip install pyarrow` fixes it. The older note that it *cannot* import on this machine is
  wrong: it imports, and both the tokenizer and the full model run locally on CPU. That
  mistaken belief is why chunking originally budgeted in words alone.
- **The Colab session disappears when idle.** `colab sessions` reporting *"No active sessions
  found on server"* is normal, not an outage — `colab new -s gentest --gpu T4` recreates it.
  Uploads fail with a bare `FAIL` when the session is gone, which reads like a path bug.
- **Git Bash rewrites `/content/...` into `C:/Program Files/Git/content/...`** on its way to
  the Colab CLI. Set `MSYS_NO_PATHCONV=1`.
- **…but that also stops *local* paths converting**, so with it set, local paths must be
  relative. Absolute local paths break.
- **The Colab contents API does not create directories**, and uploading into a nested path
  fails even after `mkdir`. Ship one zip and unpack it on the VM.
- **A 327 MB upload gets reset** by the proxy. Per-file, or one modest zip.
- **The Colab VM can be recycled mid-run**, and the dead session then holds the GPU quota as an
  orphan that the CLI cannot kill. Clear it from the web UI.
- **A vanished session is not proof the run failed.** One OCR run went 404 with its output file
  gone and looked dead at 27/50; it had actually completed all 50 files, and the polling
  download simply raced the teardown. Read the job's own stdout before concluding anything.
- **Running locally on Apple Silicon uses `mps`, not `cpu`.** `encoder_e5()` now falls back to
  `mps` before `cpu`. On an 8GB Mac, fp32 at `batch_size=64` OOMs the MPS backend and hangs
  rather than raising (`Insufficient Memory, kIOGPUCommandBufferCallbackErrorOutOfMemory`) — the
  code casts to fp16 on `mps` for this reason, so do not remove that cast to "simplify" device
  handling. The encoder is also loaded only *after* extraction finishes, not before: having
  e5-large resident during EasyOCR's own GPU/MPS work measured real contention on the same 8GB
  machine (a 2.5–6.5s page hanging 9+ minutes).

---

## 11. Glossary

**Chunk / fragmento** — a piece of a document, a few hundred words, encoded as one vector.

**Cosine similarity** — angle between two vectors, −1 to 1. On normalised vectors it equals the
dot product.

**Embedding / vector** — a list of numbers (1,024 here) representing a text's meaning, such
that similar meanings are geometrically close.

**Encoder** — a model that turns text into an embedding. It generates no text, which is why it
is permitted where generative models are not.

**FAISS** — Facebook AI Similarity Search. Stores vectors, finds nearest neighbours fast.
Mandatory here.

**F1@3** — the score for the 3 returned documents, treated as a set.

**Fenómeno** — one of the three corpus themes (1, 2, 3).

**`fuente`** — the original ADL file a chunk came from. **The key the ground truth matches on.**

**IndexFlatIP** — a FAISS index that compares against every vector exactly, using inner
product.

**NDCG@10** — the score for the 10 returned fragments, rewarding relevance *and* good ordering.

**Normalisation** — scaling a vector to length 1, so dot product equals cosine similarity.

**OCR** — optical character recognition; reading text from an image of a page.

**Token** — the sub-word unit an encoder actually counts. The encoder ceiling is 512, of which
506 are usable after the `"passage: "` prefix and the two special tokens.

Measured for this model, **not** the ~1.3 tokens/word rule of thumb: about **1.54** for English
and **2.43** for Spanish. That ratio is the whole reason the word cap alone was insufficient —
250 Spanish words is roughly 600 tokens, comfortably past the ceiling.

---

## 12. Before delivery — the remaining gates

Most of what is measured in this document is still a **25-file stratified sample**; the corpus
(1,826 rows in the inventory) is now present on the development machine and extraction has been
run against it in full (§8, "Does not exist yet" → full-corpus extraction, 22 minutes). Chunking,
encoding and retrieval have not yet been re-run at full scale, which is what most of the
remaining blockers below are about.

### Blocking — the delivery cannot be produced without these

**1. ~~Get the corpus onto a machine that can run it.~~ Done.** The corpus is on the development
machine and a full-corpus extraction has completed (22 minutes, all formats, OCR included, after
the tuning in §5.1). `cache/textos.jsonl` now holds the full-corpus extraction output.

**2. Run `pipeline_final.py --sample N` on the real corpus before the full chunk/encode pass.**
Two of the four rows in the index-composition table (§9, debt 1) are *assumed* at 250 words per
chunk, not measured — PDF and JSON. Tabular already broke that assumption badly (178 w/chunk,
not 250). If prose breaks it too, the index is larger than the projected ~136,500 chunks and the
encode budget is wrong. Extraction being done removes one variable, but chunking and encoding at
full scale are still unmeasured. Measuring costs minutes; discovering it after a full encode
costs the run.

**3. Decide the tabular policy from those numbers.** Tabular projects to ~69% of the index, and
one CSV alone to ~29%. The lever is already plumbed — `formato` is in the metadata, so a
post-filter or a per-format cap on the ten fragments is a few lines. **This decision is cheap
before the full encode and expensive after it.**

**4. Rehearse the resume path on real volume.** `pipeline_final selftest` covers resume against
a *simulated* kill with three chunks. It has never faced a real Colab disconnect at 100k chunks
across multiple checkpoint flushes — and Colab sessions do vanish; two runs were lost that way.
Kill a real run deliberately at ~30% and resume it. This is the difference between losing twenty
minutes and losing the run.

**5. `informe_tecnico.pdf`.** Graded, not started, ≤8 pages. §3.2 requires the chunking strategy
be justified *explicitly*, and the strategy is now a hybrid — paragraph packing, sentence
granularity, a dual word/token cap, the five-level escalera, and prose-only overlap. More to
justify than when that requirement was written, not less. §5.5 and §9 are the raw material.

**6. Produce and validate the delivery tree.** Exactly the four items in §7, nothing extra:
`resultados.jsonl`, `generador.py`, `informe_tecnico.pdf`, `base_vectorial/`. Then
`python _gentest/validate_resultados.py` against it, and `python generador.py selftest`, whose
check 11 runs the delivery standalone from an empty directory with no repository imports. §1.4
is pass/fail: *"Si no es posible reproducir los resultados, se excluirá de la evaluación."*

### Not blocking, but each is a known cost

**7. Chunk-text deduplication in the fragment walk.** Overlap means adjacent chunks share a
sentence and score similarly, so both can occupy fragment slots and one is spent on a repeat.
`generador._fragmentos_de` deduplicates nothing at chunk level. A few lines, but the threshold
is a tuning call and an over-eager filter drops legitimately distinct fragments.

**8. Prune the escalera.** Levels 4 (single newlines) and 5 (commas) have never fired. Step 1
item 4 says delete levels that never fire; 25 files is too thin to retire them on.
`run_manifest.json` records the counters on every run — decide from the full-corpus numbers.

**9. Strip running headers in extraction.** `remove_repeated_lines` cannot see a header carrying
a page number, because the number makes each occurrence unique. ~2% of prose chunks open with
one. Cosmetic for compliance, real for retrieval quality; the fix touches every PDF, so it needs
its own measurement rather than a guess.

**10. Sweep `OVERLAP_ORACIONES` and `MAX_WORDS`** once there is ground truth. Cache 2 keys on
chunk text, so each sweep re-encodes only what moved. See §5.5, *"Is the overlap right?"*.

### Already closed

`requirements.txt` pinned with the model revision recorded; `chunker.py` stripped to chunking
only; extraction row-joins, one-document-per-`.pbf`, language, `adl_doc_id` and `catalogo_*`;
inventory reconciliation; `pipeline_final.py` with both caches, resume and manifest;
`generador.py`; overlap; and a full end-to-end T4 run producing a **VALID** `resultados.jsonl`.
