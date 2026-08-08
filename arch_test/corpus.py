"""Corpus extraction and stratified sampling for the architecture comparison.

Extraction follows spec section 2.1 for the formats ADL ships (PDF, HTML, JSON,
CSV, XLSX, MD/TXT, images, PBF). Formats this box cannot handle are recorded in
the manifest with a reason and counted in the run log -- they are never dropped
silently, because a sample that quietly lost every PDF would invalidate the
whole comparison.

The sampler stratifies over phenomenon x language so that architectures are not
compared on an accidentally Spanish-only, phenomenon-2-only slice.
"""

import argparse
import csv
import io
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from chunker import clean

TEXT_EXT = {".md", ".txt", ".markdown", ".rst"}
SKIPPED = []  # (path, reason) for anything we could not read
JSON_TRAZAS = []  # per-document extraction provenance; written out as the schema census


def _pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "") for p in reader.pages]
    return _drop_repeated_lines(pages)


def _drop_repeated_lines(pages):
    """Spec section 2.2: strip running headers/footers and page numbers.

    A line appearing on more than half the pages of a multi-page document is
    boilerplate, not content.
    """
    if len(pages) < 4:
        return "\n\n".join(pages)
    counts = Counter()
    for p in pages:
        for line in {l.strip() for l in p.splitlines() if l.strip()}:
            counts[line] += 1
    threshold = len(pages) * 0.5
    boiler = {l for l, c in counts.items() if c > threshold}
    out = []
    for p in pages:
        kept = [l for l in p.splitlines()
                if l.strip() not in boiler and not re.fullmatch(r"\s*\d{1,4}\s*", l)]
        out.append("\n".join(kept))
    return "\n\n".join(out)


def _html(path):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        tag.decompose()
    return soup.get_text("\n")


def _json(path):
    """Spec section 2.1: take explicit text fields, keep descriptive ones as metadata.

    Delegates to json_extract, the submission-grade extractor, so the harness and the
    submission cannot drift into two different readings of the same file. Its
    JsonSinTexto / JsonIlegible both subclass Exception, so build_manifest records them in
    skipped.json with their reason rather than dropping the file silently.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from json_extract import extract_json
    texto, meta, traza = extract_json(path)
    JSON_TRAZAS.append(traza)
    return texto, meta


def _csv(path):
    """Each row becomes 'column: value' pairs so values keep their column context."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    dialect = csv.Sniffer().sniff(raw[:4096]) if raw[:4096].strip() else csv.excel
    rows = list(csv.DictReader(io.StringIO(raw), dialect=dialect))
    return "\n".join(
        "; ".join(f"{k}: {v}" for k, v in row.items() if k and v and str(v).strip())
        for row in rows)


