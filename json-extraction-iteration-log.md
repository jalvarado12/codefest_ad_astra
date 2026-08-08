# JSON extraction — iteration log

Audit trail for the design in `json-extraction-strategy.md`. Kept separate so that document reads as a
recommendation rather than a diary. Three iterations were run against a permitted cap of four.

## Iteration 0 — state verification (before proposing anything)

The originating request asserted that no code existed yet and that the corpus's `.json` files could be
sampled. Both were checked rather than believed. Both were wrong.

- `arch_test/corpus.py:67-102` already contains a `_json` extractor: alias table, recursive walk,
  all-string-leaf fallback. `tasks/01`–`tasks/10` exist, so the next free task number is 11, not 001.
- The ADL corpus is not on this machine. Searched the user profile for directories matching `corpus`,
  `fenomeno`, `documentos`, `adl`, `codefest`, `astra`; the only hit is the project directory itself. The
  only `.json` files present are `arch_test/data/_smoke/*` (this project's own harness artifacts) and an
  unrelated project's `graphify-out/` cache. Task 01 recorded the same on 2026-08-05; this is an
  independent re-check on 2026-08-06, not a citation of it.

Effect on the loop: the grounding criterion is unsatisfiable in this session, so it was carried as a
known failure through every iteration rather than being quietly dropped from the checklist. The other
criteria were still worth iterating on, and the code audit gave real evidence to iterate against.

## Iteration 1 — extend the alias table in `corpus.py:_json`

**Proposed.** Keep the existing structure. Widen `body_keys` and `meta_keys` to cover the observed
variants (headline, article_body, contenido, resumen, published_at, byline, …), matching keys
case-insensitively and accent-stripped.

**Failed: no implicit document-coverage gap.** The alias table was never the binding constraint. Three
silent-drop paths survive untouched:

- A body key whose value is a **dict** (`{"content": {"blocks": [...]}}`) matches the alias branch at
  line 81, satisfies neither the `str` nor the `list` case, appends nothing, and — because the branch was
  taken — never reaches the `else: walk(v)` recursion. The body vanishes with no record.
- A body key whose value is a **list of objects** (`"body_paragraphs": [{"text": "..."}]`) hits
  `extend(x for x in v if isinstance(x, str))` at line 85, which yields nothing, with no recursion.
- BOM-prefixed files and NDJSON files fail at `json.loads` (line 69 reads `utf-8`, not `utf-8-sig`), so
  the whole document is lost at load.

**Failed: no per-document audit.** Nothing records which path fired or which keys matched, so there is no
way to learn whether a widened table is actually sufficient — the iteration cannot even be evaluated
after the fact.

**Changed for the next pass.** Fix the walk's value-type handling and the loader. Treat the key list as
the least important part of the problem, and add provenance recording so coverage becomes measurable.

## Iteration 2 — recurse into unmatched body-value types; keep the existing fallback

**Proposed.** Iteration 1's alias table, plus: recurse into dict-valued and list-of-object-valued body
keys; load with `utf-8-sig` and retry NDJSON on decode failure; record a per-document trace of matched
key paths. Leave the existing all-string-leaf fallback (lines 93-101) as the safety net.

**Failed: descriptive fields kept out of body text (§2.1).** The retained fallback is unfiltered. When it
fires, it concatenates every string leaf — urls, ISO dates, author names, tags, ids, slugs — straight
into the body. §2.1 says explicitly to keep those as document metadata instead of mixing them into the
body text. It also degrades retrieval, since ids and slugs get embedded alongside prose.

**Failed: no implicit document-coverage gap.** The fallback fires only when `parts` is empty. A file
matching `title` but not its body extracts about five words, passes the fallback's emptiness check
without triggering it, then trips `extract()`'s 30-word floor at line 174 and lands in `skipped.json`
labelled "extracted only N words (scanned/empty?)". The document is not silently lost, but its cause of
death is recorded wrongly — a schema miss filed as a scan failure. Every such file would be
mis-triaged during Task 12's review, which is precisely the review that is supposed to catch schema
gaps.

**Changed for the next pass.** Filter the fallback so §2.1's separation holds on that path too. Trigger
it on a word floor rather than on emptiness, and reuse `extract()`'s existing 30-word constant so the
trigger and the reject criterion cannot drift apart.

## Iteration 3 — tiered design with recorded provenance

**Proposed.** The design in `json-extraction-strategy.md`: robust load (utf-8-sig, NDJSON retry, explicit
`JsonIlegible`); tier 1 alias harvest that recurses on unmatched body-value types; tier 2 filtered
generic harvest triggered by the shared 30-word floor; tier 3 explicit `JsonSinTexto` carrying the file's
top-level key set; per-document `traza` plus a `--resumen` census mode.

**Checked against every criterion:**

- *Explicit fallback for unrecognized shapes* — passes. Four enumerated outcomes, each recorded.
- *§2.1 separation and order* — passes. META keys never emit as body in either tier; tier 2 additionally
  filters urls, dates, ids and short strings; emission follows traversal order, with no title-first
  reordering, per "respetando su orden de aparición".
- *Determinism (§1.4)* — passes. Pure function of the file's bytes; insertion-ordered dict iteration;
  frozen regex constants; no model, network or clock. The LLM-classification option was rejected on
  exactly this ground, and separately on the §4.2/§8.3 decoder ban.
- *No implicit coverage gap* — passes, structurally rather than by discipline. Four recorded states, and
  no code path returns empty text without raising.
- *Task files numbered correctly* — passes. 11 and 12, verified against `tasks/01`–`tasks/10`.
- *Nothing implemented, executed, or committed* — passes.
- *Grounded in a real corpus sample* — **still fails.** Unchanged since iteration 0, and not addressable
  by any design change.

**Accepted, with that one failure carried openly.** The design's response to the missing survey is to
make the extractor self-surveying, so the census is a byproduct of the first real run rather than a
prerequisite for it. That is a mitigation and is labelled as one in the strategy document: it guarantees
the gap becomes visible and measurable, but it is not evidence, and the alias table's coverage remains an
untested guess until Task 12 runs.

## Iteration 4 — the real corpus (2026-08-06)

The corpus arrived at `corpus_by_type/json/` — 964 files, 13 MB. The iteration that could not be run
became the one that mattered. Design built, run over every file, revised on what the data showed.

**What the pre-run census (964 files, key-set and nesting sweep) established.** The corpus is scraper
output from 20 sources, one consistent shape per source: 15 distinct top-level key sets, nesting never
deeper than one named level, zero parse failures. Far more regular than the planning assumption. The
three illustrative shapes were a poor guide — the `{"article": {...}}` wrapper does not occur at all, and
`headline` appears nowhere.

**Failed: paragraph order and non-duplication (§2.1).** 485 of 848 article files carry both `body_text`
and `body_paragraphs`, the former being the latter re-joined — measured on an ATLCOUNCIL file at 6 147 vs
6 136 characters with an identical opening sentence. The design as planned emitted both, **doubling every
one of those 485 documents**. Fixed by giving paragraph lists precedence over whole-body blobs, which is
also what §2.1 asks for.

**Failed: duplicate collapse.** The planned rule collapsed only *adjacent* exact duplicates. All 363
ALERTAS files repeat `body_paragraphs[1]` verbatim at `alerta_meta.tema_clave`, far apart in the
traversal, so the adjacent rule caught none of them. Fixed by collapsing exact duplicates globally.

**Failed: descriptive fields out of body text.** Recursing into `images`, `links` and the various
`*_links` containers dragged anchor text, alt text and file paths into the body. Fixed with a `NOISE` set
that tier 1 never enters and tier 2 enters only as a rescue.

**Failed: no coverage gap — one real bug.** `SWF_5-handbook-for-new-actors-in-space-chinese.json` holds
503 characters of genuine Chinese prose but roughly ten whitespace tokens, so `len(text.split())` rejected
a full page as under the 30-word floor. The floor had silently assumed whitespace-delimited script. Fixed
by counting CJK ideographs individually; the file now extracts at tier 1.

**Result after the revisions.** 938 of 964 at tier 1, 0 at tier 2, 26 at tier 3, 0 unparseable.
938 + 26 = 964 exactly. All 26 tier-3 files were read individually: 14 are scraper machinery (tile
indexes, download manifests, url→path registries), 11 are real pages whose scrape captured nothing, and 1
is genuinely thin. **Not one was a missing alias**, so the alias table gained no entries.

**Accepted.** Every definition-of-done criterion passes, including the grounding criterion that failed
through iterations 1–3.

## What the loop got wrong, worth recording

The three pre-corpus iterations spent their effort on the wrong axis. They assumed the hard problem was
**unrecognised field names** and iterated on alias coverage and fallback behaviour. The real corpus is
regular enough that the alias table needed no additions at all; the actual failure mode was
**duplication** — one body stored twice under two keys, in 848 files between the two patterns — which a
pure alias-table reading would have silently doubled into the index without ever tripping a coverage
check. A survey pre-pass, had one been possible, would have found this in an hour.

The self-surveying design did its job in the end, but it is worth being precise about what it bought:
it made the corpus's shape *measurable on first contact*, not *predictable in advance*. Iteration 4 still
had to happen, and it still changed four things.

## Open question for the human

Twelve documents are excluded from the index by `corpus.extract()`'s 30-word floor (`corpus.py:174`):
eleven empty scrapes and `ESA_space-debris-by-the-numbers`, which holds one genuine 17-word paragraph of
phenomenon-2 content. They carry real titles, URLs and topics, and a title-only document is still
retrievable for F1@3, which matches on `fuente`. That floor is global policy for every format, so
raising or relaxing it for thin-but-real documents is a corpus-wide decision and was deliberately not
taken inside this task.
