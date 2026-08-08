"""Salvage per-entry metadata from catalog/registry JSON files instead of
indexing them as spurious title-salad pseudo-documents.

Background: json_extract.py's tier-1 harvest cannot distinguish "a JSON list
of several short real articles" (legitimate) from "a JSON list of scraper
bookkeeping entries -- title/url/status per downloaded PDF, no body prose"
(catalog/registry). Six of the latter pass tier-1 clean and become fake
documents; the other fourteen correctly fail tier-3 and are simply dropped,
losing real descriptive metadata (author, country, year, tags...) that the
matching PDF itself often lacks. See json-extraction-audit-doubts.md, finding
#1, for the corpus-wide verification behind this list.

Decision: neither index the catalog as its own document nor drop it. Match
each entry to the real file it describes (already present in the corpus in
its own right -- verified in the audit) and save the catalog's metadata keyed
to that file's `fuente`, so a downstream indexing step can enrich the real
document's metadata instead. Entries that cannot be matched (e.g. MAPPOEA:
68 of 78 entries are 404/503 dead scrapes, nothing to enrich) are recorded as
unmatched, never silently discarded.

The corpus root is a CLI argument, not corpus_by_type/: that directory is a
local per-extension convenience copy (sort_by_extension.py) built for testing
and will not exist in the production layout. This script walks whatever root
it is given, same as corpus.py's build_manifest.
"""

import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

# The 20 catalog/registry files identified by the corpus-wide audit: 14 that
# already fail json_extract's tier-3 (no body prose) and 6 that pass tier-1
# as pseudo-documents. Nothing speculative -- observed names only.
CATALOG_FILES = {
    # tier-3 today (json_extract rejects them, but they still carry entries
    # worth salvaging metadata from)
    "AMAZONUW_tiles-index.json", "ceeep_registro.json", "CEOBS_catalog-2.json",
    "ceobs_full_registro.json", "CSIS_catalog-2.json", "DEFENSA21_articulos-2.json",
    "DEFENSA21_catalog-2.json", "mapp_catalogo.json", "mapp_registro.json",
    "resdal_catalogo.json", "resdal_registro.json", "RUTAN_catalog-2.json",
    "SIPRI_catalog-2.json", "sipri_full_registro.json",
    # tier-1 leaks today (become fake documents unless excluded upstream)
    "DAIO_catalog-2.json", "MAPPOEA_mapp-catalog.json", "RESDAL_catalog-2.json",
    "ceeep_catalogo.json", "ceobs_full_catalogo.json", "sipri_full_catalogo.json",
}

# Priority order: prefer an explicit bare filename field, fall back to
# deriving one from whichever URL-shaped field the entry actually has.
FILENAME_FIELDS = ["filename", "url_pdf", "pdf_url", "pdf", "json", "url"]

# Last-resort fallback when the entry names no filename at all (e.g. CEEEP:
# only "url"/"pdf" fields, both numeric CMS ids with no relation to the real
# file on disk). The real filename is the article title, slugified and
# truncated -- a literal substring of the normalized title, just missing the
# source/issue prefix -- so longest-common-substring against the title beats
# guessing that prefix pattern per source family.
TITLE_FIELDS = ["titulo", "title"]
MIN_TITLE_MATCH = 20  # normalized chars; short overlaps are coincidence, not a real hit


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _basename(value):
    """Extract a bare filename from a plain filename or a URL/path string."""
    if not isinstance(value, str) or not value.strip():
        return None
    tail = unquote(urlparse(value).path or value).rsplit("/", 1)[-1]
    return tail if "." in tail else None


def _candidates(entry):
    """(basename, extension) pairs to try, in priority order."""
    out = []
    for field in FILENAME_FIELDS:
        v = entry.get(field)
        values = v if isinstance(v, list) else [v]
        for item in values:
            b = _basename(item)
            if b:
                out.append((b, Path(b).suffix.lower()))
    return out


def build_file_index(root):
    """normalized-stem -> [Path, ...], grouped by extension for safe matching."""
    index = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            index.setdefault(p.suffix.lower(), {}).setdefault(_norm(p.stem), []).append(p)
    return index


def _title_match(entry, root, index):
    title = next((entry[f] for f in TITLE_FIELDS if entry.get(f)), None)
    if not isinstance(title, str):
        return None
    slug = _norm(title)
    best, best_len = None, MIN_TITLE_MATCH - 1
    for stem, paths in index.get(".json", {}).items():
        m = difflib.SequenceMatcher(None, stem, slug).find_longest_match(0, len(stem), 0, len(slug))
        if m.size > best_len:
            best, best_len = paths[0], m.size
    return str(best.relative_to(root)).replace("\\", "/") if best else None


