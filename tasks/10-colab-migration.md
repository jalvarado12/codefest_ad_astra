# Task 10 — Restructure to the standard stack and move execution to Colab

**Status: CODE DONE. Colab run BLOCKED on one interactive auth step.**

## Goal
Drop the Windows workarounds, use each model's documented library, and execute on Colab GPU instead of
a 2-core CPU laptop.

## Context
Task 02 had to drive both models through plain `transformers` because sklearn/pyarrow is blocked by a
Windows Application Control policy locally. With the Colab CLI available, execution moves to a Linux
GPU VM where the normal stack works, and the CPU-throughput ceiling from Task 02 stops mattering.

## Graphify queries to run first
N/A — no graph exists. Read `arch_test/*.py` by name.

## Read only these files
- `arch_test/encoders.py`, `arch_test/harness.py`
- `colab skill` output (the CLI's own bundled operating guide)

## Create/modify exactly these files
- `arch_test/encoders.py` (rewritten), `arch_test/harness.py` (GPU-aware)
- `arch_test/requirements.txt`, `arch_test/colab_job.py`, `arch_test/run_on_colab.sh`
- `arch_test/_winshim/termios.py`, `arch_test/_winshim/tty.py`

## Detailed instructions
1. `encoders.py`: e5-large through `SentenceTransformer` (`normalize_embeddings=True`,
   `max_seq_length=512`); BGE-M3 through `BGEM3FlagModel` (`return_dense=True, return_sparse=True`,
   one call). Keep the class API identical so `harness.py` and `validation.py` need no changes.
   Normalise FlagEmbedding's string token-id keys to int.
2. `harness.py`: record `gpu_peak_mb` via `torch.cuda.max_memory_allocated`, reset per phase; batch
   size defaults to 32 on GPU, 4 on CPU.
3. `colab_job.py`: stages `selftest,verify,bench,pipeline,score`, each skipped when inputs are absent.
4. `run_on_colab.sh`: `colab new --gpu T4` → `install -r` → upload modules → run → download → **stop**.

## Do NOT
- Do not make `encoders.py` import on Windows — that is what forced the previous workaround.
- Do not upload a corpus file-by-file; `colab upload` is a round trip per file. Zip, upload once, unpack
  on the VM (preserving relative layout, which phenomenon-from-path detection depends on).
- Do not use `exec(open(...).read())` to launch the job — `__file__` is then unset and the job cannot
  locate its sibling modules. Use `runpy.run_path(..., run_name="__main__")`.
- Do not leave a session running; idle VMs burn compute units.
- The score stage still refuses anything that is not a CONFIRMED validation set.

## Acceptance criteria
All modules compile; the model-free self-checks still pass locally; `run_on_colab.sh` passes `bash -n`;
the `verify` and `bench` stages complete on a Colab GPU VM.

## Verify
Done locally: `py_compile` clean on all seven modules, `bash -n` clean, and `chunker.py` / `metrics.py`
/ `validation.py selftest` all still pass.
Colab CLI works on Windows once shimmed — `colab --help` and `colab skill` respond; `_winshim` supplies
the POSIX `termios`/`tty` that `colab_cli.console` imports at module scope. WSL was checked as an
alternative and rejected: the subsystem is present but **no distribution is installed**, and installing
one needs elevation plus a reboot.
**Blocked:** `colab sessions` / `colab whoami` require an interactive OAuth code paste, which an agent
cannot perform. One human step, once.

## Update graphify / CLAUDE.md
CLAUDE.md harness section updated: standard stack, Colab as execution target, the `_winshim`
requirement, and the local-import caveat.
