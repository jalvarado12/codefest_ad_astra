"""Extract the whole JSON corpus for chunking hand-off.

Scope: JSON-format documents only (json_extract.py's domain). Catalog/registry
files are excluded -- they carry no chunkable prose, see catalog_metadata.py.
Fenomeno comes from the top-level corpus folder (F1/F2/F3_*), which is how the
real corpus is organized -- corpus.py's phenomenon_from_path regex does not
match this naming (no word boundary after the digit before an underscore).

Output: sample_for_chunking.jsonl, one extracted document per line, every
JSON document in the corpus that clears json_extract's tier-3 floor.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from catalog_metadata import CATALOG_FILES
from json_extract import JsonIlegible, JsonSinTexto, extract_json

CORPUS_ROOT = Path("CORPUS CODEFEST AD ASTRA 2026")
SEED = 20260807


def fenomeno_from_top_folder(path, root):
    top = path.relative_to(root).parts[0]
    m = re.match(r"[Ff]([123])_", top)
    return int(m.group(1)) if m else 0


def source_org(path, root):
    parts = path.relative_to(root).parts
    return parts[1] if len(parts) > 2 else parts[0]


def build_sample():
    files = sorted(p for p in CORPUS_ROOT.rglob("*.json") if p.name not in CATALOG_FILES)

    picked, skipped = [], []
    for p in files:
        try:
            texto, meta, traza = extract_json(p)
        except (JsonSinTexto, JsonIlegible) as e:
            skipped.append((str(p), type(e).__name__))
            continue
        fen = fenomeno_from_top_folder(p, CORPUS_ROOT)
        picked.append({
            "fuente": str(p.relative_to(CORPUS_ROOT)).replace("\\", "/"),
            "formato": "json",
            "fenomeno": fen,
            "texto": texto,
            "n_words": len(texto.split()),
        })

    for n, doc in enumerate(picked):
        doc["doc_id"] = f"DOC-{n:04d}"

    return picked, skipped


def main():
    sample, skipped = build_sample()
    out = Path("sample_for_chunking.jsonl")
    with out.open("w", encoding="utf-8") as fh:
        for doc in sample:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")

    print(f"sample: {len(sample)} docs -> {out}")
    print(f"skipped (tier 3 / unparseable): {len(skipped)}")
    grid = Counter((d["fenomeno"], d["fuente"].split("/")[1]) for d in sample)
    for (fen, org), c in sorted(grid.items()):
        print(f"  fenomeno {fen}  {org}: {c}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
