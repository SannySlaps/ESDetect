$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exePath = Join-Path $root "dist\\CalciumImaging.exe"

if (-not (Test-Path $exePath)) {
    throw "Executable not found at $exePath. Build first with: .\\build_calcium_imaging_exe.ps1"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Calcium Imaging.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path $exePath -Parent
$shortcut.IconLocation = "$exePath,0"
$shortcut.Description = "Launch Calcium Imaging GUI"
$shortcut.Save()

Write-Host "Desktop shortcut created:"
Write-Host $shortcutPath
