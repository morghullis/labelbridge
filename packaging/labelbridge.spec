# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: genera dist/LabelBridge/LabelBridge.exe (carpeta, arranque rápido y menos falsos positivos de antivirus)
# Uso (en Windows): pyinstaller packaging/labelbridge.spec --noconfirm --clean

import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
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
    ],
    hiddenimports=[
        "keyring.backends.Windows",
        "win32timezone",
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
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LabelBridge",
    console=False,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(version_file),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LabelBridge")
