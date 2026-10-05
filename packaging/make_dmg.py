"""Crea el .dmg de Mac: LabelBridge.app y un acceso a Aplicaciones para arrastrarla.

Firma: con MAC_SIGN_IDENTITY (el certificado propio de LabelBridge, que la compilación de GitHub toma de sus secretos)
cada versión es «la misma app» para la Mac, así las actualizaciones no vuelven a pedir permisos ni la contraseña del
Llavero. Sin él se firma ad hoc (sirve para probar). En los dos casos la primera vez se abre con Ajustes del Sistema →
Privacidad y seguridad → Abrir igualmente; con una cuenta del Apple Developer Program se firmaría y notarizaría aquí.

Uso (en Mac, después de PyInstaller): python packaging/make_dmg.py  →  dist/installer/LabelBridge-<versión>-mac.dmg
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_MACOS = (15, 0)  # LSMinimumSystemVersion de labelbridge.spec (los módulos de PySide6 6.11 piden macOS 15)
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}


def app_version() -> str:
    text = (ROOT / "labelbridge" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def minimum_macos(binary: Path) -> tuple[int, ...] | None:
    """La macOS mínima que pide un binario (parte arm64), según sus comandos de carga."""
    out = subprocess.run(["otool", "-arch", "arm64", "-l", str(binary)], capture_output=True, text=True,
                         check=False).stdout
    found = re.search(r"cmd LC_BUILD_VERSION\s.*?minos (\d+)\.(\d+)", out, re.DOTALL) or re.search(
        r"cmd LC_VERSION_MIN_MACOSX\s.*?version (\d+)\.(\d+)", out, re.DOTALL)
    return (int(found.group(1)), int(found.group(2))) if found else None


def too_new_for_minimum(app: Path) -> list[str]:
    """Los binarios de la app que no abrirían en la macOS mínima que promete (p. ej. un Python compilado para otra)."""
    problems = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(4) not in MACHO_MAGIC:
                continue
        minos = minimum_macos(path)
        if minos and minos > MIN_MACOS:
            problems.append(f"{path.relative_to(app)} (macOS {minos[0]}.{minos[1]})")
    return problems


def main() -> None:
    app = ROOT / "dist" / "LabelBridge.app"
    if not app.is_dir():
        raise SystemExit(f"No existe {app}: compila primero con PyInstaller")
    problems = too_new_for_minimum(app)
    if problems:
        text = f"{len(problems)} binario(s) piden una macOS más nueva que {MIN_MACOS[0]}.{MIN_MACOS[1]}: " + ", ".join(problems[:5])
        if os.environ.get("CI") == "true":
            raise SystemExit(text)
        print("Aviso (solo en esta Mac de pruebas):", text)
    # Sin firma macOS dice que la app «está dañada»; firmada, solo pide confirmarla la primera vez.
    identity = os.environ.get("MAC_SIGN_IDENTITY", "").strip() or "-"
    subprocess.run(["codesign", "--force", "--deep", "--timestamp=none", "--sign", identity, str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    requirement = subprocess.run(["codesign", "-d", "-r-", str(app)], capture_output=True, text=True, check=False)
    print("Firma:", "ad hoc" if identity == "-" else identity, "·", (requirement.stdout or requirement.stderr).strip().splitlines()[-1])
    out = ROOT / "dist" / "installer" / f"LabelBridge-{app_version()}-mac.dmg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "LabelBridge"
        stage.mkdir()
        subprocess.run(["ditto", str(app), str(stage / "LabelBridge.app")], check=True)
        (stage / "Applications").symlink_to("/Applications")
        subprocess.run(["hdiutil", "create", "-volname", "LabelBridge", "-srcfolder", str(stage), "-ov",
                        "-format", "UDZO", str(out)], check=True)
    print(f"Imagen de disco: {out} ({out.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
