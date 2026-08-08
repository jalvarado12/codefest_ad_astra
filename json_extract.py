"""Deterministic tiered extraction of body text + metadata from corpus JSON files.

Spec section 2.1 says to interpret the object, select explicitly the fields holding each
article's text, concatenate them in order of appearance, join paragraph lists preserving
that order, and keep descriptive fields (url, date, authors, tags) as document metadata
rather than mixing them into the body. It names field names only "por ejemplo", so the
real corpus needs adaptive handling.

Section 1.4 requires generador.py to reproduce results exactly, so every stage here is a
pure function of the file's bytes: dicts are iterated in insertion order, the pattern set
is a frozen constant, and there is no model, network, clock or filesystem-order dependence.

Three outcomes, always recorded, never silent:

    tier 1   alias harvest matched known text-bearing fields
    tier 2   filtered fallback recovered text the alias table missed
    tier 3   JsonSinTexto -- parsed fine, but holds no usable prose
             JsonIlegible -- could not be parsed at all

The `traza` returned alongside the text records which tier fired and which key paths
emitted, so a corpus run doubles as its own schema survey (--resumen).
"""

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

# Same floor corpus.extract() rejects a document on (corpus.py:174). Shared rather than
# retyped so the fallback trigger and the reject criterion cannot drift apart.
MIN_WORDS = 30

# A string shorter than this under an unrecognised key is a slug, a label or a tag, not
# prose. Long enough to drop "Space Security" and keep a real sentence or article title.
MIN_STR = 40
MIN_STR_TIER2 = 15  # tier 2 is already a rescue path; let shorter titles through

TRUNC_META = 300  # metadata values are for filtering, not reading; keep them bounded


class JsonIlegible(Exception):
    """The file could not be parsed as JSON or as NDJSON."""

    def __init__(self, path, detalle):
        super().__init__(f"{path}: {detalle}")
        self.path, self.detalle = str(path), detalle


class JsonSinTexto(Exception):
    """The file parsed, but yielded no body text above the floor."""

    def __init__(self, path, traza):
        super().__init__(f"{path}: no usable text (tier 3)")
        self.path, self.traza = str(path), traza


# --- field roles -------------------------------------------------------------------
# Every name below was observed in the real ADL corpus census (964 files, 15 shapes) or
# is named by spec section 2.1 itself. Nothing here is speculative.

TITLE = {"title", "titulo", "headline", "heading", "subject", "nombre", "encabezado",
         "subtitle", "subtitulo"}

# Whole-body blobs: one string holding the entire article.
BODY_BLOB = {"body_text", "bodytext", "body", "content", "contenido", "texto", "text",
             "full_text", "fulltext", "article_body", "articlebody"}

# Paragraph lists: the same body, already split on its real paragraph boundaries.
BODY_PARA = {"body_paragraphs", "paragraphs", "parrafos", "parragrafos"}

# Standalone prose that is not a duplicate of the body.
BODY_OTHER = {"abstract", "resumen", "excerpt", "summary", "sumario", "description",
              "descripcion", "lead", "lede", "extract", "tema_clave"}

BODY = BODY_BLOB | BODY_PARA | BODY_OTHER

META = {"url", "link", "permalink", "canonical_url", "source_url", "pdf_url", "page_url",
        "date", "fecha", "fecha_emision", "published", "published_at", "pubdate",
        "datepublished", "year", "año", "scraped_at",
        "author", "authors", "autor", "autores", "byline", "editors",
        "tags", "keywords", "topics", "categories", "categoria", "category", "section",
        "source", "fuente", "language", "languages", "idioma", "lang",
        "id", "doc_id", "codigo", "doi", "issue", "tipo", "municipios", "country",
        "fenomeno", "edition", "filename", "status"}

# Link and asset containers. Observed in the census as pure URL/anchor/alt-text carriers;
# recursing into them drags navigation chrome and file paths into the body. Never
# traversed at tier 1, traversed only as a last resort at tier 2.
NOISE = {"images", "links", "pdf_links", "doc_links", "external_links", "internal_links",
         "science_links", "all_links", "additional_links", "pdfs", "urls", "hashes",
         "articulos", "fields", "lists_links", "detail_url", "image_preview",
         "local_path", "dest", "path", "file", "json", "src", "href"}

