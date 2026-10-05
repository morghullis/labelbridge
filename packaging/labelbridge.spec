# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: en Windows genera dist/LabelBridge/LabelBridge.exe (carpeta: arranque rápido y menos falsos positivos de
# antivirus); en Mac, dist/LabelBridge.app (después packaging/make_dmg.py hace el .dmg).
# Uso: pyinstaller packaging/labelbridge.spec --noconfirm --clean

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).parent
MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "labelbridge" / "__init__.py").read_text()).group(1)
parts = (VERSION.split(".") + ["0", "0", "0"])[:4]
VERSION_TUPLE = tuple(int(p) for p in parts)

version_file = ROOT / "build" / "version_info.txt"
version_file.parent.mkdir(exist_ok=True)
version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={VERSION_TUPLE}, prodvers={VERSION_TUPLE}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040A04B0', [
      StringStruct('CompanyName', 'LabelBridge'),
      StringStruct('FileDescription', 'LabelBridge - impresión automática de etiquetas'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'LabelBridge'),
      StringStruct('OriginalFilename', 'LabelBridge.exe'),
      StringStruct('ProductName', 'LabelBridge'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])
  ]
)""",
    encoding="utf-8",
)

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "n8n"), "n8n"),
        (str(ROOT / "docs"), "docs"),
        # El cliente HTTP del SDK de Anthropic lee su versión de los metadatos del paquete al importarse.
        *copy_metadata("httpx2"),
        *copy_metadata("httpcore2"),
        *copy_metadata("anthropic"),
    ],
    hiddenimports=[
        *(["keyring.backends.Windows", "win32timezone"] if WINDOWS else []),
        *(["keyring.backends.macOS"] if MAC else []),
        "PySide6.QtPdf",
        "PySide6.QtSvg",
        "PySide6.QtPrintSupport",
        "PySide6.QtNetwork",
    ],
    excludes=[
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth",
        "PySide6.QtLocation",
        "PySide6.QtSensors",
    ],
    noarchive=False,
)
def _keep(entry) -> bool:
    """La app usa widgets sin OpenGL: fuera el OpenGL por software, D3D y traducciones que no usamos."""
    dest = str(entry[0]).replace("\\", "/").lower()
    name = dest.rsplit("/", 1)[-1]
    if name in ("opengl32sw.dll", "d3dcompiler_47.dll"):
        return False
    if "/translations/" in dest and name.endswith(".qm") and not name.endswith("_es.qm"):
        return False
    return True


a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LabelBridge",
    console=False,
    icon=str(ROOT / "assets" / ("icon.icns" if MAC else "icon.ico")),
    version=str(version_file) if WINDOWS else None,
    argv_emulation=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LabelBridge")

if MAC:
    app = BUNDLE(
        coll,
        name="LabelBridge.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="com.labelbridge.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "LabelBridge",
            "CFBundleDisplayName": "LabelBridge",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "15.0",
            "LSApplicationCategoryType": "public.app-category.business",
            "NSHighResolutionCapable": True,
            # macOS pide permiso para usar la red local: buscar impresoras (Bonjour) e imprimir en ellas por IP.
            "NSLocalNetworkUsageDescription": "LabelBridge busca impresoras en tu red local e imprime en ellas.",
            "NSBonjourServices": ["_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp"],
        },
    )
