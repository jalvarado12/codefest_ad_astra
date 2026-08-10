import json
import re
from pathlib import Path


class RegistroDocumentos:
    """Asigna y persiste doc_id únicos, compartido entre todos los
    adaptadores para no repetir IDs entre formatos."""

    def __init__(self, registry_path: str = "doc_registry.json"):
        self.registry_path = Path(registry_path)
        if self.registry_path.exists():
            with open(self.registry_path, "r", encoding="utf-8") as f:
                self._mapa = json.load(f)
        else:
            self._mapa = {}
        self._siguiente_num = self._calcular_siguiente_num()

    def _calcular_siguiente_num(self) -> int:
        numeros = [int(v.split("-")[1]) for v in self._mapa.values()]
        return max(numeros, default=0) + 1

    def obtener_o_crear(self, ruta_relativa: str) -> str:
        clave = str(ruta_relativa)
        if clave in self._mapa:
            return self._mapa[clave]
        doc_id = f"DOC-{self._siguiente_num:04d}"
        self._mapa[clave] = doc_id
        self._siguiente_num += 1
        return doc_id

    def guardar(self):
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(self._mapa, f, ensure_ascii=False, indent=2)


FENOMENO_PATTERNS = [
    re.compile(r"fen[oó]meno[\s_-]*([123])", re.IGNORECASE),
    re.compile(r"^f([123])[_\s-]", re.IGNORECASE),
]


def inferir_fenomeno(ruta_relativa: Path) -> int | None:
    """Recorre cada segmento de la ruta buscando el patrón de fenómeno.
    Devuelve 1, 2 o 3, o None si ningún segmento coincide."""
    for segmento in ruta_relativa.parts:
        for patron in FENOMENO_PATTERNS:
            coincidencia = patron.search(segmento)
            if coincidencia:
                return int(coincidencia.group(1))
    return None
