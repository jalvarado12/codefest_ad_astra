#!/usr/bin/env bash
# End-to-end generador test on Colab T4: real sample corpus -> real index -> generador.py
#
# PATH RULES, all learned the hard way in this session:
#  1. MSYS_NO_PATHCONV=1 is required or Git Bash rewrites /content/... into
#     C:/Program Files/Git/content/... and every remote path is wrong.
#  2. It also stops LOCAL posix paths converting, so local paths must be RELATIVE
#     (run from the repo root) — absolute local paths break.
#  3. Uploading into nested remote directories fails even after mkdir. Ship ONE zip
#     and unpack it on the VM; that also preserves the relative corpus layout that
#     fenomeno-from-path depends on.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
export PYTHONPATH='C:\Users\User\Downloads\AD_Astra\codefest_ad_astra\_winshim'
export MSYS_NO_PATHCONV=1
S="${SESSION:-gentest}"

echo "== deps =="
printf 'import subprocess,sys\nsubprocess.run([sys.executable,"-m","pip","install","-q","PyMuPDF","easyocr","sentence-transformers","faiss-cpu","langdetect","openpyxl","pandas","mapbox-vector-tile"],check=True)\nimport torch,faiss\nprint("torch",torch.__version__,"cuda",torch.cuda.is_available(),"faiss ok")\n' \
  | colab exec -s "$S" --timeout 1800 2>&1 | tail -2

echo "== code =="
for f in extraccion_final.py chunker.py generador.py; do
  colab upload -s "$S" "$f" "/content/$f" >/dev/null 2>&1 && echo "  up $f" || echo "  FAIL $f"
done
colab upload -s "$S" "_gentest/build_index_gpu.py" /content/build_index_gpu.py >/dev/null 2>&1 && echo "  up builder" || echo "  FAIL builder"
colab upload -s "$S" "Extracto_Preguntas_50_v2.pdf" /content/queries.pdf >/dev/null 2>&1 && echo "  up queries" || echo "  FAIL queries"

echo "== corpus (single 22.6 MB zip, unpacked on the VM) =="
colab upload -s "$S" "_gentest/corpus_sample.zip" /content/corpus_sample.zip >/dev/null 2>&1 \
  && echo "  up corpus_sample.zip" || { echo "  FAIL corpus zip"; exit 1; }

printf 'import shutil, pathlib\np=pathlib.Path("/content/gentest/corpus")\nshutil.rmtree(p, ignore_errors=True)\np.mkdir(parents=True, exist_ok=True)\nshutil.unpack_archive("/content/corpus_sample.zip", str(p))\nfiles=[q for q in p.rglob("*") if q.is_file()]\nprint("unpacked files:", len(files))\nimport collections\nprint(collections.Counter(q.suffix.lower() for q in files))\n' \
  | colab exec -s "$S" --timeout 600 2>&1 | tail -3

echo "== build index on GPU =="
printf 'exec(open("/content/build_index_gpu.py").read())\n' | colab exec -s "$S" --timeout 5400 2>&1 | tail -50

echo "== run generador.py against the real index =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/generador.py",\n  "--index","/content/entrega/base_vectorial/encoder_multilingual-e5-large/index.faiss",\n  "--metadata","/content/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl",\n  "--queries","/content/queries.pdf",\n  "--out","/content/entrega/resultados.jsonl",\n  "--k","50"],capture_output=True,text=True)\nprint(r.stdout[-3500:])\nprint("STDERR:", r.stderr[-2000:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 3600 2>&1 | tail -40

echo "== pull results =="
colab download -s "$S" /content/entrega/resultados.jsonl _gentest/resultados.jsonl 2>&1 | tail -1
colab download -s "$S" /content/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl _gentest/metadata.jsonl 2>&1 | tail -1
echo "== done =="