def match_entry(entry, root, index):
    for basename, ext in _candidates(entry):
        by_ext = index.get(ext)
        if not by_ext:
            continue
        key = _norm(Path(basename).stem)
        if len(key) < 6:  # too short to trust a substring match
            continue
        if key in by_ext:
            hit = by_ext[key][0]
        else:
            hit = next((paths[0] for stem, paths in by_ext.items()
                        if key in stem or stem in key), None)
        if hit:
            return str(hit.relative_to(root)).replace("\\", "/")
    return _title_match(entry, root, index)


def process(root, catalog_path, index):
    entries = json.loads(Path(catalog_path).read_text(encoding="utf-8-sig"))
    if isinstance(entries, dict):
        entries = next((v for v in entries.values() if isinstance(v, list)), [])
    matched, unmatched = [], []
    for e in entries:
        if not isinstance(e, dict):
            unmatched.append(e)
            continue
        fuente = match_entry(e, root, index)
        row = {"catalogo": Path(catalog_path).name, "fuente": fuente,
               "metadata_catalogo": e}
        (matched if fuente else unmatched).append(row)
    return matched, unmatched


def run(root, out_dir):
    root = Path(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = build_file_index(root)

    found = sorted(p for p in root.rglob("*.json") if p.name in CATALOG_FILES)
    all_matched, summary = [], []
    for p in found:
        matched, unmatched = process(root, p, index)
        all_matched.extend(matched)
        summary.append((p.name, len(matched) + len(unmatched), len(matched)))

    out_path = out_dir / "catalog_metadata.jsonl"
    out_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_matched),
        encoding="utf-8")

    missing = CATALOG_FILES - {p.name for p in found}
    print(f"catalog files processed: {len(found)}  missing from root: {sorted(missing)}")
    for name, total, n_matched in summary:
        print(f"  {name:35s} {n_matched:4d}/{total:<4d} matched")
    print(f"total matched entries written: {len(all_matched)}  -> {out_path}")


# --- self-check --------------------------------------------------------------------

def _selfcheck(tmp):
    tmp = Path(tmp)
    (tmp / "RESDAL" / "pdfs").mkdir(parents=True)
    (tmp / "RESDAL" / "pdfs" / "RESDAL_01-esp-el-marco-legal.pdf").write_bytes(b"%PDF-1.4 stub")
    (tmp / "RESDAL" / "catalog").mkdir()
    (tmp / "CEEEP" / "articulos").mkdir(parents=True)
    (tmp / "CEEEP" / "articulos" / "CEEEP_issue10-55-las-consecuencias-que-se-derivan.json").write_text(
        json.dumps({"body_text": "x"}), encoding="utf-8")
    (tmp / "CEEEP" / "catalog").mkdir()
    ceeep_catalog = [{"titulo": "Las consecuencias que se derivan de un evento cualquiera",
                       "url": "https://x.test/article/view/95", "pdf": "https://x.test/article/download/95/338"}]
    (tmp / "CEEEP" / "catalog" / "ceeep_catalogo.json").write_text(json.dumps(ceeep_catalog), encoding="utf-8")

    catalog = [
        {"title": "Atlas 2024 ESP - Cap. 1: El Marco Legal", "year": "2024",
         "filename": "01_ESP_El_Marco_Legal.pdf", "url": "https://x.test/01_ESP_El_Marco_Legal.pdf",
         "status": 200},
        {"title": "Dead link entry", "filename": "never-downloaded.pdf", "status": 404},
    ]
    catalog_path = tmp / "RESDAL" / "catalog" / "RESDAL_catalog-2.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    out = tmp / "out"
    run(tmp, out)
    rows = [json.loads(l) for l in (out / "catalog_metadata.jsonl").read_text(encoding="utf-8").splitlines()]
    by_catalog = {r["catalogo"]: r for r in rows}
    assert len(rows) == 2, rows
    resdal = by_catalog["RESDAL_catalog-2.json"]
    assert resdal["fuente"] == "RESDAL/pdfs/RESDAL_01-esp-el-marco-legal.pdf", resdal
    assert resdal["metadata_catalogo"]["year"] == "2024", resdal
    ceeep = by_catalog["ceeep_catalogo.json"]
    assert ceeep["fuente"] == "CEEEP/articulos/CEEEP_issue10-55-las-consecuencias-que-se-derivan.json", ceeep
    print("self-check OK (2 fixtures: filename match, title-fallback match, dead-link non-match)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus_root", nargs="?", help="root to walk (e.g. 'CORPUS CODEFEST AD ASTRA 2026')")
    ap.add_argument("--out", default="catalog", help="output directory (default: catalog/)")
    args = ap.parse_args()

    if not args.corpus_root:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _selfcheck(tmp)
        return

    run(args.corpus_root, args.out)


if __name__ == "__main__":
    main()
