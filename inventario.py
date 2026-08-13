"""
Step 3 — Inventory reconciliation.

`Indice_Datos_Codefest.xlsx`, sheet "Inventario de Archivos", is ADL's own list
of the corpus: 1,826 rows, one per file. Its `Carpeta` + `Nombre estandarizado`
pair reconstructs exactly the relative POSIX path this pipeline uses as `fuente`,
which makes it both the reconciliation key and the source of `adl_doc_id`.

Two uses:

    python inventario.py <corpus_dir>      reconcile a corpus tree against it
    from inventario import cargar_inventario   fuente -> row, for extraction

Reconciliation is not cosmetic. A file on disk with no inventory row is either
an admin file that must be excluded or a path bug; an inventory row with no file
is a document the graders expect and this pipeline will never return.
"""
import sys
import unicodedata
from pathlib import Path

HOJA = "Inventario de Archivos"
INVENTARIO = "Indice_Datos_Codefest.xlsx"

# Column names carry accents; match them tolerantly so an encoding wobble in the
# spreadsheet does not silently produce an empty reconciliation.
_COLUMNAS = {
    "fenomeno": ("fenomeno",),
    "observatorio": ("observatorio",),
    "codigo": ("codigoobservatorio",),
    "adl_doc_id": ("docid",),
    "nombre": ("nombreestandarizado",),
    "carpeta": ("carpeta",),
    "tipo": ("tipo",),
}


def _norm_col(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _mapear_columnas(columnas):
    por_norm = {_norm_col(c): c for c in columnas}
    mapa = {}

    for destino, candidatos in _COLUMNAS.items():
        for cand in candidatos:
            if cand in por_norm:
                mapa[destino] = por_norm[cand]
                break

    faltan = set(_COLUMNAS) - set(mapa)
    if faltan:
        raise ValueError(f"inventory is missing columns for {sorted(faltan)}; "
                         f"found {list(columnas)}")
    return mapa


def cargar_inventario(path=INVENTARIO):
    """fuente (relative POSIX path) -> inventory row, as plain dicts."""
    import pandas as pd

    df = pd.read_excel(path, sheet_name=HOJA, dtype=str).fillna("")
    col = _mapear_columnas(df.columns)

    filas = {}
    for _, fila in df.iterrows():
        carpeta = fila[col["carpeta"]].strip().replace("\\", "/").rstrip("/")
        nombre = fila[col["nombre"]].strip()

        if not nombre:
            continue

        fuente = f"{carpeta}/{nombre}" if carpeta else nombre
        filas[fuente] = {destino: fila[origen].strip()
                         for destino, origen in col.items()}

    return filas


def archivos_del_corpus(corpus_dir):
    """Relative POSIX paths of every file under corpus_dir."""
    raiz = Path(corpus_dir)
    return {
        str(p.relative_to(raiz)).replace("\\", "/")
        for p in raiz.rglob("*") if p.is_file()
    }


def reconciliar(corpus_dir, path=INVENTARIO):
    """(coincidencias, filas_sin_archivo, archivos_sin_fila)."""
    inventario = cargar_inventario(path)
    archivos = archivos_del_corpus(corpus_dir)

    coinciden = archivos & set(inventario)

    return (
        {f: inventario[f] for f in sorted(coinciden)},
        {f: r for f, r in sorted(inventario.items()) if f not in archivos},
        sorted(archivos - set(inventario)),
    )


def _informe(corpus_dir, path=INVENTARIO):
    from collections import Counter

    inventario = cargar_inventario(path)
    archivos = archivos_del_corpus(corpus_dir)
    coinciden, sin_archivo, sin_fila = reconciliar(corpus_dir, path)

    print(f"filas de inventario : {len(inventario)}")
    print(f"archivos en disco   : {len(archivos)}")
    print(f"coinciden           : {len(coinciden)}")
    print(f"filas sin archivo   : {len(sin_archivo)}")
    print(f"archivos sin fila   : {len(sin_fila)}")

    # The join key must be unique or the whole reconciliation is meaningless.
    print(f"\nclave unica         : {len(inventario)} rutas distintas")
    nombres = Counter(r["nombre"] for r in inventario.values())
    colisiones = {n: c for n, c in nombres.items() if c > 1}
    print(f"basenames repetidos : {sum(colisiones.values())} filas en "
          f"{len(colisiones)} nombres -- por eso fuente es la ruta, no el basename")

    if sin_fila:
        print("\narchivos sin fila de inventario (primeros 15):")
        for f in sin_fila[:15]:
            print(f"   {f}")

    if sin_archivo and len(archivos) >= len(inventario) * 0.9:
        # Only interesting when the tree is supposed to be complete; on a sample
        # corpus almost every row is legitimately missing.
        print("\nfilas de inventario sin archivo (primeras 15):")
        for f in list(sin_archivo)[:15]:
            print(f"   {f}")

    por_tipo = Counter(r["tipo"] for r in coinciden.values())
    print(f"\ncoincidencias por tipo: {dict(por_tipo)}")

    return sin_fila


def _selfcheck():
    """Runs against the real inventory; needs only the spreadsheet."""
    inventario = cargar_inventario()

    assert len(inventario) > 1000, len(inventario)

    ejemplo = next(iter(inventario))
    assert "/" in ejemplo and "\\" not in ejemplo, ejemplo

    fila = inventario[ejemplo]
    assert fila["adl_doc_id"], fila
    assert ejemplo.endswith(fila["nombre"]), (ejemplo, fila["nombre"])

    print(f"OK  {len(inventario)} filas, clave ejemplo {ejemplo!r} -> "
          f"{fila['adl_doc_id']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selfcheck()
    elif len(sys.argv) > 1:
        _informe(sys.argv[1], *(sys.argv[2:3] or [INVENTARIO]))
    else:
        print(__doc__)
        sys.exit(2)
