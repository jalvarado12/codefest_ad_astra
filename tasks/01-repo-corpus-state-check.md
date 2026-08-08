# Task 01 — Repo and corpus state check

**Status: DONE (2026-08-05, executed in-session)**

## Goal
Establish what actually exists before planning anything else: prior chunking code, prior test
harness, corpus location, git state, hardware ceiling.

## Context
The orchestration prompt stated "this repo is one shared local git repository" and assumed a corpus of
~2000 documents was reachable. Both had to be verified rather than believed.

## Graphify queries to run first
N/A — no graph exists for this project.

## Read only these files
- `ad_astra.md` (spec, full)
- `final-architecture-decision-report.md` (full)

## Create/modify exactly these files
None — investigation only.

## Detailed instructions
1. List the repo root; check for `.py`, task files, `graphify-out/`, prior harness code.
2. `git rev-parse --is-inside-work-tree` to confirm version-control state.
3. Search the user profile for corpus-shaped directories (corpus/codefest/fenomeno/documentos/fuentes/adl).
4. Record CPU, RAM, GPU, free disk, Python version, installed ML packages.

## Do NOT
- Do not assume a blank slate; do not assume the corpus is where the prompt implies.

## Acceptance criteria
Findings recorded before any further task is planned.

## Verify
Findings, as measured:
- Repo root holds **only** `ad_astra.md`, `embedding_research_report.md`,
  `final-architecture-decision-report.md`, `CLAUDE.md`. **No chunking code, no harness, no task files,
  no `graphify-out/`.**
- **Not a git repository** — `fatal: not a git repository`. The prompt's "shared local git repository"
  is wrong for this directory. The no-commit constraint is satisfied trivially; nothing to commit to.
- **The ADL corpus is not on this machine.** `AD_Astra` in Downloads is the same directory (Windows is
  case-insensitive), not a second one. No PDF/HTML/JSON/CSV/XLSX/PBF corpus anywhere under the user
  profile. `webpage_arch` is an unrelated web project.
- Hardware: **Intel i5-6300U, 2 cores / 4 threads, 7.9 GB RAM, no GPU**, 187 GB free disk,
  Python 3.10.9, only `numpy` preinstalled. Far below the prompt's assumed compute.

## Update graphify / CLAUDE.md
CLAUDE.md gained an "Architecture comparison harness" section recording these findings.
