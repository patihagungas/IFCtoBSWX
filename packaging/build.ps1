# Builds the installable app into  release\IFCtoBSWX-<version>-Setup.zip
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$version = (Select-String -Path ifc2bswx\gui.py -Pattern "^VERSION = '(.+)'").Matches[0].Groups[1].Value

# 1. clean virtual environment with only what the converter needs (keeps the app small)
$venv = Join-Path $root 'build\venv'
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    python -m venv $venv
    & "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    & "$venv\Scripts\python.exe" -m pip install --quiet ifcopenshell==0.8.4.post1 numpy==2.4.3 scipy==1.17.1 pyinstaller==6.19.0
}

# 2. freeze
& "$venv\Scripts\python.exe" -m PyInstaller packaging\IFCtoBSWX.spec --noconfirm --clean `
    --distpath build\dist --workpath build\work
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

# 3. assemble the installer package
$stage = Join-Path $root "build\IFCtoBSWX-$version-Setup"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
Copy-Item build\dist\IFCtoBSWX "$stage\app" -Recurse
Copy-Item packaging\Install.cmd, packaging\install.ps1, packaging\uninstall.ps1, packaging\INSTALL.txt $stage
Copy-Item README.md "$stage\app\README.md"
New-Item -ItemType Directory release -Force | Out-Null
$zip = "release\IFCtoBSWX-$version-Setup.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$stage\*" -DestinationPath $zip
Write-Host "Built $zip"
