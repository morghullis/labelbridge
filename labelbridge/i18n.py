"""Idioma de la interfaz: español o inglés.

El texto del código está en español. En inglés, tr() lo busca en assets/locale/en.json (el texto en español es la
clave) y, si no está, lo deja en español. Los huecos van numerados: tr("Se guardaron {0} pedidos", n).

Las constantes con texto (columnas, estados, tamaños…) se guardan en español y se traducen donde se muestran, así
el orden en que se importan los módulos no importa. El idioma se elige al arrancar: el de Ajustes, si no el que se
eligió en el instalador y si no el de Windows. Cambiarlo pide reiniciar la app.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import sys

log = logging.getLogger(__name__)

LANGUAGES = {"es": "Español", "en": "English"}
DEFAULT = "es"

_language = DEFAULT
_catalog: dict[str, str] = {}


def tr(text: str, *args, **kwargs) -> str:
    """El texto en el idioma de la app, con sus huecos rellenos ({0}, {1}… o por nombre)."""
    out = _catalog.get(text, text) if _catalog else text
    if not args and not kwargs:
        return out
    try:
        return out.format(*args, **kwargs)
    except (IndexError, KeyError, ValueError):
        return text.format(*args, **kwargs)  # traducción con huecos mal escritos: mejor el original


def language() -> str:
    return _language


def set_language(code: str) -> str:
    """Activa un idioma (los desconocidos quedan en español). Devuelve el que quedó."""
    global _language, _catalog
    code = code if code in LANGUAGES else DEFAULT
    _catalog = load_catalog(code) if code != DEFAULT else {}
    _language = code
    return code


def load_catalog(code: str) -> dict[str, str]:
    from .paths import resource_path

    path = resource_path("assets", "locale", f"{code}.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("No se pudo leer la traducción %s", path)
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def system_language() -> str:
    """El idioma de Windows (o del sistema): español si lo es, si no inglés."""
    code = ""
    if sys.platform == "win32":
        try:
            import ctypes

            code = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        except Exception:
            code = ""
    if not code:
        for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
            if os.environ.get(name):
                code = os.environ[name]
                break
    if not code:
        try:
            code = locale.getlocale()[0] or ""
        except ValueError:
            code = ""
    return "es" if code.lower().startswith("es") else "en"


def choose(configured: str = "", installer: str = "") -> str:
    """Ajustes > instalador > sistema."""
    for code in (configured, installer):
        if code in LANGUAGES:
            return code
    return system_language()
