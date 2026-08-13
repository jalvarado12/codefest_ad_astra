#!/usr/bin/env bash
# Tabular-only run on Colab: how much of the index will CSV/XLSX be, and do
# tabular chunks behave? No GPU needed — 3 files, no OCR, tens of chunks.
#
# Same path rules as run_step7.sh; see its header.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
export PYTHONPATH='C:\Users\User\Downloads\AD_Astra\codefest_ad_astra\_winshim'
export MSYS_NO_PATHCONV=1
S="${SESSION:-tabular}"

echo "== deps =="
# PyMuPDF is not optional even for a tabular corpus: generador.py reads the
# queries out of a PDF. Trimming it from this list cost a run.
printf 'import subprocess,sys\nsubprocess.run([sys.executable,"-m","pip","install","-q","PyMuPDF","sentence-transformers","faiss-cpu","langdetect","openpyxl","pandas","pyarrow"],check=True)\nimport torch,faiss\nprint("torch",torch.__version__,"cuda",torch.cuda.is_available(),"faiss ok")\n' \
  | colab exec -s "$S" --timeout 1800 2>&1 | tail -2

echo "== code =="
for f in extraccion_final.py chunker.py generador.py pipeline_final.py inventario.py; do
  colab upload -s "$S" "$f" "/content/$f" >/dev/null 2>&1 && echo "  up $f" || echo "  FAIL $f"
done
colab upload -s "$S" "_gentest/composicion.py" /content/composicion.py >/dev/null 2>&1 \
  && echo "  up composicion" || echo "  FAIL composicion"
colab upload -s "$S" "_gentest/validate_resultados.py" /content/validate_resultados.py >/dev/null 2>&1 \
  && echo "  up validator" || echo "  FAIL validator"
colab upload -s "$S" "Extracto_Preguntas_50_v2.pdf" /content/queries.pdf >/dev/null 2>&1 \
  && echo "  up queries" || echo "  FAIL queries"

echo "== tabular corpus =="
colab upload -s "$S" "_gentest/tabular_sample.zip" /content/tabular_sample.zip >/dev/null 2>&1 \
  && echo "  up tabular_sample.zip" || { echo "  FAIL zip"; exit 1; }

printf 'import shutil, pathlib, collections\np=pathlib.Path("/content/tab/corpus")\nshutil.rmtree(p, ignore_errors=True)\np.mkdir(parents=True, exist_ok=True)\nshutil.unpack_archive("/content/tabular_sample.zip", str(p))\nfiles=[q for q in p.rglob("*") if q.is_file()]\nprint("unpacked:", len(files), collections.Counter(q.suffix.lower() for q in files))\n' \
  | colab exec -s "$S" --timeout 600 2>&1 | tail -2

echo "== composicion del indice =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/composicion.py","/content/tab/corpus"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-5000:])\nif r.returncode: print("STDERR:", r.stderr[-2500:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 1800 2>&1 | tail -40

echo "== pipeline_final sobre solo tabular =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/pipeline_final.py",\n  "--corpus","/content/tab/corpus",\n  "--out","/content/tab/entrega",\n  "--cache","/content/tab/cache",\n  "--vectors","/content/tab/vectors",\n  "--batch-size","32"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-4000:])\nif r.returncode: print("STDERR:", r.stderr[-2500:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 3600 2>&1 | tail -25

echo "== generador sobre un indice 100% tabular =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/generador.py",\n  "--index","/content/tab/entrega/base_vectorial/encoder_multilingual-e5-large/index.faiss",\n  "--metadata","/content/tab/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl",\n  "--queries","/content/queries.pdf",\n  "--out","/content/tab/entrega/resultados.jsonl",\n  "--k","50"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-2500:])\nif r.returncode: print("STDERR:", r.stderr[-2000:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 3600 2>&1 | tail -20

echo "== validador =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/validate_resultados.py",\n  "/content/tab/entrega/resultados.jsonl",\n  "/content/tab/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-2500:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 900 2>&1 | tail -20

echo "== pull =="
mkdir -p _gentest/tabular
colab download -s "$S" /content/tab/entrega/run_manifest.json _gentest/tabular/run_manifest.json 2>&1 | tail -1
echo "== done =="
