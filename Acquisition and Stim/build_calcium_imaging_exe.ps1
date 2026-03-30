param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if ($Clean) {
    if (Test-Path ".\\build") { Remove-Item ".\\build" -Recurse -Force }
    if (Test-Path ".\\dist") { Remove-Item ".\\dist" -Recurse -Force }
}

$pyinstaller = ".\\venv\\Scripts\\pyinstaller.exe"
if (-not (Test-Path $pyinstaller)) {
    throw "PyInstaller not found in .\\venv\\Scripts. Install with: .\\venv\\Scripts\\pip.exe install pyinstaller"
}

$python = ".\\venv\\Scripts\\python.exe"
& $python -c "import pycromanager" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "pycromanager is not installed in this venv. Install with: .\\venv\\Scripts\\pip.exe install pycromanager"
}

& $pyinstaller --noconfirm ".\\CalciumImaging.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Executable folder: $root\\dist"
Write-Host "Executable file:   $root\\dist\\CalciumImaging.exe"
