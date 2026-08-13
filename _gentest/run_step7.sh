#!/usr/bin/env bash
# Step 7 — the real pipeline on a Colab GPU, end to end.
#
# Supersedes run_gentest.sh, which drove the build_index_gpu.py prototype. This
# one runs pipeline_final.py itself, so what is exercised here is what ships.
#
# PATH RULES, all learned the hard way:
#  1. MSYS_NO_PATHCONV=1 is required or Git Bash rewrites /content/... into
#     C:/Program Files/Git/content/... and every remote path is wrong.
#  2. It also stops LOCAL posix paths converting, so local paths must be RELATIVE
#     (run from the repo root) — absolute local paths break.
#  3. Uploading into nested remote directories fails even after mkdir. Ship ONE zip
#     and unpack it on the VM; that also preserves the relative corpus layout that
#     fenomeno-from-path depends on.
#  4. A session that has been idle is often gone: `colab sessions` reporting
#     nothing means `colab new -s "$S" --gpu T4` first.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
export PYTHONPATH='C:\Users\User\Downloads\AD_Astra\codefest_ad_astra\_winshim'
export MSYS_NO_PATHCONV=1
S="${SESSION:-gentest}"
CORPUS_ZIP="${CORPUS_ZIP:-_gentest/corpus_sample.zip}"
BATCH="${BATCH:-64}"

echo "== deps =="
printf 'import subprocess,sys\nsubprocess.run([sys.executable,"-m","pip","install","-q","PyMuPDF","easyocr","sentence-transformers","faiss-cpu","langdetect","openpyxl","pandas","mapbox-vector-tile"],check=True)\nimport torch,faiss\nprint("torch",torch.__version__,"cuda",torch.cuda.is_available(),"faiss ok")\n' \
  | colab exec -s "$S" --timeout 1800 2>&1 | tail -2

echo "== code =="
for f in extraccion_final.py chunker.py generador.py pipeline_final.py inventario.py; do
  colab upload -s "$S" "$f" "/content/$f" >/dev/null 2>&1 && echo "  up $f" || echo "  FAIL $f"
done
colab upload -s "$S" "_gentest/validate_resultados.py" /content/validate_resultados.py >/dev/null 2>&1 \
  && echo "  up validator" || echo "  FAIL validator"
colab upload -s "$S" "Extracto_Preguntas_50_v2.pdf" /content/queries.pdf >/dev/null 2>&1 \
  && echo "  up queries" || echo "  FAIL queries"
colab upload -s "$S" "Indice_Datos_Codefest.xlsx" /content/Indice_Datos_Codefest.xlsx >/dev/null 2>&1 \
  && echo "  up inventario" || echo "  FAIL inventario"

echo "== selftests on the VM, before spending GPU time =="
printf 'import subprocess,sys\nfor m in ("chunker.py","pipeline_final.py"):\n    a=[sys.executable,"/content/"+m] + ([] if m=="chunker.py" else ["selftest"])\n    r=subprocess.run(a,capture_output=True,text=True,cwd="/content")\n    print(m,"rc=",r.returncode)\n    print(r.stdout[-1200:])\n    if r.returncode: print("STDERR:",r.stderr[-1500:])\n' \
  | colab exec -s "$S" --timeout 900 2>&1 | tail -30

echo "== corpus zip, unpacked on the VM =="
colab upload -s "$S" "$CORPUS_ZIP" /content/corpus_sample.zip >/dev/null 2>&1 \
  && echo "  up $CORPUS_ZIP" || { echo "  FAIL corpus zip"; exit 1; }

printf 'import shutil, pathlib, collections\np=pathlib.Path("/content/gentest/corpus")\nshutil.rmtree(p, ignore_errors=True)\np.mkdir(parents=True, exist_ok=True)\nshutil.unpack_archive("/content/corpus_sample.zip", str(p))\nfiles=[q for q in p.rglob("*") if q.is_file()]\nprint("unpacked files:", len(files))\nprint(collections.Counter(q.suffix.lower() for q in files))\n' \
  | colab exec -s "$S" --timeout 600 2>&1 | tail -3

echo "== reconciliacion de inventario (Step 3) =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/inventario.py","/content/gentest/corpus","/content/Indice_Datos_Codefest.xlsx"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-2000:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 600 2>&1 | tail -15

echo "== pipeline_final on GPU =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/pipeline_final.py",\n  "--corpus","/content/gentest/corpus",\n  "--out","/content/entrega",\n  "--cache","/content/cache",\n  "--vectors","/content/vectors",\n  "--inventario","/content/Indice_Datos_Codefest.xlsx",\n  "--batch-size","%s"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-6000:])\nif r.returncode: print("STDERR:", r.stderr[-3000:])\nprint("rc=",r.returncode)\n' "$BATCH" \
  | colab exec -s "$S" --timeout 7200 2>&1 | tail -45

echo "== generador.py against the real index =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/generador.py",\n  "--index","/content/entrega/base_vectorial/encoder_multilingual-e5-large/index.faiss",\n  "--metadata","/content/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl",\n  "--queries","/content/queries.pdf",\n  "--out","/content/entrega/resultados.jsonl",\n  "--k","50"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-3000:])\nif r.returncode: print("STDERR:", r.stderr[-2000:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 3600 2>&1 | tail -25

echo "== validador =="
printf 'import subprocess,sys\nr=subprocess.run([sys.executable,"/content/validate_resultados.py",\n  "/content/entrega/resultados.jsonl",\n  "/content/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl"],capture_output=True,text=True,cwd="/content")\nprint(r.stdout[-2500:])\nprint("rc=",r.returncode)\n' \
  | colab exec -s "$S" --timeout 900 2>&1 | tail -25

echo "== pull artifacts =="
mkdir -p _gentest/step7
for f in resultados.jsonl run_manifest.json; do
  colab download -s "$S" "/content/entrega/$f" "_gentest/step7/$f" 2>&1 | tail -1
done
colab download -s "$S" /content/entrega/base_vectorial/encoder_multilingual-e5-large/metadata.jsonl \
  _gentest/step7/metadata.jsonl 2>&1 | tail -1
echo "== done =="
