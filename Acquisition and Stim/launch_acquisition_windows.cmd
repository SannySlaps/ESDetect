@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "APP_MAIN=%SCRIPT_DIR%Calcium_Imaging_copy.py"
set "APP_PY=%SCRIPT_DIR%venv\Scripts\python.exe"

if exist "%APP_PY%" (
    "%APP_PY%" "%APP_MAIN%"
) else (
    python "%APP_MAIN%"
)
