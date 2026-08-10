import re
import unicodedata
from collections import Counter

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WEIRD_SPACES_RE = re.compile(r"[ ​‌‍﻿]")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def remove_control_chars(text: str) -> str:
    text = _WEIRD_SPACES_RE.sub(" ", text)
    return _CONTROL_CHARS_RE.sub("", text)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def collapse_whitespace(text: str) -> str:
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def remove_repeated_lines(text: str, min_repeats: int = 3, max_len: int = 80) -> str:
    """Elimina boilerplate: líneas cortas que se repiten (cabeceras, pies de
    página, menús de navegación). No toca líneas largas ni únicas."""
    lines = text.split("\n")
    counts = Counter(line.strip() for line in lines if line.strip())
    repeated = {
        line for line, n in counts.items()
        if n >= min_repeats and len(line) <= max_len
    }
    cleaned = [line for line in lines if line.strip() not in repeated]
    return "\n".join(cleaned)


def clean_text(raw_text: str) -> str:
    """Limpieza estándar compartida por todos los adaptadores de extracción."""
    if not raw_text:
        return ""
    text = remove_control_chars(raw_text)
    text = normalize_unicode(text)
    text = remove_repeated_lines(text)
    text = collapse_whitespace(text)
    return text


def fila_a_texto(fila: dict, separador: str = " | ") -> str:
    """Convierte una fila de CSV/Excel en 'columna: valor | ...', omitiendo
    celdas vacías."""
    pares = [
        f"{columna}: {valor}"
        for columna, valor in fila.items()
        if valor is not None and str(valor).strip() != "" and str(valor).lower() != "nan"
    ]
    return separador.join(pares)
