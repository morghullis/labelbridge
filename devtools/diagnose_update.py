"""Diagnóstico de la actualización automática en Windows (lo usa .github/workflows/diagnose-update.yml).

Hace lo mismo que la app al pulsar «Actualizar ahora»: deja el mutex de la app tomado mientras «se cierra»,
lanza el mismo PowerShell que espera a que el proceso termine y ejecuta el instalador en silencio, y sale.

Uso: python devtools/diagnose_update.py <instalador.exe> [segundos_que_tarda_en_cerrarse]
     python devtools/diagnose_update.py --hold <segundos>
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    if sys.argv[1] == "--hold":  # solo mantener el mutex, como una app que no termina de cerrarse
        from labelbridge.system.single_instance import WINDOWS_MUTEX

        ctypes.windll.kernel32.CreateMutexW(None, False, WINDOWS_MUTEX)
        time.sleep(float(sys.argv[2]))
        return 0
    installer = Path(sys.argv[1]).resolve()
    closing_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    from labelbridge import updater
    from labelbridge.system.single_instance import WINDOWS_MUTEX

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, WINDOWS_MUTEX)  # como la app abierta
    print(f"PID {os.getpid()} · mutex {WINDOWS_MUTEX} tomado: {bool(mutex)}", flush=True)
    updater.can_install = lambda: True
    updater.install_and_restart(installer)
    print(f"PowerShell lanzado; la «app» tarda {closing_seconds:.0f} s en cerrarse", flush=True)
    time.sleep(closing_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
