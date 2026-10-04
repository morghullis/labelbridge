"""Preferencias de accesibilidad del sistema."""

from __future__ import annotations

import os
import sys

SPI_GETCLIENTAREAANIMATION = 0x1042


def reduced_motion() -> bool:
    """¿El usuario pidió menos animaciones? (Windows: Configuración → Accesibilidad → Efectos de animación)."""
    if os.environ.get("LABELBRIDGE_REDUCED_MOTION") == "1":
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        return bool(ok) and not enabled.value
    except Exception:
        return False
