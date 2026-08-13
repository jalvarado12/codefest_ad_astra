"""Pick a stratified random sample for the end-to-end generador test.

Seeded, so the sample is reproducible. Deliberately includes both PDF kinds
(text-layer and OCR-only), because they exercise completely different paths in
PDFExtractor, and the OCR path is the one that has never been through chunking.

CSVs are sampled from the SMALL end on purpose: the corpus median CSV is fine
but one holds 7.1M words, which alone would be ~28k chunks and would swamp a
25-file smoke test.
"""
import json
import random
import shutil
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import fitz

CORPUS = Path(r"C:\Users\User\Downloads\AD_Astra\CORPUS CODEFEST AD ASTRA 2026")
OUT = Path("_gentest/corpus")
EXCL = {"Extracto_Preguntas_50_v2.pdf", "Indice_Datos_Codefest.xlsx",
        "FASE ORDENADA CODEFEST.xlsx"}

random.seed(20260812)
shutil.rmtree(OUT, ignore_errors=True)

# split PDFs by whether they carry a text layer
pdf_text, pdf_ocr = [], []
for p in sorted(CORPUS.rglob("*.pdf")):
    if p.name in EXCL:
        continue
    try:
        d = fitz.open(str(p))
        n = len("".join(pg.get_text() for pg in d).strip())
        pages = len(d)
        d.close()
    except Exception:
        continue
    (pdf_ocr if n < 50 else pdf_text).append((p, pages))

small_ocr = [p for p, pg in sorted(pdf_ocr, key=lambda t: t[1]) if pg <= 2]
mid_text = [p for p, pg in pdf_text if 5 <= pg <= 40]

def by_size(pattern, lo, hi):
    return [p for p in sorted(CORPUS.rglob(pattern))
            if p.name not in EXCL and lo <= p.stat().st_size <= hi]

plan = {
    "pdf_text_layer": random.sample(mid_text, 8),
    "pdf_ocr_only":   random.sample(small_ocr, 3),
    "json":           random.sample([p for p in sorted(CORPUS.rglob("*.json"))
                                     if p.stat().st_size > 3000], 8),
    "csv_small":      random.sample(by_size("*.csv", 0, 400_000), 2),
    "xlsx":           random.sample(by_size("*.xlsx", 0, 5_000_000), 1),
    "imagen":         random.sample(sorted(CORPUS.rglob("*.jpg")), 2),
    "txt":            sorted(CORPUS.rglob("*.txt"))[:1],
}

manifest = []
for grupo, files in plan.items():
    for p in files:
        rel = p.relative_to(CORPUS)
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        manifest.append({"grupo": grupo,
                         "fuente": str(rel).replace("\\", "/"),
                         "bytes": p.stat().st_size})

Path("_gentest/manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

total_mb = sum(m["bytes"] for m in manifest) / 1e6
print(f"sampled {len(manifest)} files, {total_mb:.1f} MB")
for grupo in plan:
    rows = [m for m in manifest if m["grupo"] == grupo]
    print(f"\n  {grupo}  ({len(rows)})")
    for m in rows:
        print(f"     {m['bytes']/1000:8.0f} KB  {m['fuente']}")
