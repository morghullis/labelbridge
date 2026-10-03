"""Rutas de datos de la app (configuración, base de datos, registros, cola)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_ID, APP_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    override = os.environ.get("LABELBRIDGE_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / APP_ID.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    return _sub("logs")


def spool_dir() -> Path:
    return _sub("spool")


def output_dir() -> Path:
    return _sub("salida")


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "jobs.db"


def resource_path(*parts: str) -> Path:
    """Archivos empaquetados (assets, plantillas n8n, docs)."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)