# Values that are identifiers or machinery, never prose.
RE_JUNK = re.compile(
    r"^(?:https?://\S+"                        # urls
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\S*)?"          # ISO dates / timestamps
    r"|[\d\s.,%+-]+"                           # pure numerics
    r"|[0-9a-f]{16,}"                          # hashes
    r"|[0-9a-f]{8}-[0-9a-f]{4}-\S+"            # uuids
    r"|\w+/[\w.+-]+"                           # mime types, relative paths
    r"|\S+\.(?:pdf|json|html?|jpe?g|png|pbf|csv|xlsx)"  # bare filenames
    r")$", re.I)


def _norm(key):
    """Lowercase and strip accents so 'Título' and 'titulo' are the same key."""
    k = unicodedata.normalize("NFKD", str(key)).encode("ascii", "ignore").decode()
    return k.strip().lower()


def _scalar(v):
    return isinstance(v, (str, int, float, bool))


# CJK scripts write without spaces, so str.split() undercounts them by roughly the length
# of the text. Counting ideographs individually keeps the floor meaningful for a Chinese
# or Japanese document instead of rejecting a full page as "8 words".
RE_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def _wc(text):
    """Word count that does not assume whitespace-delimited script."""
    return len(text.split()) + len(RE_CJK.findall(text))


# --- stage L: load -----------------------------------------------------------------

def _load(path):
    """Parse as a single JSON value, else as NDJSON. Raise JsonIlegible if neither."""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    try:
        return json.loads(raw), "json"
    except json.JSONDecodeError as first:
        lines = [l for l in raw.splitlines() if l.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(l) for l in lines], "ndjson"
            except json.JSONDecodeError:
                pass
        raise JsonIlegible(path, f"{first.msg} (line {first.lineno} col {first.colno})")


# --- stage 1: alias harvest --------------------------------------------------------

def _harvest(obj, out, meta, traza, prefix="", tier2=False, role=None):
    """Walk in insertion order, emitting (role, key_path, text) into `out`.

    `role` carries the body context down through unnamed containers, so a paragraph list
    of objects -- body_paragraphs: [{"text": ...}] -- is still recognised as body.
    """
    min_str = MIN_STR_TIER2 if tier2 else MIN_STR

    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = _norm(k)
            path = f"{prefix}.{k}" if prefix else str(k)

            if nk in META and (_scalar(v) or (isinstance(v, list) and all(_scalar(x) for x in v))):
                if nk not in meta:
                    val = ", ".join(str(x) for x in v) if isinstance(v, list) else v
                    meta[nk] = str(val)[:TRUNC_META] if isinstance(val, str) else val
                    traza["claves_meta"].append(path)
                continue

            if nk in NOISE and not tier2:
                continue

            if nk in BODY or nk in TITLE:
                sub = "para" if nk in BODY_PARA else "blob" if nk in BODY_BLOB else "other"
                _harvest(v, out, meta, traza, path, tier2, role=sub)
            else:
                _harvest(v, out, meta, traza, path, tier2, role=None)

    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            _harvest(x, out, meta, traza, f"{prefix}[{i}]", tier2, role=role)

    elif isinstance(obj, str):
        s = obj.strip()
        if not s or RE_JUNK.match(s):
            return
        # Inside a recognised text field, take the string whatever its length. Outside
        # one, only strings long enough to be prose rather than a label.
        if role is None and len(s) < min_str:
            return
        out.append((role or "libre", prefix, s))


def _assemble(out):
    """Drop blob bodies duplicated by paragraph lists, then dedupe exactly, keeping order.

    In the real corpus 485 of 848 article files carry both `body_text` and
    `body_paragraphs`, where the former is the latter joined -- emitting both would double
    every one of those documents. The paragraph list wins: section 2.1 asks for paragraph
    order to be preserved, and the list is where the real boundaries are.
    """
    has_para = any(r == "para" for r, _, _ in out)
    seen, kept, rutas = set(), [], []
    for role, path, s in out:
        if has_para and role == "blob":
            continue
        if s in seen:
            continue
        seen.add(s)
        kept.append(s)
        rutas.append(path)
    return "\n\n".join(kept), rutas


# --- public API --------------------------------------------------------------------

