#!/usr/bin/env bash
# Provision a Colab T4, upload generador.py + the delivery artifacts, run it
# against the real intfloat/multilingual-e5-large, and pull resultados.jsonl
# back. Mirrors the pattern in `origin/embedding:arch_test/run_on_colab.sh`
# (same colab-cli verbs: new / install / upload / exec / download / stop).
#
# Prerequisite: the Colab CLI must be authenticated once, interactively:
#     colab sessions          # prompts, opens a URL, asks for the code
#
# Windows notes (both required, not optional):
#   1. The colab-cli needs the termios/tty shims in _winshim/ (POSIX-only
#      modules imported by colab_cli.console) -- PYTHONPATH below supplies
#      them. On Linux/macOS that line is a harmless no-op.
#   2. Git Bash rewrites any argument that looks like an absolute POSIX path,
#      so "/content/generador/index.faiss" becomes
#      "C:/Program Files/Git/content/generador/index.faiss" unless
#      MSYS_NO_PATHCONV=1 is set. This script sets it for its own colab-cli
#      calls; it is exported, so it also covers colab-cli's own subprocesses.
#
# Usage:
#   ./colab_generador.sh
#   ARTIFACTS_DIR=entrega/base_vectorial/encoder_multilingual-e5-large \
#   QUERIES_FILE=Extracto_Preguntas_50_v2.pdf ./colab_generador.sh
set -euo pipefail

SESSION="${SESSION:-adastra-generador}"
GPU="${GPU:-T4}"
TIMEOUT="${TIMEOUT:-3600}"                    # exec timeout, seconds (1h default)

# Local paths (relative to this script's directory) of what gets uploaded.
ARTIFACTS_DIR="${ARTIFACTS_DIR:-entrega/base_vectorial/encoder_multilingual-e5-large}"
QUERIES_FILE="${QUERIES_FILE:-Extracto_Preguntas_50_v2.pdf}"
OUT_LOCAL="${OUT_LOCAL:-entrega/resultados.jsonl}"

# generador.py CLI flags, forwarded as-is.
K="${K:-50}"
THRESHOLD="${THRESHOLD:--1.0}"
BATCH_SIZE="${BATCH_SIZE:-32}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PYTHONPATH must be a WINDOWS-form path: MSYS_NO_PATHCONV=1 (needed so colab-cli
# remote paths like /content/... are not rewritten to C:/Program Files/Git/content/...)
# also stops Git Bash converting this one, and Python cannot import from /c/Users/...
if command -v cygpath >/dev/null 2>&1; then
  export PYTHONPATH="$(cygpath -w "$HERE/_winshim")${PYTHONPATH:+;$PYTHONPATH}"
else
  export PYTHONPATH="$HERE/_winshim${PYTHONPATH:+:$PYTHONPATH}"
fi
export MSYS_NO_PATHCONV=1
REMOTE=/content/generador

echo "== creating session '$SESSION' ($GPU) =="
colab new -s "$SESSION" --gpu "$GPU"

echo "== installing dependencies =="
# torch/faiss/numpy are normally preinstalled on the Colab GPU image; pinned
# loosely so this does not fight the base image. sentence-transformers and
# pymupdf are the two genuinely missing pieces.
cat > "${TMPDIR:-/tmp}/requirements_generador.txt" <<'EOF'
sentence-transformers>=3.0
torch>=2.1
faiss-cpu>=1.8
pymupdf>=1.24
numpy>=1.26
EOF
colab install -s "$SESSION" -r "${TMPDIR:-/tmp}/requirements_generador.txt"

echo "== uploading generador.py + artifacts =="
# The contents API does NOT create intermediate directories: uploading into a
# missing $REMOTE returns 500. Verified the hard way during the OCR run.
printf 'import pathlib\npathlib.Path("%s").mkdir(parents=True, exist_ok=True)\nprint("remote dir ready")\n' "$REMOTE" \
  | colab exec -s "$SESSION" --timeout 300

colab upload -s "$SESSION" "$HERE/generador.py" "$REMOTE/generador.py"
colab upload -s "$SESSION" "$HERE/$ARTIFACTS_DIR/index.faiss" "$REMOTE/index.faiss"
colab upload -s "$SESSION" "$HERE/$ARTIFACTS_DIR/metadata.jsonl" "$REMOTE/metadata.jsonl"
colab upload -s "$SESSION" "$HERE/$QUERIES_FILE" "$REMOTE/queries.pdf"

echo "== running generador.py on GPU (real multilingual-e5-large, fp16) =="
# runpy (not exec(open(...))) so __file__ is set inside generador.py.
printf 'import runpy, sys\nsys.argv = ["generador.py", "--index", "%s/index.faiss", "--metadata", "%s/metadata.jsonl", "--queries", "%s/queries.pdf", "--out", "%s/resultados.jsonl", "--k", "%s", "--threshold", "%s", "--batch-size", "%s"]\nrunpy.run_path("%s/generador.py", run_name="__main__")\n' \
  "$REMOTE" "$REMOTE" "$REMOTE" "$REMOTE" "$K" "$THRESHOLD" "$BATCH_SIZE" "$REMOTE" \
  | colab exec -s "$SESSION" --timeout "$TIMEOUT"

echo "== pulling resultados.jsonl back =="
mkdir -p "$(dirname "$HERE/$OUT_LOCAL")"
colab download -s "$SESSION" "$REMOTE/resultados.jsonl" "$HERE/$OUT_LOCAL"

echo "== stopping session (idle VMs burn compute units) =="
colab stop -s "$SESSION"
echo "done. results at $HERE/$OUT_LOCAL"
