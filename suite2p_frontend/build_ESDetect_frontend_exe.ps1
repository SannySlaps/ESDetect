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

$pyinstaller = @("-m", "PyInstaller")
try {
    python @pyinstaller --version | Out-Null
} catch {
    throw "PyInstaller not found in the active environment. Install with: pip install pyinstaller"
}

python @pyinstaller --noconfirm ".\\ESDetectFrontend.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Output folder: $root\\dist\\ESDetect"
Write-Host "Executable:    $root\\dist\\ESDetect\\ESDetect.exe"