def _xlsx(path):
    from openpyxl import load_workbook
    wb = load_workbook(str(path), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        try:
            header = [str(h) if h is not None else "" for h in next(rows)]
        except StopIteration:
            continue
        for row in rows:
            cells = [f"{h}: {v}" for h, v in zip(header, row) if v is not None and str(v).strip()]
            if cells:
                out.append("; ".join(cells))
    wb.close()
    return "\n".join(out)


def _image(path):
    """OCR per spec section 2.1. Requires the tesseract binary, not just the wrapper."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise RuntimeError("OCR unavailable: pytesseract/Pillow not installed")
    try:
        return pytesseract.image_to_string(Image.open(path), lang="spa+eng+por")
    except Exception as e:  # tesseract binary missing or language packs absent
        raise RuntimeError(f"OCR unavailable: {e}")


def _pbf(path):
    """Map tiles. Needs a vector-tile/OSM reader; recorded as unsupported if absent."""
    try:
        import mapbox_vector_tile  # noqa: F401
    except ImportError:
        raise RuntimeError("PBF unsupported: no mapbox-vector-tile/osmium reader installed")
    raise RuntimeError("PBF extraction not implemented in this harness")


EXTRACTORS = {".pdf": _pdf, ".html": _html, ".htm": _html, ".xhtml": _html,
              ".json": _json, ".csv": _csv, ".tsv": _csv, ".xlsx": _xlsx, ".xls": _xlsx,
              ".png": _image, ".jpg": _image, ".jpeg": _image, ".tif": _image,
              ".tiff": _image, ".webp": _image, ".pbf": _pbf}


def extract(path):
    """Return (clean_text, extra_metadata). Raises on unsupported/unreadable input."""
    path = Path(path)
    ext = path.suffix.lower()
    meta = {}
    if ext in TEXT_EXT:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif ext in EXTRACTORS:
        got = EXTRACTORS[ext](path)
        text, meta = got if isinstance(got, tuple) else (got, {})
    else:
        raise RuntimeError(f"unsupported extension {ext}")
    text = clean(text)
    if len(text.split()) < 30:
        raise RuntimeError(f"extracted only {len(text.split())} words (scanned/empty?)")
    return text, meta


def detect_language(text):
    """ES/EN/PT only -- anything else is reported as-is so the sample log shows it."""
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0
    try:
        return detect(text[:4000])
    except Exception:
        return "unknown"


PHENOMENON_HINTS = {
    1: ["inteligencia artificial", "artificial intelligence", "inteligência artificial",
        "defensa", "defence", "defense", "militar", "military", "ai index", "innovación"],
    2: ["órbita", "orbit", "leo", "space debris", "basura espacial", "detritos espaciais",
        "satélite", "satellite", "espacial", "space sustainability", "kessler"],
    3: ["territorial", "américa latina", "latin america", "migración", "migration",
        "gobernanza", "governance", "desigualdad", "conflicto", "caribe", "violencia"],
}


def guess_phenomenon(text, path):
    """Fallback only. If the corpus is foldered by phenomenon, use the path instead."""
    hay = (str(path) + " " + text[:20000]).lower()
    scores = {ph: sum(hay.count(k) for k in keys) for ph, keys in PHENOMENON_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else 0


def phenomenon_from_path(path, root):
    """Read phenomenon from a path component like 'fenomeno_2' / 'phenomenon-2' / 'f2'."""
    rel = str(Path(path).relative_to(root)).lower()
    m = re.search(r"(?:fen[oó]meno|phenomenon|fenomeno|ph|f)[_\-\s]?([123])\b", rel)
    return int(m.group(1)) if m else None


def build_manifest(root, phenomenon_mode="auto"):
    """Walk the corpus, extract every file, and record what worked and what didn't."""
    root = Path(root)
    files = [p for p in root.rglob("*") if p.is_file()]
    manifest = []
    for p in files:
        try:
            text, extra = extract(p)
        except Exception as e:
            SKIPPED.append((str(p), str(e)))
            continue
        ph = None
        if phenomenon_mode in ("auto", "path"):
            ph = phenomenon_from_path(p, root)
        if ph is None and phenomenon_mode in ("auto", "keywords"):
            ph = guess_phenomenon(text, p)
        manifest.append({
            "doc_id": f"DOC-{len(manifest):04d}",
            "fuente": str(p.relative_to(root)).replace("\\", "/"),
            "formato": p.suffix.lower().lstrip("."),
            "fenomeno": ph or 0,
            "idioma": detect_language(text),
            "n_words": len(text.split()),
            "path": str(p),
            **extra,
        })
    return manifest


def stratified_sample(manifest, n_docs, seed=20260805):
    """Sample n_docs balanced across phenomenon x language cells.

    Round-robins over cells so an over-represented cell (typically ES /
    phenomenon 3) cannot swallow the sample; within a cell, picks are random but
    seeded, and mid-length documents are preferred over 200-word stubs and
    500-page tomes that would dominate the chunk budget on their own.
    """
    rng = random.Random(seed)
    cells = defaultdict(list)
    for d in manifest:
        cells[(d["fenomeno"], d["idioma"])].append(d)
    for docs in cells.values():
        med = sorted(d["n_words"] for d in docs)[len(docs) // 2]
        docs.sort(key=lambda d: (abs(d["n_words"] - med), rng.random()))

    order = sorted(cells)
    picked, i = [], 0
    while len(picked) < n_docs and any(cells[c] for c in order):
        cell = order[i % len(order)]
        if cells[cell]:
            picked.append(cells[cell].pop(0))
        i += 1
    return picked


def main():
    ap = argparse.ArgumentParser(description="Build corpus manifest and draw a stratified sample")
    ap.add_argument("corpus_root")
    ap.add_argument("--n-docs", type=int, default=100)
    ap.add_argument("--out", default="arch_test/data")
    ap.add_argument("--phenomenon-mode", choices=["auto", "path", "keywords"], default="auto")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(args.corpus_root, args.phenomenon_mode)
    sample = stratified_sample(manifest, args.n_docs)

    (out / "manifest_full.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "skipped.json").write_text(json.dumps(SKIPPED, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "extraccion_json.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in JSON_TRAZAS), encoding="utf-8")

    print(f"corpus files readable: {len(manifest)}   skipped: {len(SKIPPED)}")
    if SKIPPED:
        reasons = Counter(r.split(":")[0] for _, r in SKIPPED)
        for r, c in reasons.most_common():
            print(f"  skipped {c:4d}  {r}")
    print(f"\nsample: {len(sample)} docs, {sum(d['n_words'] for d in sample):,} words")
    grid = Counter((d["fenomeno"], d["idioma"]) for d in sample)
    for (ph, lang), c in sorted(grid.items()):
        print(f"  fenomeno {ph}  {lang}: {c}")
    print(f"formats: {dict(Counter(d['formato'] for d in sample))}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