def extract_json(path):
    """Return (texto, meta, traza). Raise JsonIlegible or JsonSinTexto -- never silence."""
    data, carga = _load(path)
    traza = {"archivo": Path(path).name, "tier": 1, "carga": carga,
             "claves_texto": [], "claves_meta": [], "claves_raiz": [], "advertencias": []}
    traza["claves_raiz"] = (sorted(data.keys()) if isinstance(data, dict)
                            else ["<lista-nivel-raiz>"])

    out, meta = [], {}
    _harvest(data, out, meta, traza)
    texto, traza["claves_texto"] = _assemble(out)

    if _wc(texto) < MIN_WORDS:
        traza["tier"] = 2
        out, meta2 = [], {}
        _harvest(data, out, meta2, traza, tier2=True)
        for k, v in meta2.items():
            meta.setdefault(k, v)
        texto, traza["claves_texto"] = _assemble(out)
        traza["advertencias"].append("tier1 bajo el piso; recuperado por barrido filtrado")

    if _wc(texto) < MIN_WORDS:
        traza["tier"] = 3
        raise JsonSinTexto(path, traza)

    traza["n_palabras"] = _wc(texto)
    return texto, meta, traza


# --- census ------------------------------------------------------------------------

def resumen(log_path):
    """Print the schema census from a provenance log written by a corpus run."""
    rows = [json.loads(l) for l in Path(log_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    tiers = collections.Counter(r["tier"] for r in rows)
    print(f"documentos: {len(rows)}")
    for t in sorted(tiers):
        print(f"  tier {t}: {tiers[t]}")
    print(f"  carga ndjson: {sum(1 for r in rows if r.get('carga') == 'ndjson')}")

    paths = collections.Counter()
    for r in rows:
        for p in r["claves_texto"]:
            paths[re.sub(r"\[\d+\]", "[]", p)] += 1
    print("\n== rutas de texto mas comunes ==")
    for p, c in paths.most_common(25):
        print(f"{c:5d}  {p}")

    metas = collections.Counter(m.split(".")[-1] for r in rows for m in r["claves_meta"])
    print("\n== claves de metadata vistas ==")
    for m, c in metas.most_common(30):
        print(f"{c:5d}  {m}")

    print("\n== tier 2 (tabla de alias insuficiente) ==")
    for r in rows:
        if r["tier"] == 2:
            print(f"  {r['archivo']:60s} {r['claves_raiz']}")

    print("\n== tier 3 (sin texto -- decidir uno por uno) ==")
    for r in rows:
        if r["tier"] == 3:
            print(f"  {r['archivo']:60s} {r['claves_raiz']}")


# --- self-check --------------------------------------------------------------------

def _selfcheck(tmp):
    import tempfile

    def run(name, obj, raw=None):
        p = Path(tmp) / name
        if raw is not None:
            p.write_bytes(raw)
        else:
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return extract_json(p)

    P = "Esta es una oracion de relleno con suficientes palabras para superar el piso de treinta palabras que exige el extractor y asi poder comprobar el comportamiento real del arbol de decisiones sin recurrir al nivel dos."
    Q = "Segundo parrafo distinto del primero que tambien aporta bastantes palabras al conteo total del documento para que el resultado quede comodamente por encima del umbral fijado."

    # 1. flat title + body_text
    t, m, z = run("a.json", {"title": "Titulo A", "body_text": P})
    assert z["tier"] == 1 and "Titulo A" in t and P in t, t

    # 2. headline/content/author -- author is metadata, never body
    t, m, z = run("b.json", {"headline": "Titulo B", "content": P, "author": "Ada Lovelace"})
    assert "Ada Lovelace" not in t and m["author"] == "Ada Lovelace", (t, m)

    # 3. nested article.title + article.paragraphs, order preserved
    t, m, z = run("c.json", {"article": {"title": "Titulo C", "paragraphs": [P, Q]}})
    assert t.index(P) < t.index(Q) and "Titulo C" in t, t

    # 4. body key whose value is a dict -- must recurse, not drop (defect 1)
    t, m, z = run("d.json", {"content": {"blocks": [P, Q]}})
    assert P in t and Q in t, t

    # 5. body key whose value is a list of objects (defect 2)
    t, m, z = run("e.json", {"body_paragraphs": [{"text": P}, {"text": Q}]})
    assert P in t and Q in t, t

    # 6. BOM-prefixed file must load (defect 4)
    t, m, z = run("f.json", None, raw=b"\xef\xbb\xbf" + json.dumps({"body_text": P}).encode())
    assert P in t, t

    # 7. NDJSON must load (defect 5)
    raw = (json.dumps({"body_text": P}) + "\n" + json.dumps({"body_text": Q})).encode()
    t, m, z = run("g.json", None, raw=raw)
    assert z["carga"] == "ndjson" and P in t and Q in t, (z, t)

    # 8. top-level list of articles -- one document, file order
    t, m, z = run("h.json", [{"title": "T1", "body_text": P}, {"title": "T2", "body_text": Q}])
    assert t.index(P) < t.index(Q), t

    # 9. title present, body missing -> tier 2 rescues (defect 3)
    t, m, z = run("i.json", {"title": "Solo titulo", "notas": {"raro": P}})
    assert z["tier"] == 1 and P in t, (z, t)  # unknown long string caught already at tier 1
    t, m, z = run("i2.json", {"title": "Solo titulo corto", "images": [{"alt": P}]})
    assert z["tier"] == 2 and P in t, (z, t)  # only reachable by entering a noise container

    # 10. metadata only -> explicit tier 3
    try:
        run("j.json", {"url": "https://x.test/a", "date": "2026-01-01", "tags": ["a", "b"]})
        raise AssertionError("expected JsonSinTexto")
    except JsonSinTexto as e:
        assert e.traza["tier"] == 3

    # 11. invalid JSON -> explicit JsonIlegible
    try:
        run("k.json", None, raw=b'{"title": "trunc')
        raise AssertionError("expected JsonIlegible")
    except JsonIlegible:
        pass

    # 12. body_text duplicating body_paragraphs must not double the document
    t, m, z = run("l.json", {"body_paragraphs": [P, Q], "body_text": P + "\n" + Q})
    assert t.count(P) == 1 and t.count(Q) == 1, t

    # 13. exact duplicate strings collapse even when far apart (ALERTAS tema_clave)
    t, m, z = run("m.json", {"body_paragraphs": [P, Q], "alerta_meta": {"tema_clave": P}})
    assert t.count(P) == 1, t

    # 14. no url or date leaks into body on any path (section 2.1)
    t, m, z = run("n.json", {"url": "https://x.test/very/long/path/that/exceeds/forty/chars",
                             "date": "2026-01-01", "body_text": P,
                             "pdf_links": ["https://x.test/another/long/url/file.pdf"]})
    assert "https://" not in t and "2026-01-01" not in t, t

    # 15. identical input twice -> identical output (section 1.4)
    a = run("o.json", {"title": "R", "body_paragraphs": [P, Q]})
    b = run("o.json", {"title": "R", "body_paragraphs": [P, Q]})
    assert a[0] == b[0] and a[1] == b[1], "not reproducible"

    # 16. CJK prose has no spaces; the floor must not reject a full page as "8 words"
    zh = "外空正在迅速变化。每年，越来越多、更加多元化的参与者在外空开展新颖、创新性和颠覆性的活动。他们加入了目前已在地球轨道上运行着超过1,500颗卫星的70多个国家、商业公司和国际组织。"
    t, m, z = run("p.json", {"title": "手册", "body_paragraphs": [zh]})
    assert z["tier"] == 1 and zh in t, (z, t)

    # 17. the trace must list the paths actually kept, not the ones harvested then dropped
    t, m, z = run("q.json", {"body_paragraphs": [P, Q], "body_text": P + "\n" + Q})
    assert "body_text" not in z["claves_texto"], z["claves_texto"]

    print("self-check OK (17 fixtures)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--resumen", metavar="LOG", help="print the schema census from a provenance log")
    ap.add_argument("--run", metavar="DIR", help="extract every .json under DIR, writing a provenance log")
    ap.add_argument("--log", default="arch_test/data/extraccion_json.jsonl")
    args = ap.parse_args()

    if args.resumen:
        resumen(args.resumen)
        return
    if args.run:
        files = sorted(Path(args.run).rglob("*.json"))
        log = Path(args.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        n_ok = 0
        with log.open("w", encoding="utf-8") as fh:
            for p in files:
                try:
                    texto, meta, traza = extract_json(p)
                    traza["n_meta"] = len(meta)
                    n_ok += 1
                except JsonSinTexto as e:
                    traza = e.traza
                except JsonIlegible as e:
                    traza = {"archivo": p.name, "tier": 0, "carga": "error",
                             "claves_texto": [], "claves_meta": [],
                             "claves_raiz": [], "advertencias": [e.detalle]}
                fh.write(json.dumps(traza, ensure_ascii=False) + "\n")
        print(f"archivos: {len(files)}  con texto: {n_ok}  log: {log}")
        return

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _selfcheck(tmp)


if __name__ == "__main__":
    main()
