"""Textos para traducir: el primer argumento de cada tr("…") y N_("…") del código (en orden de aparición).

Uso: python devtools/i18n_extract.py            → lista los que aún no están en assets/locale/en.json
     python devtools/i18n_extract.py --json     → todos, como JSON (para revisar o completar la traducción)
Las pruebas (tests/test_i18n.py) comprueban que todos estén traducidos y con los mismos huecos.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def keys() -> dict[str, str]:
    """{texto: "archivo:línea"} de todos los tr()/N_() con texto literal."""
    found: dict[str, str] = {}
    for path in sorted((ROOT / "labelbridge").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            if name not in ("tr", "N_"):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.strip():
                found.setdefault(first.value, f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def catalog(code: str = "en") -> dict[str, str]:
    path = ROOT / "assets" / "locale" / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


if __name__ == "__main__":
    all_keys = keys()
    if "--json" in sys.argv:
        print(json.dumps(list(all_keys), ensure_ascii=False, indent=1))
    else:
        done = catalog()
        missing = [k for k in all_keys if k not in done]
        for key in missing:
            print(f"{all_keys[key]}: {key[:120]!r}")
        print(f"{len(all_keys)} textos · {len(missing)} sin traducir")
