$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appMain = Join-Path $scriptDir 'Calcium_Imaging_copy.py'
$venvPython = Join-Path $scriptDir 'venv\Scripts\python.exe'

Set-Location $scriptDir

if (Test-Path $venvPython) {
    & $venvPython $appMain
} else {
    python $appMain
}
