# Compila LabelBridge en Windows: pruebas -> ejecutable (PyInstaller) -> instalador (Inno Setup).
# Uso: abre PowerShell en la carpeta del proyecto y ejecuta:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv")) { py -3.12 -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt -r requirements-dev.txt

$env:QT_QPA_PLATFORM = "offscreen"
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Las pruebas fallaron" }
Remove-Item Env:QT_QPA_PLATFORM

& $py packaging\make_icon.py
& $py packaging\make_installer_images.py
& $py -m PyInstaller packaging\labelbridge.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }

$version = & $py -c "import labelbridge; print(labelbridge.__version__)"
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc "/DAppVersion=$version" packaging\installer.iss
    Write-Host "Instalador listo en dist\installer\LabelBridge-Setup-$version.exe" -ForegroundColor Green
} else {
    Write-Warning "Ejecutable listo en dist\LabelBridge\. Instala Inno Setup 6 (https://jrsoftware.org/isdl.php) para generar el instalador."
}
