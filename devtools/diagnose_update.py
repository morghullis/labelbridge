"""Prueba la actualización automática en Windows (la usan los workflows diagnose-update y update-e2e).

Hace lo mismo que la app al pulsar «Actualizar ahora»: mantiene el mutex de la app mientras «se cierra», prepara
la instalación con el código del actualizador y sale. Con --src se usa el actualizador de otra versión (p. ej. la
que tiene instalada el usuario), porque es esa versión la que ejecuta la actualización.

Uso:
  python devtools/diagnose_update.py <instalador.exe> [--close-seconds N] [--src carpeta] [--app LabelBridge.exe]
  python devtools/diagnose_update.py --hold <segundos>
"""

from __future__ import annotations

import argparse
import ctypes
import inspect
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUTEX = "LabelBridgeRunningMutex"


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--hold":  # solo mantener el mutex, como una app que tarda en cerrarse
        ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX)
        time.sleep(float(sys.argv[2]))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("installer")
    parser.add_argument("--close-seconds", type=float, default=0.0)
    parser.add_argument("--src", default=str(ROOT))
    parser.add_argument("--app", default="")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.src).resolve()))
    from labelbridge import updater

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX)  # como la app abierta
    print(f"PID {os.getpid()} · mutex tomado: {bool(mutex)} · actualizador de {updater.__file__}", flush=True)
    updater.can_install = lambda: True
    installer = Path(args.installer).resolve()
    if args.app and "app" in inspect.signature(updater.install_and_restart).parameters:
        updater.install_and_restart(installer, Path(args.app))
    else:
        updater.install_and_restart(installer)
    print(f"Actualización preparada; la «app» tarda {args.close_seconds:.0f} s en cerrarse", flush=True)
    time.sleep(args.close_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
