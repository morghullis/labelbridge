"""En qué sistema corre la app: los textos y las funciones que cambian entre Windows y Mac.

Los textos van en pares marcados para traducir: pick(N_("…Windows…"), N_("…Mac…")) y después tr() donde se muestran.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TypeVar

from ..paths import is_frozen

T = TypeVar("T")

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"


def pick(windows: T, mac: T) -> T:
    """El texto de este sistema (fuera de Windows y Mac, el de Windows)."""
    return mac if MAC else windows


def mac_app_bundle() -> Path | None:
    """La carpeta LabelBridge.app de la app instalada en la Mac (el ejecutable está en Contents/MacOS)."""
    if not (MAC and is_frozen()):
        return None
    return Path(sys.executable).resolve().parents[2]


def mac_opened_from_disk_image(bundle: Path | None = None) -> bool:
    """La app se abrió desde el .dmg (o desde la copia aislada que macOS hace de una app sin mover): aún no está en
    Aplicaciones, así que ni el inicio automático ni las actualizaciones funcionarían."""
    bundle = bundle or mac_app_bundle()
    if bundle is None:
        return False
    text = str(bundle)
    return "/AppTranslocation/" in text or (text.startswith("/Volumes/") and not os.access(bundle, os.W_OK))
