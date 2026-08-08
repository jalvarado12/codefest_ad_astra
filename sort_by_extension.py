"""Copy corpus files into per-extension dirs, for extractor testing (no metadata)."""
import shutil
import sys
from pathlib import Path

CORPUS = Path("CORPUS CODEFEST AD ASTRA 2026")
OUT = Path("corpus_by_type")
SKIP = {".ds_store"}


def main():
    if not CORPUS.exists():
        sys.exit(f"missing: {CORPUS}")
    n = 0
    for src in CORPUS.rglob("*"):
        if not src.is_file():
            continue
        ext = src.suffix.lower().lstrip(".") or "noext"
        if f".{ext}" in SKIP:
            continue
        dest_dir = OUT / ext
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}__{n}{src.suffix}"
        shutil.copy2(src, dest)
        n += 1
    print(f"copied {n} files into {OUT}/<ext>/")


if __name__ == "__main__":
    main()
