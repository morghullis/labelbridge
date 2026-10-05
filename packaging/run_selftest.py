"""Ejecuta la autoprueba de la app empaquetada (Mac) con tiempo límite y muestra el informe.

Uso: python packaging/run_selftest.py dist/LabelBridge.app/Contents/MacOS/LabelBridge
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    binary = sys.argv[1]
    report = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "selftest.txt"
    report.unlink(missing_ok=True)  # nunca mostrar el informe de una prueba anterior
    try:
        code = subprocess.run([binary, "--self-test", str(report)], timeout=600, check=False).returncode
    except subprocess.TimeoutExpired:
        print("La autoprueba no terminó en 10 minutos")
        return 1
    print(report.read_text(encoding="utf-8") if report.exists() else "No se generó el informe")
    if code:
        print(f"La autoprueba de la app falló (código {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
