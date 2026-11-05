# Installs IFC to BSWX Converter for the current user (no administrator rights needed).
$ErrorActionPreference = 'Stop'
$name    = 'IFC to BSWX Converter'
$version = '1.0.0'
$src     = Join-Path $PSScriptRoot 'app'
$dest    = Join-Path $env:LOCALAPPDATA 'Programs\IFCtoBSWX'
$exe     = Join-Path $dest 'IFCtoBSWX.exe'

if (-not (Test-Path (Join-Path $src 'IFCtoBSWX.exe'))) { throw "The 'app' folder was not found next to this script. Extract the whole zip first." }
Get-Process IFCtoBSWX -ErrorAction SilentlyContinue | Stop-Process -Force
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory $dest -Force | Out-Null
Copy-Item "$src\*" $dest -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') $dest -Force

# shortcuts: Start menu + Desktop
$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"),
                   (Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk"))) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $exe; $s.WorkingDirectory = $dest; $s.IconLocation = "$exe,0"
    $s.Description = 'Convert IFC models to StruWalker / BIMReview .bswx'
    $s.Save()
}

# "Open with" entry for .ifc files (does not change the default program)
$cmd = 'HKCU:\Software\Classes\Applications\IFCtoBSWX.exe\shell\open\command'
New-Item $cmd -Force | Out-Null
Set-ItemProperty $cmd -Name '(default)' -Value "`"$exe`" `"%1`""
New-Item 'HKCU:\Software\Classes\.ifc\OpenWithList\IFCtoBSWX.exe' -Force | Out-Null

# Apps & features entry
$un = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\IFCtoBSWX'
New-Item $un -Force | Out-Null
$size = [int]((Get-ChildItem $dest -Recurse | Measure-Object Length -Sum).Sum / 1KB)
$props = @{ DisplayName = $name; DisplayVersion = $version; Publisher = 'IFCtoBSWX'; DisplayIcon = $exe
            InstallLocation = $dest; EstimatedSize = $size; NoModify = 1; NoRepair = 1
            UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`"" }
foreach ($k in $props.Keys) { Set-ItemProperty $un -Name $k -Value $props[$k] }

Write-Host "$name $version installed to $dest"
Write-Host 'Start it from the Start menu or the Desktop shortcut.'
