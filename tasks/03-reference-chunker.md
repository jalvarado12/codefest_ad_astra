# Task 03 — Reference chunker

**Status: DONE (2026-08-05, executed in-session)**

## Goal
A minimal, spec-compliant chunker so that A, B and C are fed byte-identical chunks.

## Context
Task 01 confirmed no chunking code exists in this repo. This chunker is **reference-only for the
comparison** — it is not a claim about the team's final submission chunking strategy. Its one
load-bearing property is that all three architectures see the same fragments.

## Graphify queries to run first
N/A — no graph exists.

## Read only these files
- `ad_astra.md` §2.2 (cleaning), §3.3 (linguistic completeness), §3.4 + Table 1 (mandatory metadata)

## Create/modify exactly these files
- `arch_test/chunker.py`

## Detailed instructions
1. Sentence splitting for ES/EN/PT: terminators `. ! ? …`, tolerate closing quotes/brackets, handle
   `¿ ¡` openers, suppress splits after known abbreviations (`Dr.`, `EE.UU.`, `etc.`, initials).
2. Pack sentences up to a token budget; never split a sentence across chunks (§3.3). A single sentence
   over budget is emitted alone and flagged `oversized`.
3. Budget defaults to **480, not 512** — both encoders add special tokens and e5 prepends
   `"passage: "`; without headroom chunks get silently truncated at encode time.
4. Emit every Table 1 field: `doc_id, chunk_id, fuente, formato, fenomeno, posicion, num_tokens, texto`.
5. Merge a runt tail chunk (< 32 tokens) into its predecessor when it still fits.

## Do NOT
- Do not cut mid-sentence under any circumstance, including for oversized sentences.
- Do not add nltk/spacy — regex + the model tokenizer are sufficient.

## Acceptance criteria
`python arch_test/chunker.py` prints `chunker self-check OK`.

## Verify
Self-check asserts: abbreviation handling, `¿`-initial sentences, every chunk within budget or flagged,
every chunk ends on a sentence terminator, **rejoined chunks reproduce the input exactly** (no text lost
or reordered), oversized sentence emitted whole, all Table 1 fields present. Passing.
On the real spec document: 59 chunks, median 426 tokens.

## Update graphify / CLAUDE.md
Recorded in CLAUDE.md under the harness section.
