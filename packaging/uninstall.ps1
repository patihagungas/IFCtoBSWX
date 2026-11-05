# Removes IFC to BSWX Converter (installed by install.ps1). Converted .bswx files are not touched.
$name = 'IFC to BSWX Converter'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\IFCtoBSWX'
Get-Process IFCtoBSWX -ErrorAction SilentlyContinue | Stop-Process -Force
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"),
                   (Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk"))) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
}
Remove-Item 'HKCU:\Software\Classes\Applications\IFCtoBSWX.exe' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Classes\.ifc\OpenWithList\IFCtoBSWX.exe' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\IFCtoBSWX' -Recurse -Force -ErrorAction SilentlyContinue
# the running script lives inside $dest, so delete the folder from a short-lived helper process
Start-Process cmd.exe -WindowStyle Hidden -ArgumentList "/c timeout /t 2 >nul & rmdir /s /q `"$dest`""
Write-Host "$name has been removed."
