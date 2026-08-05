from pypdf import PdfReader
from docx import Document as DocxDocument

# ── Loaders ───────────────────────────────────────────────────────────────────

def load_pdf(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def load_docx(file_path: Path) -> str:
    doc = DocxDocument(str(file_path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def load_document(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(file_path)
    if suffix in (".docx", ".doc"):
        return load_docx(file_path)
    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8")
    raise ValueError(f"Unsupported file type: {suffix}")



# ── FAISS index ───────────────────────────────────────────────────────────────

def build_faiss_index(embeddings: np.ndarray) -> faiss.IndexFlatL2:
    index = faiss.IndexFlatL2(embeddings.shape[1])
    index.add(embeddings)
    return index

#CAMBIAR PICKLE
def save_index(
    index: faiss.IndexFlatL2,
    metadata: List[Dict],
    index_path: Path = INDEX_PATH,
    metadata_path: Path = METADATA_PATH,
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))
    with open(metadata_path, "wb") as f:
        pickle.dump(metadata, f)


#CAMBIAR PICKLE
def load_index(
    index_path: Path = INDEX_PATH,
    metadata_path: Path = METADATA_PATH,
) -> Tuple[faiss.IndexFlatL2, List[Dict]]:
    index = faiss.read_index(str(index_path))
    with open(metadata_path, "rb") as f:
        metadata = pickle.load(f)
    return index, metadata


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    words = text.split()
    chunks: List[str] = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start : start + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

