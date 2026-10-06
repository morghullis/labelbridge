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


_audit: set[str] | None = None  # pruebas: lo que devolvió tr(), para ver qué texto visible no pasó por tr()


def tr(text: str, *args, **kwargs) -> str:
    """El texto en el idioma de la app, con sus huecos rellenos ({0}, {1}… o por nombre)."""
    out = _catalog.get(text, text) if _catalog else text
    if args or kwargs:
        try:
            out = out.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            out = text.format(*args, **kwargs)  # traducción con huecos mal escritos: mejor el original
    if _audit is not None:
        _audit.add(out)
    return out


def set_audit(on: bool) -> set[str] | None:
    """Empieza (o termina) a recordar lo que devuelve tr(). Devuelve el conjunto que se va llenando."""
    global _audit
    _audit = set() if on else None
    return _audit


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
    if not code and sys.platform == "darwin":
        try:
            from PySide6.QtCore import QLocale

            languages = QLocale.system().uiLanguages()
            code = languages[0] if languages else ""
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


def date_text(when, *, year: bool = True, clock: str = "%H:%M") -> str:
    """Fecha corta como se escribe en el idioma de la app: 04/10/2026 13:05 (día/mes) o 10/04/2026 13:05 (mes/día).

    `when` es un datetime o una marca de tiempo; `clock=""` deja solo la fecha.
    """
    from datetime import datetime

    if not isinstance(when, datetime):
        when = datetime.fromtimestamp(float(when))
    pattern = ("%m/%d" if _language == "en" else "%d/%m") + ("/%Y" if year else "")
    return when.strftime(f"{pattern} {clock}" if clock else pattern)


def decimal(value: float, digits: int = 1) -> str:
    """Número con decimales: con coma en español (35,2) y con punto en inglés (35.2)."""
    text = f"{value:.{digits}f}"
    return text if _language == "en" else text.replace(".", ",")


def N_(text: str) -> str:  # noqa: N802
    """Marca un texto fijo (constante de módulo) para traducirlo donde se muestra con tr(): aquí no lo cambia."""
    return text


WEEKDAYS = (N_("lunes"), N_("martes"), N_("miércoles"), N_("jueves"), N_("viernes"), N_("sábado"), N_("domingo"))
MONTHS = (N_("enero"), N_("febrero"), N_("marzo"), N_("abril"), N_("mayo"), N_("junio"), N_("julio"), N_("agosto"),
          N_("septiembre"), N_("octubre"), N_("noviembre"), N_("diciembre"))


def long_date(when=None) -> str:
    """«domingo 5 de octubre» / «Sunday, October 5»."""
    from datetime import datetime

    if when is None:
        when = datetime.now()
    elif not isinstance(when, datetime):
        when = datetime.fromtimestamp(float(when))
    return tr("{0} {1} de {2}", tr(WEEKDAYS[when.weekday()]), when.day, tr(MONTHS[when.month - 1]))
