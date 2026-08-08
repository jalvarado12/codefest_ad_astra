#!/usr/bin/env bash
# Provision a Colab GPU session, sync the harness, run it, pull results back.
#
# Prerequisite: the Colab CLI must be authenticated once, interactively:
#     colab sessions          # prompts, opens a URL, asks for the code
#
# On Windows the CLI needs the termios/tty shims in _winshim (POSIX-only modules
# imported by colab_cli.console); PYTHONPATH below supplies them. On Linux/macOS
# that line is a harmless no-op.
set -euo pipefail

SESSION="${SESSION:-adastra}"
GPU="${GPU:-T4}"
STAGES="${STAGES:-selftest,verify,bench}"
N_DOCS="${N_DOCS:-100}"
CORPUS_DIR="${CORPUS_DIR:-}"          # local corpus dir; uploaded to /content/corpus
TIMEOUT="${TIMEOUT:-14400}"           # exec timeout, seconds (default 4h)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$HERE/_winshim${PYTHONPATH:+:$PYTHONPATH}"
REMOTE=/content/arch_test

echo "== creating session '$SESSION' (${GPU:-CPU}) =="
colab new -s "$SESSION" ${GPU:+--gpu "$GPU"}

echo "== installing dependencies =="
colab install -s "$SESSION" -r "$HERE/requirements.txt"

echo "== uploading harness =="
for f in chunker.py corpus.py encoders.py harness.py metrics.py validation.py colab_job.py \
         arch_a_e5_dense.py arch_b_bgem3_hybrid.py arch_c_e5_plus_bgem3_sparse.py \
         arch_d_minilm_dense.py arch_e_minilm_plus_bgem3_sparse.py; do
  colab upload -s "$SESSION" "$HERE/$f" "$REMOTE/$f"
done
colab upload -s "$SESSION" "$HERE/../ad_astra.md" /content/ad_astra.md

if [[ -n "$CORPUS_DIR" ]]; then
  echo "== uploading corpus from $CORPUS_DIR =="
  # One zip, not one call per file: `colab upload` is a round trip per file and a
  # few hundred documents would take longer to ship than to encode. Unzipping on
  # the VM preserves the relative layout, so phenomenon-from-path still works.
  ZIP="${TMPDIR:-/tmp}/adastra_corpus.zip"
  rm -f "$ZIP"
  (cd "$CORPUS_DIR" && zip -qr "$ZIP" .)
  colab upload -s "$SESSION" "$ZIP" /content/corpus.zip
  printf 'import shutil, pathlib\nshutil.unpack_archive("/content/corpus.zip", "/content/corpus")\nprint("corpus files:", sum(1 for _ in pathlib.Path("/content/corpus").rglob("*") if _.is_file()))\n' \
    | colab exec -s "$SESSION" --timeout 600
fi

echo "== running stages: $STAGES =="
# runpy (not exec(open(...))) so __file__ is set and colab_job.py can find its
# sibling modules and its data directory.
printf 'import runpy, sys\nsys.argv = ["colab_job.py", "--stages", "%s", "--n-docs", "%s"]\nrunpy.run_path("%s/colab_job.py", run_name="__main__")\n' \
  "$STAGES" "$N_DOCS" "$REMOTE" | colab exec -s "$SESSION" --timeout "$TIMEOUT"

echo "== pulling results =="
mkdir -p "$HERE/data"
for f in benchmark.json timings.json timings_encode.json summary.json chunks.jsonl sample.json \
         results_A.jsonl results_B.jsonl results_C.jsonl results_D.jsonl results_E.jsonl; do
  colab download -s "$SESSION" "$REMOTE/data/$f" "$HERE/data/$f" 2>/dev/null \
    || echo "  (no $f)"
done

echo "== stopping session (idle VMs burn compute units) =="
colab stop -s "$SESSION"
echo "done."
