"""Quién hace LabelBridge y sus documentos: términos de uso, política de privacidad, aviso del asistente de IA y
licencias de terceros.

Los textos viven en assets/legal/<documento>.<idioma>.md y son los mismos en la app (Acerca de y las ventanas para
aceptarlos), en el instalador de Windows (packaging/make_legal.py los pasa a RTF) y en la página de descargas.
Cuando cambia lo que hay que aceptar, sube su versión aquí: la app lo vuelve a pedir una vez.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .i18n import language
from .paths import resource_path

DEVELOPER = "LabelBridge"
CONTACT_EMAIL = "tonymonzon94@gmail.com"
WEBSITE = "https://morghullis.github.io/labelbridge-updates/"
COPYRIGHT = "© 2026 LabelBridge"
TERMS_VERSION = 1  # términos de uso y política de privacidad (se aceptan juntos)
AI_NOTICE_VERSION = 1  # lo que el asistente envía al proveedor de IA

TERMS, PRIVACY, AI_NOTICE, THIRD_PARTY = "terms", "privacy", "ai", "third-party"


def document_path(name: str, lang: str | None = None) -> Path:
    """El archivo del documento en el idioma pedido (o el de la app); si no existe en ese idioma, el español."""
    folder = resource_path("assets", "legal")
    if name == THIRD_PARTY:  # bilingüe, un solo archivo
        return folder / f"{name}.md"
    path = folder / f"{name}.{lang or language()}.md"
    return path if path.exists() else folder / f"{name}.es.md"


def document(name: str, lang: str | None = None) -> str:
    """El texto (Markdown) del documento."""
    return document_path(name, lang).read_text(encoding="utf-8")


def system_info() -> str:
    """Lo que soporte necesita para ayudar (sin datos de la tienda ni claves): para copiar desde Acerca de."""
    try:
        from PySide6 import __version__ as pyside_version
    except ImportError:  # sin interfaz (pruebas de consola)
        pyside_version = "—"
    system = platform.platform(terse=True)
    if sys.platform == "darwin":
        system = f"macOS {platform.mac_ver()[0]}"
    return "\n".join((
        f"{APP_NAME} {__version__}",
        f"{system} · {platform.machine()}",
        f"Python {platform.python_version()} · Qt for Python {pyside_version}",
        f"{language()}",
    ))
